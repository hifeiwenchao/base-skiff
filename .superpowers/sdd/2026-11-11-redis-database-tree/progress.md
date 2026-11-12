# SDD ledger — plan: docs/superpowers/plans/2026-11-11-redis-database-tree.md

Workspace is not a Git repository; use current checkout, file ownership and
independent review in place of Git worktrees/commit-based review packages.
Requirements are the user's Redis DB hierarchy/search placement and follow-up
removal of data-grid double-click editing. No additional approval is required.

| Tasks / shared boundary | Producer / consumer | Preflight finding |
| --- | --- | --- |
| Acceptance / implementation | Native DB nodes, scope roles and lazy workers | Agree on DB string scope and public node kinds; retain raw key identity |
| Implementation / migration | New DB navigation replaces old combo | Remove selector rather than hide it; migrate existing behavioral assertions |
| Implementation / visuals | Disposable host and persistent search group | Uniform row heights must be false; defer rebuild while mode popup is open |
| Implementation / tabs | Explicit node scope into existing workspace identity | Same key in different DBs must retain distinct tabs and writes |
| Acceptance | DB structure and actual Qt lifecycle | Nine initial tests failed on old production implementation as expected |
| Validation / package | Current source and frozen artifact | Redis change requires new isolated build; previous Mongo-only no-rebuild finding is historical |
| Documentation | New behavior in both languages | Preserve previous service validation records and clarify current build |

Ruling: Apply removal of double-click editing to both the ordinary data grid
and Redis entry grid, preserving explicit edit buttons, history reopen and tree
expansion. This matches the request's no-special-action data-grid behavior.

Task 1: complete — initial native acceptance tests red (9 failed in 1.20s),
expected missing DB hierarchy, pre-expansion scan and absent per-DB branches.
Additional lifecycle, late response, scoped tabs, popup and narrow layout
acceptance tests added before production implementation landed.

Task 2: complete — table_tabs_implementation owns window.py, redis_tree.py,
theme.py, redis_value.py. Implementation begins after observed red baseline.

Task 3: complete — redis_tree_test_migration owns previous hierarchy/combo
tests and tests/redis_tree_helpers.py; initial migrated hierarchy test also red.

Review: table_tabs_final_review independently checks lifecycle/native gestures,
popup refresh, scopes and callback ownership without editing production files.

Review fixes completed with failing regression evidence: latest DB and key
navigation intent during validation; original-scope key clicks; writes
invalidating in-flight search; mode width in real screenshots; popup clear
preserving pre-search expansion; explicit-key reveal mounting controls; and
reopening a collapsed connection mounting its already-expanded DB controls.
Independent review rechecked original reproductions, binary read/write scopes,
UTF-16 selection in both directions, menu lifetime and native gestures.

Current native acceptance: 29 cases. Migrated initial 107 unit/live cases
passed; follow-up Redis browser and explicit editor-button 37 cases passed
with zero keyring imports. Actual six Qt screenshots generated for zh/en at
200/350/560px and visually inspected. SQL and Redis grid native double-click
probes caused no edit action. Three preliminary full runs exposed the final
host/reveal paths plus an existing Oracle ORA-01466 same-DDL-time flake;
production fixes are frozen and Oracle test timing is under independent
native-driver investigation. No service settings or production SQL adapters
were modified in this round. A package built before the final root-reopen fix
is superseded by a new isolated build from the frozen sources.

Oracle readiness was reproduced independently with native oracledb: fixed 1.1s
was insufficient (1466 at 1.325s, ready 1.489s). Only the test fixture now polls
native read-only snapshot readiness up to 5s for code 1466. Formal app assertions
are not retried; all 9 Oracle cases passed and owned object audit was zero.

Final scoped navigation review reproduced DB0 outer-tab selection being stolen
by a pending DB1 validation. A three-line origin workspace identity guard fixes
this; the regression failed first (DB1 != DB0), then 43 tree/DB UI cases passed.
The legacy reactivation test now activates a real connection workspace before
starting the switch instead of leaving current_connection and workspace owners
inconsistent. Independent probes confirmed both explicit tab safety and same
workspace reactivation semantics. Production is frozen again; the prior full
813-case run passed with zero keyring imports, with final full run and isolated
rebuild underway after this small final fix.

Task 4: complete within available environment — final source 713 passed,
101 live cases deselected, 31.76s, zero keyring imports. Final full live attempt
was interrupted after native MySQL fixture SSL read stalled; two independent
read-only probes timed out for Docker API and fresh MySQL greetings. No service
restart/configuration change was authorized or performed. The earlier 813-case
complete run remains a pre-final-guard result, not claimed for final sources.
Diagnostic and pytest processes were interrupted and private config removed.

Task 5: complete — README/Redis guide/live/SSH/verification docs updated with
accurate chronology and environment boundary. Final artifact:
dist/build-20261111-180322-ibh6qd8s/BaseSkiff-macOS-arm64.zip (48.7 MiB).
Frozen smoke passed, strict deep ad-hoc codesign passed, ZIP CRC passed, every
1211 PYZ entry extracted, all 23 BaseSkiff modules matched current source,
no keyring modules. Previous user app bundles remain untouched. Windows/Linux
remain configured build targets, without claiming local native validation.

Ruling: Retain this small ledger because the checkout has no Git history; it is
the recovery record for completed work and the final live-service limitation.
