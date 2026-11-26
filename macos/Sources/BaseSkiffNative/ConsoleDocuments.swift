import AppKit
import SwiftUI

@MainActor final class ConsoleDocuments: ObservableObject {
  @Published var documents: [WireValue] = []
  @Published var busy = false
  private var loaded = false
  func load(store: WorkbenchStore, tab: WorkTab, force: Bool = false) async {
    guard !loaded || force else { return }
    do {
      documents = values(try await store.rpc.call("list_console_documents", params: tab.scope))
      loaded = true
    } catch { store.report(error) }
  }
  func select(_ id: String, store: WorkbenchStore, tab: WorkTab) async {
    guard !busy, id != tab.consoleDocumentID else { return }
    busy = true
    defer { busy = false }
    tab.saveTask?.cancel()
    do {
      try await flush(store: store, tab: tab)
      documents = values(try await store.rpc.call("list_console_documents", params: tab.scope))
      let content: String
      let name: String
      if id.isEmpty {
        content = text(try await store.rpc.call("load_console", params: tab.scope))
        name = ""
      } else {
        guard let document = documents.first(where: { text($0["document_id"]) == id }) else {
          return
        }
        content = text(document["content"])
        name = text(document["name"])
      }
      guard tab.alive else { return }
      tab.consoleDocumentID = id
      tab.consoleDocumentName = name
      tab.draft = content
      store.persistSession()
    } catch { store.report(error) }
  }
  func create(store: WorkbenchStore, tab: WorkTab) async {
    guard !busy,
      let name = namePrompt(store: store, value: "Query \(documents.count + 1)", rename: false)
    else { return }
    busy = true
    defer { busy = false }
    tab.saveTask?.cancel()
    let identifier = UUID().uuidString
    do {
      try await flush(store: store, tab: tab)
      _ = try await store.rpc.call(
        "save_console_document",
        params: tab.scope.merging([
          "document_id": .string(identifier), "name": .string(name), "content": .string(""),
        ]) { _, new in new })
      guard tab.alive else { return }
      tab.consoleDocumentID = identifier
      tab.consoleDocumentName = name
      tab.draft = ""
      await load(store: store, tab: tab, force: true)
      store.persistSession()
    } catch { store.report(error) }
  }
  func rename(store: WorkbenchStore, tab: WorkTab) async {
    guard !busy, !tab.consoleDocumentID.isEmpty,
      let name = namePrompt(store: store, value: tab.consoleDocumentName, rename: true)
    else { return }
    busy = true
    defer { busy = false }
    tab.saveTask?.cancel()
    do {
      _ = try await store.rpc.call(
        "save_console_document",
        params: tab.scope.merging([
          "document_id": .string(tab.consoleDocumentID), "name": .string(name),
          "content": .string(tab.draft),
        ]) { _, new in new })
      tab.consoleDocumentName = name
      await load(store: store, tab: tab, force: true)
      store.persistSession()
    } catch { store.report(error) }
  }
  private func flush(store: WorkbenchStore, tab: WorkTab) async throws {
    var params = tab.scope.merging(["content": .string(tab.draft)]) { _, new in new }
    if !tab.consoleDocumentID.isEmpty {
      params["document_id"] = .string(tab.consoleDocumentID)
      params["name"] = .string(tab.consoleDocumentName)
      _ = try await store.rpc.call("save_console_document", params: params)
    } else {
      _ = try await store.rpc.call("save_console", params: params)
    }
  }
  func delete(store: WorkbenchStore, tab: WorkTab) async {
    guard !busy, !tab.consoleDocumentID.isEmpty else { return }
    let alert = NSAlert()
    alert.messageText = store.t("删除此查询文档？", "Delete this query document?")
    alert.informativeText = tab.consoleDocumentName
    alert.addButton(withTitle: store.t("删除", "Delete"))
    alert.addButton(withTitle: store.t("取消", "Cancel"))
    guard alert.runModal() == .alertFirstButtonReturn else { return }
    busy = true
    defer { busy = false }
    tab.saveTask?.cancel()
    do {
      _ = try await store.rpc.call(
        "delete_console_document",
        params: tab.scope.merging(["document_id": .string(tab.consoleDocumentID)]) { _, new in new }
      )
      let content = text(try await store.rpc.call("load_console", params: tab.scope))
      guard tab.alive else { return }
      tab.consoleDocumentID = ""
      tab.consoleDocumentName = ""
      tab.draft = content
      await load(store: store, tab: tab, force: true)
      store.persistSession()
    } catch { store.report(error) }
  }
  private func namePrompt(store: WorkbenchStore, value: String, rename: Bool) -> String? {
    let alert = NSAlert()
    alert.messageText =
      rename ? store.t("重命名查询文档", "Rename query document") : store.t("新建查询文档", "New query document")
    let field = NSTextField(string: value)
    field.frame = NSRect(x: 0, y: 0, width: 340, height: 28)
    alert.accessoryView = field
    alert.addButton(withTitle: store.t("保存", "Save"))
    alert.addButton(withTitle: store.t("取消", "Cancel"))
    alert.window.initialFirstResponder = field
    guard alert.runModal() == .alertFirstButtonReturn else { return nil }
    let name = field.stringValue.trimmingCharacters(in: .whitespacesAndNewlines)
    return name.isEmpty ? nil : name
  }
}

@MainActor final class ConsoleCompletion: ObservableObject {
  @Published var words: [String] = []
  private var tables: [String]?
  private var columns: [String: [String]] = [:]
  private var requested: Set<String> = []
  private var generation = 0
  func request(store: WorkbenchStore, tab: WorkTab, source: String, utf16Caret: Int) async {
    guard sqlKinds.contains(tab.kind), source.utf16.count <= 200_000 else {
      words = []
      return
    }
    generation += 1
    let request = generation
    let position = sqlCodepointOffset(source, utf16Caret: utf16Caret)
    let params: [String: WireValue] = ["sql": .string(source), "position": .int(Int64(position))]
    do {
      let context = try await store.rpc.call("completion_context", params: params)
      guard request == generation else { return }
      guard context != .null else {
        words = []
        return
      }
      if tables == nil {
        tables = values(try await store.rpc.call("list_tables", params: tab.base)).map { value in
          let name = text(value["name"])
          let schema = text(value["schema"])
          return schema.isEmpty ? name : schema + "." + name
        }
      }
      let fragment = values(try await store.rpc.call("completion_fragment", params: params))
      let qualifier = fragment.first?.string ?? ""
      let prefix = fragment.count > 1 ? text(fragment[1]) : ""
      var scope = values(
        try await store.rpc.call(
          "completion_scope",
          params: params.merging([
            "columns": .object(columns.mapValues { .array($0.map { .string($0) }) })
          ]) { _, new in new }))
      let missing = scope.count > 1 ? values(scope[1]).map { $0.displayText } : []
      for table in missing where !requested.contains(table.lowercased()) {
        requested.insert(table.lowercased())
        do {
          columns[table] = values(
            try await store.rpc.call(
              "get_schema", params: tab.base.merging(["table": .string(table)]) { _, new in new })
          ).map { text($0["name"]) }
        } catch {
          // Unavailable metadata must not prevent keyword/table completion.
        }
      }
      if !missing.isEmpty {
        scope = values(
          try await store.rpc.call(
            "completion_scope",
            params: params.merging([
              "columns": .object(columns.mapValues { .array($0.map { .string($0) }) })
            ]) { _, new in new }))
      }
      guard request == generation, tab.alive else { return }
      let relations = scope.first?.object ?? [:]
      let ctes = scope.count > 2 ? values(scope[2]).map { $0.displayText } : []
      let before = String(String(source.prefixUnicodeScalars(position)).dropLast(prefix.count))
      let tableContext =
        before.range(
          of: #"\b(?:FROM|JOIN|UPDATE|INTO)\s+(?:[\w$]+\.)?$"#,
          options: [.regularExpression, .caseInsensitive]) != nil
      let choices: [String]
      if !qualifier.isEmpty && tableContext {
        choices = (tables ?? []).filter { $0.lowercased().hasPrefix(qualifier.lowercased() + ".") }
          .map { String($0.dropFirst(qualifier.count + 1)) }
      } else if !qualifier.isEmpty {
        choices = values(relations[qualifier.lowercased()]).map { $0.displayText }
      } else {
        choices =
          QueryTextView.sqlKeywords + (tables ?? []) + ctes
          + relations.values.flatMap { values($0).map { $0.displayText } }
      }
      words = Array(Set(choices.filter { $0.lowercased().hasPrefix(prefix.lowercased()) })).sorted {
        $0.localizedCaseInsensitiveCompare($1) == .orderedAscending
      }.prefix(200).map { $0 }
    } catch { if request == generation { words = QueryTextView.sqlKeywords + (tables ?? []) } }
  }
}

func sqlCodepointOffset(_ source: String, utf16Caret: Int) -> Int {
  String(decoding: source.utf16.prefix(max(0, utf16Caret)), as: UTF16.self).unicodeScalars.count
}

extension String {
  fileprivate func prefixUnicodeScalars(_ length: Int) -> String {
    String(String.UnicodeScalarView(unicodeScalars.prefix(max(0, length))))
  }
}
