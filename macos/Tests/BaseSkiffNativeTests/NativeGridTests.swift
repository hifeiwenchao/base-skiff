import AppKit
import SwiftUI
import XCTest

@testable import BaseSkiffNative

final class NativeGridTests: XCTestCase {
  @MainActor func testPrimaryKeyProtectionIsSpecificToPersistedRows() {
    let grid = NativeGrid(
      columns: ["id", "payload"], rows: [["id": .int(1)], ["id": .null]],
      editableColumns: ["id", "payload"], onEdit: { _, _, _ in }, protectedColumns: [0: ["id"]])
    let coordinator = grid.makeCoordinator()
    XCTAssertFalse(coordinator.canEdit(row: 0, column: "id"))
    XCTAssertTrue(coordinator.canEdit(row: 1, column: "id"))
    XCTAssertTrue(coordinator.canEdit(row: 0, column: "payload"))
  }
  @MainActor func testDeletedRowsRemainProtectedAndRenderStrikethrough() throws {
    _ = NSApplication.shared
    let grid = NativeGrid(
      columns: ["value"], rows: [["value": .string("old")]], editableColumns: ["value"],
      onEdit: { _, _, _ in }, deletedRows: [0])
    let coordinator = grid.makeCoordinator()
    let table = NSTableView()
    coordinator.table = table
    coordinator.refresh(grid)
    XCTAssertFalse(coordinator.canEdit(row: 0, column: "value"))
    let cell = try XCTUnwrap(
      coordinator.tableView(table, viewFor: table.tableColumns[0], row: 0) as? NSTableCellView)
    XCTAssertEqual(
      cell.textField?.attributedStringValue.attribute(
        .strikethroughStyle, at: 0, effectiveRange: nil) as? Int, NSUnderlineStyle.single.rawValue)
  }
  @MainActor func testSelectionRefreshPreservesMultipleRows() {
    _ = NSApplication.shared
    var selected: Set<Int> = [0, 2]
    let selection = Binding<Set<Int>>(get: { selected }, set: { selected = $0 })
    let grid = NativeGrid(
      columns: ["id"], rows: [["id": .int(1)], ["id": .int(2)], ["id": .int(3)]],
      selectedRows: selection)
    let coordinator = grid.makeCoordinator()
    let table = NSTableView()
    table.dataSource = coordinator
    table.delegate = coordinator
    table.allowsMultipleSelection = true
    coordinator.table = table
    coordinator.refresh(grid)
    XCTAssertEqual(Set(table.selectedRowIndexes), [0, 2])
    coordinator.refresh(grid)
    XCTAssertEqual(Set(table.selectedRowIndexes), [0, 2])
  }
}

extension NativeGridTests {
  @MainActor func testBinaryCellsMayBeSetNullWithoutTextCoercion() {
    let binary: WireValue = .object(["$type": .string("bytes"), "value": .string("AP8=")])
    let grid = NativeGrid(
      columns: ["payload"], rows: [["payload": binary]], editableColumns: ["payload"],
      onEdit: { _, _, _ in }, onSetNull: { _, _ in })
    let coordinator = grid.makeCoordinator()
    XCTAssertFalse(coordinator.canEdit(row: 0, column: "payload"))
    XCTAssertTrue(coordinator.canSetNull(row: 0, column: "payload"))
  }
}
