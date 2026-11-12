# Persistent table tabs implementation plan

**Goal:** Keep opened tables in closable tabs in the right workspace, with fast
switching and independent data/query state during the current application run.

**Design:** Add an outer `table_tabs` QTabBar above the existing Data/Query/
Structure widget. Retain one shared workspace view and store explicit per-tab
state in a focused module. Use `(connection.id, database, original table/key)`
identity, preserving binary Redis keys. A fresh instance token distinguishes a
closed tab from a later tab opened for the same identity.

**Behavior:** Opening an existing identity selects it; opening another table
creates a tab. Close buttons remove only their tab; closing the active tab selects
an adjacent one, and closing the last clears the workspace. Labels and tooltips
distinguish connections/DBs. Tabs are scrollable and movable. Existing inner tabs
and both interface languages remain available. Session restart restoration is
outside this request.

**State:** Preserve page/page size, filters/sort, schema/data results, query text/
cursor/results/status/running state and inner tab index, selected rows/scroll/
column widths, inspector visibility, Redis snapshot and raw string draft/preview
filter. Stop pending shared debounce timers before context changes; restore
controls with signals blocked. A pending filter must eventually fetch the
correct table rather than silently retain mismatched rows.

**Async:** Reads, schema and queries capture the originating tab object and a
per-operation request token. Inactive results update that tab's cache; active
results also render. Closed/superseded requests cannot update another tab or
resurrect one. Errors belong to the originating tab. Preserve captured Redis DB
operations and the global Redis write locks. Writes invalidate/refresh their
origin data, including after switching away; they cannot target the newly
selected table. Connection removal closes its tabs and ignores stale replies.

**Constraints:** Python/PySide6, no system credential backend, existing 500-key
and 1,000-preview limits, no changes to adapter authentication/data formats.
Keep the native sidebar splitter and existing SQL/Mongo/Redis workflows.

- [x] Add failing native SQLite acceptance tests for opening/switching/reusing/
      closing, retained data/filter/page/query state and connection isolation.
- [x] Implement tab state and outer bar with scoped styling and translations;
      integrate activation, table opens, background reads/queries/writes and
      connection lifecycle with explicit identity/request ownership.
- [x] Verify races: same-connection A/B, A/B/A supersession, close/reopen, inactive
      query completion, errors, pending filter debounce, Redis DB and binary keys.
- [x] Review requirements and code quality; run relevant regressions and live
      MySQL/Redis workflows. MongoDB live checks depend on local service health.
- [x] Inspect actual Chinese/English Qt screenshots, update user docs and
      verification, build a fresh macOS artifact and verify smoke/signature/ZIP.

This directory has no Git repository; no worktree or commit is required.

Completed: final guarded suite **737 passed in 60.68s**, including 67 live
MySQL/Redis tests; 14 unavailable MongoDB live cases excluded. Independent review,
Ruff, compileall, pip checks, bilingual visual inspection, frozen smoke test,
ad-hoc signature verification, all 1,211 Python archive entries and ZIP CRC passed.
Artifact: `dist/build-20261111-163911-scf0pjol/BaseSkiff-macOS-arm64.zip`.
