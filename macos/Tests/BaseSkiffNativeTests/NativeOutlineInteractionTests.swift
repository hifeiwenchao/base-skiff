import AppKit
import SwiftUI
import XCTest

@testable import BaseSkiffNative

final class NativeOutlineInteractionTests: XCTestCase {
  @MainActor func testConnectionTitleExpandsOnceAndLaterTogglesWithoutSelectLoad() throws {
    _ = NSApplication.shared
    var selected: String?
    var selections = 0
    var expansions: [Bool] = []
    let root = SidebarNode(
      id: "connection", label: "Connection", kind: "connection",
      children: [SidebarNode(id: "table", label: "Table", kind: "table")])
    let view = NativeOutline(
      nodes: [root], selectedID: Binding(get: { selected }, set: { selected = $0 }),
      onSelect: { _ in selections += 1 }, onExpand: { _, state in expansions.append(state) },
      revision: 1)
    let coordinator = view.makeCoordinator()
    let outline = SidebarOutlineView()
    outline.dataSource = coordinator
    outline.delegate = coordinator
    coordinator.outline = outline
    coordinator.refresh(view)
    let stable = try XCTUnwrap(coordinator.registry["connection"])
    coordinator.titleClicked(stable)
    XCTAssertTrue(outline.isItemExpanded(stable))
    XCTAssertEqual(expansions, [true])
    XCTAssertEqual(selections, 0)
    coordinator.titleClicked(stable)
    XCTAssertFalse(outline.isItemExpanded(stable))
    XCTAssertEqual(expansions, [true, false])
    XCTAssertEqual(selections, 0)
    coordinator.titleClicked(stable)
    XCTAssertEqual(expansions, [true, false, true])
    XCTAssertEqual(selected, "connection")
    let leaf = try XCTUnwrap(coordinator.registry["table"])
    coordinator.titleClicked(leaf)
    XCTAssertEqual(selections, 1)
  }
  @MainActor func testKeyboardSelectionStillOpensLeafOnce() {
    _ = NSApplication.shared
    var selections: [String] = []
    let view = NativeOutline(
      nodes: [SidebarNode(id: "leaf", label: "Table", kind: "table")], selectedID: .constant(nil),
      onSelect: { selections.append($0.id) }, revision: 1)
    let coordinator = view.makeCoordinator()
    let outline = SidebarOutlineView()
    outline.dataSource = coordinator
    outline.delegate = coordinator
    coordinator.outline = outline
    coordinator.refresh(view)
    outline.selectRowIndexes(IndexSet(integer: 0), byExtendingSelection: false)
    XCTAssertEqual(selections, ["leaf"])
  }
  @MainActor func testStableRevisionSkipsLargeTreeReconciliationAndNextRevisionAppliesMutation()
    throws
  {
    _ = NSApplication.shared
    let children = (0..<10_000).map {
      SidebarNode(id: "key-\($0)", label: "Key \($0)", kind: "redis")
    }
    let root = SidebarNode(id: "db", label: "DB0", kind: "database", children: children)
    var selected: String?
    let selection = Binding<String?>(get: { selected }, set: { selected = $0 })
    let initial = NativeOutline(
      nodes: [root], selectedID: selection, onSelect: { _ in }, revision: 1)
    let coordinator = initial.makeCoordinator()
    let outline = SidebarOutlineView()
    outline.dataSource = coordinator
    outline.delegate = coordinator
    coordinator.outline = outline
    coordinator.refresh(initial)
    let stable = try XCTUnwrap(coordinator.registry["key-9999"])
    children[9999].label = "Changed"
    selected = "key-9999"
    coordinator.refresh(initial)
    XCTAssertEqual(coordinator.reconciliationPasses, 1)
    XCTAssertTrue(coordinator.registry["key-9999"] === stable)
    XCTAssertEqual(stable.label, "Key 9999")
    let updated = NativeOutline(
      nodes: [root], selectedID: selection, onSelect: { _ in }, revision: 2)
    coordinator.refresh(updated)
    XCTAssertEqual(coordinator.reconciliationPasses, 2)
    XCTAssertEqual(stable.label, "Changed")
  }
}
