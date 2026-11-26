import XCTest

@testable import BaseSkiffNative

final class WorkbenchTests: XCTestCase {
  @MainActor func testSQLAndRedisParametersRespectServiceScope() {
    let sql = WorkTab(
      connection: .object(["id": .string("sql"), "kind": .string("SQLite")]),
      database: "path.sqlite", object: .string("customers"), mode: "table", title: "Customers")
    XCTAssertNil(sql.base["database"])
    XCTAssertEqual(sql.scope["database"], .string("path.sqlite"))
    let redis = WorkTab(
      connection: .object(["id": .string("r"), "kind": .string("Redis")]), database: "2",
      object: .string("key"), mode: "redis", title: "key")
    XCTAssertEqual(redis.base["database"], .string("2"))
  }
  func testTypedScalarEditsValidateWithoutLosingTag() throws {
    for (tag, input) in [
      ("integer", "9007199254740993000"), ("decimal", "1234567890.1234567890"),
      ("date", "2026-11-26"), ("uuid", "01234567-89ab-cdef-0123-456789abcdef"),
    ] {
      XCTAssertEqual(
        try typedValue(input, like: .object(["$type": .string(tag), "value": .string("old")])),
        .object(["$type": .string(tag), "value": .string(input)]))
    }
    for (tag, input) in [
      ("integer", "1.5"), ("decimal", "abc"), ("date", "2026-02-31"), ("datetime", "yesterday"),
      ("time", "25:99"), ("uuid", "not-uuid"),
    ] {
      XCTAssertThrowsError(
        try typedValue(input, like: .object(["$type": .string(tag), "value": .string("old")])))
    }
    XCTAssertEqual(try typedValue("NULL", like: .string("hello")), .string("NULL"))
    XCTAssertThrowsError(try typedValue("NaN", like: .double(1)))
  }

  @MainActor func testLiveWorkerSQLiteUsesIsolatedDataDirectory() async throws {
    let env = ProcessInfo.processInfo.environment
    guard let python = env["BASESKIFF_PYTHON"], !python.isEmpty else {
      throw XCTSkip("Set BASESKIFF_PYTHON to run the real worker integration")
    }
    let root = URL(fileURLWithPath: #filePath).deletingLastPathComponent()
      .deletingLastPathComponent().deletingLastPathComponent().deletingLastPathComponent()
    let data = FileManager.default.temporaryDirectory.appendingPathComponent(
      "baseskiff-swift-test-\(UUID().uuidString)")
    try FileManager.default.createDirectory(at: data, withIntermediateDirectories: true)
    defer { try? FileManager.default.removeItem(at: data) }
    let client = RPCClient(
      workerExecutable: URL(fileURLWithPath: python),
      arguments: ["-m", "baseskiff.native.worker", "--data-dir", data.path])
    setenv("BASESKIFF_SOURCE", root.path, 1)
    try client.start()
    defer { client.stop() }
    let hello = try await client.hello()
    XCTAssertEqual(hello["version"], .int(1))
    let connections = try await client.call("list_connections")
    let connection = try XCTUnwrap(
      connections.array?.first(where: { $0["kind"]?.string == "SQLite" }))
    let result = try await client.call(
      "execute",
      params: [
        "connection_id": try XCTUnwrap(connection["id"]),
        "query": .string("SELECT COUNT(*) AS total FROM customers"),
      ])
    XCTAssertEqual(result["rows"]?.array?.first?["total"], .int(128))
  }
}

extension WorkbenchTests {
  @MainActor func testSQLiteHistoryAndConsoleScopeMatchesExistingQtBasename() {
    let tab = WorkTab(
      connection: .object([
        "id": .string("sql"), "kind": .string("SQLite"), "path": .string("/tmp/playground.sqlite"),
      ]), database: "", object: .null, mode: "console", title: "Console")
    XCTAssertEqual(tab.scope["database"], .string("playground.sqlite"))
    XCTAssertNil(tab.base["database"])
  }
}

extension WorkbenchTests {
  @MainActor func testStructuredErrorPositionMapsPythonUnicodeToUTF16OnlyForCurrentDraft()
    async throws
  {
    let script =
      #"while IFS= read -r line; do id=$(printf '%s' "$line" | sed -E 's/.*"id":"([^"]+)".*/\1/'); printf '{"version":1,"id":"%s","error":{"message":"bad SQL","position":3,"line":1,"column":3}}\n' "$id"; done"#
    let rpc = RPCClient(
      workerExecutable: URL(fileURLWithPath: "/bin/sh"), arguments: ["-c", script])
    try rpc.start()
    defer { rpc.stop() }
    let store = WorkbenchStore(rpc: rpc)
    let tab = WorkTab(
      connection: .object(["id": .string("c"), "kind": .string("PostgreSQL")]), database: "",
      object: .null, mode: "console", title: "Console")
    tab.draft = "😀xBAD"
    _ = await store.operation(tab, method: "execute", params: ["query": .string(tab.draft)])
    XCTAssertEqual(tab.errorPosition, 3)
    XCTAssertEqual(tab.errorLine, 1)
    XCTAssertEqual(tab.errorColumn, 3)
    _ = await store.operation(
      tab, method: "execute", params: ["query": .string("different selection")])
    XCTAssertNil(tab.errorPosition)
    XCTAssertTrue(store.error.contains("bad SQL"))
  }
}
