import XCTest

@testable import BaseSkiffNative

final class ChunkTests: XCTestCase {
  func testInterleavedChunksPreserveExactBytesAndRejectOutOfOrder() throws {
    var decoder = ResponseChunks()
    let response: WireValue = .object([
      "version": .int(1), "id": .string("one"), "result": .string("中文🛶"),
    ])
    let bytes = try JSONEncoder().encode(response)
    let cut = bytes.count / 2
    func chunk(_ id: String, _ index: Int, _ data: Data, _ final: Bool) -> WireValue {
      .object([
        "version": .int(1), "id": .string(id),
        "chunk": .object([
          "index": .int(Int64(index)), "data": .string(data.base64EncodedString()),
          "final": .bool(final),
        ]),
      ])
    }
    XCTAssertNil(try decoder.accept(chunk("one", 0, bytes.prefix(cut), false)))
    XCTAssertEqual(
      try decoder.accept(
        chunk(
          "two", 0, try JSONEncoder().encode(.object(["id": .string("two")]) as WireValue), true)),
      WireValue.object(["id": .string("two")]))
    XCTAssertEqual(
      try decoder.accept(chunk("one", 1, bytes.suffix(bytes.count - cut), true)), response)
    XCTAssertThrowsError(try decoder.accept(chunk("one", 2, Data(), true)))
  }
}
