# Native worker protocol v1

Launch `python -m baseskiff.native.worker --data-dir /absolute/path` (or the frozen worker with the same flag). Omit `--data-dir` only for an intentional real application session; tests must use a temporary directory. No Qt module is imported. The process reuses `DatabaseService`, its metadata and encrypted local credentials, database drivers and SSH behavior.

Transport is UTF-8 JSON, one envelope per LF-terminated line on stdin/stdout, up to **16 MiB per frame**. stdout contains only protocol frames. Requests are independent and responses can arrive out of order. At most four database tasks run concurrently, with 32 total active/queued requests. A full queue produces an error; clients must not automatically retry writes. EOF drains accepted tasks and exits cleanly. The client must set its own startup timeout and detect process exit; never retry an interrupted write without checking its outcome.

```json
{"version":1,"id":"unique-active-id","method":"list_connections","params":{}}
{"version":1,"id":"unique-active-id","result":[{"id":"...","name":"...","kind":"SQLite"}]}
```

Every successful response has `version`, `id`, and `result` (which may be null). Errors have `error: {"message":"...","cancelled":false}` instead of `result`. Invalid envelopes may have a null id. IDs must be nonempty strings of at most 256 characters and unique among active requests. Unknown methods are rejected; no arbitrary object or attribute access is exposed. Clients must ignore unknown result fields for forward compatibility.

Call `hello` with empty params first. Its result contains `version:1`, `application:"BaseSkiff"`, `methods`, `max_message_bytes:16777216`, and diagnostic `qt_loaded:false`. The methods list is authoritative for feature availability. Version mismatch is an error.

## Values

Dataclass results are ordinary JSON objects using their field names. Lists and ordinary string-key dictionaries remain arrays and objects. Binary and exact values use tags:

| Python value | Wire value |
| --- | --- |
| bytes | `{"$type":"bytes","value":"BASE64"}` |
| integer outside ±(2^53−1) | `{"$type":"integer","value":"9007199254740993"}` |
| Decimal | `{"$type":"decimal","value":"1.00000000001"}` |
| date / datetime / time | `{"$type":"date","value":"2026-11-26"}` (corresponding tag and ISO 8601) |
| UUID | `{"$type":"uuid","value":"..."}` |
| BSON scalar | `{"$type":"bson","value":"CANONICAL_EXTENDED_JSON"}` |
| nonfinite float | `{"$type":"float","value":"nan"}` (`inf`, `-inf`) |
| dictionary with non-string keys or a `$type` key | `{"$type":"map","value":[[KEY,VALUE],...]}` |

Small integers remain JSON integers, never floats. BSON Binary preserves its subtype. BSON ObjectId, Decimal128, Regex and Timestamp preserve their BSON types. Unknown values fail encoding rather than using `str`. Nesting is limited to 100 levels. Tag-shaped user dictionaries must use the `map` envelope to avoid collision.

A Redis continuation is `{"$type":"handle","value":"opaque-id","kind":"RedisKeyContinuation"}` or `RedisValueContinuation`. `RedisValue` additionally contains a `snapshot` handle for `continue_redis_value`. Pass handles unchanged as `params.continuation` or `params.snapshot`. Handles are process-local, bound to the request's connection/database scope, expire after 30 minutes and are capped at 256. Use the same explicit database argument when continuing; after expiration or restart refresh the view. Release unused handles with `release {"handles":["opaque-id",...]}`. A released/foreign handle fails; it never silently restarts a scan. The client should release superseded snapshots and continuations as a view advances or closes.

## Service calls

Parameters match `DatabaseService` keyword signatures exactly, including `connection_id`, `database`, `table`, `query`, and the original binary Redis `key`. Omit `database` for SQL connections; only Redis supports this service override. `save_connection` and `test_connection` accept `{"connection":{...Connection fields...}}`. Saved/listed connections return empty secret fields; blank secrets on edit preserve existing encrypted values. No credentials are passed on the command line or logged.

Exposed service methods:

- Connections: `list_connections`, `save_connection`, `delete_connection`, `test_connection`.
- Browsing: `list_tables`, `fetch_rows`, `get_schema`, `list_indexes`, `list_constraints`.
- SQL/Mongo/Redis commands: `execute`, `explain`.
- Record mutation: `insert_row`, `update_row`, `delete_rows`, `apply_grid_changes` (updates are `[keys,values]` pairs; inserts are field objects; deletes are key objects).
- DDL: `rename_table`, `truncate_table`, `add_column`, `drop_column`, `drop_table`, `create_index`, `drop_index`. The UI owns all destructive confirmations; the service enforces read-only restrictions.
- Redis: `list_redis_databases`, `set_redis_database`, `search_redis_keys`, `read_redis_key`, `continue_redis_value`, `mutate_redis_entry`, `set_redis_ttl`.
- History: `list_history`, `count_history`, `clear_history`.
- Console drafts: `load_console`, `save_console`, `list_console_documents`, `save_console_document`, `delete_console_document`.
- Redis searches: `list_redis_search_history`, `record_redis_search`, `set_redis_search_favorite`, `delete_redis_search_history`.
- CSV: `export_csv`, `import_csv`. Files are streamed by the existing service. Options for import/preview are a plain `CsvOptions` object (`encoding`, `delimiter`, `null_value`).

Pure helpers: `preview_value(value,format="auto")`, `encode_preview_value(original,format,text)`, `preview_csv(source,options=null,limit=20)`, `format_sql(sql)`, `current_statement(sql,position)`, `current_statement_span(sql,position)`, `completion_context(sql,position)`, `completion_scope(sql,position,columns)`, `completion_fragment(sql,position)`.

`structure_preview` accepts `kind`, `table`, `columns`, optional `indexes`, `constraints`, and `actions:[{"method":"add_column","params":{...}}]`. Columns/indexes/constraints use their service dataclass field objects. Explicit allowed actions are add/alter/drop column, create/drop index and create/drop constraint. It returns statements and updated definitions without database access. Executing a multi-statement draft requires the UI's explicit workflow; `execute` retains the existing single-statement contract.

## Cancellation and progress

```json
{"version":1,"id":"cancel-1","method":"cancel","params":{"request_id":"query-1"}}
{"version":1,"id":"cancel-1","result":{"accepted":true}}
```

Cancellation is handled by the stdin reader, independently of the database queue. It signals the existing `CancellationToken`; driver interruption runs independently too. `accepted:false` means no active target or commit already began. Acceptance is a request to cancel, not a guarantee that an operation which finished concurrently was undone. Only an operation response with `error.cancelled:true` reports cancellation. Methods without token support may finish normally even if cancellation was accepted; the UI must retain their actual result. Never present a committed write as reverted.

CSV callbacks emit `{"version":1,"id":"...","progress":{"args":[...]}}` before the terminal response; callback argument semantics match the existing service. Progress frames are not terminal results.

## Current limits

Terminal responses above 8 MiB use the chunk transport described below; full SQL CSV export uses the existing streaming file path. Requests remain capped at 16 MiB and reassembled responses at 256 MiB. The client must bound pending calls and discard stale responses using workspace lifetime and request generation. Redis scans retain the existing 500-key batches and service semantics. No worker method bypasses the existing read-only / transactional / credential protections.

## Editing helpers

`bson_to_text(value)` returns formatted canonical Extended JSON; `bson_from_text(text)` parses Extended JSON and returns ordinary wire values with BSON scalar tags. Use these for Mongo document editors rather than ordinary JSON stringify/parse, which loses ObjectId, Decimal128, dates and binary subtypes.

`apply_structure(connection_id,table,columns,indexes=[],constraints=[],actions=[])` infers the saved SQL connection kind, builds the same allowlisted `StructureDraft` as `structure_preview`, and executes each generated statement separately through `DatabaseService.execute`. It accepts no arbitrary SQL statement list and no database override. Success returns `{completed,total,columns,indexes,constraints}` with refreshed metadata. On failure it stops immediately and reports `Completed N/T statements; remaining steps were not executed`, followed by the error and a refresh instruction. Earlier DDL may already be committed; reload metadata before allowing another attempt. Read-only and operation-history checks remain those of the existing service. No atomicity or cancellation is claimed for this multi-step helper.

To create Redis keys use existing `insert_row(connection_id,table,values,database)` with `table` set to the key and `values:{key,type,value,ttl}`. Supported types are string/hash/list/set/zset; hash/zset values use objects, list/set use arrays. Collections must be nonempty with at most 1,000 entries. Existing keys are not overwritten by insertion. For list deletion, disable the UI when `RedisValue.truncated` or `size>1000`; the adapter also rechecks server length and rejects deletion above the limit.

`csv_preview(source,options=null,limit=20)` returns `{columns,rows,stat:[size,mtime_ns]}` and rejects files that change during preview. Pass the returned `stat` array unchanged to `import_csv.expected_stat`; mtime nanoseconds are tagged large integers and must never pass through floating-point conversion. `import_csv.mapping` maps integer CSV source-column indexes to target column names, for example `{0:"id",1:"name"}` in Python, encoded using the wire `map` tag because JSON objects cannot retain integer keys.


## Chunked responses

`hello` advertises `chunked_responses:true` and `max_response_bytes:268435456`. A terminal response whose serialized JSON exceeds 8 MiB is sent as a sequence of frames:

```json
{"version":1,"id":"query-1","chunk":{"index":0,"data":"BASE64","final":false}}
{"version":1,"id":"query-1","chunk":{"index":1,"data":"BASE64","final":true}}
```

Decode each `data` from Base64 and concatenate raw bytes in `index` order, starting at zero. Each chunk contains at most 512 KiB of raw bytes; chunks may split UTF-8 sequences, so decode JSON only after the final chunk. The concatenation is the complete original response envelope (`version`, `id`, `result` or `error`), not just the result value. Dispatch the reconstructed envelope exactly as an ordinary response. Reject missing/duplicate/out-of-order indexes, invalid Base64 and reconstructed payloads over 256 MiB. Do not resolve the pending request before final JSON validates; clear incomplete chunks on timeout, cancellation cleanup or process exit. Chunks for different request IDs may interleave; progress remains an ordinary progress envelope.

The worker spools serialization to a temporary file above the 8 MiB threshold, then emits chunks under stdout backpressure without constructing an extra whole-result byte buffer. Results over 256 MiB return one ordinary error before any chunks are published, with guidance to narrow the query or export to a file. Chunking removes the previous 16 MiB result frame restriction; request limits and bounded service preview/scan limits remain unchanged.

`plan_nodes(result)` accepts the plain `QueryResult` object from `explain` and returns normalized `{id,parent,label,estimates}` nodes using the existing bounded plan parser. `execute` and `explain` error envelopes additionally include driver-derived `position` (one-based character offset), `line`, and `column`; unavailable coordinates are null. No error position is guessed from message text.
