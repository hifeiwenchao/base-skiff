import AppKit
import SwiftUI

/// One instance is retained by its DB tree row, so typing never replaces the native field.
struct EmbeddedRedisSearch: View {
  @ObservedObject var store: WorkbenchStore
  var parentID: String
  @State private var input = ""
  @State private var mode = "prefix"
  @State private var history: [WireValue] = []
  @State private var initialized = false
  @State private var searching = false
  private func t(_ zh: String, _ en: String) -> String { store.t(zh, en) }
  private var pending: Bool {
    let committed = store.redisQueries[parentID] ?? ("", "prefix")
    return input != committed.0 || mode != committed.1
  }
  var body: some View {
    VStack(alignment: .leading, spacing: 7) {
      HStack(spacing: 5) {
        RedisHistoryCombo(
          text: $input, suggestions: history.map { text($0["text"]) },
          placeholder: t("搜索键", "Search keys"), onPick: selectSuggestion, onSubmit: submit
        )
        .frame(minWidth: 45).accessibilityLabel(t("搜索此 DB 的键", "Search keys in this DB"))
        Button(action: submit) {
          if searching {
            ProgressView().controlSize(.mini)
          } else {
            Image(systemName: "magnifyingglass")
          }
        }.buttonStyle(.borderless).frame(width: 21).disabled(searching).help(
          t("提交搜索", "Submit search"))
      }
      HStack(spacing: 5) {
        Picker("", selection: $mode) {
          Text(t("前缀", "Prefix")).tag("prefix")
          Text(t("精确", "Exact")).tag("exact")
        }.labelsHidden().pickerStyle(.segmented).controlSize(.mini).frame(minWidth: 90)
        historyMenu
      }
      Text(pending ? t("按 Enter 提交", "Enter to apply") : t("仅搜索此 DB", "Searches this DB only"))
        .font(.system(size: 9)).foregroundStyle(.secondary).lineLimit(1)
    }.padding(.horizontal, 4).padding(.vertical, 7).frame(maxWidth: .infinity, alignment: .leading)
      .onChange(of: input) { _, _ in saveDraft() }
      .onChange(of: mode) { _, _ in saveDraft() }
      .task {
        if !initialized {
          let draft = store.redisDrafts[parentID] ?? store.redisQueries[parentID] ?? ("", "prefix")
          input = draft.0
          mode = draft.1
          initialized = true
        }
        await refreshHistory()
      }
  }
  private var historyMenu: some View {
    Menu {
      if history.isEmpty { Text(t("暂无搜索历史", "No search history")) }
      ForEach(history.indices, id: \.self) { index in
        let record = history[index]
        Menu(
          (record["is_favorite"]?.bool == true ? "★ " : "")
            + String(text(record["text"]).prefix(60))
        ) {
          Button(t("填入搜索框", "Use search")) { choose(record) }
          Button(
            record["is_favorite"]?.bool == true ? t("取消收藏", "Remove favorite") : t("收藏", "Favorite")
          ) { Task { await favorite(record) } }
          Button(t("删除记录", "Delete history"), role: .destructive) { Task { await delete(record) } }
        }
      }
      Divider()
      Button(t("刷新历史", "Refresh history")) { Task { await refreshHistory() } }
    } label: {
      Image(systemName: "clock.arrow.circlepath").font(.system(size: 11))
    }
    .menuStyle(.borderlessButton).fixedSize().help(t("历史与收藏", "History and favorites"))
  }
  private func saveDraft() { store.redisDrafts[parentID] = (input, mode) }
  private func selectSuggestion(_ text: String) {
    if let record = history.first(where: { $0["text"]?.string == text }) {
      choose(record)
    } else {
      input = text
      saveDraft()
    }
  }
  private func choose(_ record: WireValue) {
    input = text(record["text"])
    mode = text(record["mode"], "prefix")
    saveDraft()
  }
  private func submit() {
    guard !searching else { return }
    let query = input
    let queryMode = mode
    saveDraft()
    searching = true
    Task { @MainActor in
      await store.searchKeys(parentID: parentID, text: query, mode: queryMode)
      await refreshHistory()
      searching = false
    }
  }
  private func refreshHistory() async {
    guard let target = store.targets[parentID] else { return }
    do {
      history = values(
        try await store.rpc.call(
          "list_redis_search_history",
          params: [
            "connection_id": target.connection["id"] ?? .null, "database": .string(target.database),
          ]))
    } catch { store.report(error) }
  }
  private func favorite(_ record: WireValue) async {
    do {
      _ = try await store.rpc.call(
        "set_redis_search_favorite",
        params: [
          "identifier": record["id"] ?? .null,
          "favorite": .bool(record["is_favorite"]?.bool != true),
        ])
      await refreshHistory()
    } catch { store.report(error) }
  }
  private func delete(_ record: WireValue) async {
    do {
      _ = try await store.rpc.call(
        "delete_redis_search_history", params: ["identifier": record["id"] ?? .null])
      await refreshHistory()
    } catch { store.report(error) }
  }
}

/// Native editable combo provides inline prefix completion without taking focus on updates.
private struct RedisHistoryCombo: NSViewRepresentable {
  @Binding var text: String
  var suggestions: [String]
  var placeholder: String
  var onPick: (String) -> Void
  var onSubmit: () -> Void
  func makeCoordinator() -> Coordinator { Coordinator(self) }
  func makeNSView(context: Context) -> NSComboBox {
    let combo = NSComboBox()
    combo.usesDataSource = true
    combo.dataSource = context.coordinator
    combo.delegate = context.coordinator
    combo.completes = true
    combo.numberOfVisibleItems = 8
    combo.controlSize = .small
    combo.font = .systemFont(ofSize: 11)
    combo.stringValue = text
    combo.placeholderString = placeholder
    combo.setContentHuggingPriority(.defaultLow, for: .horizontal)
    return combo
  }
  func updateNSView(_ combo: NSComboBox, context: Context) {
    let changed =
      context.coordinator.parent.suggestions != suggestions
      || context.coordinator.parent.text != text
    context.coordinator.parent = self
    combo.placeholderString = placeholder
    if combo.stringValue != text { combo.stringValue = text }
    if changed {
      context.coordinator.refreshChoices()
      combo.reloadData()
    }
  }
  final class Coordinator: NSObject, NSComboBoxDataSource, NSComboBoxDelegate {
    var parent: RedisHistoryCombo
    private var popupOpen = false
    private var choices: [String] = []
    init(_ parent: RedisHistoryCombo) {
      self.parent = parent
      super.init()
      refreshChoices()
    }
    func refreshChoices() {
      choices = parent.suggestions.filter { parent.text.isEmpty || $0.hasPrefix(parent.text) }
    }
    func numberOfItems(in comboBox: NSComboBox) -> Int { choices.count }
    func comboBox(_ comboBox: NSComboBox, objectValueForItemAt index: Int) -> Any? {
      choices.indices.contains(index) ? choices[index] : nil
    }
    func comboBox(_ comboBox: NSComboBox, completedString string: String) -> String? {
      parent.suggestions.first { $0.hasPrefix(string) }
    }
    func controlTextDidChange(_ obj: Notification) {
      guard let combo = obj.object as? NSComboBox else { return }
      parent.text = combo.stringValue
    }
    func comboBoxWillPopUp(_ notification: Notification) { popupOpen = true }
    func comboBoxWillDismiss(_ notification: Notification) { popupOpen = false }
    func comboBoxSelectionDidChange(_ notification: Notification) {
      guard let combo = notification.object as? NSComboBox else { return }
      let items = choices
      let index = combo.indexOfSelectedItem
      if items.indices.contains(index) { parent.onPick(items[index]) }
    }
    func control(_ control: NSControl, textView: NSTextView, doCommandBy commandSelector: Selector)
      -> Bool
    {
      guard commandSelector == #selector(NSResponder.insertNewline(_:)) else { return false }
      if popupOpen { return false }
      parent.onSubmit()
      return true
    }
  }
}
