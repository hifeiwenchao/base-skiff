# SQL Console Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a persistent per-connection/database SQL Console, organize SQL sidebar nodes as Console/History/Tables, and remove the table Query page.

**Architecture:** Store Console editor text in a scoped `sql_consoles` metadata table. Reuse the existing SQL query editor, result table and execution service inside a dedicated Console workspace, while table workspaces retain only Data and Structure. Scope every load, save and execution by the captured connection/database identity.

**Tech Stack:** Python 3.11+, PySide6, SQLite metadata storage, existing `DatabaseService`, Qt workers and pytest/pytest-qt.

**Spec:** `docs/superpowers/specs/2026-11-13-sql-console-design.md`

## Global Constraints

- Use the project Conda environment named `base-skiff` for Python commands and dependency use.
- Keep SQL execution single-statement and preserve existing read-only and database-scope protections.
- Keep SQLite, PostgreSQL, MySQL and Oracle SQL nodes grouped; MongoDB and Redis tree behavior stays unchanged.
- Persist only Console text locally; do not persist query results.
- Run `python -m pytest -q`, Ruff, compileall and `scripts/build.py --smoke-test` in the local Conda environment before completion.

---

### Task 1: Persist scoped Console text

**Files:**
- Modify: `baseskiff/service.py` metadata schema and `DatabaseService` methods.
- Modify: `tests/test_service.py` or a focused new service test module.

**Interfaces:**
- Produce `DatabaseService.load_console(connection_id, database) -> str`.
- Produce `DatabaseService.save_console(connection_id, database, content) -> None`.
- Extend connection deletion to remove rows for the deleted connection.

- [ ] **Step 1: Write failing tests** for save/load round trips, connection/database isolation, restart persistence through a new `DatabaseService`, and deletion cleanup.
- [ ] **Step 2: Run the focused tests** and verify they fail because the table and methods do not exist.
- [ ] **Step 3: Add the `sql_consoles` table** with `(connection_id, database)` primary key, content and updated timestamp, then implement parameterized load/save/delete methods.
- [ ] **Step 4: Run the focused tests** and verify all pass.
- [ ] **Step 5: Run Ruff on `baseskiff/service.py` and the focused tests.**

### Task 2: Build the SQL sidebar hierarchy

**Files:**
- Modify: `baseskiff/ui/window.py` tree rebuilding, click handling and expansion capture.
- Modify: `tests/test_workspace_compact_layout.py`, `tests/test_connection_collapse.py`, `tests/test_redis_tree_ui.py`.

**Interfaces:**
- Add tree node kind `sql_console` with scope `(connection_id, database)`.
- Add tree node kind `sql_tables` with scope `(connection_id, database)`.
- Keep `sql_history` scoped to the same pair.

- [ ] **Step 1: Add failing Qt assertions** for `SQL root -> Console, History, Tables -> table`, group expansion persistence, and table click navigation.
- [ ] **Step 2: Run those tests** and verify they fail against the current flat SQL tree.
- [ ] **Step 3: Create the three SQL child nodes** during `rebuild_tree`, move table children under `sql_tables`, include `sql_tables` in expansion capture/click handling, and route `sql_console` to the Console workspace.
- [ ] **Step 4: Run the focused tree tests** and verify they pass without changing MongoDB or Redis trees.
- [ ] **Step 5: Run the connection-collapse and tree UI test modules.**

### Task 3: Convert the workspace to a dedicated Console

**Files:**
- Modify: `baseskiff/ui/window.py` workspace page, query execution, navigation, shortcuts and state restoration.
- Modify: `baseskiff/ui/workspace_tabs.py` only if the existing query state needs a scoped Console state holder.
- Modify: `tests/test_table_tabs.py`, `tests/test_table_tab_races.py` and a focused Console UI test module.

**Interfaces:**
- Add `show_console(connection, database=None)` to activate the scoped Console.
- Add a scoped Console editor state with `connection_id`, `database`, `content`, `query_result`, `query_state` and request generation.
- Keep `run_query()` as the execution entry point and make it select the current text range when present.

- [ ] **Step 1: Add failing tests** asserting two table inner tabs (Data and Structure), Console text restoration, selection execution, and Shift+Enter execution.
- [ ] **Step 2: Run the focused UI tests** and verify they fail because Query is still present and Console navigation is absent.
- [ ] **Step 3: Remove the table Query tab**, mount the existing editor/result widgets in a Console page, add Console navigation and scope activation, and preserve the current connection/database before worker submission.
- [ ] **Step 4: Add a debounced Console save** on text changes plus final save on scope switch and window close; load the saved text when Console is opened.
- [ ] **Step 5: Add Shift+Enter handling** on the editor and keep Ctrl/Cmd+Enter using the selected range when available, otherwise the full document.
- [ ] **Step 6: Run focused Console and table-tab tests** and verify late query results cannot overwrite another Console scope.

### Task 4: Update translations and product documentation

**Files:**
- Modify: `baseskiff/i18n.py` and `baseskiff/ui/window.py` translation labels/tooltips.
- Modify: `README.md` and `docs/REDIS_TREE.md` only where the workspace navigation description references SQL query tabs.

**Interfaces:**
- Provide Chinese and English labels for Console, Tables, selected execution and persistence hints.

- [ ] **Step 1: Add translation assertions** for Console and Tables labels.
- [ ] **Step 2: Add the labels and update the navigation documentation** to describe the new Console and removed table Query page.
- [ ] **Step 3: Run the translation and focused UI tests.**

### Task 5: Full verification, commit, push and local package

**Files:**
- No production file changes expected.

- [ ] **Step 1: Run** `conda run --name base-skiff python -m pytest -q`.
- [ ] **Step 2: Run** `conda run --name base-skiff ruff check baseskiff tests` and `conda run --name base-skiff python -m compileall -q baseskiff tests`.
- [ ] **Step 3: Review `git diff --check`, commit with a Chinese `feature:` message, and push `origin main`.
- [ ] **Step 4: Run** `conda run --name base-skiff python scripts/build.py --smoke-test`.
- [ ] **Step 5: Report the test counts, commit hash, pushed branch and absolute `.app` path.
