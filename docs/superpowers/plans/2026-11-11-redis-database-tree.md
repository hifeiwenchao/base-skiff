# Redis database tree implementation plan

**Goal:** Make Redis navigation visibly connection → DB → key folders/keys,
and place key search inside its selected DB instead of above every connection.

**Design:** Keep the native ConnectionTree and existing per-DB service/cache.
Every Redis connection has database children, including available empty DBs.
Discover database metadata on connection expansion; load at most 500 keys only
when a DB is expanded or an explicit key/history/tab needs that scope. Cached,
clean branches expand locally. DB discovery must never scan every database.

**Scope:** Add a `(connection_id, db)` node role to DB/control/folder/key nodes.
Preserve original bytes in key identities and existing table tabs. Build all
loaded DB branches from `_redis_key_pages`, not the current-DB compatibility
cache. Open and mutate keys against their node/tab DB. Validation failure keeps
the previous DB/workspace; pending callbacks cannot switch an unrelated view.

**Search:** Retire the DB dropdown. Mount the existing persistent search controls
as the first child of the active expanded DB, with a clear DB-specific label,
Prefix/Exact modes, refresh and limit/status text. The global connection search
stays above the tree. Use a disposable index-widget host; before tree.clear,
reparent the actual controls to a permanent hidden parking widget. Only hosts
belong to QTreeWidget's deletion lifecycle. Use variable row heights, fit narrow
sidebars and restore focus/UTF-16 selection/scroll only when the same search
control owned focus in the same scope. Do not steal editor focus on background
rebuilds. Preserve safe interaction with an open mode popup.

**Navigation:** Native arrow/label/double-click and keyboard expansion must all
load once. Root and DB collapse states persist through refresh/language changes;
filtered expansion applies only to the matching DB's folders. Empty, restricted,
unknown-count and Cluster DB catalogs retain existing backend behavior. Provide
"Choose another DB…" when discovery is incomplete. No hidden dropdown retained
solely for old tests; migrate tests to the new intended UI.

**Constraints:** No changes to adapter/auth/SSH/credentials. Python/PySide6,
Chinese/English, local encrypted credentials, 500-key cap, binary keys, per-DB
search tokens and write ownership, movable/closable table tabs and splitter stay
supported. This workspace is not a Git repository.

- [x] Add failing native acceptance tests for the new hierarchy, lazy loading,
      scoped search position, DB separation, empty DBs and branch gestures.
- [x] Implement DB nodes, explicit scope, lazy activation and search hosts in
      window.py/redis_tree.py/theme.py (small helper module if needed).
- [x] Migrate previous combo/hierarchy tests, preserving behavioral assertions;
      verify lifetime, focus, Unicode selection, stale responses and tab scopes.
- [x] Review and run full guarded regression against all five local services,
      inspect actual Chinese/English screenshots at narrow/wide sidebar sizes.
- [x] Update user docs and verification; build an independent macOS bundle,
      check frozen smoke/signature/archive/ZIP and deliver the new artifact.

**Final validation boundary:** The full suite passed 813 cases before the final
three-line tab navigation ownership guard. Final sources passed 43 focused
cases and 713 local cases (101 live cases explicitly deselected). A complete
final live rerun was interrupted when Docker API and MySQL handshakes stopped
responding; read-only probes confirmed this external blocker. It remains
recorded in VERIFICATION.md, without restarting or reconfiguring user services.
The final independent macOS bundle matches all 23 production modules and passes
its startup, archive, signature and ZIP checks.
