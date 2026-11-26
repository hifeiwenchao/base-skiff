from baseskiff.models import QueryResult
from baseskiff.query_plan import normalize_query_plan


def test_sqlite_parent_ids_and_missing_root_parent():
    nodes = normalize_query_plan(QueryResult(rows=[
        {'id': 3, 'parent': 0, 'notused': 0, 'detail': 'SCAN customers'},
        {'id': 8, 'parent': 3, 'notused': 0, 'detail': 'SEARCH orders USING INDEX idx'},
    ]))
    assert [(n.id, n.parent, n.label) for n in nodes] == [('3', None, 'SCAN customers'), ('8', '3', 'SEARCH orders USING INDEX idx')]


def test_postgres_text_preserves_tree_and_estimates():
    nodes = normalize_query_plan(QueryResult(rows=[{'QUERY PLAN': line} for line in [
        'Hash Join  (cost=1.20..30.00 rows=4 width=16)',
        '  Hash Cond: (c.id = o.id)',
        '  ->  Seq Scan on customers c  (cost=0.00..10.00 rows=5 width=8)',
        '        Filter: (active = true)',
        '  ->  Hash  (cost=10.00..10.00 rows=5 width=8)',
        '        ->  Seq Scan on orders o  (cost=0.00..10.00 rows=5 width=8)',
    ]]))
    assert [n.label for n in nodes] == ['Hash Join', 'Seq Scan on customers c', 'Hash', 'Seq Scan on orders o']
    assert [n.parent for n in nodes] == [None, nodes[0].id, nodes[0].id, nodes[2].id]
    assert nodes[0].estimates['rows'] == '4'
    assert nodes[1].estimates['Filter'] == '(active = true)'


def test_postgres_json_native_and_serialized():
    import json
    plan = [{'Plan': {'Node Type': 'Nested Loop', 'Plan Rows': 4, 'Total Cost': 12.5,
                      'Plans': [{'Node Type': 'Index Scan', 'Relation Name': 'customers', 'Index Name': 'customers_pkey'}]}}]
    for value in (plan, json.dumps(plan)):
        nodes = normalize_query_plan(QueryResult(rows=[{'QUERY PLAN': value}]))
        assert len(nodes) == 2
        assert nodes[0].label == 'Nested Loop'
        assert nodes[1].parent == nodes[0].id
        assert 'customers' in nodes[1].label
        assert nodes[0].estimates['Plan Rows'] == 4


def test_mysql_rows_do_not_invent_hierarchy_from_duplicate_select_ids():
    nodes = normalize_query_plan(QueryResult(rows=[
        {'id': 1, 'select_type': 'SIMPLE', 'table': 'c', 'type': 'ALL', 'rows': 20, 'Extra': 'Using where'},
        {'id': 1, 'select_type': 'SIMPLE', 'table': 'o', 'type': 'ref', 'rows': 2, 'key': 'customer_id'},
    ]))
    assert len({n.id for n in nodes}) == 2
    assert all(n.parent is None for n in nodes)
    assert 'c' in nodes[0].label and nodes[0].estimates['rows'] == 20


def test_mysql_json_preserves_nested_loop_order():
    import json
    value = {'query_block': {'select_id': 1, 'nested_loop': [
        {'table': {'table_name': 'c', 'access_type': 'ALL', 'rows_examined_per_scan': 20}},
        {'table': {'table_name': 'o', 'access_type': 'ref', 'rows_examined_per_scan': 2}},
    ]}}
    nodes = normalize_query_plan(QueryResult(rows=[{'EXPLAIN': json.dumps(value)}]))
    assert any('c' in node.label and node.estimates.get('rows_examined_per_scan') == 20 for node in nodes)
    assert any('o' in node.label for node in nodes)


def test_oracle_text_table_uses_operation_indent():
    lines = ['Plan hash value: 123', '----------------------------------------------',
             '| Id | Operation          | Name | Rows | Cost |',
             '----------------------------------------------',
             '|  0 | SELECT STATEMENT   |      |  10  |   2  |',
             '|  1 |  TABLE ACCESS FULL | CUST |  10  |   2  |',
             '----------------------------------------------']
    nodes = normalize_query_plan(QueryResult(rows=[{'PLAN_TABLE_OUTPUT': line} for line in lines]))
    assert len(nodes) == 2
    assert nodes[1].parent == nodes[0].id
    assert 'CUST' in nodes[1].label


def test_empty_unknown_text_and_bounded_deep_json():
    assert normalize_query_plan(QueryResult()) == []
    nodes = normalize_query_plan(QueryResult(rows=[{'plan': 'SCAN some_table'}]))
    assert nodes[0].label == 'SCAN some_table'
    root = {'Node Type': 'root'}
    leaf = root
    for _ in range(100):
        child = {'Node Type': 'child'}
        leaf['Plans'] = [child]
        leaf = child
    nodes = normalize_query_plan(QueryResult(rows=[{'QUERY PLAN': [{'Plan': root}]}]))
    assert len(nodes) <= 65


def test_mysql_cost_metadata_stays_on_parent_and_mixed_case_sqlite_columns():
    value = {'query_block': {'nested_loop': [{'table': {'table_name': 'c'}}], 'cost_info': {'query_cost': '12'}}}
    nodes = normalize_query_plan(QueryResult(rows=[{'EXPLAIN': value}]))
    assert nodes[0].estimates['query_cost'] == '12'
    assert 'query_cost' not in nodes[-1].estimates
    nodes = normalize_query_plan(QueryResult(rows=[{'Id': 1, 'Parent': 0, 'Detail': 'outer'}, {'Id': 2, 'Parent': 1, 'Detail': 'inner'}]))
    assert nodes[1].parent == nodes[0].id
