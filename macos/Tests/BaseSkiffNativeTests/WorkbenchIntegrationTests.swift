import XCTest

@testable import BaseSkiffNative

final class WorkbenchIntegrationTests: XCTestCase {
  @MainActor func testStagedGridDoesNotWriteUntilCommitAndRollsBackFailure() async throws {
    guard let python = ProcessInfo.processInfo.environment["BASESKIFF_PYTHON"] else {
      throw XCTSkip("Set BASESKIFF_PYTHON")
    }
    let data = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
    try FileManager.default.createDirectory(at: data, withIntermediateDirectories: true)
    defer { try? FileManager.default.removeItem(at: data) }
    let rpc = RPCClient(
      workerExecutable: URL(fileURLWithPath: python),
      arguments: ["-m", "baseskiff.native.worker", "--data-dir", data.path])
    let store = WorkbenchStore(rpc: rpc, defaults: UserDefaults(suiteName: UUID().uuidString)!)
    try rpc.start()
    defer { rpc.stop() }
    let connections = try await rpc.call("list_connections")
    let c = try XCTUnwrap(connections.array?.first)
    let id = try XCTUnwrap(c["id"])
    _ = try await rpc.call(
      "execute",
      params: [
        "connection_id": id,
        "query": .string("CREATE TABLE native_stage(id INTEGER PRIMARY KEY, value TEXT UNIQUE)"),
      ])
    _ = try await rpc.call(
      "execute",
      params: [
        "connection_id": id,
        "query": .string("INSERT INTO native_stage VALUES (1,'original'),(2,'taken')"),
      ])
    let tab = WorkTab(
      connection: c, database: "", object: .string("native_stage"), mode: "table",
      title: "native_stage")
    await store.load(tab)
    XCTAssertEqual(tab.rows.count, 2)
    XCTAssertTrue(tab.canEdit)
    store.edit(tab, row: 0, column: "value", value: "draft")
    let unchanged = try await rpc.call(
      "execute",
      params: ["connection_id": id, "query": .string("SELECT value FROM native_stage WHERE id=1")])
    XCTAssertEqual(unchanged["rows"]?.array?.first?["value"], .string("original"))
    await store.commit(tab)
    XCTAssertEqual(tab.pending, 0)
    XCTAssertEqual(tab.rows.first?["value"], .string("draft"))
    store.edit(tab, row: 0, column: "value", value: "taken")
    await store.commit(tab)
    XCTAssertEqual(tab.pending, 1)
    XCTAssertFalse(store.error.isEmpty)
    let failed = try await rpc.call(
      "execute",
      params: ["connection_id": id, "query": .string("SELECT value FROM native_stage WHERE id=1")])
    XCTAssertEqual(failed["rows"]?.array?.first?["value"], .string("draft"))
    store.revert(tab)
    XCTAssertEqual(tab.pending, 0)
    store.edit(tab, row: 0, column: "id", value: "99")
    XCTAssertEqual(tab.rows.first?["id"], .int(1))
  }
}

extension WorkbenchIntegrationTests {
  @MainActor func testBusyCommitRejectsStagingRevertAndClose() {
    let store = WorkbenchStore()
    let tab = WorkTab(
      connection: .object(["id": .string("c"), "kind": .string("SQLite")]), database: "",
      object: .string("t"), mode: "table", title: "t")
    tab.schema = [
      .object(["name": .string("id"), "primary_key": .bool(true)]),
      .object(["name": .string("value")]),
    ]
    tab.originalRows = [["id": .int(1), "value": .string("old")]]
    tab.rows = [["id": .int(1), "value": .string("draft")]]
    tab.updates = [0: ["value": .string("draft")]]
    tab.busy = true
    tab.operationName = "apply_grid_changes"
    store.tabs = [tab]
    store.activeID = tab.id
    store.addRow(tab)
    store.revert(tab)
    store.close(tab)
    XCTAssertEqual(tab.rows.count, 1)
    XCTAssertEqual(tab.rows[0]["value"], .string("draft"))
    XCTAssertEqual(tab.pending, 1)
    XCTAssertTrue(tab.alive)
    XCTAssertEqual(store.tabs.count, 1)
  }
  @MainActor func testPendingConnectionCannotBeRedirectedAndUnloadedConsoleCannotEraseDraft()
    async throws
  {
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
    let listed = try await rpc.call("list_connections")
    let connection = try XCTUnwrap(listed.array?.first)
    let tab = WorkTab(
      connection: connection, database: "", object: .string("customers"), mode: "table",
      title: "customers")
    tab.inserts = [[:]]
    store.tabs = [tab]
    var replacement = try XCTUnwrap(connection.object)
    replacement["path"] = .string(data.appendingPathComponent("replacement.sqlite").path)
    do {
      try await store.saveConnection(.object(replacement))
      XCTFail("Must reject redirecting pending connection")
    } catch {
      XCTAssertTrue(
        error.localizedDescription.contains("pending") || error.localizedDescription.contains("待提交")
      )
    }
    let after = try await rpc.call("list_connections")
    XCTAssertEqual(after.array?.first?["path"], connection["path"])
    let console = WorkTab(
      connection: connection, database: "", object: .null, mode: "console", title: "Console")
    _ = try await rpc.call(
      "save_console",
      params: console.scope.merging(["content": .string("saved draft")]) { _, new in new })
    await store.saveConsole(console)
    let saved = try await rpc.call("load_console", params: console.scope)
    XCTAssertEqual(saved, .string("saved draft"))
  }
}

extension WorkbenchIntegrationTests {
  @MainActor func testSQLTypeMarkerColumnCanBeEditedWithoutProtocolCollision() async throws {
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
    let connections = try await rpc.call("list_connections")
    let c = try XCTUnwrap(connections.array?.first)
    let id = try XCTUnwrap(c["id"])
    _ = try await rpc.call(
      "execute",
      params: [
        "connection_id": id,
        "query": .string(
          "CREATE TABLE native_marker (id INTEGER PRIMARY KEY, \"$type\" TEXT, value TEXT)"),
      ])
    _ = try await rpc.call(
      "execute",
      params: [
        "connection_id": id,
        "query": .string("INSERT INTO native_marker VALUES (1,'literal','original')"),
      ])
    let tab = WorkTab(
      connection: c, database: "", object: .string("native_marker"), mode: "table",
      title: "native_marker")
    await store.load(tab)
    XCTAssertEqual(tab.rows.first?["$type"], .string("literal"))
    store.edit(tab, row: 0, column: "$type", value: "updated")
    await store.commit(tab)
    XCTAssertEqual(tab.pending, 0)
    XCTAssertEqual(tab.rows.first?["$type"], .string("updated"))
    store.addRow(tab)
    store.edit(tab, row: 1, column: "id", value: "2")
    store.edit(tab, row: 1, column: "$type", value: "new")
    await store.commit(tab)
    XCTAssertEqual(tab.rows.count, 2)
    XCTAssertEqual(tab.rows.last?["$type"], .string("new"))
  }
}

extension WorkbenchIntegrationTests {
  @MainActor func testRealCSVPreviewFingerprintCanBeSubmittedWithoutPrecisionLoss() async throws {
    guard let python = ProcessInfo.processInfo.environment["BASESKIFF_PYTHON"] else {
      throw XCTSkip("Set Python")
    }
    let data = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
    try FileManager.default.createDirectory(at: data, withIntermediateDirectories: true)
    defer { try? FileManager.default.removeItem(at: data) }
    let source = data.appendingPathComponent("input.csv")
    try "id,name\n1,中文\n".write(to: source, atomically: true, encoding: .utf8)
    let rpc = RPCClient(
      workerExecutable: URL(fileURLWithPath: python),
      arguments: ["-m", "baseskiff.native.worker", "--data-dir", data.path])
    try rpc.start()
    defer { rpc.stop() }
    let connections = try await rpc.call("list_connections")
    let id = try XCTUnwrap(connections.array?.first?["id"])
    _ = try await rpc.call(
      "execute",
      params: [
        "connection_id": id,
        "query": .string("CREATE TABLE csv_native(id INTEGER PRIMARY KEY,name TEXT)"),
      ])
    let options: WireValue = .object([
      "encoding": .string("utf-8"), "delimiter": .string(","), "null_value": .string("\\N"),
    ])
    let preview = try await rpc.call(
      "csv_preview", params: ["source": .string(source.path), "options": options])
    XCTAssertEqual(preview["stat"]?.array?.last?["$type"], .string("integer"))
    var params = try CSVImportPreview(source: source.path, options: options, result: preview)
      .importParameters(
        source: source.path, options: options, mapping: [0: "id", 1: "name"],
        allowedColumns: ["id", "name"])
    params["connection_id"] = id
    params["table"] = .string("csv_native")
    let imported = try await rpc.call("import_csv", params: params)
    XCTAssertEqual(imported["affected"], .int(1))
    let rows = try await rpc.call(
      "fetch_rows", params: ["connection_id": id, "table": .string("csv_native")])
    XCTAssertEqual(rows["rows"]?.array?.first?["name"], .string("中文"))
  }
  @MainActor func testMongoMutationRegistersBusyAndBlocksEndpointChanges() async throws {
    let script =
      #"while IFS= read -r line; do id=$(printf '%s' "$line" | sed -E 's/.*"id":"([^"]+)".*/\1/'); case "$line" in *'"method":"insert_row"'*) sleep 0.1;result='{"affected":1}';; *)result='{"columns":[],"rows":[]}';;esac;printf '{"version":1,"id":"%s","result":%s}\n' "$id" "$result";done"#
    let rpc = RPCClient(
      workerExecutable: URL(fileURLWithPath: "/bin/sh"), arguments: ["-c", script])
    try rpc.start()
    defer { rpc.stop() }
    let store = WorkbenchStore(rpc: rpc)
    let connection: WireValue = .object(["id": .string("mongo"), "kind": .string("MongoDB")])
    let tab = WorkTab(
      connection: connection, database: "test", object: .string("docs"), mode: "table",
      title: "docs")
    store.tabs = [tab]
    let task = Task {
      await store.mutateDocument(tab, action: "insert", values: .object(["name": .string("x")]))
    }
    try await Task.sleep(nanoseconds: 20_000_000)
    XCTAssertTrue(tab.busy)
    XCTAssertEqual(tab.operationName, "insert_row")
    do {
      try await store.saveConnection(connection)
      XCTFail("Changed an active connection")
    } catch {
      XCTAssertTrue(
        error.localizedDescription.contains("pending") || error.localizedDescription.contains("任务"))
    }
    await task.value
    XCTAssertFalse(tab.busy)
  }
}
