# SQL Console Design

## Goal

Give each SQL data source a persistent Console that replaces the table Query tab, while organizing the sidebar as `Console / History / Tables`.

## Scope and behavior

- SQL connections (SQLite, PostgreSQL, MySQL and Oracle) expose three direct children: Console, History and Tables. Tables contain the existing table nodes. MongoDB and Redis keep their existing sidebar structure.
- Console state is scoped by `(connection_id, database)`. SQLite uses its database filename as the database scope, matching operation history. The editor text is stored in the local metadata SQLite database and restored after restarting BaseSkiff or reopening the Console. Query results and execution status are transient.
- The Console is an independent workspace with one SQL editor, an execution action, a result grid and status text. A selected editor range is executed when present; otherwise the whole editor text is executed. Shift+Enter executes the selected range, and Ctrl+Enter / Cmd+Enter runs the current selection or the whole document.
- Table workspaces retain Data and Structure pages. The Query page is removed from the table workspace because SQL execution belongs to Console.
- Existing single-statement SQL execution, read-only enforcement, operation history, table editing, database scope and asynchronous request guards remain unchanged.

## Persistence

Add a `sql_consoles` table to `metadata.sqlite`:

```sql
CREATE TABLE IF NOT EXISTS sql_consoles (
    connection_id TEXT NOT NULL,
    database TEXT NOT NULL,
    content TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (connection_id, database)
)
```

Saving replaces the row for the current scope. Removing a connection removes its Console rows. Empty editor content is still stored so an intentional cleared Console remains cleared. No query result bodies are persisted.

## UI and data flow

`MainWindow` owns the Console editor and result widgets. Clicking the Console node selects the SQL connection, loads the scoped content through a worker, and switches to the Console workspace. Text changes are debounced before persistence and a final save occurs when switching away or closing the window. Running a query captures the connection/database scope and selected text before queueing the service operation; late results update only the originating Console state.

The existing query editor/result widgets are moved into the Console workspace rather than duplicated. Table workspaces keep their data grid, filters, staged edits and structure view, but no longer expose the Query inner tab.

## Validation

- Sidebar tests assert the exact SQL hierarchy, group expansion and table navigation.
- Service tests cover Console save/load, scope isolation, connection deletion cleanup and restart persistence.
- Qt tests cover Console selection, debounced persistence, selected-text execution, Shift+Enter, query-tab removal and late result isolation.
- Full pytest, Ruff, compileall and the local Conda PyInstaller smoke build must pass before commit.
