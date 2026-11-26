import XCTest

@testable import BaseSkiffNative

final class NativeRedisIntegrationTests: XCTestCase {
  @MainActor func testRedisBatchesAccumulateAndUnsubmittedDraftPausesLoading() async throws {
    let env = ProcessInfo.processInfo.environment
    guard let python = env["BASESKIFF_PYTHON"], let configPath = env["BASESKIFF_LIVE_CONFIG"] else {
      throw XCTSkip("Set live configuration and Python")
    }
    let config = try JSONDecoder().decode(
      WireValue.self, from: Data(contentsOf: URL(fileURLWithPath: configPath)))
    let data = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
    try FileManager.default.createDirectory(at: data, withIntermediateDirectories: true)
    defer { try? FileManager.default.removeItem(at: data) }
    let rpc = RPCClient(
      workerExecutable: URL(fileURLWithPath: python),
      arguments: ["-m", "baseskiff.native.worker", "--data-dir", data.path])
    try rpc.start()
    defer { rpc.stop() }
    let store = WorkbenchStore(rpc: rpc, defaults: UserDefaults(suiteName: UUID().uuidString)!)
    var c = fields(config["redis"])
    c["name"] = .string("Native Redis test")
    c["kind"] = .string("Redis")
    c["database"] = .string("0")
    c["host"] = config["host"]
    c = c.filter {
      ["name", "kind", "database", "host", "port", "username", "password"].contains($0.key)
    }
    let saved = try await rpc.call("save_connection", params: ["connection": .object(c)])
    let connectionID = text(saved["id"])
    let parentID = connectionID + ":db:0"
    let prefix =
      "baseskiff_swift_" + UUID().uuidString.replacingOccurrences(of: "-", with: "") + ":"
    let keys = (0..<1200).map { prefix + "group:" + String($0) }
    let scope: [String: WireValue] = [
      "connection_id": .string(connectionID), "database": .string("0"),
    ]
    _ = try await rpc.call(
      "execute",
      params: scope.merging([
        "query": .string("MSET " + keys.map { $0 + " value" }.joined(separator: " "))
      ]) { _, new in new })
    do {
      let parent = SidebarNode(id: parentID, label: "DB0", kind: "database")
      store.nodes = [parent]
      store.targets[parentID] = TreeTarget(connection: saved, database: "0", action: "database")
      store.redisQueries[parentID] = (prefix, "prefix")
      store.redisDrafts[parentID] = (prefix, "prefix")
      await store.loadKeys(parentID: parentID, append: false)
      XCTAssertLessThanOrEqual(store.loadedKeyIDs[parentID]?.count ?? 0, 500)
      XCTAssertNotEqual(store.redisPages[parentID], .null)
      let first = store.loadedKeyIDs[parentID] ?? []
      store.redisDrafts[parentID] = (prefix + "unsubmitted", "prefix")
      await store.loadKeys(parentID: parentID, append: true)
      XCTAssertEqual(store.loadedKeyIDs[parentID], first)
      store.redisDrafts[parentID] = (prefix, "prefix")
      var iterations = 0
      while store.redisPages[parentID] != .null && store.redisPages[parentID] != nil
        && iterations < 30
      {
        await store.loadKeys(parentID: parentID, append: true)
        iterations += 1
      }
      XCTAssertEqual(store.loadedKeyIDs[parentID]?.count, 1200)
      XCTAssertTrue(first.isSubset(of: store.loadedKeyIDs[parentID] ?? []))
      XCTAssertEqual(parent.children.first?.kind, "redisSearch")
      let hashKey = prefix + "hash"
      _ = try await rpc.call(
        "insert_row",
        params: scope.merging([
          "table": .string(hashKey),
          "values": .object([
            "type": .string("hash"), "value": .object(["field": .string("value")]), "ttl": .int(60),
          ]),
        ]) { _, new in new })
      await store.open(
        TreeTarget(connection: saved, database: "0", object: .string(hashKey), action: "redis"))
      XCTAssertEqual(store.current?.redis["kind"], .string("hash"))
      XCTAssertEqual(store.current?.redis["value"]?["field"], .string("value"))
      _ = try await rpc.call(
        "execute", params: scope.merging(["query": .string("DEL " + hashKey)]) { _, new in new })
    } catch {
      _ = try? await rpc.call(
        "execute",
        params: scope.merging(["query": .string("DEL " + keys.joined(separator: " "))]) { _, new in
          new
        })
      throw error
    }
    _ = try await rpc.call(
      "execute",
      params: scope.merging(["query": .string("DEL " + keys.joined(separator: " "))]) { _, new in
        new
      })
  }

  @MainActor func testLargeBinaryWorkerResponseIsReassembledWithoutTruncation() async throws {
    guard let python = ProcessInfo.processInfo.environment["BASESKIFF_PYTHON"] else {
      throw XCTSkip("Set Python")
    }
    let data = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
    try FileManager.default.createDirectory(at: data, withIntermediateDirectories: true)
    defer { try? FileManager.default.removeItem(at: data) }
    let rpc = RPCClient(
      workerExecutable: URL(fileURLWithPath: python),
      arguments: ["-m", "baseskiff.native.worker", "--data-dir", data.path])
    try rpc.start()
    defer { rpc.stop() }
    let c = try await rpc.call("list_connections")
    let id = try XCTUnwrap(c.array?.first?["id"])
    let result = try await rpc.call(
      "execute",
      params: ["connection_id": id, "query": .string("SELECT zeroblob(12582912) AS payload")])
    let value = try XCTUnwrap(result["rows"]?.array?.first?["payload"])
    XCTAssertEqual(value["$type"], .string("bytes"))
    XCTAssertEqual(Data(base64Encoded: text(value["value"]))?.count, 12_582_912)
  }
}
