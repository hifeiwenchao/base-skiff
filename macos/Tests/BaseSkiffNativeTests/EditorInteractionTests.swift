import AppKit
import SwiftUI
import XCTest

@testable import BaseSkiffNative

final class EditorInteractionTests: XCTestCase {
  func testErrorRangeRespectsUnicodeAndEndOfDocument() {
    XCTAssertEqual(sqlErrorRange(in: "a😀z", position: 1), NSRange(location: 1, length: 2))
    XCTAssertEqual(sqlErrorRange(in: "a😀z", position: 2), NSRange(location: 1, length: 2))
    XCTAssertEqual(sqlErrorRange(in: "e\u{301}", position: 1), NSRange(location: 0, length: 2))
    XCTAssertEqual(sqlErrorRange(in: "ab", position: 2), NSRange(location: 1, length: 1))
    XCTAssertNil(sqlErrorRange(in: "", position: 0))
    XCTAssertNil(sqlErrorRange(in: "a", position: -1))
  }
  @MainActor func testErrorMarkerIsTemporaryAndPreservesSelection() throws {
    _ = NSApplication.shared
    let view = NativeEditor(text: .constant("SELECT invalid"), onRun: { _ in })
    let coordinator = view.makeCoordinator()
    let editor = QueryTextView(frame: NSRect(x: 0, y: 0, width: 500, height: 200))
    editor.string = "SELECT invalid"
    editor.setSelectedRange(NSRange(location: 2, length: 3))
    coordinator.editor = editor
    coordinator.markError(7, scroll: false)
    let layout = try XCTUnwrap(editor.layoutManager)
    XCTAssertNotNil(
      layout.temporaryAttribute(.backgroundColor, atCharacterIndex: 7, effectiveRange: nil))
    XCTAssertNil(editor.textStorage?.attribute(.backgroundColor, at: 7, effectiveRange: nil))
    XCTAssertEqual(editor.selectedRange(), NSRange(location: 2, length: 3))
    coordinator.clearError()
    XCTAssertNil(
      layout.temporaryAttribute(.backgroundColor, atCharacterIndex: 7, effectiveRange: nil))
  }
  @MainActor func testHeaderClickDelegatesSortingWithoutReorderingDisplayedData() {
    _ = NSApplication.shared
    var clicked: String?
    let rows: [[String: WireValue]] = [["id": .int(2)], ["id": .int(1)]]
    let view = NativeGrid(
      columns: ["id"], rows: rows, onSortColumn: { clicked = $0 }, sortColumn: "id",
      sortDescending: true)
    let coordinator = view.makeCoordinator()
    let table = NSTableView()
    coordinator.table = table
    coordinator.refresh(view)
    coordinator.tableView(table, didClick: table.tableColumns[0])
    XCTAssertEqual(clicked, "id")
    XCTAssertEqual(coordinator.parent.rows, rows)
    XCTAssertNotNil(table.indicatorImage(in: table.tableColumns[0]))
  }
}
