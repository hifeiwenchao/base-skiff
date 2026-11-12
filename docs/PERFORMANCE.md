# Performance audit — 2026-11-12

This audit covers Redis key discovery/tree navigation, grid rendering and selection,
resident tabs, history, SQL batch mutations, query conversion and SSH teardown.
The comparison starts at `7e65aef`, after the Windows CI fixes. Measurements use
macOS arm64, Python 3.14.7 and PySide6 6.12.0, with temporary application data and
synthetic inputs unless explicitly marked as live. They are comparative local
measurements, not latency guarantees for other machines or remote databases.

## Measured fixes

| Scenario | Before | After |
| --- | --- | --- |
| Restore 200 selected rows × 20 columns | 19,557 ms | 3.07 ms |
| Repaint a grid cell containing 1 MiB binary data | 755.9 ms | 3.47 ms |
| Populate 200 × 20 nested values | 41.43 ms | 29.25 ms |
| Refresh a 20,000-key Redis tree with many folders | 2,103 ms | 590 ms |
| Build that tree's icons from a cold cache | 52,001 SVG renders | 2 renders |
| Refresh controls for a 2M-character Redis String | 395.3 ms | 20.18 ms |
| Stale String drafts retained by six Hash tabs | 1,200,246 bytes | 0 bytes |
| Delete 500 SQL rows | 502 connections / 501 schema queries | 2 connections / 1 schema query |
| Name 1,000 identically named query columns | 933.70 ms | 0.132 ms |
| Overview history statistic, 10,000 entries with 11.7 KB query bodies | 8.88 ms | 0.499 ms |
| Convert a Redis response with 100,000 top-level values | 12.07 ms / 100,000 decodes | 0.176 ms / 1,000 decodes |
| Close idle SSH listeners | approximately 201–202 ms | 0.182 ms median over 250 runs |

Grid selection now restores complete row ranges in one selection operation and
updates actions once, including Redis collection actions. Grid painting limits
only the delegate's layout text to 4,096 characters. Stored text, tooltips, raw
values, copying and CSV exports remain complete. Each cell is formatted once.

Redis trees reuse up to 128 rendered icon variants and skip per-character escaping
for ordinary short labels. Returned icons are independent copies. All Unicode
code points were compared against the previous text-editability rules, with no
differences. Binary/control-character labels retain their existing behavior.

Starting or retrying a Redis continuation updates the existing final DB row rather
than clearing the tree. The loaded key nodes and search host stay alive; failures
retain the cursor and loaded keys. New results still merge by the full original
key identity and rebuild once. Each batch remains limited to 500 keys, while every
previously loaded key remains visible. Idle processing and scrolling cause no tree
model resets. With the final row visible and layout settled, starting a batch took
17.10 / 17.07 / 17.97 ms for 500 / 5,000 / 20,000 folder-heavy keys, respectively;
including event processing took 27.21 / 26.95 / 28.47 ms. The earlier popup/rebuild
loop is described in `VERIFICATION.md`.

SQL deletion validates the schema once and validates every key before any DELETE;
all deletions still share one transaction. Column naming keeps the same collision
suffixes. Redis query conversion skips only invisible top-level ordered rows;
nested shapes, binary values and truncation indicators retain their semantics.
History counting preserves the existing 1,000-entry display cap and avoids reading
query/error bodies. Preview formatting slices before replacing newlines, and an
empty history search does not copy full query bodies.

SSH shutdown sets the stop flag, wakes each local listener and waits for its accept
worker before closing the listening file descriptor. Wakeup connections are
rejected before forwarding. Keeping that descriptor valid avoids the macOS race
where closing it before the wakeup is consumed leaves `select` waiting. Partial
startup, repeated close, active forwards and partial proxy handshakes are covered.

## Scope and remaining costs

Fifty SQL tabs and 450 switches retained a stable widget count of 351; sampled RSS
was approximately 149–155 MB. Redis discovery over 10,000 synthetic keys retained
one SCAN plus one TYPE pipeline per 500-key batch without increasing batch cost.
No new connection pool or unbounded text cache was introduced.

Loading an entire very large editable value still requires Qt to create its text
document: a 2M-character String took approximately 0.9 seconds after the fixes.
Opening or fully refreshing tens of thousands of accumulated tree nodes also has
layout costs. These operations retain complete data and editable drafts; the
results do not imply constant-time refreshes for arbitrarily large inputs.

## Regressions

The added tests check callback counts, schema/connection counts, bounded decode
work, icon render reuse/eviction, complete exports, Unicode boundaries, retained
selection/current cell/scroll/drafts, continuation identity and SSH cancellation.
They avoid performance thresholds that depend on CI machine speed.

Real-service tests use unique test schemas, collections and key prefixes against
local MySQL, Redis, MongoDB, PostgreSQL and Oracle, including real ephemeral SSH
forwarding. Oracle's SSH fixture now uses the existing native snapshot-readiness
helper after creating its test table; faster teardown exposed the old implicit
wait. Application operations are not retried by that helper. Final commands,
counts, build results and CI links are recorded in `VERIFICATION.md`.
