import Foundation

struct SessionTab: Codable, Equatable {
  var connectionID: String
  var database: String
  var object: WireValue
  var mode: String
  var title: String
  var consoleDocumentID: String? = nil
  var consoleDocumentName: String? = nil
  var sortColumn: String? = nil
  var descending: Bool? = nil
  var searchDraft: String? = nil
  var appliedSearch: String? = nil
  var pageSize = 50
  var whereDraft = ""
  var orderDraft = ""
  var appliedWhere = ""
  var appliedOrder = ""
  var inner = "data"
}
struct WorkspaceSession: Codable, Equatable {
  var version = 1
  var tabs: [SessionTab]
  var active: Int
}

enum SessionPersistence {
  static func legacyPreferences() -> [String: Any] {
    let path = FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent(
      "Library/Preferences/com.baseskiff.BaseSkiff.plist")
    guard let bytes = try? Data(contentsOf: path),
      let result = try? PropertyListSerialization.propertyList(from: bytes, format: nil)
        as? [String: Any]
    else { return [:] }
    return result
  }
  /// Import once, adding only separate native keys. The Qt settings remain untouched.
  static func migrate(defaults: UserDefaults, legacy: [String: Any]) {
    guard defaults.integer(forKey: "native.migrationVersion") < 1 else { return }
    let mappings = [
      ("language", "native.language"), ("display_density", "native.rowDensity"),
      ("display_font_size", "native.fontSize"), ("sidebar_width", "native.sidebarWidth"),
    ]
    for (old, new) in mappings where defaults.object(forKey: new) == nil {
      guard let value = legacy[old] else { continue }
      switch old {
      case "language":
        if let item = value as? String, ["zh", "en"].contains(item) {
          defaults.set(item, forKey: new)
        }
      case "display_density":
        if let item = value as? String, ["compact", "comfortable"].contains(item) {
          defaults.set(item, forKey: new)
        }
      case "display_font_size":
        if let number = Double(String(describing: value)) {
          defaults.set(min(16, max(11, number)), forKey: new)
        }
      case "sidebar_width":
        if let number = Double(String(describing: value)) {
          defaults.set(min(560, max(200, number)), forKey: new)
        }
      default: break
      }
    }
    if defaults.data(forKey: "native.workspaceSession") == nil,
      let json = legacy["workspaceSession"] as? String, let data = json.data(using: .utf8),
      let value = try? JSONDecoder().decode(WireValue.self, from: data),
      value["version"]?.integer == 1
    {
      defaults.set(data, forKey: "native.legacyWorkspaceSession")
    }
    defaults.set(1, forKey: "native.migrationVersion")
  }
  static func legacySession(_ data: Data, connections: [WireValue]) -> WorkspaceSession? {
    guard let payload = try? JSONDecoder().decode(WireValue.self, from: data),
      payload["version"]?.integer == 1
    else { return nil }
    var tabs: [SessionTab] = []
    for record in (payload["tabs"]?.array ?? []).prefix(100) {
      guard let id = record["connection_id"]?.string,
        let connection = connections.first(where: { $0["id"]?.string == id })
      else { continue }
      let kind = connection["kind"]?.string ?? ""
      let object: WireValue
      if let base64 = record["key_base64"]?.string, Data(base64Encoded: base64) != nil {
        object = .object(["$type": .string("bytes"), "value": .string(base64)])
      } else if let name = record["table"]?.string, !name.isEmpty {
        object = .string(name)
      } else {
        continue
      }
      var tab = SessionTab(
        connectionID: id,
        database: kind == "Redis"
          ? record["database"]?.string ?? "0" : connection["database"]?.string ?? "",
        object: object, mode: kind == "Redis" ? "redis" : "table", title: object.displayText)
      tab.pageSize = min(1000, max(1, Int(record["page_size"]?.integer ?? 50)))
      tab.sortColumn = record["sort_column"]?.string
      tab.descending = record["descending"]?.bool
      tab.searchDraft = record["search"]?.string
      tab.appliedSearch = record["search"]?.string
      tab.whereDraft = record["where_draft"]?.string ?? ""
      tab.orderDraft = record["order_by_draft"]?.string ?? ""
      tab.appliedWhere = record["where_clause"]?.string ?? ""
      tab.appliedOrder = record["order_by"]?.string ?? ""
      tab.inner = [1, 2].contains(record["inner_tab"]?.integer ?? 0) ? "structure" : "data"
      tabs.append(tab)
    }
    var active = Int(payload["active"]?.integer ?? 0)
    if let console = payload["console"]?.array, console.count >= 2, let id = console[0].string,
      connections.contains(where: { $0["id"]?.string == id })
    {
      var tab = SessionTab(
        connectionID: id, database: console[1].string ?? "", object: .null, mode: "console",
        title: "Console")
      tab.consoleDocumentID = console.count > 2 ? console[2].string : nil
      tabs.append(tab)
      active = tabs.count - 1
    }
    return WorkspaceSession(tabs: tabs, active: active)
  }
  static func handles(in value: WireValue) -> Set<String> {
    if value["$type"]?.string == "handle", let id = value["value"]?.string { return [id] }
    return (value.array ?? Array(value.object?.values ?? [:].values)).reduce(into: Set<String>()) {
      $0.formUnion(handles(in: $1))
    }
  }
}
