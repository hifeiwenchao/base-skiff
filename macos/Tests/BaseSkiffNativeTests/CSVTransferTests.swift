import XCTest

@testable import BaseSkiffNative

final class CSVTransferTests: XCTestCase {
  private let options: WireValue = .object([
    "encoding": .string("utf-8"), "delimiter": .string(","), "null_value": .string("\\N"),
  ])
  private var preview: CSVImportPreview {
    CSVImportPreview(
      source: "/tmp/source.csv", options: options,
      result: .object([
        "columns": .array([.string("name"), .string("name")]),
        "rows": .array([.array([.string("first"), .string("second")])]),
        "stat": .array([
          .int(123),
          .object(["$type": .string("integer"), "value": .string("1700000000123456789")]),
        ]),
      ]))
  }
  func testImportRejectsChangedSourceAndEachParsingOption() {
    XCTAssertThrowsError(
      try preview.importParameters(
        source: "/tmp/other.csv", options: options, mapping: [0: "target"],
        allowedColumns: ["target"]))
    for (key, value) in [("encoding", "utf-16"), ("delimiter", ";"), ("null_value", "NULL")] {
      var modified = options.object!
      modified[key] = .string(value)
      XCTAssertThrowsError(
        try preview.importParameters(
          source: preview.source, options: .object(modified), mapping: [0: "target"],
          allowedColumns: ["target"]))
    }
  }
  func testDuplicateHeadersRemainSeparateAndImportUsesSourceIndex() throws {
    XCTAssertEqual(preview.displayColumns, ["1 · name", "2 · name"])
    XCTAssertEqual(preview.displayRows[0]["1 · name"], .string("first"))
    XCTAssertEqual(preview.displayRows[0]["2 · name"], .string("second"))
    let params = try preview.importParameters(
      source: preview.source, options: options, mapping: [0: "", 1: "target"],
      allowedColumns: ["target"])
    XCTAssertEqual(
      params["mapping"],
      .object(["$type": .string("map"), "value": .array([.array([.int(1), .string("target")])])]))
    XCTAssertEqual(
      params["expected_stat"],
      .array([
        .int(123), .object(["$type": .string("integer"), "value": .string("1700000000123456789")]),
      ]))
    XCTAssertEqual(params["options"], options)
    XCTAssertThrowsError(
      try preview.importParameters(
        source: preview.source, options: options, mapping: [0: "target", 1: "target"],
        allowedColumns: ["target"]))
    XCTAssertThrowsError(
      try preview.importParameters(
        source: preview.source, options: options, mapping: [2: "target"], allowedColumns: ["target"]
      ))
  }
  func testDisplayedGB18030UsesSelectedEncoding() throws {
    let data = try displayedCSVData(
      columns: ["v"], rows: [["v": .string("中文")]], encoding: "gb18030", delimiter: ",",
      nullValue: "\\N")
    XCTAssertEqual(
      data, Data([0x22, 0x76, 0x22, 13, 10, 0x22, 0xD6, 0xD0, 0xCE, 0xC4, 0x22, 13, 10]))
  }
  func testDisplayedCSVPreservesQuotesNewlinesNULLAndBOM() throws {
    let rows: [[String: WireValue]] = [["a": .string("x\"y\nz"), "b": .null]]
    let data = try displayedCSVData(
      columns: ["a", "b"], rows: rows, encoding: "utf-8-sig", delimiter: ";", nullValue: "<NULL>")
    XCTAssertEqual(data.prefix(3), Data([0xEF, 0xBB, 0xBF]))
    XCTAssertEqual(
      String(data: data.dropFirst(3), encoding: .utf8),
      "\"a\";\"b\"\r\n\"x\"\"y\nz\";\"<NULL>\"\r\n")
    let utf16 = try displayedCSVData(
      columns: ["a"], rows: [], encoding: "utf-16", delimiter: ",", nullValue: "")
    XCTAssertEqual(String(data: utf16, encoding: .utf16), "\"a\"\r\n")
  }
  func testQuoteAndNewlineDelimitersAreRejected() {
    for delimiter in ["", "::", "\"", "\r", "\n", "e\u{301}"] {
      XCTAssertThrowsError(try validateCSVDelimiter(delimiter))
    }
    XCTAssertNoThrow(try validateCSVDelimiter("\t"))
  }
}
