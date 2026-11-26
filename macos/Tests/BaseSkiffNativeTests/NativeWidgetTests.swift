import XCTest

@testable import BaseSkiffNative

final class NativeWidgetTests: XCTestCase {
  func testStatementExecutionSkipsQuotedAndCommentDelimiters() {
    for prefix in [
      "SELECT ';'", "SELECT 'it''s;fine'", "SELECT \"a;b\"", "SELECT `a;b`", "SELECT [a;b]",
      "SELECT $$a;b$$", "SELECT $tag$a;b$tag$", "SELECT 1 /* ; /* nested ; */ ; */",
      "SELECT 1 -- ;\n", "SELECT '中文😀;'",
    ] {
      let sql = prefix + "; SELECT 2;"
      XCTAssertEqual(
        currentSQLStatement(in: sql, at: (sql as NSString).length - 3), "SELECT 2", sql)
      XCTAssertEqual(
        currentSQLStatement(in: sql, at: 2), prefix.trimmingCharacters(in: .whitespacesAndNewlines),
        sql)
    }
  }
  func testStatementAtTrailingWhitespaceAndEmptyDocument() {
    XCTAssertEqual(currentSQLStatement(in: "SELECT 1;  ", at: 11), "SELECT 1")
    XCTAssertEqual(currentSQLStatement(in: "", at: 0), "")
  }
  func testCompletionCaretConvertsUTF16ToPythonUnicodeOffsets() {
    let source = "SELECT '中文😀'; SELECT c."
    XCTAssertEqual(
      sqlCodepointOffset(source, utf16Caret: (source as NSString).length),
      source.unicodeScalars.count)
    XCTAssertEqual(sqlCodepointOffset(source, utf16Caret: 0), 0)
  }
  @MainActor func testCompletionMetadataIsFetchedOnceAcrossExplicitRequests() async throws {
    let script = #"""
      completion_table_calls=0
      completion_schema_calls=0
      while IFS= read -r line; do
        id=$(printf '%s' "$line" | sed -E 's/.*"id":"([^"]+)".*/\1/')
        case "$line" in
          *'"method":"completion_context"'*) result='"SELECT c. FROM customers c"';;
          *'"method":"list_tables"'*) completion_table_calls=$((completion_table_calls+1)); result='[{"name":"customers","schema":""}]';;
          *'"method":"get_schema"'*) completion_schema_calls=$((completion_schema_calls+1)); result='[{"name":"id"},{"name":"name"}]';;
          *'"method":"completion_fragment"'*) result='["c",""]';;
          *'"method":"completion_scope"'*) result='[{"c":["id","name"]},["customers"],[]]';;
          *'"method":"stats"'*) result="[$completion_table_calls,$completion_schema_calls]";;
          *) result='null';;
        esac
        printf '{"version":1,"id":"%s","result":%s}\n' "$id" "$result"
      done
      """#
    let client = RPCClient(
      workerExecutable: URL(fileURLWithPath: "/bin/sh"), arguments: ["-c", script])
    try client.start()
    defer { client.stop() }
    let store = WorkbenchStore(rpc: client)
    let tab = WorkTab(
      connection: .object(["id": .string("test"), "kind": .string("SQLite")]), database: "",
      object: .null, mode: "console", title: "Console")
    let completion = ConsoleCompletion()
    for _ in 0..<2 {
      await completion.request(
        store: store, tab: tab, source: "SELECT c. FROM customers c", utf16Caret: 9)
    }
    XCTAssertEqual(completion.words, ["id", "name"])
    let calls = try await client.call("stats")
    XCTAssertEqual(calls, .array([.int(1), .int(1)]))
  }
  func testRedisPayloadKeepsLiteralTextAndExplicitExpiry() throws {
    let payload = try newRedisKeyValues(
      key: " literal:key ", kind: "hash", string: "",
      rows: [
        RedisDraftRow(field: "amount", value: "001.20"),
        RedisDraftRow(field: "json", value: "{\"a\":1}"),
      ], ttl: "123")
    XCTAssertEqual(payload["key"], .string(" literal:key "))
    XCTAssertEqual(payload["value"]?["amount"], .string("001.20"))
    XCTAssertEqual(payload["value"]?["json"], .string("{\"a\":1}"))
    XCTAssertEqual(payload["ttl"], .int(123))
    let empty = try newRedisKeyValues(key: "empty", kind: "string", string: "", rows: [], ttl: "")
    XCTAssertEqual(empty["value"], .string(""))
    XCTAssertEqual(empty["ttl"], .null)
  }
  func testRedisRejectsInvalidCollectionsScoresAndTTL() {
    for ttl in ["0", "-1", "2147483648", "1.5", "NaN"] {
      XCTAssertThrowsError(
        try newRedisKeyValues(key: "key", kind: "string", string: "", rows: [], ttl: ttl))
    }
    for kind in ["hash", "list", "set", "zset"] {
      XCTAssertThrowsError(
        try newRedisKeyValues(key: "key", kind: kind, string: "", rows: [], ttl: ""))
    }
    XCTAssertThrowsError(
      try newRedisKeyValues(
        key: "key", kind: "hash", string: "",
        rows: [RedisDraftRow(field: "a"), RedisDraftRow(field: "a")], ttl: ""))
    XCTAssertThrowsError(
      try newRedisKeyValues(key: "key", kind: "hash", string: "", rows: [RedisDraftRow()], ttl: ""))
    for score in ["nan", "inf", "-inf", "not a number"] {
      XCTAssertThrowsError(
        try newRedisKeyValues(
          key: "key", kind: "zset", string: "",
          rows: [RedisDraftRow(value: "member", score: score)], ttl: ""))
    }
  }
  func testRedisZSetAndListPreserveShape() throws {
    let sorted = try newRedisKeyValues(
      key: "z", kind: "zset", string: "", rows: [RedisDraftRow(value: "member", score: "-12.5")],
      ttl: "2147483647")
    XCTAssertEqual(sorted["value"]?["member"], .double(-12.5))
    let list = try newRedisKeyValues(
      key: "l", kind: "list", string: "",
      rows: [RedisDraftRow(value: "1"), RedisDraftRow(value: "")], ttl: "")
    XCTAssertEqual(list["value"], .array([.string("1"), .string("")]))
  }
}

extension NativeWidgetTests {
  func testRedisUserMarkerFieldIsEscapedOnCreation() throws {
    let result = try newRedisKeyValues(
      key: "safe", kind: "hash", string: "",
      rows: [RedisDraftRow(field: "$type", value: "business")], ttl: "")
    XCTAssertEqual(result["value"]?["$type"], .string("map"))
    XCTAssertEqual(result["value"]?.stringKeyedFields?["$type"], .string("business"))
  }
}

extension NativeWidgetTests {
  func testRedisPreviewNoticesNeverEnableDestructiveEditing() {
    XCTAssertFalse(
      redisPreviewCanEdit(
        format: "text", chain: ["text"], notice: "Preview truncated; export retains all bytes."))
    XCTAssertFalse(redisPreviewCanEdit(format: "gzip", chain: ["gzip", "hex"], notice: ""))
    XCTAssertFalse(
      redisPreviewCanEdit(
        format: "text", chain: ["text"],
        notice: "Invalid UTF-8 or control characters are shown as escapes."))
    XCTAssertTrue(redisPreviewCanEdit(format: "gzip", chain: ["gzip", "msgpack"], notice: ""))
    XCTAssertTrue(redisPreviewCanEdit(format: "json", chain: ["json"], notice: ""))
  }
}
