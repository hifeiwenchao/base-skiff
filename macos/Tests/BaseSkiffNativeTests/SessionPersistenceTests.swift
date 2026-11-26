import XCTest

@testable import BaseSkiffNative

final class SessionPersistenceTests: XCTestCase {
  func testSessionRoundTripsBinaryObjectAndActiveIdentity() throws {
    let binary: WireValue = .object(["$type": .string("bytes"), "value": .string("AP8=")])
    let tab = SessionTab(
      connectionID: "connection", database: "4", object: binary, mode: "redis", title: "binary")
    let saved = WorkspaceSession(tabs: [tab], active: 0)
    XCTAssertEqual(
      try JSONDecoder().decode(WorkspaceSession.self, from: JSONEncoder().encode(saved)), saved)
  }
  func testQtMigrationDoesNotOverwriteNativePreferences() throws {
    let suite = "baseskiff-migration-test-\(UUID())"
    let defaults = try XCTUnwrap(UserDefaults(suiteName: suite))
    defer { defaults.removePersistentDomain(forName: suite) }
    defaults.set("zh", forKey: "native.language")
    let qt: [String: Any] = [
      "language": "en", "display_density": "comfortable", "display_font_size": 15,
      "sidebar_width": 900,
      "workspaceSession":
        #"{"version":1,"tabs":[{"connection_id":"c","database":"1","key_base64":"AP8="}],"active":0}"#,
    ]
    SessionPersistence.migrate(defaults: defaults, legacy: qt)
    XCTAssertEqual(defaults.string(forKey: "native.language"), "zh")
    XCTAssertEqual(defaults.double(forKey: "native.sidebarWidth"), 560)
    XCTAssertEqual(defaults.string(forKey: "native.rowDensity"), "comfortable")
    XCTAssertNotNil(defaults.data(forKey: "native.legacyWorkspaceSession"))
    SessionPersistence.migrate(defaults: defaults, legacy: ["display_font_size": 11])
    XCTAssertEqual(defaults.double(forKey: "native.fontSize"), 15)
  }
  @MainActor func testLateSchemaAfterCloseCannotAlterTab() async throws {
    let script =
      #"while IFS= read -r line; do id=$(printf '%s' "$line" | sed -E 's/.*"id":"([^"]+)".*/\1/'); case "$line" in *'"method":"fetch_rows"'*) printf '{"version":1,"id":"%s","result":{"rows":[],"columns":[]}}\n' "$id";; *'"method":"get_schema"'*) sleep 0.1; printf '{"version":1,"id":"%s","result":[{"name":"late"}]}\n' "$id";; *) printf '{"version":1,"id":"%s","result":{}}\n' "$id";; esac; done"#
    let rpc = RPCClient(
      workerExecutable: URL(fileURLWithPath: "/bin/sh"), arguments: ["-c", script])
    try rpc.start()
    defer { rpc.stop() }
    let store = WorkbenchStore(rpc: rpc)
    let tab = WorkTab(
      connection: .object(["id": .string("c"), "kind": .string("SQLite")]), database: "",
      object: .string("t"), mode: "table", title: "t")
    let task = Task { await store.load(tab) }
    try await Task.sleep(nanoseconds: 40_000_000)
    tab.alive = false
    await task.value
    XCTAssertTrue(tab.schema.isEmpty)
  }
}

extension SessionPersistenceTests {
  @MainActor func testRestoreRetainsBinaryIdentityAndLoadsOnlyActiveTab() async throws {
    let suite = "baseskiff-restore-test-\(UUID())"
    let defaults = try XCTUnwrap(UserDefaults(suiteName: suite))
    defer { defaults.removePersistentDomain(forName: suite) }
    let binary: WireValue = .object(["$type": .string("bytes"), "value": .string("AP8=")])
    let records = [
      SessionTab(
        connectionID: "c", database: "0", object: .string("first"), mode: "redis", title: "first"),
      SessionTab(connectionID: "c", database: "3", object: binary, mode: "redis", title: "binary"),
    ]
    defaults.set(
      try JSONEncoder().encode(WorkspaceSession(tabs: records, active: 1)),
      forKey: "native.workspaceSession")
    let script =
      #"while IFS= read -r line; do id=$(printf '%s' "$line" | sed -E 's/.*"id":"([^"]+)".*/\1/'); printf '{"version":1,"id":"%s","result":{}}\n' "$id"; done"#
    let rpc = RPCClient(
      workerExecutable: URL(fileURLWithPath: "/bin/sh"), arguments: ["-c", script])
    try rpc.start()
    defer { rpc.stop() }
    let store = WorkbenchStore(rpc: rpc, defaults: defaults)
    store.connections = [.object(["id": .string("c"), "kind": .string("Redis")])]
    await store.restoreSession()
    try await Task.sleep(nanoseconds: 30_000_000)
    XCTAssertEqual(store.tabs.count, 2)
    XCTAssertEqual(store.current?.object, binary)
    XCTAssertEqual(store.current?.database, "3")
    XCTAssertTrue(store.tabs[0].needsRestoreLoad)
    XCTAssertFalse(store.tabs[1].needsRestoreLoad)
    store.persistSession()
    let saved = try JSONDecoder().decode(
      WorkspaceSession.self, from: try XCTUnwrap(defaults.data(forKey: "native.workspaceSession")))
    XCTAssertEqual(saved.tabs[1].object, binary)
    XCTAssertEqual(saved.active, 1)
  }
}

extension SessionPersistenceTests {
  @MainActor func testLateConsoleLoadAfterCloseCannotRestoreDraft() async throws {
    let script =
      #"while IFS= read -r line; do id=$(printf '%s' "$line" | sed -E 's/.*"id":"([^"]+)".*/\1/'); case "$line" in *'"method":"load_console"'*) sleep 0.1; printf '{"version":1,"id":"%s","result":"late draft"}\n' "$id";; *) printf '{"version":1,"id":"%s","result":null}\n' "$id";; esac; done"#
    let rpc = RPCClient(
      workerExecutable: URL(fileURLWithPath: "/bin/sh"), arguments: ["-c", script])
    try rpc.start()
    defer { rpc.stop() }
    let store = WorkbenchStore(rpc: rpc)
    let connection: WireValue = .object(["id": .string("c"), "kind": .string("SQLite")])
    let operation = Task {
      await store.open(TreeTarget(connection: connection, database: "", action: "console"))
    }
    try await Task.sleep(nanoseconds: 30_000_000)
    let tab = try XCTUnwrap(store.current)
    store.close(tab)
    await operation.value
    XCTAssertEqual(tab.draft, "")
    XCTAssertFalse(tab.alive)
  }
}
