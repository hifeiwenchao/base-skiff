import XCTest

@testable import BaseSkiffNative

final class RPCClientTests: XCTestCase {
  @MainActor func testRealProcessConcurrentRoutingAndRemoteError() async throws {
    let client = RPCClient(
      workerExecutable: URL(fileURLWithPath: "/bin/sh"),
      arguments: [
        "-c",
        #"while IFS= read -r line; do id=$(printf '%s' "$line" | sed -E 's/.*"id":"([^"]+)".*/\1/'); case "$line" in *'"method":"fail"'*) printf '{"version":1,"id":"%s","error":{"message":"deliberate failure"}}\n' "$id";; *) printf '{"version":1,"id":"%s","result":{"ok":true}}\n' "$id";; esac; done"#,
      ])
    try client.start()
    defer { client.stop() }
    async let first = client.call("one")
    async let second = client.call("two")
    let values = try await [first, second]
    XCTAssertEqual(values, [.object(["ok": .bool(true)]), .object(["ok": .bool(true)])])
    do {
      _ = try await client.call("fail")
      XCTFail("Expected remote failure")
    } catch { XCTAssertTrue(error.localizedDescription.contains("deliberate failure")) }
  }

  @MainActor func testWorkerExitFailsPendingRequest() async throws {
    let client = RPCClient(
      workerExecutable: URL(fileURLWithPath: "/bin/sh"), arguments: ["-c", "read line; exit 3"])
    try client.start()
    defer { client.stop() }
    do {
      _ = try await client.call("one")
      XCTFail("Expected EOF error")
    } catch { XCTAssertTrue(error.localizedDescription.contains("worker")) }
  }
}

extension RPCClientTests {
  @MainActor func testMismatchedProtocolFailsRequest() async throws {
    let client = RPCClient(
      workerExecutable: URL(fileURLWithPath: "/bin/sh"),
      arguments: [
        "-c", #"read line; printf '{"version":2,"id":"one","result":null}\n'; read line"#,
      ])
    try client.start()
    defer { client.stop() }
    do {
      _ = try await client.call("one", requestID: "one")
      XCTFail("Expected version error")
    } catch { XCTAssertTrue(error.localizedDescription.contains("Incompatible")) }
  }

  @MainActor func testHungRequestTimesOutWithoutRetry() async throws {
    let client = RPCClient(
      workerExecutable: URL(fileURLWithPath: "/bin/sh"),
      arguments: ["-c", "while read line; do :; done"])
    try client.start()
    defer { client.stop() }
    do {
      _ = try await client.call("one", timeout: 0.05)
      XCTFail("Expected timeout")
    } catch { XCTAssertTrue(error.localizedDescription.contains("timed out")) }
  }
}

extension RPCClientTests {
  @MainActor func testLateEphemeralDataDirectoryIsPassedExplicitly() async throws {
    let old = ProcessInfo.processInfo.environment["BASESKIFF_DATA_DIR"]
    _ = ProcessInfo.processInfo.environment
    let value = "/tmp/baseskiff-native-late-" + UUID().uuidString
    setenv("BASESKIFF_DATA_DIR", value, 1)
    defer {
      if let old { setenv("BASESKIFF_DATA_DIR", old, 1) } else { unsetenv("BASESKIFF_DATA_DIR") }
    }
    let script =
      #"while IFS= read -r line; do id=$(printf '%s' "$line" | sed -E 's/.*"id":"([^"]+)".*/\1/'); printf '{"version":1,"id":"%s","result":"%s"}\n' "$id" "$1"; done"#
    let client = RPCClient(
      workerExecutable: URL(fileURLWithPath: "/bin/sh"), arguments: ["-c", script])
    try client.start()
    defer { client.stop() }
    let result = try await client.call("location")
    XCTAssertEqual(result, .string(value))
  }
}
