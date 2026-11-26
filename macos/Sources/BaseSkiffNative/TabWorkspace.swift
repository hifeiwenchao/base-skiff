import AppKit
import SwiftUI

struct TabWorkspace: View {
  @ObservedObject var store: WorkbenchStore
  @ObservedObject var tab: WorkTab
  @State private var transfer = false
  @State private var inspecting = false
  @State private var inspectText = ""
  @StateObject private var documents = ConsoleDocuments()
  @StateObject private var completions = ConsoleCompletion()
  @State private var runToken = 0
  @State private var explainNext = false
  var body: some View {
    VStack(spacing: 0) {
      if tab.mode == "redis" {
        RedisWorkspace(store: store, tab: tab)
      } else if tab.mode == "console" {
        console
      } else {
        if tab.mode == "table" {
          HStack(spacing: 18) {
            Button(store.t("数据", "Data")) { tab.inner = "data" }.foregroundStyle(
              tab.inner == "data" ? Color.skiff : Color.muted)
            Button(store.t("结构", "Structure")) { tab.inner = "structure" }.foregroundStyle(
              tab.inner == "structure" ? Color.skiff : Color.muted)
            Spacer()
            if tab.readOnly {
              Label(store.t("只读", "Read-only"), systemImage: "lock").foregroundStyle(Color.muted)
            }
          }.buttonStyle(.plain).padding(.horizontal, 20).frame(height: 40).overlay(
            alignment: .bottom
          ) { Divider() }
        }
        if tab.inner == "structure" && sqlKinds.contains(tab.kind) {
          StructureWorkspace(store: store, tab: tab)
        } else if tab.inner == "structure" && tab.kind == "MongoDB" {
          NativeGrid(
            columns: ["name", "data_type", "primary_key", "nullable"],
            rows: tab.schema.map { fields($0) })
        } else {
          if tab.mode == "table" && sqlKinds.contains(tab.kind) { conditions }
          if tab.mode == "table" { quickFilter }
          toolbar
          grid
          pagination
        }
      }
    }
    .sheet(isPresented: $transfer) { TransferWorkspace(store: store, tab: tab) }
    .sheet(isPresented: $inspecting) {
      VStack(alignment: .leading, spacing: 12) {
        Text(store.t("记录详情", "Record details")).font(.title2)
        TextEditor(text: $inspectText).font(.system(.body, design: .monospaced))
        HStack {
          Spacer()
          Button(store.t("关闭", "Close")) { inspecting = false }
        }
      }.padding(22).frame(width: 680, height: 520)
    }
  }
  var conditions: some View {
    HStack(spacing: 10) {
      Text("WHERE").font(.system(size: 10, weight: .semibold)).foregroundStyle(Color.muted)
      TextField(store.t("条件表达式", "Condition expression"), text: $tab.whereDraft).textFieldStyle(
        .roundedBorder
      ).onSubmit(apply)
      Text("ORDER BY").font(.system(size: 10, weight: .semibold)).foregroundStyle(Color.muted)
      TextField("column ASC / DESC", text: $tab.orderDraft).textFieldStyle(.roundedBorder).onSubmit(
        apply)
      Button(store.t("查询", "Apply"), action: apply).buttonStyle(SkiffButtonStyle(primary: true))
        .disabled(tab.busy || tab.pending > 0)
    }.padding(.horizontal, 18).padding(.vertical, 11).background(Color.sidebar.opacity(0.5))
  }
  var toolbar: some View {
    HStack(spacing: 8) {
      Button {
        Task { await store.load(tab) }
      } label: {
        Image(systemName: "arrow.clockwise")
      }.help(store.t("刷新", "Refresh"))
      if tab.canEdit {
        Divider().frame(height: 20)
        Button {
          store.addRow(tab)
        } label: {
          Image(systemName: "plus")
        }.help(store.t("新增行", "Add row"))
        Button {
          store.deleteSelectedRows(tab)
        } label: {
          Image(systemName: "minus")
        }.disabled(tab.selected == nil).help(store.t("暂存删除", "Stage deletion"))
        Button(store.t("提交", "Commit") + (tab.pending > 0 ? " (\(tab.pending))" : "")) {
          Task { await store.commit(tab) }
        }.buttonStyle(SkiffButtonStyle(primary: tab.pending > 0)).disabled(
          tab.pending == 0 || tab.busy)
        if tab.pending > 0 {
          Text(store.t("修改尚未写入数据库", "Changes are not saved yet")).foregroundStyle(Color.muted).font(
            .system(size: 10))
        }
      }
      if tab.busy {
        ProgressView().controlSize(.small)
        Button(store.t("中断", "Stop")) { Task { await store.cancel(tab) } }
      }
      Spacer()
      Button {
        if let row = tab.selected, tab.rows.indices.contains(row) {
          inspectText = pretty(.object(tab.rows[row]))
          inspecting = true
        }
      } label: {
        Image(systemName: "sidebar.right")
      }.disabled(tab.selected == nil).help(store.t("记录详情", "Record details"))
      Menu {
        if tab.canEdit {
          Button(store.t("撤销修改", "Revert changes")) { store.revert(tab) }.disabled(tab.pending == 0)
        }
        Button(store.t("CSV 导入 / 导出…", "CSV import / export…")) { transfer = true }
        if tab.mode == "history" {
          Button(store.t("在 Console 中打开", "Open in Console")) {
            if let row = tab.selected, tab.rows.indices.contains(row) {
              let draft = text(tab.rows[row]["query"])
              Task {
                await store.open(
                  TreeTarget(
                    connection: tab.connection,
                    database: text(tab.rows[row]["database"], tab.database), action: "console"))
                store.current?.draft = draft
              }
            }
          }.disabled(tab.selected == nil)
          Button(store.t("清空历史…", "Clear history…")) { clearHistory() }
        }
        if tab.mode == "table" && tab.kind == "MongoDB" {
          Button(store.t("新增文档…", "Insert document…")) { mongoEdit(false) }
          Button(store.t("编辑文档…", "Edit document…")) { mongoEdit(true) }.disabled(
            tab.selected == nil)
          Button(store.t("删除文档…", "Delete document…")) { mongoDelete() }.disabled(
            tab.selected == nil)
        }
      } label: {
        Image(systemName: "ellipsis")
      }
    }.buttonStyle(SkiffButtonStyle()).padding(.horizontal, 18).frame(height: 47).overlay(
      alignment: .bottom
    ) { Divider() }
  }
  var quickFilter: some View {
    HStack(spacing: 8) {
      Image(systemName: "magnifyingglass").foregroundStyle(Color.muted)
      TextField(store.t("所有字段快捷筛选", "Quick filter across all fields"), text: $tab.searchDraft)
        .textFieldStyle(.roundedBorder).onSubmit(applySearch).frame(maxWidth: 400)
      Button(store.t("筛选", "Filter"), action: applySearch).buttonStyle(SkiffButtonStyle())
      if tab.searchDraft != tab.appliedSearch {
        Text(store.t("尚未应用", "Not applied")).font(.system(size: 10)).foregroundStyle(Color.muted)
      }
      Spacer(minLength: 0)
    }.disabled(tab.busy || tab.pending > 0).padding(.horizontal, 18).padding(.vertical, 8)
      .background(Color.sidebar.opacity(0.25))
  }
  var grid: some View {
    NativeGrid(
      columns: tab.columns, rows: tab.rows, selectedRow: $tab.selected,
      editableColumns: tab.canEdit && !tab.busy ? Set(tab.columns) : [],
      onEdit: { row, column, value in store.edit(tab, row: row, column: column, value: value) },
      fontSize: UserDefaults.standard.double(forKey: "native.fontSize") == 0
        ? 12 : UserDefaults.standard.double(forKey: "native.fontSize"),
      rowHeight: UserDefaults.standard.string(forKey: "native.rowDensity") == "comfortable"
        ? 43 : 32,
      protectedColumns: Dictionary(
        uniqueKeysWithValues: tab.originalRows.indices.map { ($0, Set(tab.primaryKeys)) }),
      changedCells: tab.updates.mapValues { Set($0.keys) }, deletedRows: tab.deleted,
      insertedRows: Set(tab.originalRows.count..<tab.rows.count),
      onSetNull: tab.canEdit && !tab.busy
        ? { row, column in store.setNull(tab, row: row, column: column) } : nil,
      selectedRows: $tab.selectedRows,
      onSortColumn: tab.mode == "table" ? { column in sort(column) } : nil,
      sortColumn: tab.sortColumn, sortDescending: tab.descending)
  }
  var pagination: some View {
    HStack(spacing: 12) {
      Text("\(tab.rows.count) " + store.t("行", "rows")).foregroundStyle(Color.muted)
      if !tab.deleted.isEmpty { Text("−\(tab.deleted.count)").foregroundStyle(.red) }
      if tab.mode == "console", let line = tab.errorLine {
        Text(
          store.t(
            "查询错误 \(line):\(tab.errorColumn ?? 1)", "Query error \(line):\(tab.errorColumn ?? 1)")
        ).foregroundStyle(.red).help(
          store.t("位置相对于此次执行的 SQL。", "Coordinates are relative to the executed SQL."))
      }
      Spacer()
      Text(tab.status).foregroundStyle(Color.muted)
      if tab.mode == "table" {
        Picker(
          store.t("每页", "Page size"),
          selection: Binding(
            get: { tab.pageSize },
            set: { size in
              guard !tab.busy, tab.pending == 0 else { return }
              tab.pageSize = size
              tab.page = 1
              tab.cursors = [:]
              Task { await store.load(tab) }
            })
        ) {
          ForEach([50, 100, 200, 500, 1000], id: \.self) {
            Text("\($0) / " + store.t("页", "page")).tag($0)
          }
        }.labelsHidden().frame(width: 105).disabled(tab.busy || tab.pending > 0)
        Button {
          tab.page = max(1, tab.page - 1)
          Task { await store.load(tab) }
        } label: {
          Image(systemName: "chevron.left")
        }.disabled(tab.page <= 1 || tab.busy || tab.pending > 0)
        Text("\(tab.page)")
        Button {
          tab.page += 1
          Task { await store.load(tab) }
        } label: {
          Image(systemName: "chevron.right")
        }.disabled(!tab.hasMore || tab.busy || tab.pending > 0)
        if let total = tab.total {
          Text(store.t("共 \(total) 行", "\(total) total"))
        } else {
          Button(store.t("统计总数", "Count rows")) { Task { await store.load(tab, exactCount: true) } }
            .disabled(tab.busy || tab.pending > 0)
        }
      }
    }.font(.system(size: 11)).buttonStyle(.borderless).padding(.horizontal, 18).frame(height: 37)
      .background(Color.sidebar.opacity(0.55)).overlay(alignment: .top) { Divider() }
  }
  var console: some View {
    VStack(spacing: 0) {
      if sqlKinds.contains(tab.kind) { documentBar }
      HStack(spacing: 8) {
        Button(store.t("运行查询", "Run query")) {
          explainNext = false
          runToken += 1
        }.buttonStyle(SkiffButtonStyle(primary: true)).disabled(
          tab.busy || documents.busy || !tab.consoleLoaded)
        if tab.busy {
          ProgressView().controlSize(.small)
          Button(store.t("中断查询", "Stop query")) { Task { await store.cancel(tab) } }.buttonStyle(
            SkiffButtonStyle())
        }
        if sqlKinds.contains(tab.kind) {
          Button(store.t("格式化", "Format")) {
            Task {
              let source = tab.draft
              let document = tab.consoleDocumentID
              do {
                let formatted = text(
                  try await store.rpc.call("format_sql", params: ["sql": .string(source)]))
                if tab.alive && tab.draft == source && tab.consoleDocumentID == document {
                  tab.draft = formatted
                }
              } catch { store.report(error) }
            }
          }.buttonStyle(SkiffButtonStyle()).disabled(
            tab.busy || documents.busy || !tab.consoleLoaded)
          Button(store.t("执行计划", "Explain")) {
            explainNext = true
            runToken += 1
          }.buttonStyle(SkiffButtonStyle()).disabled(
            tab.busy || documents.busy || !tab.consoleLoaded)
        }
        Spacer()
        Text("⌘Enter / ⇧Enter").font(.system(size: 10)).foregroundStyle(Color.muted)
        Menu {
          Button(store.t("打开 SQL 文件…", "Open SQL file…")) { openSQL() }
          Button(store.t("保存 SQL 文件…", "Save SQL file…")) { saveSQL() }
          Button(store.t("导出结果…", "Export result…")) { transfer = true }
        } label: {
          Image(systemName: "ellipsis")
        }.disabled(!tab.consoleLoaded || documents.busy)
      }.padding(.horizontal, 18).frame(height: 48).overlay(alignment: .bottom) { Divider() }
      VSplitView {
        NativeEditor(
          text: $tab.draft, runToken: runToken, errorPosition: tab.errorPosition,
          completionWords: completions.words, usesDefaultCompletions: false,
          usesCurrentStatement: sqlKinds.contains(tab.kind),
          onCompletionRequest: { source, caret in
            Task {
              await completions.request(store: store, tab: tab, source: source, utf16Caret: caret)
            }
          },
          onRun: { query in
            let explain = explainNext
            explainNext = false
            if tab.consoleLoaded && !tab.busy && !documents.busy {
              Task { await store.execute(tab, query: query, explain: explain) }
            }
          }
        ).disabled(!tab.consoleLoaded || documents.busy).frame(minHeight: 120, idealHeight: 250)
        VStack(spacing: 0) {
          if !tab.plan.isEmpty { QueryPlanView(plan: tab.plan) } else { grid }
          pagination
        }.frame(minHeight: 120)
      }
    }.onChange(of: tab.draft) { _, _ in
      tab.errorPosition = nil
      tab.errorLine = nil
      tab.errorColumn = nil
      if tab.consoleLoaded { store.scheduleSave(tab) }
    }
    .task { if sqlKinds.contains(tab.kind) { await documents.load(store: store, tab: tab) } }
  }
  var documentBar: some View {
    HStack(spacing: 8) {
      Image(systemName: "doc.text").foregroundStyle(Color.muted)
      Picker(
        store.t("查询文档", "Query document"),
        selection: Binding(
          get: { tab.consoleDocumentID },
          set: { id in Task { await documents.select(id, store: store, tab: tab) } })
      ) {
        Text(store.t("默认 Console", "Default Console")).tag("")
        ForEach(documents.documents.indices, id: \.self) { index in
          Text(text(documents.documents[index]["name"])).tag(
            text(documents.documents[index]["document_id"]))
        }
      }.labelsHidden().frame(maxWidth: 280)
      Button {
        Task { await documents.create(store: store, tab: tab) }
      } label: {
        Image(systemName: "plus")
      }.help(store.t("新建文档", "New document"))
      Menu {
        Button(store.t("重命名…", "Rename…")) {
          Task { await documents.rename(store: store, tab: tab) }
        }.disabled(tab.consoleDocumentID.isEmpty)
        Button(store.t("删除文档…", "Delete document…")) {
          Task { await documents.delete(store: store, tab: tab) }
        }.disabled(tab.consoleDocumentID.isEmpty)
      } label: {
        Image(systemName: "ellipsis")
      }
      if documents.busy { ProgressView().controlSize(.small) }
      Spacer()
      Text(store.t("自动保存在本机", "Saved automatically on this Mac")).font(.system(size: 10))
        .foregroundStyle(Color.muted)
    }.disabled(documents.busy || tab.busy || !tab.consoleLoaded).buttonStyle(.borderless).padding(
      .horizontal, 18
    ).frame(height: 38).background(Color.sidebar.opacity(0.45)).overlay(alignment: .bottom) {
      Divider()
    }
  }
  func applySearch() {
    guard !tab.busy, tab.pending == 0 else { return }
    tab.appliedSearch = tab.searchDraft
    tab.page = 1
    tab.cursors = [:]
    Task { await store.load(tab) }
  }
  func sort(_ column: String) {
    guard !tab.busy, tab.pending == 0 else { return }
    tab.descending = tab.sortColumn == column ? !tab.descending : false
    tab.sortColumn = column
    tab.appliedOrder = ""
    tab.orderDraft = ""
    tab.page = 1
    tab.cursors = [:]
    Task { await store.load(tab) }
  }
  func apply() {
    guard !tab.busy, tab.pending == 0 else { return }
    tab.appliedWhere = tab.whereDraft
    tab.appliedOrder = tab.orderDraft
    if !tab.orderDraft.isEmpty {
      tab.sortColumn = ""
      tab.descending = false
    }
    tab.page = 1
    tab.cursors = [:]
    Task { await store.load(tab) }
  }
  func openSQL() {
    let panel = NSOpenPanel()
    panel.allowedContentTypes = [.plainText]
    if panel.runModal() == .OK, let url = panel.url {
      do { tab.draft = try String(contentsOf: url, encoding: .utf8) } catch { store.report(error) }
    }
  }
  func saveSQL() {
    let panel = NSSavePanel()
    panel.nameFieldStringValue = "query.sql"
    if panel.runModal() == .OK, let url = panel.url {
      do { try tab.draft.write(to: url, atomically: true, encoding: .utf8) } catch {
        store.report(error)
      }
    }
  }
  func clearHistory() {
    let a = NSAlert()
    a.messageText = store.t("清空此数据库历史？", "Clear history for this database?")
    a.addButton(withTitle: store.t("清空", "Clear"))
    a.addButton(withTitle: store.t("取消", "Cancel"))
    if a.runModal() == .alertFirstButtonReturn {
      Task {
        do {
          _ = try await store.rpc.call("clear_history", params: tab.scope)
          await store.load(tab)
        } catch { store.report(error) }
      }
    }
  }
  func mongoEdit(_ editing: Bool) { Task { await mongoEditAsync(editing) } }
  @MainActor func mongoEditAsync(_ editing: Bool) async {
    guard !tab.readOnly, !tab.busy, tab.alive else { return }
    let row = tab.selected.flatMap { tab.rows.indices.contains($0) ? tab.rows[$0] : nil }
    let a = NSAlert()
    a.messageText =
      editing
      ? store.t("编辑文档（Extended JSON）", "Edit document (Extended JSON)")
      : store.t("新增文档（Extended JSON）", "Insert document (Extended JSON)")
    let editor = NSTextView(frame: NSRect(x: 0, y: 0, width: 580, height: 300))
    editor.font = .monospacedSystemFont(ofSize: 12, weight: .regular)
    do {
      editor.string =
        editing
        ? text(try await store.rpc.call("bson_to_text", params: ["value": .userObject(row ?? [:])]))
        : "{}"
    } catch {
      store.report(error)
      return
    }
    let scroll = NSScrollView(frame: editor.frame)
    scroll.documentView = editor
    scroll.hasVerticalScroller = true
    a.accessoryView = scroll
    a.addButton(withTitle: store.t("提交", "Submit"))
    a.addButton(withTitle: store.t("取消", "Cancel"))
    guard a.runModal() == .alertFirstButtonReturn else { return }
    Task {
      do {
        let document = try await store.rpc.call(
          "bson_from_text", params: ["text": .string(editor.string)])
        if editing {
          guard let id = row?["_id"] else { throw CocoaError(.coderInvalidValue) }
          await store.mutateDocument(
            tab, action: "update", values: document, keys: .object(["_id": id]))
        } else {
          await store.mutateDocument(tab, action: "insert", values: document)
        }
      } catch { store.report(error) }
    }
  }
  func mongoDelete() {
    guard !tab.readOnly, !tab.busy, tab.alive, let row = tab.selected,
      tab.rows.indices.contains(row),
      let id = tab.rows[row]["_id"]
    else { return }
    let a = NSAlert()
    a.messageText = store.t("删除选中文档？", "Delete selected document?")
    a.addButton(withTitle: store.t("删除", "Delete"))
    a.addButton(withTitle: store.t("取消", "Cancel"))
    if a.runModal() == .alertFirstButtonReturn {
      Task {
        await store.mutateDocument(tab, action: "delete", keys: .object(["_id": id]))
      }
    }
  }
}

func pretty(_ value: WireValue) -> String {
  let e = JSONEncoder()
  e.outputFormatting = [.prettyPrinted, .sortedKeys, .withoutEscapingSlashes]
  return (try? String(data: e.encode(value), encoding: .utf8)) ?? value.displayText
}
