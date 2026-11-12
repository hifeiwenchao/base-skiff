# BaseSkiff Implementation Plan

> **For agentic workers:** Use subagent-driven-development with independent file ownership and final review.

**Goal:** Deliver a working Python database desktop app named BaseSkiff, with six source adapters, CRUD, history, bilingual UI and cross-platform packaging.

**Architecture:** Qt-independent dataclasses and database service; native PySide6 widgets; background QRunnable tasks; locally encrypted credentials and SQLite metadata.

**Tech Stack:** Python 3.11+, PySide6, sqlite3, PyMySQL, psycopg, PyMongo, redis, oracledb, cryptography, pytest, PyInstaller.

**Spec:** docs/superpowers/specs/2026-11-11-baseskiff-design.md

## Global Constraints

- Python application and native Qt UI, macOS/Windows/Linux.
- Immediate Chinese/English switching.
- Real SQLite sample and explicit external credentials; no simulated remote success.
- No plaintext persisted passwords; bind values and quote identifiers.
- Background database tasks; bounded query results and paginated table browsing.

### Task 1: Database service

Files: baseskiff/service.py, baseskiff/adapters.py, tests/test_service.py. Consume baseskiff/models.py dataclasses. Produce DatabaseService(data_dir=None) with list_connections(), save_connection(Connection), delete_connection(id), test_connection(Connection), list_tables(id), get_schema(id, table), fetch_rows(id, table, page=1, page_size=50, search='', sort_column='', descending=False), execute(id, query), insert_row(id, table, values), update_row(id, table, keys, values), delete_rows(id, table, keys_list), list_history(), clear_history(). Return dataclasses for typed values and QueryResult for operations. Write and run SQLite behavioral tests first, then implement six adapters and metadata persistence. Each database operation opens its own connection for worker safety.

### Task 2: Native dialogs and localization

Files: baseskiff/i18n.py, baseskiff/ui/dialogs.py. Produce Translator(language='zh') with tr(key, **kwargs), set_language(language), language property. Produce ConnectionDialog(parent=None, connection=None, language='zh') with result_connection() -> Connection; RowDialog(columns, row=None, parent=None, language='zh') with values() -> dict. Support all six kinds, file chooser, grouped credentials and read-only. Use QSS object names and accept/reject controls. Support JSON values for MongoDB and Redis.

### Task 3: Main native interface

Files: baseskiff/app.py, baseskiff/ui/window.py, baseskiff/ui/theme.py, baseskiff/ui/icons.py, baseskiff/ui/workers.py, tests/test_ui.py. Run Qt smoke tests before implementing interface. Build sidebar, data source overview, data grid, query editor, schema panel, history, language switch, connection and row actions, primary-key guards, selection inspector, CSV export, keyboard shortcuts. Never block Qt on network I/O. Launch via python -m baseskiff.

### Task 4: Packaging and review

Files: pyproject.toml, requirements.txt, BaseSkiff.spec, scripts/build.py, .github/workflows/build.yml, README.md. Install isolated dependencies. Run pytest, compileall and ruff. Build with PyInstaller on Mac; launch packaged executable using offscreen smoke test. Configure independent target OS CI jobs. Review adapters and destructive actions, fix actionable issues, rerun affected checks. Deliver local executable path and honest live-server limitations.
