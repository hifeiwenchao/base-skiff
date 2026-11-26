import AppKit
import SwiftUI

/// A virtualized native grid. Row selection never rebuilds the table contents.
struct NativeGrid: NSViewRepresentable {
  var columns: [String]
  var rows: [[String: WireValue]]
  @Binding var selectedRow: Int?
  @Binding var selectedRows: Set<Int>
  private var bindsMultipleSelection: Bool
  var protectedColumns: [Int: Set<String>] = [:]
  var changedCells: [Int: Set<String>] = [:]
  var deletedRows: Set<Int> = []
  var insertedRows: Set<Int> = []
  var onSetNull: ((Int, String) -> Void)? = nil
  var onSelectionChange: ((Set<Int>) -> Void)? = nil
  var editableColumns: Set<String> = []
  var onEdit: ((Int, String, String) -> Void)? = nil
  var fontSize: Double = 12
  var rowHeight: Double = 32
  var onSortColumn: ((String) -> Void)? = nil
  var sortColumn: String? = nil
  var sortDescending = false

  init(
    columns: [String], rows: [[String: WireValue]], selectedRow: Binding<Int?> = .constant(nil),
    editableColumns: Set<String> = [], onEdit: ((Int, String, String) -> Void)? = nil,
    fontSize: Double = 12, rowHeight: Double = 32, protectedColumns: [Int: Set<String>] = [:],
    changedCells: [Int: Set<String>] = [:], deletedRows: Set<Int> = [], insertedRows: Set<Int> = [],
    onSetNull: ((Int, String) -> Void)? = nil, selectedRows: Binding<Set<Int>>? = nil,
    onSelectionChange: ((Set<Int>) -> Void)? = nil, onSortColumn: ((String) -> Void)? = nil,
    sortColumn: String? = nil, sortDescending: Bool = false
  ) {
    self.columns = columns
    self.rows = rows
    self._selectedRow = selectedRow
    self.editableColumns = editableColumns
    self.onEdit = onEdit
    self.fontSize = fontSize
    self.rowHeight = rowHeight
    self.protectedColumns = protectedColumns
    self.changedCells = changedCells
    self.deletedRows = deletedRows
    self.insertedRows = insertedRows
    self.onSetNull = onSetNull
    self._selectedRows = selectedRows ?? .constant([])
    self.bindsMultipleSelection = selectedRows != nil
    self.onSelectionChange = onSelectionChange
    self.onSortColumn = onSortColumn
    self.sortColumn = sortColumn
    self.sortDescending = sortDescending
  }
  func makeCoordinator() -> Coordinator { Coordinator(self) }
  func makeNSView(context: Context) -> NSScrollView {
    let table = CopyTableView()
    table.delegate = context.coordinator
    table.dataSource = context.coordinator
    table.menuProvider = { [weak coordinator = context.coordinator] event in
      coordinator?.contextMenu(event)
    }
    table.copyAction = { [weak coordinator = context.coordinator] in coordinator?.copySelection() }
    table.usesAlternatingRowBackgroundColors = true
    table.backgroundColor = NSColor(calibratedRed: 0.985, green: 0.98, blue: 0.96, alpha: 1)
    table.gridStyleMask = [.solidHorizontalGridLineMask]
    table.rowHeight = 27
    table.allowsMultipleSelection = true
    table.columnAutoresizingStyle = .noColumnAutoresizing
    table.autosaveTableColumns = false
    let scroll = NSScrollView()
    scroll.documentView = table
    scroll.hasVerticalScroller = true
    scroll.hasHorizontalScroller = true
    scroll.autohidesScrollers = true
    context.coordinator.table = table
    context.coordinator.refresh(self)
    return scroll
  }
  func updateNSView(_ nsView: NSScrollView, context: Context) { context.coordinator.refresh(self) }

  final class Coordinator: NSObject, NSTableViewDataSource, NSTableViewDelegate, NSTextFieldDelegate
  {
    var parent: NativeGrid
    weak var table: NSTableView?
    private var rendered: [[String]] = []
    private var applying = false
    init(_ parent: NativeGrid) { self.parent = parent }
    func refresh(_ value: NativeGrid) {
      guard let table else {
        parent = value
        return
      }
      let schemaChanged =
        parent.columns != value.columns || table.tableColumns.count != value.columns.count
      let dataChanged =
        schemaChanged || parent.rows != value.rows || rendered.count != value.rows.count
        || parent.fontSize != value.fontSize || parent.editableColumns != value.editableColumns
        || parent.protectedColumns != value.protectedColumns
        || parent.changedCells != value.changedCells || parent.deletedRows != value.deletedRows
        || parent.insertedRows != value.insertedRows
      parent = value
      table.rowHeight = CGFloat(max(20, value.rowHeight))
      if schemaChanged {
        for column in table.tableColumns { table.removeTableColumn(column) }
        for name in value.columns {
          let column = NSTableColumn(identifier: NSUserInterfaceItemIdentifier(name))
          column.title = name
          column.width = 170
          column.minWidth = 65
          table.addTableColumn(column)
        }
      }
      if dataChanged {
        let origin = table.enclosingScrollView?.contentView.bounds.origin
        rendered = value.rows.map { row in
          value.columns.map { String((row[$0]?.displayText ?? "NULL").prefix(2048)) }
        }
        applying = true
        table.reloadData()
        applying = false
        if let origin { table.enclosingScrollView?.contentView.scroll(to: origin) }
      }
      for column in table.tableColumns {
        let image =
          column.identifier.rawValue == value.sortColumn
          ? NSImage(
            systemSymbolName: value.sortDescending ? "chevron.down" : "chevron.up",
            accessibilityDescription: value.sortDescending ? "Descending" : "Ascending") : nil
        table.setIndicatorImage(image, in: column)
      }
      let selected = value.selectedRow.flatMap { value.rows.indices.contains($0) ? $0 : nil }
      let desired: IndexSet
      if value.bindsMultipleSelection {
        desired = IndexSet(value.selectedRows.filter { value.rows.indices.contains($0) })
      } else if selected != (table.selectedRow >= 0 ? table.selectedRow : nil) {
        desired = selected.map { IndexSet(integer: $0) } ?? []
      } else {
        desired = table.selectedRowIndexes
      }
      if table.selectedRowIndexes != desired {
        applying = true
        table.selectRowIndexes(desired, byExtendingSelection: false)
        applying = false
      }
    }
    func numberOfRows(in tableView: NSTableView) -> Int { parent.rows.count }
    func tableView(_ tableView: NSTableView, didClick tableColumn: NSTableColumn) {
      parent.onSortColumn?(tableColumn.identifier.rawValue)
    }
    func tableView(_ tableView: NSTableView, viewFor tableColumn: NSTableColumn?, row: Int)
      -> NSView?
    {
      guard let column = tableColumn,
        let index = parent.columns.firstIndex(of: column.identifier.rawValue), row < rendered.count
      else { return nil }
      let identifier = NSUserInterfaceItemIdentifier("cell-" + column.identifier.rawValue)
      let cell =
        tableView.makeView(withIdentifier: identifier, owner: self) as? NSTableCellView
        ?? NSTableCellView()
      if cell.textField == nil {
        let field = NSTextField()
        field.isBordered = false
        field.drawsBackground = false
        field.font = .monospacedSystemFont(ofSize: 12, weight: .regular)
        field.lineBreakMode = .byTruncatingTail
        field.translatesAutoresizingMaskIntoConstraints = false
        cell.addSubview(field)
        cell.textField = field
        cell.identifier = identifier
        NSLayoutConstraint.activate([
          field.leadingAnchor.constraint(equalTo: cell.leadingAnchor, constant: 7),
          field.trailingAnchor.constraint(equalTo: cell.trailingAnchor, constant: -7),
          field.centerYAnchor.constraint(equalTo: cell.centerYAnchor),
        ])
      }
      let field = cell.textField!
      let name = column.identifier.rawValue
      let deleted = parent.deletedRows.contains(row)
      let attributes: [NSAttributedString.Key: Any] =
        deleted ? [.strikethroughStyle: NSUnderlineStyle.single.rawValue] : [:]
      field.attributedStringValue = NSAttributedString(
        string: rendered[row][index], attributes: attributes)
      field.drawsBackground =
        deleted || parent.insertedRows.contains(row)
        || parent.changedCells[row]?.contains(name) == true
      if deleted {
        field.backgroundColor = NSColor.systemRed.withAlphaComponent(0.13)
      } else if parent.insertedRows.contains(row) {
        field.backgroundColor = NSColor.systemGreen.withAlphaComponent(0.13)
      } else if parent.changedCells[row]?.contains(name) == true {
        field.backgroundColor = NSColor.systemYellow.withAlphaComponent(0.22)
      } else {
        field.backgroundColor = .clear
      }
      field.font = .monospacedSystemFont(ofSize: CGFloat(max(9, parent.fontSize)), weight: .regular)
      field.isEditable = canEdit(row: row, column: column.identifier.rawValue)
      field.isSelectable = true
      field.delegate = self
      field.textColor =
        parent.rows[row][column.identifier.rawValue].map {
          $0.displayText == "NULL" ? .secondaryLabelColor : .labelColor
        } ?? .secondaryLabelColor
      field.toolTip = String(
        (parent.rows[row][column.identifier.rawValue]?.displayText ?? "NULL").prefix(4096))
      return cell
    }
    func canSetNull(row: Int, column: String) -> Bool {
      parent.rows.indices.contains(row) && parent.editableColumns.contains(column)
        && parent.onSetNull != nil && parent.protectedColumns[row]?.contains(column) != true
        && !parent.deletedRows.contains(row)
    }
    func canEdit(row: Int, column: String) -> Bool {
      guard parent.rows.indices.contains(row), parent.editableColumns.contains(column),
        parent.onEdit != nil, parent.protectedColumns[row]?.contains(column) != true,
        !parent.deletedRows.contains(row)
      else { return false }
      switch parent.rows[row][column] ?? .null {
      case .array: return false
      case .object(let value):
        // Preserve precision for scalar protocol types, but never edit binary/nested values as text.
        return ["integer", "decimal", "date", "datetime", "time", "uuid"].contains(
          value["$type"]?.string ?? "")
      default: return true
      }
    }
    func controlTextDidBeginEditing(_ obj: Notification) {
      guard let field = obj.object as? NSTextField, let table else { return }
      let row = table.row(for: field)
      let column = table.column(for: field)
      guard row >= 0, column >= 0, column < parent.columns.count,
        canEdit(row: row, column: parent.columns[column])
      else { return }
      let full = parent.rows[row][parent.columns[column]]?.displayText ?? "NULL"
      field.stringValue = full
      // NSTextField's shared field editor already exists when this notification arrives.
      // Install the full source there as well so editing the 2 KiB preview cannot erase its tail.
      if let editor = field.currentEditor() {
        editor.string = full
        editor.selectedRange = NSRange(location: 0, length: (full as NSString).length)
      }
    }
    func controlTextDidEndEditing(_ obj: Notification) {
      guard let field = obj.object as? NSTextField, let table else { return }
      let row = table.row(for: field)
      let column = table.column(for: field)
      guard row >= 0, column >= 0, column < parent.columns.count,
        canEdit(row: row, column: parent.columns[column])
      else { return }
      parent.onEdit?(row, parent.columns[column], field.stringValue)
    }
    func tableViewSelectionDidChange(_ notification: Notification) {
      guard !applying, let table else { return }
      let selected = table.selectedRow >= 0 ? table.selectedRow : nil
      let indexes = Set(table.selectedRowIndexes)
      if parent.selectedRow != selected { parent.selectedRow = selected }
      if parent.bindsMultipleSelection, parent.selectedRows != indexes {
        parent.selectedRows = indexes
      }
      parent.onSelectionChange?(indexes)
    }
    private var menuTarget: (Int, String)?
    func contextMenu(_ event: NSEvent) -> NSMenu? {
      guard let table else { return nil }
      let point = table.convert(event.locationInWindow, from: nil)
      let row = table.row(at: point)
      let column = table.column(at: point)
      guard parent.rows.indices.contains(row), parent.columns.indices.contains(column) else {
        return nil
      }
      let name = parent.columns[column]
      menuTarget = (row, name)
      let menu = NSMenu()
      let copy = menu.addItem(
        withTitle: "Copy field / 复制字段", action: #selector(copyField), keyEquivalent: "")
      copy.target = self
      if canSetNull(row: row, column: name) {
        let null = menu.addItem(
          withTitle: "Set NULL / 设为 NULL", action: #selector(setNull), keyEquivalent: "")
        null.target = self
      }
      return menu
    }
    @objc private func copyField() {
      guard let (row, column) = menuTarget, parent.rows.indices.contains(row) else { return }
      NSPasteboard.general.clearContents()
      NSPasteboard.general.setString(
        parent.rows[row][column]?.displayText ?? "NULL", forType: .string)
    }
    @objc private func setNull() {
      guard let (row, column) = menuTarget, canSetNull(row: row, column: column) else { return }
      parent.onSetNull?(row, column)
    }
    func copySelection() {
      guard let table else { return }
      let text = table.selectedRowIndexes.compactMap { index -> String? in
        guard index < parent.rows.count else { return nil }
        return parent.columns.map { parent.rows[index][$0]?.displayText ?? "NULL" }.joined(
          separator: "\t")
      }.joined(separator: "\n")
      NSPasteboard.general.clearContents()
      NSPasteboard.general.setString(text, forType: .string)
    }
  }
}

private final class CopyTableView: NSTableView {
  var copyAction: (() -> Void)?
  var menuProvider: ((NSEvent) -> NSMenu?)?
  override func menu(for event: NSEvent) -> NSMenu? { menuProvider?(event) }
  override func performKeyEquivalent(with event: NSEvent) -> Bool {
    if event.modifierFlags.intersection(.deviceIndependentFlagsMask) == .command
      && event.charactersIgnoringModifiers == "c"
    {
      copyAction?()
      return true
    }
    return super.performKeyEquivalent(with: event)
  }
}
