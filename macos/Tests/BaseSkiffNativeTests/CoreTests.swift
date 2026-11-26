import XCTest

@testable import BaseSkiffNative

final class CoreTests: XCTestCase {
  func testTaggedAndPreciseValuesRoundTrip() throws {
    let text =
      #"{"decimal":{"$type":"decimal","value":"12345678901234567890.1234"},"integer":9223372036854775807,"boolean":true,"null":null,"bytes":{"$type":"bytes","value":"AP8="}}"#
    let value = try JSONDecoder().decode(WireValue.self, from: Data(text.utf8))
    XCTAssertEqual(value["integer"]?.integer, Int64.max)
    XCTAssertEqual(value["decimal"]?["value"]?.string, "12345678901234567890.1234")
    XCTAssertEqual(value["boolean"]?.bool, true)
    XCTAssertEqual(value["null"], .null)
    XCTAssertEqual(
      try JSONDecoder().decode(WireValue.self, from: JSONEncoder().encode(value)), value)
  }

  @MainActor func testClosedReopenedWorkspaceRejectsLateResults() {
    let store = WorkspaceStore()
    let id = UUID()
    let old = store.open(id)
    XCTAssertTrue(store.accepts(old))
    store.close(id)
    XCTAssertFalse(store.accepts(old))
    let current = store.open(id)
    XCTAssertTrue(store.accepts(current))
    XCTAssertFalse(store.accepts(old))
    let next = store.advance(id)
    XCTAssertFalse(store.accepts(current))
    XCTAssertTrue(store.accepts(next))
  }
}

extension CoreTests {
  func testUserDictionaryMarkerIsEscapedAndRoundTripsFields() throws {
    let fields: [String: WireValue] = [
      "$type": .string("bytes"), "value": .string("this is user data"),
    ]
    let value = WireValue.userObject(fields)
    XCTAssertEqual(value["$type"]?.string, "map")
    XCTAssertEqual(value.stringKeyedFields, fields)
    let decoded = try JSONDecoder().decode(WireValue.self, from: JSONEncoder().encode(value))
    XCTAssertEqual(decoded.stringKeyedFields, fields)
  }
}
