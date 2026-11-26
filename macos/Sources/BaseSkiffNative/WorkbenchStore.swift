import AppKit
import Combine
import SwiftUI

let sqlKinds: Set<String> = ["SQLite", "MySQL", "PostgreSQL", "Oracle"]
func text(_ value: WireValue?, _ fallback: String = "") -> String { value?.string ?? fallback }
func values(_ value: WireValue?) -> [WireValue] { value?.array ?? [] }
func fields(_ value: WireValue?) -> [String: WireValue] { value?.stringKeyedFields ?? [:] }
func number(_ value: WireValue?, _ fallback: Int = 0) -> Int {
  Int(value?.integer ?? Int64(fallback))
}
func label(_ zh: String, _ en: String, _ language: String) -> String { language == "zh" ? zh : en }

@MainActor final class StructureDraftState: ObservableObject {
  @Published var actions: [WireValue] = []
  @Published var sql = ""
  @Published var locked = false
  @Published var indexes: [WireValue] = []
  @Published var constraints: [WireValue] = []
  @Published var initialized = false
  var hasPending: Bool { !actions.isEmpty }
  func discard() {
    actions = []
    sql = ""
    locked = false
  }
}

@MainActor final class WorkTab: ObservableObject, Identifiable {
  let id = UUID()
  let structureDraft = StructureDraftState()
  private var draftObserver: AnyCancellable?
  @Published var lastExecutedQuery: String?
  let connection: WireValue
  let database: String
  let object: WireValue
  let mode: String
  let title: String
  @Published var rows: [[String: WireValue]] = []
  @Published var columns: [String] = []
  @Published var schema: [WireValue] = []
  @Published var originalRows: [[String: WireValue]] = []
  @Published var updates: [Int: [String: WireValue]] = [:]
  @Published var inserts: [[String: WireValue]] = []
  @Published var deleted: Set<Int> = []
  @Published var selected: Int?
  @Published var selectedRows: Set<Int> = []
  @Published var plan: [WireValue] = []
  @Published var startedAt: Date?
  @Published var operationName = ""
  @Published var errorPosition: Int?
  @Published var errorLine: Int?
  @Published var errorColumn: Int?
  @Published var sortColumn = ""
  @Published var descending = false
  @Published var searchDraft = ""
  @Published var appliedSearch = ""
  @Published var consoleLoaded = false
  @Published var draft = ""
  @Published var consoleDocumentID = ""
  @Published var consoleDocumentName = ""
  @Published var whereDraft = ""
  @Published var orderDraft = ""
  var appliedWhere = "", appliedOrder = ""
  @Published var page = 1
  @Published var pageSize = 50
  @Published var total: Int?
  @Published var hasMore = false
  @Published var busy = false
  @Published var status = ""
  @Published var inner = "data"
  @Published var redis: WireValue = .null
  @Published var requestID: String?
  var generation = 0
  var alive = true
  var needsRestoreLoad = false
  var cursors: [Int: WireValue] = [:]
  var saveTask: Task<Void, Never>?
  init(connection: WireValue, database: String, object: WireValue, mode: String, title: String) {
    self.connection = connection
    self.database = database
    self.object = object
    self.mode = mode
    self.title = title
    draftObserver = structureDraft.objectWillChange.sink { [weak self] in
      self?.objectWillChange.send()
    }
  }
  var connectionID: String { text(connection["id"]) }
  var kind: String { text(connection["kind"]) }
  var readOnly: Bool { connection["read_only"]?.bool ?? false }
  var pending: Int { updates.count + inserts.count + deleted.count }
  var hasPending: Bool { pending > 0 || structureDraft.hasPending }
  var primaryKeys: [String] {
    schema.filter { $0["primary_key"]?.bool == true }.map { text($0["name"]) }
  }
  var canEdit: Bool {
    mode == "table" && sqlKinds.contains(kind) && !readOnly && !primaryKeys.isEmpty
      && !structureDraft.hasPending
  }
  var scope: [String: WireValue] {
    [
      "connection_id": .string(connectionID),
      "database": .string(
        database.isEmpty && kind == "SQLite"
          ? URL(fileURLWithPath: text(connection["path"])).lastPathComponent : database),
    ]
  }
  var base: [String: WireValue] {
    kind == "Redis" ? scope : ["connection_id": .string(connectionID)]
  }
}

struct TreeTarget {
  var connection: WireValue
  var database: String = ""
  var object: WireValue = .null
  var action: String
  var consoleDocumentID = ""
  var consoleDocumentName = ""
}

@MainActor final class WorkbenchStore: ObservableObject {
  let rpc: RPCClient
  @Published var connections: [WireValue] = []
  @Published var treeRevision = 0
  @Published var nodes: [SidebarNode] = []
  @Published var selectedNode: String?
  @Published var tabs: [WorkTab] = [] { didSet { persistSession() } }
  @Published var activeID: UUID? {
    didSet {
      persistSession()
      if let tab = current, tab.needsRestoreLoad {
        tab.needsRestoreLoad = false
        Task { await activateRestored(tab) }
      }
    }
  }
  @Published var error = ""
  @Published var connected = false
  @Published var language = UserDefaults.standard.string(forKey: "native.language") ?? "zh"
  @Published var sidebarSearch = ""
  var targets: [String: TreeTarget] = [:]
  var loaded: Set<String> = []
  var loading: Set<String> = []
  var redisPages: [String: WireValue] = [:]
  var redisQueries: [String: (String, String)] = [:] { didSet { persistRedisSearches() } }
  var redisDrafts: [String: (String, String)] = [:] { didSet { persistRedisSearches() } }
  var keyGenerations: [String: Int] = [:]
  var keyLoads: Set<String> = []
  var loadedKeyIDs: [String: Set<String>] = [:]
  var keyFolders: [String: [String: SidebarNode]] = [:]
  var keyFolderCounts: [String: Int] = [:]
  var expanded: Set<String> = []
  private let defaults: UserDefaults
  private var restoringSession = false
  private var sessionReady = false
  private var closingSaves: [Task<Void, Never>] = []
  private var changingConnections: Set<String> = []
  private let readOperations: Set<String> = [
    "fetch_rows", "read_redis_key", "continue_redis_value", "list_history", "get_schema", "explain",
    "export_csv",
  ]
  init(rpc: RPCClient = RPCClient(), defaults: UserDefaults = .standard) {
    self.rpc = rpc
    self.defaults = defaults
  }
  private var persistentSession: Bool {
    getenv("BASESKIFF_DATA_DIR") == nil
  }
  var current: WorkTab? { tabs.first { $0.id == activeID } }
  func t(_ zh: String, _ en: String) -> String { label(zh, en, language) }
  func start() async {
    do {
      try rpc.start()
      _ = try await rpc.hello()
      connected = true
      try await reloadConnections()
      restoreRedisSearches()
      await restoreSession()
    } catch { report(error) }
  }
  func report(_ failure: Error) { error = failure.localizedDescription }
  func toggleLanguage() {
    language = language == "zh" ? "en" : "zh"
    UserDefaults.standard.set(language, forKey: "native.language")
  }
  func saveConnection(_ connection: WireValue) async throws {
    let id = text(connection["id"])
    guard changingConnections.insert(id).inserted else {
      throw RPCError.remote("Connection settings are already being saved")
    }
    defer { changingConnections.remove(id) }
    let affected = tabs.filter { $0.connectionID == id }
    guard !affected.contains(where: { $0.hasPending || $0.busy }) else {
      throw RPCError.remote(
        t(
          "此连接仍有待提交修改或运行中的任务，请先处理后再保存连接。",
          "Resolve pending changes and running tasks before saving this connection."))
    }
    for tab in affected where tab.mode == "console" {
      tab.saveTask?.cancel()
      await saveConsole(tab)
    }
    _ = try await rpc.call("save_connection", params: ["connection": connection])
    for tab in affected { close(tab) }
    invalidateConnectionTree(id)
    try await reloadConnections()
  }
  func removeConnection(_ id: String) async throws {
    guard !tabs.contains(where: { $0.connectionID == id && ($0.hasPending || $0.busy) }) else {
      throw RPCError.remote(
        t("请先处理该连接的修改与任务。", "Resolve changes and tasks for this connection first."))
    }
    _ = try await rpc.call("delete_connection", params: ["connection_id": .string(id)])
    for tab in tabs.filter({ $0.connectionID == id }) {
      tab.alive = false
      tab.generation += 1
      tab.saveTask?.cancel()
      tabs.removeAll { $0.id == tab.id }
    }
    if !tabs.contains(where: { $0.id == activeID }) { activeID = tabs.last?.id }
    invalidateConnectionTree(id)
    try await reloadConnections()
  }
  @discardableResult func chooseRedisDatabase(_ connection: WireValue, index: Int) async throws
    -> String
  {
    guard text(connection["kind"]) == "Redis", index >= 0 else {
      throw RPCError.remote("Choose a nonnegative Redis database index")
    }
    let id = text(connection["id"])
    guard !changingConnections.contains(id) else {
      throw RPCError.remote("Connection settings are being saved")
    }
    let saved = try await rpc.call(
      "set_redis_database", params: ["connection_id": .string(id), "index": .int(Int64(index))])
    let updated = saved.object == nil ? connection : saved
    if let position = connections.firstIndex(where: { text($0["id"]) == id }) {
      connections[position] = updated
    }
    for key in Array(targets.keys) where text(targets[key]?.connection["id"]) == id {
      targets[key]?.connection = updated
    }
    targets[id] = TreeTarget(connection: updated, database: String(index), action: "connection")
    let dbID = id + ":db:" + String(index)
    targets[dbID] = TreeTarget(connection: updated, database: String(index), action: "database")
    if let root = nodes.first(where: { $0.id == id }),
      !root.children.contains(where: { $0.id == dbID })
    {
      root.children.append(
        SidebarNode(
          id: dbID, label: "DB" + String(index), kind: "database",
          children: [SidebarNode(id: dbID + ":placeholder", label: "…", kind: "placeholder")]))
    }
    let historyID = id + ":history"
    targets[historyID] = TreeTarget(connection: updated, database: String(index), action: "history")
    treeRevision += 1
    objectWillChange.send()
    return dbID
  }
  func invalidateConnectionTree(_ id: String) {
    treeRevision += 1
    loaded = loaded.filter { $0 != id && !$0.hasPrefix(id + ":") }
    for key in Array(redisPages.keys) where key.hasPrefix(id + ":") {
      if let value = redisPages.removeValue(forKey: key) { releaseHandles(value) }
      keyGenerations[key, default: 0] += 1
      loadedKeyIDs.removeValue(forKey: key)
      keyFolders.removeValue(forKey: key)
    }
    targets = targets.filter { text($0.value.connection["id"]) != id }
    if let node = nodes.first(where: { $0.id == id }) { node.children = [] }
  }
  func reloadConnections() async throws {
    connections = values(try await rpc.call("list_connections"))
    treeRevision += 1
    let old = Dictionary(uniqueKeysWithValues: nodes.map { ($0.id, $0) })
    nodes = connections.map { c in
      let id = text(c["id"])
      targets[id] = TreeTarget(connection: c, database: text(c["database"]), action: "connection")
      let node =
        old[id]
        ?? SidebarNode(
          id: id, label: text(c["name"]), detail: text(c["kind"]), kind: "connection", children: [])
      node.label = text(c["name"])
      node.detail = text(c["kind"])
      if node.children.isEmpty {
        node.children = [SidebarNode(id: id + ":placeholder", label: "…", kind: "placeholder")]
      }
      return node
    }
  }
  func select(_ node: SidebarNode) {
    guard let target = targets[node.id] else { return }
    switch target.action {
    case "connection", "database": Task { await expand(node, true) }
    case "table", "redis", "console", "history": Task { await open(target) }
    case "more": Task { await loadKeys(parentID: text(target.object), append: true) }
    default: break
    }
  }
  func expand(_ node: SidebarNode, _ state: Bool) async {
    guard let node = findNode(node.id) else { return }
    guard state, let target = targets[node.id], !loading.contains(node.id) else { return }
    if loaded.contains(node.id) {
      if target.action == "database" {
        do {
          _ = try await chooseRedisDatabase(target.connection, index: Int(target.database) ?? 0)
          if let page = redisPages[node.id], page != .null {
            await loadKeys(parentID: node.id, append: true)
          }
        } catch { report(error) }
      }
      return
    }
    loading.insert(node.id)
    defer { loading.remove(node.id) }
    do {
      if target.action == "connection" {
        if text(target.connection["kind"]) == "Redis" {
          let result = try await rpc.call(
            "list_redis_databases",
            params: ["connection_id": .string(text(target.connection["id"]))])
          node.children = values(result["databases"]).map { db in
            let database = String(number(db["index"]))
            let id = node.id + ":db:" + database
            targets[id] = TreeTarget(
              connection: target.connection, database: database, action: "database")
            let count =
              db["key_count"] == .null ? "" : db["key_count"].map { " [\($0.displayText)]" } ?? ""
            return SidebarNode(
              id: id, label: "DB" + database + count, kind: "database",
              children: [SidebarNode(id: id + ":placeholder", label: "…", kind: "placeholder")])
          }
          let historyID = node.id + ":history"
          targets[historyID] = TreeTarget(
            connection: target.connection, database: target.database, action: "history")
          node.children.insert(SidebarNode(id: historyID, label: "History", kind: "history"), at: 0)
        } else {
          let list = values(
            try await rpc.call(
              "list_tables", params: ["connection_id": .string(text(target.connection["id"]))]))
          let tableNodes = list.enumerated().map { index, value -> SidebarNode in
            let name =
              text(value["schema"]).isEmpty
              ? value["name"] ?? .null : .string(text(value["schema"]) + "." + text(value["name"]))
            let id = node.id + ":table:" + String(index)
            targets[id] = TreeTarget(
              connection: target.connection, database: target.database, object: name,
              action: "table")
            return SidebarNode(id: id, label: name.displayText, kind: "table")
          }
          if sqlKinds.contains(text(target.connection["kind"])) {
            let consoleID = node.id + ":console"
            let historyID = node.id + ":history"
            targets[consoleID] = TreeTarget(
              connection: target.connection, database: target.database, action: "console")
            targets[historyID] = TreeTarget(
              connection: target.connection, database: target.database, action: "history")
            node.children = [
              SidebarNode(id: consoleID, label: "Console", kind: "console"),
              SidebarNode(id: historyID, label: "History", kind: "history"),
              SidebarNode(
                id: node.id + ":tables", label: "Tables", kind: "folder", children: tableNodes),
            ]
          } else {
            node.children = tableNodes
            let id = node.id + ":console"
            targets[id] = TreeTarget(
              connection: target.connection, database: target.database, action: "console")
            node.children.insert(SidebarNode(id: id, label: "Console", kind: "console"), at: 0)
            let historyID = node.id + ":history"
            targets[historyID] = TreeTarget(
              connection: target.connection, database: target.database, action: "history")
            node.children.insert(
              SidebarNode(id: historyID, label: "History", kind: "history"), at: 1)
          }
        }
        loaded.insert(node.id)
      } else if target.action == "database" {
        _ = try await chooseRedisDatabase(target.connection, index: Int(target.database) ?? 0)
        await loadKeys(parentID: node.id, append: false)
        loaded.insert(node.id)
      }
      treeRevision += 1
      objectWillChange.send()
    } catch { report(error) }
  }
  func findNode(_ id: String, in roots: [SidebarNode]? = nil) -> SidebarNode? {
    for node in roots ?? nodes {
      if node.id == id { return node }
      if let child = findNode(id, in: node.children) { return child }
    }
    return nil
  }
  func loadKeys(parentID: String, append: Bool) async {
    guard let target = targets[parentID], let parent = findNode(parentID) else { return }
    if append && keyLoads.contains(parentID) { return }
    let query = redisQueries[parentID] ?? ("", "prefix")
    if append, let draft = redisDrafts[parentID], draft != query { return }
    if append && (redisPages[parentID] == nil || redisPages[parentID] == .null) { return }
    keyGenerations[parentID, default: 0] += 1
    let generation = keyGenerations[parentID]!
    keyLoads.insert(parentID)
    defer { if keyGenerations[parentID] == generation { keyLoads.remove(parentID) } }
    do {
      var params: [String: WireValue] = [
        "connection_id": .string(text(target.connection["id"])),
        "database": .string(target.database), "text": .string(query.0), "mode": .string(query.1),
      ]
      if append, let continuation = redisPages[parentID], continuation != .null {
        params["continuation"] = continuation
      }
      let result = try await rpc.call("search_redis_keys", params: params)
      guard keyGenerations[parentID] == generation,
        (redisQueries[parentID] ?? ("", "prefix")) == query
      else {
        releaseHandles(result)
        return
      }
      let oldPage = redisPages[parentID] ?? .null
      redisPages[parentID] = result["continuation"]
      releaseHandles(oldPage, retaining: result)
      if !append {
        parent.children = [SidebarNode(id: parentID + ":search", label: "", kind: "redisSearch")]
        loadedKeyIDs[parentID] = []
        keyFolders[parentID] = [:]
        for id in Array(keyFolderCounts.keys) where id.hasPrefix(parentID + ":folder:") {
          keyFolderCounts.removeValue(forKey: id)
        }
        for id in Array(targets.keys) where id.hasPrefix(parentID + ":key:") {
          targets.removeValue(forKey: id)
        }
      }
      parent.children.removeAll { $0.kind == "more" }
      let separator = text(target.connection["redis_separator"], ":")
      for value in values(result["keys"]) {
        let key = value["name"] ?? .null
        let display = keyDisplayLabel(key)
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.sortedKeys]
        let keyID = parentID + ":key:" + (try encoder.encode(key)).base64EncodedString()
        guard loadedKeyIDs[parentID, default: []].insert(keyID).inserted else { continue }
        targets[keyID] = TreeTarget(
          connection: target.connection, database: target.database, object: key, action: "redis")
        let components =
          separator.isEmpty || key.string == nil
          ? [key.string ?? display] : (key.string ?? "").components(separatedBy: separator)
        var folder = parent
        var parts: [String] = []
        for part in components.dropLast() {
          parts.append(part)
          let prefix = parentID + ":folder:" + (try encoder.encode(parts)).base64EncodedString()
          keyFolderCounts[prefix, default: 0] += 1
          if let existing = keyFolders[parentID]?[prefix] {
            folder = existing
          } else {
            let child = SidebarNode(
              id: prefix, label: keyDisplayLabel(.string(part)), kind: "folder")
            folder.children.append(child)
            keyFolders[parentID, default: [:]][prefix] = child
            folder = child
          }
          folder.detail = "[\(keyFolderCounts[prefix] ?? 0)]"
        }
        folder.children.append(
          SidebarNode(
            id: keyID,
            label: key.string == nil ? display : keyDisplayLabel(.string(components.last ?? "")),
            detail: text(value["kind"]), kind: "redis"))
      }
      if result["truncated"]?.bool == true {
        let id = parentID + ":more"
        targets[id] = TreeTarget(
          connection: target.connection, database: target.database, object: .string(parentID),
          action: "more")
        parent.children.append(
          SidebarNode(id: id, label: t("加载更多（最多 500）", "Load more (up to 500)"), kind: "more"))
      }
      treeRevision += 1
      objectWillChange.send()
    } catch { report(error) }
  }
  func searchKeys(parentID: String, text: String, mode: String) async {
    redisQueries[parentID] = (text, mode)
    redisDrafts[parentID] = (text, mode)
    await loadKeys(parentID: parentID, append: false)
    guard let target = targets[parentID], !text.isEmpty else { return }
    do {
      _ = try await rpc.call(
        "record_redis_search",
        params: [
          "connection_id": .string(self.textID(target.connection)),
          "database": .string(target.database), "text": .string(text), "mode": .string(mode),
        ])
    } catch { report(error) }
  }
  private func textID(_ c: WireValue) -> String { text(c["id"]) }
  func open(_ target: TreeTarget) async {
    guard !changingConnections.contains(text(target.connection["id"])) else { return }
    if let tab = tabs.first(where: {
      $0.connectionID == text(target.connection["id"]) && $0.database == target.database
        && $0.object == target.object && $0.mode == target.action
        && $0.consoleDocumentID == target.consoleDocumentID
    }) {
      activeID = tab.id
      return
    }
    let title =
      target.action == "console"
      ? "Console"
      : target.action == "history"
        ? "History"
        : target.action == "redis" ? keyDisplayLabel(target.object) : target.object.displayText
    let tab = WorkTab(
      connection: target.connection, database: target.database, object: target.object,
      mode: target.action, title: title)
    tab.consoleDocumentID = target.consoleDocumentID
    tab.consoleDocumentName = target.consoleDocumentName
    tabs.append(tab)
    activeID = tab.id
    if tab.mode == "console" {
      await loadConsole(tab)
    } else {
      await load(tab)
    }
  }
  func mutateDocument(
    _ tab: WorkTab, action: String, values: WireValue = .null, keys: WireValue = .null
  ) async {
    guard tab.alive, tab.kind == "MongoDB", !tab.readOnly, !tab.busy else { return }
    var params = tab.base
    params["table"] = tab.object
    let method: String
    switch action {
    case "insert":
      method = "insert_row"
      params["values"] = values
    case "update":
      method = "update_row"
      params["keys"] = keys
      params["values"] = values
    case "delete":
      method = "delete_rows"
      params["keys_list"] = .array([keys])
    default: return
    }
    if await operation(tab, method: method, params: params) != nil { await load(tab) }
  }
  func operation(_ tab: WorkTab, method: String, params: [String: WireValue]) async -> WireValue? {
    guard tab.alive, !changingConnections.contains(tab.connectionID) else { return nil }
    if tab.busy && (!readOperations.contains(tab.operationName) || !readOperations.contains(method))
    {
      error = t("请等待当前任务结束后再操作。", "Wait for the current operation to finish.")
      return nil
    }
    tab.generation += 1
    let generation = tab.generation
    let requestID = UUID().uuidString
    tab.requestID = requestID
    tab.busy = true
    tab.startedAt = Date()
    tab.operationName = method
    objectWillChange.send()
    defer {
      if tab.generation == generation {
        tab.busy = false
        tab.requestID = nil
        tab.startedAt = nil
        objectWillChange.send()
      }
    }
    do {
      let result = try await rpc.call(
        method, params: params, requestID: requestID,
        timeout: max(120, Double(tab.connection["query_timeout"]?.displayText ?? "10") ?? 10) + 30)
      guard tab.alive, tab.generation == generation else {
        releaseHandles(result)
        return nil
      }
      return result
    } catch {
      if tab.alive && tab.generation == generation {
        if let failure = error as? RPCError, case .response(let details) = failure,
          ["execute", "explain"].contains(method)
        {
          tab.errorLine = details["line"]?.integer.flatMap(Int.init(exactly:))
          tab.errorColumn = details["column"]?.integer.flatMap(Int.init(exactly:))
          if let query = params["query"]?.string, query == tab.draft,
            let position = details["position"]?.integer, position > 0,
            position <= query.unicodeScalars.count + 1
          {
            let prefix = String(
              String.UnicodeScalarView(query.unicodeScalars.prefix(Int(position) - 1)))
            tab.errorPosition = prefix.utf16.count
          } else {
            tab.errorPosition = nil
          }
        }
        report(error)
      }
      return nil
    }
  }
  func load(_ tab: WorkTab, exactCount: Bool = false) async {
    guard tab.pending == 0 else {
      error = t("请先提交或撤销修改。", "Commit or revert pending changes first.")
      return
    }
    var params = tab.base
    if tab.mode == "history" {
      if let result = await operation(tab, method: "list_history", params: tab.scope) {
        tab.rows = values(result).map { fields($0) }
        tab.columns = ["timestamp", "query", "success", "elapsed_ms", "error"]
      }
    } else if tab.mode == "redis" {
      params["key"] = tab.object
      if let result = await operation(tab, method: "read_redis_key", params: params) {
        let previous = tab.redis
        tab.redis = result
        releaseHandles(previous, retaining: result)
        tab.status = ""
      }
    } else {
      params.merge([
        "table": tab.object, "search": .string(tab.appliedSearch),
        "sort_column": .string(tab.sortColumn), "descending": .bool(tab.descending),
        "page": .int(Int64(tab.page)), "page_size": .int(Int64(tab.pageSize)),
        "where_clause": .string(tab.appliedWhere), "order_by": .string(tab.appliedOrder),
        "count_total": exactCount ? .string("exact") : .bool(false),
      ]) { _, new in new }
      if let cursor = tab.cursors[tab.page] { params["after_key"] = cursor }
      if let result = await operation(tab, method: "fetch_rows", params: params) {
        tab.columns = values(result["columns"]).map { $0.displayText }
        tab.rows = values(result["rows"]).map { fields($0) }
        tab.originalRows = tab.rows
        tab.total = result["total_known"]?.bool == true ? number(result["total"]) : nil
        tab.hasMore = result["has_more"]?.bool ?? false
        if let cursor = result["next_cursor"], cursor != .null {
          tab.cursors[tab.page + 1] = cursor
        }
        tab.status = String(
          format: "%.1f ms", Double(result["elapsed_ms"]?.displayText ?? "0") ?? 0)
        let generation = tab.generation
        do {
          let schema = try await rpc.call(
            "get_schema", params: tab.base.merging(["table": tab.object]) { _, new in new })
          guard tab.alive, tab.generation == generation else { return }
          tab.schema = values(schema)
        } catch { if tab.alive && tab.generation == generation { report(error) } }
      }
    }
  }
  func execute(_ tab: WorkTab, query: String, explain: Bool = false) async {
    guard !query.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else { return }
    tab.errorPosition = nil
    tab.errorLine = nil
    tab.errorColumn = nil
    await saveConsole(tab)
    if let result = await operation(
      tab, method: explain ? "explain" : "execute",
      params: tab.base.merging(["query": .string(query)]) { _, new in new })
    {
      tab.columns = values(result["columns"]).map { $0.displayText }
      tab.rows = values(result["rows"]).map { fields($0) }
      if !explain { tab.lastExecutedQuery = query }
      tab.plan = []
      if explain {
        let generation = tab.generation
        do {
          let plan = try await rpc.call("plan_nodes", params: ["result": result])
          if tab.alive && tab.generation == generation { tab.plan = values(plan) }
        } catch { report(error) }
      }
      tab.status =
        "\(number(result["affected"])) " + t("行", "rows") + " · "
        + String(format: "%.1f ms", Double(result["elapsed_ms"]?.displayText ?? "0") ?? 0)
    }
  }
  func saveConsole(_ tab: WorkTab) async {
    guard tab.consoleLoaded else { return }
    do {
      var params = tab.scope.merging(["content": .string(tab.draft)]) { _, new in new }
      let method: String
      if tab.consoleDocumentID.isEmpty {
        method = "save_console"
      } else {
        method = "save_console_document"
        params["document_id"] = .string(tab.consoleDocumentID)
        params["name"] = .string(
          tab.consoleDocumentName.isEmpty ? tab.consoleDocumentID : tab.consoleDocumentName)
      }
      _ = try await rpc.call(method, params: params)
    } catch { report(error) }
  }
  func scheduleSave(_ tab: WorkTab) {
    guard tab.consoleLoaded else { return }
    tab.saveTask?.cancel()
    tab.saveTask = Task {
      try? await Task.sleep(nanoseconds: 400_000_000)
      if !Task.isCancelled { await saveConsole(tab) }
    }
  }
  func cancel(_ tab: WorkTab) async {
    guard let request = tab.requestID else { return }
    do { _ = try await rpc.call("cancel", params: ["request_id": .string(request)]) } catch {
      report(error)
    }
  }
  func close(_ tab: WorkTab) {
    guard !(tab.busy && !readOperations.contains(tab.operationName)) else {
      error = t(
        "当前写入尚未完成，请等待结果后再关闭标签。",
        "A write is still running. Wait for its result before closing this tab.")
      return
    }
    if tab.hasPending {
      let alert = NSAlert()
      alert.messageText = t("丢弃未提交的修改？", "Discard pending changes?")
      alert.addButton(withTitle: t("丢弃", "Discard"))
      alert.addButton(withTitle: t("取消", "Cancel"))
      guard alert.runModal() == .alertFirstButtonReturn else { return }
    }
    tab.alive = false
    tab.generation += 1
    tab.saveTask?.cancel()
    releaseHandles(tab.redis)
    if tab.mode == "console" { closingSaves.append(Task { await saveConsole(tab) }) }
    if tab.busy { Task { await cancel(tab) } }
    tabs.removeAll { $0.id == tab.id }
    if activeID == tab.id { activeID = tabs.last?.id }
  }
  private func releaseHandles(_ value: WireValue, retaining: WireValue = .null) {
    let ids = SessionPersistence.handles(in: value).subtracting(
      SessionPersistence.handles(in: retaining))
    guard !ids.isEmpty else { return }
    Task {
      _ = try? await rpc.call("release", params: ["handles": .array(ids.map { .string($0) })])
    }
  }
  private func loadConsole(_ tab: WorkTab) async {
    tab.consoleLoaded = false
    tab.generation += 1
    let generation = tab.generation
    do {
      if tab.consoleDocumentID.isEmpty {
        let result = try await rpc.call("load_console", params: tab.scope)
        guard tab.alive, tab.generation == generation else { return }
        tab.draft = text(result)
      } else {
        let result = try await rpc.call("list_console_documents", params: tab.scope)
        guard tab.alive, tab.generation == generation else { return }
        guard
          let document = values(result).first(where: {
            $0["document_id"]?.string == tab.consoleDocumentID
          })
        else { throw RPCError.remote("This Console document no longer exists") }
        tab.draft = text(document["content"])
        tab.consoleDocumentName = text(document["name"])
      }
      tab.consoleLoaded = true
    } catch { if tab.alive && tab.generation == generation { report(error) } }
  }
  private func activateRestored(_ tab: WorkTab) async {
    guard tab.alive else { return }
    if tab.mode == "console" { await loadConsole(tab) } else { await load(tab) }
  }
  func persistSession() {
    guard sessionReady, !restoringSession, persistentSession else { return }
    let descriptors = tabs.filter(\.alive).prefix(100).map { tab -> SessionTab in
      var item = SessionTab(
        connectionID: tab.connectionID, database: tab.database, object: tab.object, mode: tab.mode,
        title: tab.title)
      item.consoleDocumentID = tab.consoleDocumentID
      item.consoleDocumentName = tab.consoleDocumentName
      item.sortColumn = tab.sortColumn
      item.descending = tab.descending
      item.searchDraft = tab.searchDraft
      item.appliedSearch = tab.appliedSearch
      item.pageSize = tab.pageSize
      item.whereDraft = tab.whereDraft
      item.orderDraft = tab.orderDraft
      item.appliedWhere = tab.appliedWhere
      item.appliedOrder = tab.appliedOrder
      item.inner = tab.inner
      return item
    }
    let session = WorkspaceSession(
      tabs: descriptors, active: tabs.firstIndex(where: { $0.id == activeID }) ?? -1)
    if let bytes = try? JSONEncoder().encode(session) {
      defaults.set(bytes, forKey: "native.workspaceSession")
    }
  }
  func persistRedisSearches() {
    guard persistentSession else { return }
    let state: [String: WireValue] = [
      "submitted": .object(redisQueries.mapValues { .array([.string($0.0), .string($0.1)]) }),
      "drafts": .object(redisDrafts.mapValues { .array([.string($0.0), .string($0.1)]) }),
    ]
    if let bytes = try? JSONEncoder().encode(WireValue.object(state)) {
      defaults.set(bytes, forKey: "native.redisSearchState")
    }
  }
  func restoreRedisSearches() {
    guard persistentSession, let bytes = defaults.data(forKey: "native.redisSearchState"),
      let state = try? JSONDecoder().decode(WireValue.self, from: bytes)
    else { return }
    let submitted = fields(state["submitted"])
    let drafts = fields(state["drafts"])
    for (id, value) in submitted {
      let pair = values(value)
      if pair.count == 2, ["prefix", "exact"].contains(pair[1].displayText) {
        redisQueries[id] = (pair[0].displayText, pair[1].displayText)
      }
    }
    for (id, value) in drafts {
      let pair = values(value)
      if pair.count == 2, ["prefix", "exact"].contains(pair[1].displayText) {
        redisDrafts[id] = (pair[0].displayText, pair[1].displayText)
      }
    }
  }
  func restoreSession() async {
    guard persistentSession else { return }
    SessionPersistence.migrate(defaults: defaults, legacy: SessionPersistence.legacyPreferences())
    language = defaults.string(forKey: "native.language") ?? language
    guard !sessionReady else { return }
    restoringSession = true
    var saved: WorkspaceSession?
    if let data = defaults.data(forKey: "native.workspaceSession") {
      saved = try? JSONDecoder().decode(WorkspaceSession.self, from: data)
    } else if let data = defaults.data(forKey: "native.legacyWorkspaceSession") {
      saved = SessionPersistence.legacySession(data, connections: connections)
    }
    if let saved, saved.version == 1, tabs.isEmpty {
      for item in saved.tabs.prefix(100) {
        guard ["console", "history", "table", "redis"].contains(item.mode),
          let connection = connections.first(where: { $0["id"]?.string == item.connectionID })
        else { continue }
        let tab = WorkTab(
          connection: connection, database: item.database, object: item.object, mode: item.mode,
          title: item.title)
        tab.consoleDocumentID = item.consoleDocumentID ?? ""
        tab.consoleDocumentName = item.consoleDocumentName ?? ""
        tab.sortColumn = item.sortColumn ?? ""
        tab.descending = item.descending ?? false
        tab.searchDraft = item.searchDraft ?? ""
        tab.appliedSearch = item.appliedSearch ?? ""
        tab.pageSize = min(1000, max(1, item.pageSize))
        tab.whereDraft = item.whereDraft
        tab.orderDraft = item.orderDraft
        tab.appliedWhere = item.appliedWhere
        tab.appliedOrder = item.appliedOrder
        tab.inner = item.inner
        tab.needsRestoreLoad = true
        tabs.append(tab)
      }
      if tabs.indices.contains(saved.active) {
        activeID = tabs[saved.active].id
      } else {
        activeID = tabs.first?.id
      }
    }
    restoringSession = false
    sessionReady = true
    persistSession()
  }
  func flushForTermination() async {
    persistSession()
    for tab in tabs where tab.mode == "console" {
      tab.saveTask?.cancel()
      await saveConsole(tab)
    }
    for task in closingSaves { await task.value }
    closingSaves = []
    for tab in tabs { releaseHandles(tab.redis) }
    for value in redisPages.values { releaseHandles(value) }
  }
  func revert(_ tab: WorkTab) {
    guard !tab.busy, !changingConnections.contains(tab.connectionID) else { return }
    tab.rows = tab.originalRows
    tab.updates = [:]
    tab.deleted = []
    tab.inserts = []
  }
  func edit(_ tab: WorkTab, row: Int, column: String, value: String) {
    guard tab.canEdit, !tab.busy, !changingConnections.contains(tab.connectionID),
      !tab.deleted.contains(row),
      !(row < tab.originalRows.count && tab.primaryKeys.contains(column)),
      tab.rows.indices.contains(row)
    else { return }
    do {
      let old = tab.rows[row][column] ?? .null
      let parsed = try typedColumnValue(
        value, original: old, column: tab.schema.first(where: { text($0["name"]) == column }),
        kind: tab.kind)
      tab.rows[row][column] = parsed
      if row < tab.originalRows.count {
        tab.updates[row, default: [:]][column] = parsed
        if parsed == tab.originalRows[row][column] {
          tab.updates[row]?.removeValue(forKey: column)
          if tab.updates[row]?.isEmpty == true { tab.updates.removeValue(forKey: row) }
        }
      } else {
        tab.inserts[row - tab.originalRows.count][column] = parsed
      }
    } catch { report(error) }
  }
  func addRow(_ tab: WorkTab) {
    guard tab.canEdit, !tab.busy, !changingConnections.contains(tab.connectionID) else { return }
    tab.inserts.append([:])
    tab.rows.append([:])
  }
  func deleteRow(_ tab: WorkTab) {
    guard tab.canEdit, !tab.busy, !changingConnections.contains(tab.connectionID),
      let row = tab.selected, tab.rows.indices.contains(row)
    else { return }
    if row >= tab.originalRows.count {
      let index = row - tab.originalRows.count
      tab.inserts.remove(at: index)
      tab.rows.remove(at: row)
    } else {
      tab.deleted.insert(row)
      tab.updates.removeValue(forKey: row)
    }
  }
  func setNull(_ tab: WorkTab, row: Int, column: String) {
    guard tab.canEdit, !tab.busy, !changingConnections.contains(tab.connectionID),
      !tab.deleted.contains(row),
      !(row < tab.originalRows.count && tab.primaryKeys.contains(column)),
      tab.rows.indices.contains(row)
    else { return }
    tab.rows[row][column] = .null
    if row < tab.originalRows.count {
      tab.updates[row, default: [:]][column] = .null
    } else {
      tab.inserts[row - tab.originalRows.count][column] = .null
    }
  }
  func deleteSelectedRows(_ tab: WorkTab) {
    guard !tab.busy, !changingConnections.contains(tab.connectionID) else { return }
    let selected =
      tab.selectedRows.isEmpty ? Set(tab.selected.map { [$0] } ?? []) : tab.selectedRows
    for row in selected.sorted(by: >) {
      tab.selected = row
      deleteRow(tab)
    }
    tab.selectedRows = []
    tab.selected = nil
  }
  func commit(_ tab: WorkTab) async {
    guard tab.canEdit, !tab.busy, !changingConnections.contains(tab.connectionID), tab.pending > 0
    else { return }
    func keys(_ index: Int) -> WireValue {
      .userObject(
        Dictionary(
          uniqueKeysWithValues: tab.primaryKeys.map { ($0, tab.originalRows[index][$0] ?? .null) }))
    }
    let updates = tab.updates.sorted(by: { $0.key < $1.key }).map {
      WireValue.array([keys($0.key), .userObject($0.value)])
    }
    let params = tab.base.merging([
      "table": tab.object, "updates": .array(updates),
      "inserts": .array(tab.inserts.map { .userObject($0) }),
      "deletes": .array(tab.deleted.sorted().map(keys)),
    ]) { _, new in new }
    if await operation(tab, method: "apply_grid_changes", params: params) != nil {
      tab.updates = [:]
      tab.deleted = []
      tab.inserts = []
      await load(tab)
    }
  }
}

func typedValue(_ input: String, like original: WireValue) throws -> WireValue {
  if input == "NULL", original != .string("NULL"), original.string == nil { return .null }
  switch original {
  case .int:
    guard let n = Int64(input) else { throw CocoaError(.formatting) }
    return .int(n)
  case .double:
    guard let n = Double(input), n.isFinite else { throw CocoaError(.formatting) }
    return .double(n)
  case .bool:
    guard ["true", "false", "1", "0"].contains(input.lowercased()) else {
      throw CocoaError(.formatting)
    }
    return .bool(input == "1" || input.lowercased() == "true")
  case .object(let fields):
    if let kind = fields["$type"]?.string,
      ["integer", "decimal", "date", "datetime", "time", "uuid"].contains(kind)
    {
      func matches(_ pattern: String, _ value: String) -> Bool {
        value.range(of: pattern, options: .regularExpression) != nil
      }
      func validDate(_ value: String) -> Bool {
        guard matches(#"^\d{4}-\d{2}-\d{2}$"#, value) else { return false }
        let parser = DateFormatter()
        parser.locale = Locale(identifier: "en_US_POSIX")
        parser.timeZone = TimeZone(secondsFromGMT: 0)
        parser.dateFormat = "yyyy-MM-dd"
        parser.isLenient = false
        guard let date = parser.date(from: value) else { return false }
        return parser.string(from: date) == value
      }
      func validTime(_ value: String) -> Bool {
        matches(
          #"^(?:[01]\d|2[0-3]):[0-5]\d(?::[0-5]\d(?:\.\d{1,6})?)?(?:Z|[+-](?:[01]\d|2[0-3]):[0-5]\d)?$"#,
          value)
      }
      let valid: Bool
      switch kind {
      case "integer": valid = matches(#"^[+-]?\d+$"#, input)
      case "decimal": valid = matches(#"^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$"#, input)
      case "date": valid = validDate(input)
      case "time": valid = validTime(input)
      case "datetime":
        let parts = input.split(whereSeparator: { $0 == "T" || $0 == " " })
        valid = parts.count == 2 && validDate(String(parts[0])) && validTime(String(parts[1]))
      case "uuid": valid = UUID(uuidString: input) != nil
      default: valid = false
      }
      guard valid else { throw CocoaError(.formatting) }
      var edited = fields
      edited["value"] = .string(input)
      return .object(edited)
    }
    throw NSError(
      domain: "BaseSkiff", code: 1,
      userInfo: [
        NSLocalizedDescriptionKey: "Use the structured value editor for binary or nested values."
      ])
  default: return .string(input)
  }
}

func typedColumnValue(_ input: String, original: WireValue, column: WireValue?, kind: String) throws
  -> WireValue
{
  if original != .null { return try typedValue(input, like: original) }
  let declaration = text(column?["data_type"]).uppercased()
  if input == "NULL" { return .null }
  if declaration.contains("BOOL") { return try typedValue(input, like: .bool(false)) }
  if declaration.contains("INT") {
    guard let value = Int64(input) else {
      return try typedValue(
        input, like: .object(["$type": .string("integer"), "value": .string("0")]))
    }
    return .int(value)
  }
  if ["DECIMAL", "NUMERIC", "NUMBER", "MONEY"].contains(where: declaration.contains) {
    return try typedValue(
      input, like: .object(["$type": .string("decimal"), "value": .string("0")]))
  }
  if ["REAL", "FLOAT", "DOUBLE"].contains(where: declaration.contains) {
    return try typedValue(input, like: .double(0))
  }
  if declaration.contains("DATE") && !declaration.contains("TIME") && kind != "Oracle" {
    return try typedValue(input, like: .object(["$type": .string("date"), "value": .string("")]))
  }
  if declaration.contains("TIME") || declaration == "DATE" && kind == "Oracle" {
    return try typedValue(
      input,
      like: .object([
        "$type": .string(
          declaration.contains("STAMP") || declaration.contains("DATETIME") || kind == "Oracle"
            ? "datetime" : "time"), "value": .string(""),
      ]))
  }
  return .string(input)
}
