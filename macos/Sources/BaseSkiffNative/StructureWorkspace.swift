import AppKit
import SwiftUI

/// Native catalog and explicit service-backed DDL actions; writes always require review.
struct StructureWorkspace: View {
  @ObservedObject var store: WorkbenchStore
  @ObservedObject var tab: WorkTab
  @ObservedObject var draft: StructureDraftState
  init(store: WorkbenchStore, tab: WorkTab) {
    self.store = store
    self.tab = tab
    self.draft = tab.structureDraft
  }
  @State private var selectedColumn = ""
  @State private var selectedIndex = ""
  @State private var loading = false
  @State private var selectedConstraint = ""
  var body: some View {
    VStack(alignment: .leading, spacing: 12) {
      HStack {
        Text(store.t("字段", "Columns")).font(.headline)
        if tab.pending > 0 {
          Text(store.t("请先提交或撤销表格修改", "Commit or revert grid changes first")).font(.caption)
            .foregroundStyle(Color.orange)
        }
        Spacer()
        Button(store.t("刷新", "Refresh")) { Task { await reload(clearDraft: true) } }
        if loading { ProgressView().controlSize(.small) }
      }
      List(selection: $selectedColumn) {
        ForEach(Array(tab.schema.enumerated()), id: \.offset) { _, column in
          HStack {
            Text(text(column["name"])).frame(width: 180, alignment: .leading)
            Text(text(column["data_type"])).frame(width: 180, alignment: .leading)
            Text(column["primary_key"]?.bool == true ? "PK" : "")
            Text(column["nullable"]?.bool == true ? "NULL" : "NOT NULL")
            Text(column["default"]?.displayText ?? "").foregroundStyle(.secondary)
          }.tag(text(column["name"]))
        }
      }.frame(minHeight: 130)
      if sqlKinds.contains(tab.kind) {
        HStack {
          Button(store.t("重命名表", "Rename table")) { rename() }.disabled(draft.hasPending)
          Button(store.t("添加字段…", "Add column…")) { addColumn() }
          Button(store.t("修改字段…", "Alter column…")) { alterColumn() }.disabled(
            selectedColumn.isEmpty)
          Button(store.t("删除字段…", "Drop column…")) { dropColumn() }.disabled(selectedColumn.isEmpty)
          Button(store.t("清空表…", "Truncate…")) {
            performConfirmed(
              "truncate_table", title: store.t("清空表中的所有数据？", "Remove all rows from this table?"))
          }.disabled(draft.hasPending)
          Button(store.t("删除表…", "Drop table…"), role: .destructive) { dropTable() }.disabled(
            draft.hasPending)
        }.disabled(tab.readOnly || tab.busy || tab.pending > 0 || loading || draft.locked)
        Text(store.t("索引", "Indexes")).font(.headline)
        List(selection: $selectedIndex) {
          ForEach(Array(draft.indexes.enumerated()), id: \.offset) { _, index in
            HStack {
              Text(text(index["name"]))
              Text(values(index["columns"]).map(\.displayText).joined(separator: ", "))
                .foregroundStyle(.secondary)
              if index["unique"]?.bool == true { Text("UNIQUE") }
            }.tag(text(index["name"]))
          }
        }.frame(minHeight: 90)
        HStack {
          Button(store.t("创建索引…", "Create index…")) { createIndex() }
          Button(store.t("删除索引…", "Drop index…")) {
            stage("drop_index", params: ["name": .string(selectedIndex)])
          }.disabled(selectedIndex.isEmpty)
        }.disabled(tab.readOnly || tab.busy || tab.pending > 0 || loading || draft.locked)
        Text(store.t("约束与外键", "Constraints and foreign keys")).font(.headline)
        List(selection: $selectedConstraint) {
          ForEach(Array(draft.constraints.enumerated()), id: \.offset) { _, value in
            Text(value.displayText).font(.system(.caption, design: .monospaced)).tag(
              text(value["name"]))
          }
        }.frame(minHeight: 60)
        HStack {
          Button(store.t("创建约束 / 外键…", "Create constraint / foreign key…")) { createConstraint() }
          Button(store.t("删除约束…", "Drop constraint…")) { stageDropConstraint() }.disabled(
            selectedConstraint.isEmpty)
        }.disabled(tab.readOnly || tab.busy || tab.pending > 0 || loading || draft.locked)
        if !draft.actions.isEmpty {
          Text(store.t("结构草稿 · SQL 预览", "Structure draft · SQL preview")).font(.headline)
          ScrollView {
            Text(draft.sql).font(.system(.caption, design: .monospaced)).textSelection(.enabled)
              .frame(maxWidth: .infinity, alignment: .leading)
          }.frame(minHeight: 90)
          Text(
            store.t(
              "DDL 可能逐条提交；失败后须刷新核实已完成的步骤。",
              "DDL may commit per statement. After a failure, refresh to inspect completed steps.")
          ).font(.caption).foregroundStyle(.secondary)
          HStack {
            Button(store.t("应用草稿…", "Apply draft…")) { applyDraft() }.disabled(
              tab.readOnly || tab.busy || tab.pending > 0 || loading || draft.locked)
            Button(store.t("丢弃草稿", "Discard draft")) {
              draft.actions = []
              draft.sql = ""
            }.disabled(loading || draft.locked)
          }
        }
      }
    }.padding(16).task { await reload() }
  }
  @MainActor private func reload(clearDraft: Bool = false) async {
    if !clearDraft && draft.initialized { return }
    guard tab.alive else { return }
    loading = true
    defer { loading = false }
    let params = tab.base.merging(["table": tab.object]) { _, new in new }
    do {
      let schema = try await store.rpc.call("get_schema", params: params)
      guard tab.alive else { return }
      tab.schema = values(schema)
      if sqlKinds.contains(tab.kind) {
        let newIndexes = try await store.rpc.call("list_indexes", params: params)
        let newConstraints = try await store.rpc.call("list_constraints", params: params)
        guard tab.alive else { return }
        draft.indexes = values(newIndexes)
        draft.constraints = values(newConstraints)
        draft.discard()
        draft.initialized = true
      }
    } catch { if tab.alive { store.report(error) } }
  }
  @MainActor private func input(_ title: String, labels: [String], defaults: [String] = [])
    -> [String]?
  {
    let alert = NSAlert()
    alert.messageText = title
    let stack = NSStackView()
    stack.orientation = .vertical
    stack.alignment = .leading
    stack.spacing = 8
    var inputs: [NSTextField] = []
    for (i, label) in labels.enumerated() {
      stack.addArrangedSubview(NSTextField(labelWithString: label))
      let field = NSTextField(string: i < defaults.count ? defaults[i] : "")
      field.frame.size = NSSize(width: 440, height: 24)
      field.widthAnchor.constraint(equalToConstant: 440).isActive = true
      stack.addArrangedSubview(field)
      inputs.append(field)
    }
    stack.layoutSubtreeIfNeeded()
    stack.frame.size = NSSize(width: 440, height: CGFloat(labels.count * 58))
    alert.accessoryView = stack
    alert.addButton(withTitle: store.t("继续", "Continue"))
    alert.addButton(withTitle: store.t("取消", "Cancel"))
    guard alert.runModal() == .alertFirstButtonReturn else { return nil }
    return inputs.map(\.stringValue)
  }
  @MainActor private func confirm(_ title: String, detail: String = "") -> Bool {
    let alert = NSAlert()
    alert.messageText = title
    alert.informativeText = detail
    alert.alertStyle = .warning
    alert.addButton(withTitle: store.t("确认执行", "Execute"))
    alert.addButton(withTitle: store.t("取消", "Cancel"))
    return alert.runModal() == .alertFirstButtonReturn
  }
  @MainActor private func perform(_ method: String, params: [String: WireValue] = [:]) async {
    guard !tab.readOnly, !tab.busy, tab.pending == 0, tab.alive else { return }
    if await store.operation(
      tab, method: method,
      params: tab.base.merging(["table": tab.object]) { _, new in new }.merging(params) { _, new in
        new
      }) != nil
    {
      await reload(clearDraft: true)
    }
  }
  @MainActor private func performConfirmed(
    _ method: String, params: [String: WireValue] = [:], title: String
  ) {
    guard !tab.readOnly, confirm(title, detail: tab.object.displayText) else { return }
    Task { await perform(method, params: params) }
  }
  @MainActor private func rename() {
    guard !tab.readOnly,
      let entered = input(store.t("重命名表", "Rename table"), labels: [store.t("新名称", "New name")]),
      let name = entered.first, !name.isEmpty,
      confirm(store.t("确认重命名？", "Rename this table?"), detail: name)
    else { return }
    Task {
      if await store.operation(
        tab, method: "rename_table",
        params: tab.base.merging(["table": tab.object, "new_name": .string(name)]) { _, new in new }
      ) != nil {
        store.close(tab)
        store.invalidateConnectionTree(tab.connectionID)
        try? await store.reloadConnections()
      }
    }
  }
  @MainActor private func dropTable() {
    guard !tab.readOnly,
      let entered = input(
        store.t("输入完整表名以删除", "Type the full table name to drop it"),
        labels: [tab.object.displayText]), entered.first == tab.object.displayText
    else { return }
    guard
      confirm(
        store.t("永久删除该表及其全部数据？", "Permanently drop this table and all its data?"),
        detail: tab.object.displayText)
    else { return }
    Task {
      if await store.operation(
        tab, method: "drop_table", params: tab.base.merging(["table": tab.object]) { _, new in new }
      ) != nil {
        store.close(tab)
        store.invalidateConnectionTree(tab.connectionID)
        try? await store.reloadConnections()
      }
    }
  }
  @MainActor private func dropColumn() {
    guard !selectedColumn.isEmpty else { return }
    stage("drop_column", params: ["name": .string(selectedColumn)])
  }
  @MainActor private func addColumn() {
    guard !tab.readOnly,
      let entered = input(
        store.t("添加字段到草稿", "Add column to draft"),
        labels: [store.t("名称", "Name"), store.t("SQL 类型", "SQL type")], defaults: ["", "TEXT"]),
      !entered[0].isEmpty
    else { return }
    stage("add_column", params: ["name": .string(entered[0]), "data_type": .string(entered[1])])
  }
  @MainActor private func alterColumn() {
    guard let column = tab.schema.first(where: { $0["name"]?.string == selectedColumn }),
      let entered = input(
        store.t("修改字段到草稿", "Alter column in draft"),
        labels: [store.t("新名称", "New name"), store.t("SQL 类型", "SQL type")],
        defaults: [selectedColumn, text(column["data_type"])])
    else { return }
    stage(
      "alter_column",
      params: [
        "original": .string(selectedColumn), "name": .string(entered[0]),
        "data_type": .string(entered[1]),
      ])
  }
  private var draftBase: [String: WireValue] {
    [
      "table": tab.object, "columns": .array(tab.schema), "indexes": .array(draft.indexes),
      "constraints": .array(draft.constraints),
    ]
  }
  @MainActor private func stage(_ method: String, params: [String: WireValue]) {
    guard !tab.readOnly, !tab.busy, tab.pending == 0, !loading, !draft.locked else { return }
    loading = true
    let proposed =
      draft.actions + [.object(["method": .string(method), "params": .object(params)])]
    Task {
      defer { loading = false }
      do {
        let preview = try await store.rpc.call(
          "structure_preview",
          params: draftBase.merging(["kind": .string(tab.kind), "actions": .array(proposed)]) {
            _, new in new
          })
        guard tab.alive else { return }
        draft.actions = proposed
        draft.sql = values(preview["statements"]).map(\.displayText).joined(separator: ";\n")
      } catch { store.report(error) }
    }
  }
  @MainActor private func applyDraft() {
    guard !tab.readOnly, !tab.busy, tab.pending == 0, !loading, !draft.actions.isEmpty,
      !draft.locked,
      confirm(store.t("执行此结构草稿？", "Execute this structure draft?"), detail: draft.sql)
    else { return }
    Task {
      loading = true
      defer { loading = false }
      do {
        guard
          let result = await store.operation(
            tab, method: "apply_structure",
            params: draftBase.merging([
              "connection_id": .string(tab.connectionID), "actions": .array(draft.actions),
            ]) { _, new in new })
        else {
          draft.locked = true
          return
        }
        guard tab.alive else { return }
        tab.schema = values(result["columns"])
        draft.indexes = values(result["indexes"])
        draft.constraints = values(result["constraints"])
        draft.actions = []
        draft.sql = ""
      }
    }
  }
  @MainActor private func stageDropConstraint() {
    guard !selectedConstraint.isEmpty,
      confirm(store.t("将删除约束加入草稿？", "Add constraint removal to draft?"), detail: selectedConstraint)
    else { return }
    stage("drop_constraint", params: ["name": .string(selectedConstraint)])
  }
  @MainActor private func createConstraint() {
    guard
      let entered = input(
        store.t("约束草稿", "Constraint draft"),
        labels: [
          "Name", "Kind: FOREIGN KEY / UNIQUE / PRIMARY KEY / CHECK", "Columns (comma separated)",
          "Reference table (FOREIGN KEY)", "Reference columns (comma separated)",
          "ON UPDATE (optional)", "ON DELETE (optional)", "CHECK expression (optional)",
        ], defaults: ["", "FOREIGN KEY", "", "", "", "", "", ""])
    else { return }
    func columns(_ value: String) -> WireValue {
      .array(value.split(separator: ",").map { .string($0.trimmingCharacters(in: .whitespaces)) })
    }
    stage(
      "create_constraint",
      params: [
        "name": .string(entered[0]), "kind": .string(entered[1].uppercased()),
        "columns": columns(entered[2]), "reference_table": .string(entered[3]),
        "reference_columns": columns(entered[4]), "on_update": .string(entered[5].uppercased()),
        "on_delete": .string(entered[6].uppercased()), "definition": .string(entered[7]),
      ])
  }
  @MainActor private func createIndex() {
    guard !tab.readOnly,
      let entered = input(
        store.t("创建索引", "Create index"),
        labels: [
          store.t("索引名", "Index name"), store.t("字段（逗号分隔）", "Columns (comma separated)"),
          store.t("唯一：true / false", "Unique: true / false"),
        ], defaults: ["", "", "false"]), !entered[0].isEmpty
    else { return }
    let columns = entered[1].split(separator: ",").map {
      WireValue.string($0.trimmingCharacters(in: .whitespaces))
    }
    guard !columns.isEmpty, ["true", "false"].contains(entered[2].lowercased()) else {
      store.report(CocoaError(.formatting))
      return
    }
    stage(
      "create_index",
      params: [
        "name": .string(entered[0]), "columns": .array(columns),
        "unique": .bool(entered[2].lowercased() == "true"),
      ])
  }
}
