import XCTest

@testable import BaseSkiffNative

final class WorkspaceParityTests: XCTestCase {
  @MainActor func testStructureDraftLivesInTabAcrossWorkspaceRecreation() {
    let store = WorkbenchStore()
    let tab = WorkTab(
      connection: .object(["id": .string("c"), "kind": .string("SQLite")]), database: "",
      object: .string("table"), mode: "table", title: "table")
    tab.structureDraft.actions = [.object(["method": .string("add_column")])]
    tab.structureDraft.sql = "ALTER TABLE table ADD COLUMN name TEXT"
    tab.structureDraft.locked = true
    let first = StructureWorkspace(store: store, tab: tab)
    let second = StructureWorkspace(store: store, tab: tab)
    XCTAssertTrue(first.draft === second.draft)
    XCTAssertTrue(tab.hasPending)
    XCTAssertEqual(second.draft.sql, tab.structureDraft.sql)
    XCTAssertTrue(second.draft.locked)
    XCTAssertFalse(tab.canEdit)
  }
  @MainActor func testFullExportUsesExecutedSelectionAndCommittedFilters() throws {
    let connection: WireValue = .object(["id": .string("c"), "kind": .string("SQLite")])
    let console = WorkTab(
      connection: connection, database: "", object: .null, mode: "console", title: "Console")
    console.draft = "DELETE FROM customers"
    console.lastExecutedQuery = "SELECT name FROM customers"
    let query = try fullCSVExportParameters(
      tab: console, destination: "/tmp/export.csv", encoding: "utf-8", delimiter: ",",
      nullValue: "\\N")
    XCTAssertEqual(query["query"], .string("SELECT name FROM customers"))
    let table = WorkTab(
      connection: connection, database: "", object: .string("customers"), mode: "table",
      title: "customers")
    table.searchDraft = "unsubmitted"
    table.appliedSearch = "literal"
    table.sortColumn = "name"
    table.descending = true
    table.appliedWhere = "id > 4"
    table.appliedOrder = "id DESC"
    let params = try fullCSVExportParameters(
      tab: table, destination: "/tmp/export.csv", encoding: "utf-8", delimiter: ",",
      nullValue: "\\N")
    XCTAssertEqual(params["search"], .string("literal"))
    XCTAssertEqual(params["sort_column"], .string("name"))
    XCTAssertEqual(params["descending"], .bool(true))
    XCTAssertEqual(params["where_clause"], .string("id > 4"))
    XCTAssertEqual(params["order_by"], .string("id DESC"))
    console.lastExecutedQuery = nil
    XCTAssertThrowsError(
      try fullCSVExportParameters(
        tab: console, destination: "/tmp/export.csv", encoding: "utf-8", delimiter: ",",
        nullValue: ""))
  }
}

extension WorkspaceParityTests {
  @MainActor func testOutlineCallbackCloneExpandsCanonicalStoreNode() async throws {
    guard let python = ProcessInfo.processInfo.environment["BASESKIFF_PYTHON"] else {
      throw XCTSkip("Set BASESKIFF_PYTHON")
    }
    let data = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
    try FileManager.default.createDirectory(at: data, withIntermediateDirectories: true)
    defer { try? FileManager.default.removeItem(at: data) }
    let rpc = RPCClient(
      workerExecutable: URL(fileURLWithPath: python),
      arguments: ["-m", "baseskiff.native.worker", "--data-dir", data.path])
    try rpc.start()
    defer { rpc.stop() }
    let store = WorkbenchStore(rpc: rpc)
    try await store.reloadConnections()
    let source = try XCTUnwrap(store.nodes.first)
    let clone = SidebarNode(id: source.id, label: source.label, kind: source.kind)
    let revision = store.treeRevision
    await store.expand(clone, true)
    XCTAssertTrue(clone.children.isEmpty)
    XCTAssertTrue(source.children.contains(where: { $0.kind == "console" }))
    XCTAssertTrue(source.children.contains(where: { $0.kind == "history" }))
    XCTAssertGreaterThan(
      source.children.first(where: { $0.kind == "folder" })?.children.count ?? 0, 0)
    XCTAssertGreaterThan(store.treeRevision, revision)
  }
  @MainActor func testFailedArbitraryRedisSelectionPreservesConnectionAndTree() async throws {
    let script =
      #"read line; id=$(printf '%s' "$line" | sed -E 's/.*"id":"([^"]+)".*/\1/'); printf '{"version":1,"id":"%s","error":{"message":"DB index is unavailable"}}\n' "$id"; read line"#
    let rpc = RPCClient(
      workerExecutable: URL(fileURLWithPath: "/bin/sh"), arguments: ["-c", script])
    try rpc.start()
    defer { rpc.stop() }
    let store = WorkbenchStore(rpc: rpc)
    let connection: WireValue = .object([
      "id": .string("r"), "kind": .string("Redis"), "database": .string("0"),
    ])
    let root = SidebarNode(
      id: "r", label: "Redis", kind: "connection",
      children: [SidebarNode(id: "r:db:0", label: "DB0", kind: "database")])
    store.nodes = [root]
    store.connections = [connection]
    do {
      _ = try await store.chooseRedisDatabase(connection, index: 22)
      XCTFail("Unavailable database must fail")
    } catch {}
    XCTAssertEqual(store.connections, [connection])
    XCTAssertEqual(root.children.map(\.id), ["r:db:0"])
    XCTAssertNil(store.targets["r:db:22"])
  }
}
