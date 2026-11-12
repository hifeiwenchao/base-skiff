"""Real MongoDB integration, opt in with a private BASESKIFF_LIVE_CONFIG."""
import json
import uuid
from dataclasses import replace
from datetime import datetime, timezone

import pytest
from bson import Binary, Decimal128, ObjectId, json_util
from pymongo import MongoClient
from pymongo.errors import DuplicateKeyError

from baseskiff.models import Connection

pytestmark = pytest.mark.integration


@pytest.fixture
def mongo_case(live_config, live_service, run_tag):
    config = live_config['mongo']
    name = run_tag + '_' + uuid.uuid4().hex[:8]
    client = MongoClient(host=live_config['host'], port=config['port'], username=config['username'],
                         password=config['password'], serverSelectionTimeoutMS=5000)
    connection = live_service.save_connection(Connection(
        name='Mongo integration', kind='MongoDB', host=live_config['host'], port=config['port'],
        username=config['username'], password=config['password'], database=name))
    try:
        yield live_service, connection, client[name]
    finally:
        client.drop_database(name)
        client.close()


def test_mongo_authentication_schema_and_typed_crud(mongo_case):
    service, connection, db = mongo_case
    assert 'successfully' in service.test_connection(connection)
    db['records'].insert_one({'name': 'first', 'score': 1, 'optional': None})
    assert any(table.name == 'records' for table in service.list_tables(connection.id))
    assert any(column.name == '_id' and column.primary_key for column in service.get_schema(connection.id, 'records'))
    original = {'_id': ObjectId(), 'name': "小舟 O'Reilly", 'number': 23, 'nested': {'tags': ['a', 'b']},
                'amount': Decimal128('1234.5600'), 'binary': Binary(b'\x00\xff', subtype=128),
                'created': datetime(2026, 11, 11, 10, 0, tzinfo=timezone.utc), 'optional': None}
    assert service.insert_row(connection.id, 'records', original).affected == 1
    stored = db.records.find_one({'_id': original['_id']})
    assert stored['amount'] == original['amount'] and stored['binary'] == original['binary']
    assert service.update_row(connection.id, 'records', {'_id': original['_id']}, {'name': 'Updated 小舟'}).affected == 1
    assert db.records.find_one({'_id': original['_id']})['name'] == 'Updated 小舟'
    assert service.delete_rows(connection.id, 'records', [{'_id': original['_id']}]).affected == 1
    assert db.records.find_one({'_id': original['_id']}) is None
    assert len(service.list_history()) == 3


def test_mongo_pagination_sort_and_literal_search(mongo_case):
    service, connection, db = mongo_case
    db.records.insert_many([{'index': index, 'name': f'Customer {index:03}', 'tag': '[literal].*' if index == 42 else 'other'} for index in range(125)])
    result = service.fetch_rows(connection.id, 'records', page=2, page_size=25, sort_column='index', descending=True)
    assert result.total == 125 and len(result.rows) == 25
    assert [row['index'] for row in result.rows] == list(range(99, 74, -1))
    found = service.fetch_rows(connection.id, 'records', search='[literal].*')
    assert found.total == 1 and found.rows[0]['index'] == 42


def test_mongo_find_aggregate_and_extended_json(mongo_case):
    service, connection, db = mongo_case
    identifier = db.records.insert_one({'name': '小舟', 'category': 'A', 'value': 3}).inserted_id
    db.records.insert_one({'name': 'Other', 'category': 'A', 'value': 5})
    result = service.execute(connection.id, json_util.dumps({'find': 'records', 'filter': {'_id': identifier}}))
    assert len(result.rows) == 1 and result.rows[0]['_id'] == identifier
    result = service.execute(connection.id, json.dumps({'aggregate': 'records', 'pipeline': [{'$group': {'_id': '$category', 'total': {'$sum': '$value'}}}]}))
    assert result.rows == [{'_id': 'A', 'total': 8}]
    assert all(entry.success for entry in service.list_history())


def test_mongo_native_write_commands_and_error_history(mongo_case):
    service, connection, db = mongo_case
    service.execute(connection.id, json.dumps({'insert': 'records', 'documents': [{'_id': 'one', 'name': 'before'}]}))
    assert db.records.find_one({'_id': 'one'})['name'] == 'before'
    service.execute(connection.id, json.dumps({'update': 'records', 'updates': [{'q': {'_id': 'one'}, 'u': {'$set': {'name': 'after'}}}]}))
    assert db.records.find_one({'_id': 'one'})['name'] == 'after'
    service.execute(connection.id, json.dumps({'delete': 'records', 'deletes': [{'q': {'_id': 'one'}, 'limit': 1}]}))
    assert db.records.count_documents({}) == 0
    with pytest.raises(ValueError):
        service.execute(connection.id, 'invalid JSON')
    assert service.list_history()[0].success is False


def test_mongo_query_editor_advertised_short_commands(mongo_case):
    service, connection, db = mongo_case
    service.execute(connection.id, json.dumps({'insertOne': 'records', 'document': {'_id': 'one', 'name': 'before'}}))
    assert db.records.find_one({'_id': 'one'})['name'] == 'before'
    service.execute(connection.id, json.dumps({'updateOne': 'records', 'filter': {'_id': 'one'}, 'update': {'$set': {'name': 'after'}}}))
    assert db.records.find_one({'_id': 'one'})['name'] == 'after'
    service.execute(connection.id, json.dumps({'deleteOne': 'records', 'filter': {'_id': 'one'}}))
    assert db.records.count_documents({}) == 0


def test_mongo_result_cap_and_duplicate_failure(mongo_case):
    service, connection, db = mongo_case
    db.records.insert_many([{'_id': index, 'number': index} for index in range(1100)])
    result = service.execute(connection.id, '{"find":"records","filter":{}}')
    assert len(result.rows) == 1000 and result.truncated
    result = service.execute(connection.id, '{"aggregate":"records","pipeline":[]}')
    assert len(result.rows) == 1000 and result.truncated
    with pytest.raises(DuplicateKeyError):
        service.insert_row(connection.id, 'records', {'_id': 0})
    assert not service.list_history()[0].success
    assert db.records.count_documents({}) == 1100


def test_mongo_read_only_enforces_query_and_grid(mongo_case):
    service, connection, db = mongo_case
    db.records.insert_one({'_id': 'safe', 'name': 'original'})
    connection = service.save_connection(replace(connection, read_only=True))
    assert service.fetch_rows(connection.id, 'records').total == 1
    assert len(service.execute(connection.id, '{"find":"records","filter":{}}').rows) == 1
    actions = [
        lambda: service.insert_row(connection.id, 'records', {'_id': 'new'}),
        lambda: service.update_row(connection.id, 'records', {'_id': 'safe'}, {'name': 'unsafe'}),
        lambda: service.delete_rows(connection.id, 'records', [{'_id': 'safe'}]),
        lambda: service.execute(connection.id, '{"delete":"records","deletes":[{"q":{},"limit":0}]}'),
        lambda: service.execute(connection.id, '{"aggregate":"records","pipeline":[{"$out":"other"}]}'),
    ]
    for action in actions:
        with pytest.raises(PermissionError):
            action()
    assert db.records.find_one({'_id': 'safe'})['name'] == 'original'
    assert all(not entry.success for entry in service.list_history()[:5])


def test_mongo_native_command_write_errors_are_not_marked_successful(mongo_case):
    from pymongo.errors import OperationFailure
    service, connection, db = mongo_case
    db.records.insert_one({'_id': 'duplicate', 'name': 'original'})
    with pytest.raises(OperationFailure, match='duplicate'):
        service.execute(connection.id, '{"insert":"records","documents":[{"_id":"duplicate","name":"replacement"}]}')
    assert db.records.find_one({'_id': 'duplicate'})['name'] == 'original'
    entry = service.list_history()[0]
    assert not entry.success and entry.error


def test_mongo_short_update_counts_upsert_with_null_identifier(mongo_case):
    service, connection, db = mongo_case
    result = service.execute(connection.id, '{"updateOne":"records","filter":{"_id":null},"update":{"$set":{"name":"inserted"}},"upsert":true}')
    assert db.records.count_documents({'_id': None}) == 1
    assert result.affected == 1
    assert result.rows[0]['matched_count'] == 0
    assert service.list_history()[0].affected == 1
