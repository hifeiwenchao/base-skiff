import AppKit
import SwiftUI

final class SidebarNode: Identifiable {
  let id: String
  var label: String
  var detail: String
  var kind: String
  var children: [SidebarNode]
  init(
    id: String, label: String, detail: String = "", kind: String = "folder",
    children: [SidebarNode] = []
  ) {
    self.id = id
    self.label = label
    self.detail = detail
    self.kind = kind
    self.children = children
  }
}

struct NativeOutline: NSViewRepresentable {
  var nodes: [SidebarNode]
  @Binding var selectedID: String?
  var onSelect: (SidebarNode) -> Void
  var onExpand: ((SidebarNode, Bool) -> Void)? = nil
  var searchContent: ((SidebarNode) -> NSView)? = nil
  var revision: Int? = nil
  func makeCoordinator() -> Coordinator { Coordinator(self) }
  func makeNSView(context: Context) -> NSScrollView {
    let outline = SidebarOutlineView()
    let column = NSTableColumn(identifier: NSUserInterfaceItemIdentifier("sidebar"))
    outline.addTableColumn(column)
    outline.outlineTableColumn = column
    outline.headerView = nil
    outline.rowHeight = 28
    outline.indentationPerLevel = 14
    outline.style = .sourceList
    outline.autoresizesOutlineColumn = true
    outline.backgroundColor = NSColor(calibratedRed: 0.94, green: 0.95, blue: 0.92, alpha: 1)
    outline.delegate = context.coordinator
    outline.dataSource = context.coordinator
    outline.onTitleClick = { [weak coordinator = context.coordinator] node in
      coordinator?.titleClicked(node)
    }
    let scroll = NSScrollView()
    scroll.documentView = outline
    scroll.hasVerticalScroller = true
    scroll.hasHorizontalScroller = true
    scroll.autohidesScrollers = true
    context.coordinator.outline = outline
    context.coordinator.refresh(self)
    return scroll
  }
  func updateNSView(_ nsView: NSScrollView, context: Context) { context.coordinator.refresh(self) }
  final class Coordinator: NSObject, NSOutlineViewDataSource, NSOutlineViewDelegate {
    var parent: NativeOutline
    weak var outline: NSOutlineView?
    var roots: [SidebarNode] = []
    var registry: [String: SidebarNode] = [:]
    var expanded: Set<String> = []
    var applying = false
    private var searchViews: [String: NSView] = [:]
    private var renderedRevision: Int?
    private var rootIdentities: [ObjectIdentifier] = []
    private var hierarchyReady = false
    private(set) var reconciliationPasses = 0
    init(_ parent: NativeOutline) { self.parent = parent }
    func refresh(_ value: NativeOutline) {
      parent = value
      guard let outline else { return }
      let identities = value.nodes.map(ObjectIdentifier.init)
      if hierarchyReady, let revision = value.revision, revision == renderedRevision,
        identities == rootIdentities
      {
        applying = true
        synchronizeSelection()
        applying = false
        return
      }
      reconciliationPasses += 1
      renderedRevision = value.revision
      rootIdentities = identities
      hierarchyReady = true
      let origin = outline.enclosingScrollView?.contentView.bounds.origin
      var next: [String: SidebarNode] = [:]
      var childChanges: [SidebarNode] = []
      var labelChanges: [SidebarNode] = []
      func reconcile(_ input: SidebarNode) -> SidebarNode {
        let node =
          registry[input.id]
          ?? SidebarNode(id: input.id, label: input.label, detail: input.detail, kind: input.kind)
        let previousChildren = node.children.map(\.id)
        if node.label != input.label || node.detail != input.detail || node.kind != input.kind {
          labelChanges.append(node)
        }
        node.label = input.label
        node.detail = input.detail
        node.kind = input.kind
        node.children = input.children.map(reconcile)
        if previousChildren != node.children.map(\.id) { childChanges.append(node) }
        next[node.id] = node
        return node
      }
      let oldRoots = roots.map(\.id)
      let newRoots = value.nodes.map(reconcile)
      let retainedExpansion = expanded
      applying = true
      roots = newRoots
      registry = next
      searchViews = searchViews.filter { next[$0.key] != nil }
      if oldRoots != roots.map(\.id) {
        outline.reloadData()
      } else {
        for node in childChanges { outline.reloadItem(node, reloadChildren: true) }
        for node in labelChanges { outline.reloadItem(node, reloadChildren: false) }
      }
      expanded = retainedExpansion.intersection(Set(next.keys))
      // Retain explicit expansion state even when a parent acquired more children.
      func restore(_ items: [SidebarNode]) {
        for node in items where expanded.contains(node.id) {
          outline.expandItem(node)
          restore(node.children)
        }
      }
      restore(roots)
      synchronizeSelection()
      if let origin { outline.enclosingScrollView?.contentView.scroll(to: origin) }
      applying = false
    }
    private func synchronizeSelection() {
      guard let outline else { return }
      if let selectedID = parent.selectedID, let node = registry[selectedID] {
        let row = outline.row(forItem: node)
        if row >= 0 && outline.selectedRow != row {
          outline.selectRowIndexes(IndexSet(integer: row), byExtendingSelection: false)
        }
      } else if outline.selectedRow >= 0 {
        outline.deselectAll(nil)
      }
    }
    func titleClicked(_ node: SidebarNode) {
      guard !applying, node.kind != "redisSearch", let outline else { return }
      if parent.selectedID != node.id { parent.selectedID = node.id }
      if ["connection", "database", "db", "folder", "tables"].contains(node.kind),
        self.outlineView(outline, isItemExpandable: node)
      {
        if outline.isItemExpanded(node) {
          outline.collapseItem(node)
        } else {
          outline.expandItem(node)
        }
      } else {
        parent.onSelect(node)
      }
    }
    func outlineView(_ outlineView: NSOutlineView, numberOfChildrenOfItem item: Any?) -> Int {
      (item as? SidebarNode)?.children.count ?? roots.count
    }
    func outlineView(_ outlineView: NSOutlineView, child index: Int, ofItem item: Any?) -> Any {
      (item as? SidebarNode)?.children[index] ?? roots[index]
    }
    func outlineView(_ outlineView: NSOutlineView, isItemExpandable item: Any) -> Bool {
      guard let node = item as? SidebarNode else { return false }
      return !node.children.isEmpty
        || ["connection", "database", "db", "tables"].contains(node.kind)
    }
    func outlineView(_ outlineView: NSOutlineView, heightOfRowByItem item: Any) -> CGFloat {
      (item as? SidebarNode)?.kind == "redisSearch" ? 100 : 28
    }
    func outlineView(_ outlineView: NSOutlineView, shouldSelectItem item: Any) -> Bool {
      (item as? SidebarNode)?.kind != "redisSearch"
    }
    func outlineView(_ outlineView: NSOutlineView, viewFor tableColumn: NSTableColumn?, item: Any)
      -> NSView?
    {
      guard let node = item as? SidebarNode else { return nil }
      if node.kind == "redisSearch", let content = parent.searchContent {
        if let cached = searchViews[node.id] { return cached }
        let view = content(node)
        view.identifier = NSUserInterfaceItemIdentifier("redis-search-" + node.id)
        searchViews[node.id] = view
        return view
      }
      let identifier = NSUserInterfaceItemIdentifier("sidebarCell")
      let cell =
        outlineView.makeView(withIdentifier: identifier, owner: self) as? NSTableCellView
        ?? NSTableCellView()
      if cell.textField == nil {
        cell.identifier = identifier
        let icon = NSImageView()
        icon.translatesAutoresizingMaskIntoConstraints = false
        let label = NSTextField(labelWithString: "")
        label.translatesAutoresizingMaskIntoConstraints = false
        label.lineBreakMode = .byTruncatingTail
        label.font = .systemFont(ofSize: 12)
        cell.addSubview(icon)
        cell.addSubview(label)
        cell.imageView = icon
        cell.textField = label
        NSLayoutConstraint.activate([
          icon.leadingAnchor.constraint(equalTo: cell.leadingAnchor, constant: 2),
          icon.centerYAnchor.constraint(equalTo: cell.centerYAnchor),
          icon.widthAnchor.constraint(equalToConstant: 15),
          icon.heightAnchor.constraint(equalToConstant: 15),
          label.leadingAnchor.constraint(equalTo: icon.trailingAnchor, constant: 6),
          label.trailingAnchor.constraint(equalTo: cell.trailingAnchor, constant: -6),
          label.centerYAnchor.constraint(equalTo: cell.centerYAnchor),
        ])
      }
      cell.textField?.stringValue = node.label + (node.detail.isEmpty ? "" : "  " + node.detail)
      let symbols = [
        "connection": "externaldrive.connected.to.line.below", "database": "externaldrive",
        "db": "externaldrive", "table": "tablecells", "collection": "square.stack.3d.up",
        "key": "key", "redis": "key", "console": "terminal", "history": "clock", "more": "ellipsis",
      ]
      cell.imageView?.image = NSImage(
        systemSymbolName: symbols[node.kind] ?? "folder", accessibilityDescription: node.kind)
      cell.imageView?.contentTintColor = NSColor(
        calibratedRed: 0.36, green: 0.49, blue: 0.39, alpha: 1)
      cell.toolTip = node.label
      return cell
    }
    func outlineViewSelectionDidChange(_ notification: Notification) {
      guard !applying, let outline, (outline as? SidebarOutlineView)?.handlingMouseEvent != true
      else { return }
      let node = outline.item(atRow: outline.selectedRow) as? SidebarNode
      if parent.selectedID != node?.id { parent.selectedID = node?.id }
      if let node { parent.onSelect(node) }
    }
    func outlineViewItemDidExpand(_ notification: Notification) {
      guard let node = notification.userInfo?["NSObject"] as? SidebarNode else { return }
      expanded.insert(node.id)
      if !applying { parent.onExpand?(node, true) }
    }
    func outlineViewItemDidCollapse(_ notification: Notification) {
      guard let node = notification.userInfo?["NSObject"] as? SidebarNode else { return }
      expanded.remove(node.id)
      if !applying { parent.onExpand?(node, false) }
    }
  }
}

/// Mouse selection and expansion are delivered as one action; keyboard selection keeps AppKit's delegate path.
final class SidebarOutlineView: NSOutlineView {
  var onTitleClick: ((SidebarNode) -> Void)?
  private(set) var handlingMouseEvent = false
  override func mouseDown(with event: NSEvent) {
    let point = convert(event.locationInWindow, from: nil)
    let row = self.row(at: point)
    let node = row >= 0 ? item(atRow: row) as? SidebarNode : nil
    let disclosure = row >= 0 && frameOfOutlineCell(atRow: row).contains(point)
    handlingMouseEvent = true
    super.mouseDown(with: event)
    handlingMouseEvent = false
    if !disclosure, event.clickCount == 1, let node { onTitleClick?(node) }
  }
}
