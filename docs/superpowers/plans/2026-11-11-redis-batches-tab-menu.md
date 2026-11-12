# Redis batches and tab context menu

User requirements: 500 is a per-expansion load bound, not a total DB tree
ceiling; every loaded key remains visible. Right-clicking an outer data tab
offers closing that tab or the other tabs. Existing Chinese/English, scoped
queries, raw binary identifiers and workspace ownership remain supported.

Design: immutable SCAN continuation retains cursor, overflow and seen keys,
bound to connection/DB/prefix. Each call returns at most 500 and makes at most
8 SCAN calls. UI merges continuation results into its per-DB cache; another DB
expansion or a final Load more row requests the next batch. Search changes and
refresh restart the scan. Tabs/history opening alone does not advance scanning.
Failed continuation retains loaded keys and its retry cursor; tokens discard
outdated replies. Search controls stay in the active DB.

Tab menus use the right-clicked tab's identity and workspace instance; nested
menu events may reorder or close tabs. Close others first activates the kept
tab then uses existing close logic. One-tab menus disable Close other tabs;
empty bar space shows no menu. No database operation is performed by closing.
Native right-button presses/releases are intercepted by the tab bar so opening
or cancelling a menu does not activate an inactive tab. Left-click navigation,
dragging and close buttons retain their existing behavior.

- [x] Red backend tests (14 failures), implement immutable native SCAN paging;
      extend direct and SSH scope validation (18 batch cases).
- [x] Red native UI tests (9 failures), implement merge/continuation/retry and
      per-load status; add adapter-to-tree 1203-key workflow.
- [x] Red native menu tests (5 failures), implement menu/identity safe closure.
- [x] Reproduce native right-button workspace switch, add regression and fix;
      independent actual mouse/context-menu Cancel and Close probes pass.
- [x] Migrate obsolete single-page and object-identity tests while preserving
      data/ownership assertions and live continuation union coverage.
- [x] Independent final review and complete guarded local regression:
      751 passed, 101 live cases deselected in 35.41s; credential imports 0;
      Ruff, compileall and dependency checks pass.
- [x] Actual Chinese/English screenshots, documentation, isolated macOS build
      and smoke/signature/module-source/archive/ZIP checks: all 1,211 PYZ
      entries extracted, 23 BaseSkiff modules match, no keyring; ZIP 48.7 MiB.
      Artifact: `dist/build-20261111-184036-xpnndd2z/BaseSkiff-macOS-arm64.zip`.

Environment: Docker API was unresponsive before this round and a bounded
read-only probe still times out. Real service cases remain unavailable, and
are reported explicitly. No service restart or configuration changes.
