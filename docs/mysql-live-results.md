# MySQL live integration validation

Validated on 2026-11-11 against the user-supplied local **MySQL 9.7.2** server,
using **Python 3.14.7** and **PyMySQL 1.2.4**. The tests call BaseSkiff's actual
`DatabaseService` and `SQLAdapter`; they do not mock database responses.

Latest live run: **12 passed in 1.52 seconds**. No MySQL adapter defect was
observed in these cases. An additional run without the private configuration
skipped all 12 cases, and a server metadata check found zero leftover MySQL
test schemas after teardown.

| Area | Verified behavior |
| --- | --- |
| Authentication | Administrator and supplied developer credentials both authenticate; a query through each service connection succeeds. |
| Developer permissions | In this supplied environment, the developer account sees only `information_schema` and `performance_schema`; access to the uniquely created administrator-owned test schema fails with server error 1044. No grants or accounts were changed. |
| Real grid CRUD and types | Insert, fetch, update, and delete persist correctly, including `Decimal`, `date`, `datetime`, JSON objects/arrays, NULL, binary bytes, Chinese text, emoji, apostrophes, and SQL-looking text stored as data. |
| Introspection | Tables, views, column order/types, composite primary keys, nullability, and defaults are returned correctly. |
| Pagination and filtering | Multiple pages, descending sort, total counts, empty later pages, literal `%`, `_`, `!`, backslash, apostrophe, and Unicode search work. Unknown sort columns are rejected. |
| Identifier quoting | Table/column identifiers containing reserved words, spaces, dots, and embedded backticks support real CRUD and search. |
| Read-only UI operations | Test connection, SELECT, DESCRIBE, table listing, schema, and filtered fetch work; six query writes/control statements and three grid mutations are rejected. |
| Server read-only enforcement | A SELECT invoking an administrator-created disposable stored function passes the editor's SELECT filter, but MySQL independently rejects the function's INSERT with error 1792. No row persists, and failed execution appears in history. |
| Grid guards | Tables without a primary key, partial/NULL/extra composite keys, and unknown columns are rejected without changing rows. |
| Errors and rollback | Duplicate insertion surfaces error 1062. A batch delete whose second deletion violates a foreign key surfaces error 1451 and rolls back the first deletion; both original parent rows remain. Failure details, duration, and connection identity appear in history, and later queries recover normally. |
| Result limits | A 1,005-row query displays 1,000 rows with truncation. Grid pages cap at 1,000 while preserving the full count and access to the final five rows. Duplicate result labels remain distinct. |
| Statement/default behavior | Multiple statements are rejected with error 1064 and perform neither insertion. Separate successful statements commit. Empty-value insertion uses MySQL defaults; empty deletion and empty update affect no rows. |

## Running safely

The suite is opt-in. Without `BASESKIFF_LIVE_CONFIG`, all cases skip. Point that
variable at a private JSON configuration outside the repository, then run:

```sh
.venv/bin/python -m pytest tests/integration/test_mysql.py -q --tb=short
```

The shared private configuration supplies `host` plus `mysql.port`,
`mysql.root_password`, `mysql.username`, and `mysql.password`. The current test
fixture encrypts credentials in a temporary local data directory and never
accesses the system keyring. No passwords are written to repository files. Each run creates
one uniquely named schema containing only its own test tables, views, and
function, and drops that schema during teardown. Existing schemas and rows,
server accounts, and grants are preserved. The cleanup was confirmed by the
passing teardown.

Developer schema visibility and error 1044 describe this particular account's
permissions, not a restriction imposed by BaseSkiff. On another configured
server, the authentication test conditionally checks schema denial only when
that developer cannot see the created schema.

## Limits

This verifies the service/driver layer against one local MySQL version and the
supplied account configuration. It does not establish compatibility with all
MySQL/MariaDB versions, remote networks, production load, TLS certificates,
other authentication plugins, or server failures/timeouts. Native widget
interaction is covered by the separate GUI validation; this suite itself does
not drive Qt. It uses administrator access only inside its disposable schema
and does not broaden the developer account's privileges.
