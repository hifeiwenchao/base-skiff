import AppKit
import CoreFoundation
import SwiftUI

struct CSVImportPreview: Equatable {
  let source: String
  let options: WireValue
  let result: WireValue
  var headers: [String] { values(result["columns"]).map(\.displayText) }
  var displayColumns: [String] { headers.enumerated().map { "\($0.offset + 1) · \($0.element)" } }
  var displayRows: [[String: WireValue]] {
    values(result["rows"]).map { row in
      Dictionary(uniqueKeysWithValues: zip(displayColumns, values(row)))
    }
  }
  func matches(source: String, options: WireValue) -> Bool {
    self.source == source && self.options == options
  }
  func importParameters(
    source: String, options: WireValue, mapping: [Int: String], allowedColumns: Set<String>
  ) throws -> [String: WireValue] {
    guard matches(source: source, options: options) else {
      throw csvFailure("The source or options changed. Preview the CSV again.")
    }
    let stat = values(result["stat"])
    guard stat.count == 2, stat.allSatisfy({ $0.integer != nil }) else {
      throw csvFailure("Preview has no valid file fingerprint. Preview the CSV again.")
    }
    let selected = mapping.filter { !$0.value.isEmpty }
    guard !selected.isEmpty else { throw csvFailure("Map at least one source column.") }
    guard selected.keys.allSatisfy({ headers.indices.contains($0) }),
      Set(selected.values).isSubset(of: allowedColumns)
    else { throw csvFailure("The CSV column mapping is invalid.") }
    guard Set(selected.values).count == selected.count else {
      throw csvFailure("Each target column may be mapped only once.")
    }
    let pairs = selected.sorted { $0.key < $1.key }.map {
      WireValue.array([.int(Int64($0.key)), .string($0.value)])
    }
    return [
      "source": .string(self.source), "options": self.options, "expected_stat": .array(stat),
      "mapping": .object(["$type": .string("map"), "value": .array(pairs)]),
    ]
  }
}

private func csvFailure(_ message: String) -> NSError {
  NSError(domain: "BaseSkiff.CSV", code: 1, userInfo: [NSLocalizedDescriptionKey: message])
}
func validateCSVDelimiter(_ delimiter: String) throws {
  guard delimiter.unicodeScalars.count == 1, !["\r", "\n", "\""].contains(delimiter) else {
    throw csvFailure("CSV delimiter must be a single character other than a quote or newline.")
  }
}
func displayedCSVData(
  columns: [String], rows: [[String: WireValue]], encoding: String, delimiter: String,
  nullValue: String
) throws -> Data {
  try validateCSVDelimiter(delimiter)
  func quote(_ value: String) -> String {
    "\"" + value.replacingOccurrences(of: "\"", with: "\"\"") + "\""
  }
  let lines =
    [columns.map(quote).joined(separator: delimiter)]
    + rows.map { row in
      columns.map { name in
        quote(row[name] == nil || row[name] == .null ? nullValue : row[name]!.displayText)
      }.joined(separator: delimiter)
    }
  let content = lines.joined(separator: "\r\n") + "\r\n"
  let codec: String.Encoding
  switch encoding {
  case "utf-8", "utf-8-sig": codec = .utf8
  case "utf-16": codec = .utf16
  case "gb18030":
    codec = String.Encoding(
      rawValue: CFStringConvertEncodingToNSStringEncoding(
        CFStringEncoding(CFStringEncodings.GB_18030_2000.rawValue)))
  default: throw csvFailure("Unsupported CSV encoding.")
  }
  guard var data = content.data(using: codec, allowLossyConversion: false) else {
    throw CocoaError(.fileWriteInapplicableStringEncoding)
  }
  if encoding == "utf-8-sig" { data = Data([0xef, 0xbb, 0xbf]) + data }
  return data
}

@MainActor func fullCSVExportParameters(
  tab: WorkTab, destination: String, encoding: String, delimiter: String, nullValue: String
) throws -> [String: WireValue] {
  try validateCSVDelimiter(delimiter)
  var params = tab.base
  params["destination"] = .string(destination)
  params["encoding"] = .string(encoding)
  params["delimiter"] = .string(delimiter)
  params["null_value"] = .string(nullValue)
  if tab.mode == "console" {
    guard let query = tab.lastExecutedQuery, !query.isEmpty, tab.plan.isEmpty else {
      throw csvFailure("Run a query before exporting its full result.")
    }
    params["query"] = .string(query)
  } else {
    guard tab.mode == "table" else {
      throw csvFailure("Full export requires a table or query result.")
    }
    params["table"] = tab.object
    params["search"] = .string(tab.appliedSearch)
    params["sort_column"] = .string(tab.sortColumn)
    params["descending"] = .bool(tab.descending)
    params["where_clause"] = .string(tab.appliedWhere)
    params["order_by"] = .string(tab.appliedOrder)
  }
  return params
}

struct TransferWorkspace: View {
  @ObservedObject var store: WorkbenchStore
  @ObservedObject var tab: WorkTab
  @Environment(\.dismiss) private var dismiss
  @State private var source = ""
  @State private var destination = ""
  @State private var encoding = "utf-8-sig"
  @State private var delimiter = ","
  @State private var nullValue = "\\N"
  @State private var preview: CSVImportPreview?
  @State private var mapping: [Int: String] = [:]
  @State private var busy = false
  @State private var message = ""
  var options: WireValue {
    .object([
      "encoding": .string(encoding), "delimiter": .string(delimiter),
      "null_value": .string(nullValue),
    ])
  }
  private var validPreview: Bool { preview?.matches(source: source, options: options) == true }
  var body: some View {
    VStack(alignment: .leading, spacing: 14) {
      Text(store.t("CSV 导入 / 导出", "CSV import / export")).font(.system(size: 22, weight: .semibold))
      HStack {
        Picker(store.t("编码", "Encoding"), selection: $encoding) {
          ForEach(["utf-8-sig", "utf-8", "gb18030", "utf-16"], id: \.self) { Text($0).tag($0) }
        }
        TextField(store.t("分隔符", "Delimiter"), text: $delimiter).frame(width: 60)
        TextField(store.t("NULL 标记", "NULL marker"), text: $nullValue)
      }.disabled(busy)
      GroupBox(store.t("导出", "Export")) {
        VStack(alignment: .leading, spacing: 10) {
          HStack {
            TextField(store.t("目标文件", "Destination"), text: $destination)
            Button(store.t("选择…", "Choose…")) {
              let panel = NSSavePanel()
              panel.nameFieldStringValue = "export.csv"
              if panel.runModal() == .OK { destination = panel.url?.path ?? "" }
            }
          }
          HStack {
            Button(store.t("导出当前结果", "Export displayed rows")) { exportDisplayed() }.disabled(
              destination.isEmpty)
            if sqlKinds.contains(tab.kind) {
              Button(store.t("后台导出全部", "Export all in background")) { Task { await exportAll() } }
                .disabled(destination.isEmpty)
            }
          }
        }.padding(8)
      }.disabled(busy)
      if sqlKinds.contains(tab.kind) && tab.mode == "table" && !tab.readOnly {
        GroupBox(store.t("事务导入", "Transactional import")) {
          VStack(alignment: .leading, spacing: 10) {
            HStack {
              TextField(store.t("源文件", "Source"), text: $source).onSubmit {
                Task { await loadPreview() }
              }
              Button(store.t("选择…", "Choose…")) {
                let panel = NSOpenPanel()
                if panel.runModal() == .OK {
                  source = panel.url?.path ?? ""
                  Task { await loadPreview() }
                }
              }
              Button(store.t("预览", "Preview")) { Task { await loadPreview() } }.disabled(
                source.isEmpty)
            }
            if let snapshot = preview, validPreview {
              ScrollView {
                VStack(spacing: 7) {
                  ForEach(snapshot.headers.indices, id: \.self) { index in
                    HStack {
                      Text("\(index + 1) · " + snapshot.headers[index]).lineLimit(1).frame(
                        width: 230, alignment: .leading)
                      Image(systemName: "arrow.right").foregroundStyle(Color.muted)
                      Picker(
                        "",
                        selection: Binding(
                          get: { mapping[index] ?? "" }, set: { mapping[index] = $0 })
                      ) {
                        Text(store.t("跳过", "Skip")).tag("")
                        ForEach(tab.columns, id: \.self) { Text($0).tag($0) }
                      }.labelsHidden()
                    }
                  }
                }
              }.frame(maxHeight: 105)
              NativeGrid(columns: snapshot.displayColumns, rows: snapshot.displayRows).frame(
                minHeight: 95, idealHeight: 130, maxHeight: 150)
              HStack {
                Text(
                  store.t(
                    "预览最多 20 行；列映射按序号区分同名表头。",
                    "Preview shows up to 20 rows; column positions distinguish duplicate headers.")
                ).font(.caption).foregroundStyle(Color.muted)
                Spacer()
                Button(store.t("确认映射并导入", "Confirm mapping and import")) { confirmImport() }
                  .buttonStyle(SkiffButtonStyle(primary: true))
              }
            } else {
              Text(
                store.t(
                  "导入前请预览；更改源文件、编码、分隔符或 NULL 标记后需重新预览。",
                  "Preview before importing. Changing the source, encoding, delimiter or NULL marker requires a new preview."
                )
              ).font(.caption).foregroundStyle(Color.muted)
            }
          }.padding(8)
        }.disabled(busy)
      }
      if busy {
        HStack {
          ProgressView().controlSize(.small)
          Text(store.t("后台处理中…", "Working in background…"))
          Button(store.t("中断", "Stop")) { Task { await store.cancel(tab) } }
        }
      }
      Text(message).font(.callout).foregroundStyle(Color.muted).textSelection(.enabled)
      Spacer(minLength: 0)
      HStack {
        Spacer()
        Button(store.t("关闭", "Close")) { dismiss() }.disabled(busy)
      }
    }.padding(24).frame(width: 790, height: 690).textFieldStyle(.roundedBorder)
      .onChange(of: source) { _, _ in invalidatePreview() }
      .onChange(of: encoding) { _, _ in invalidatePreview() }
      .onChange(of: delimiter) { _, _ in invalidatePreview() }
      .onChange(of: nullValue) { _, _ in invalidatePreview() }
  }
  private func invalidatePreview() {
    preview = nil
    mapping = [:]
    message = ""
  }
  private func loadPreview() async {
    guard !busy else { return }
    let frozenSource = source
    let frozenOptions = options
    invalidatePreview()
    do {
      try validateCSVDelimiter(delimiter)
      busy = true
      defer { busy = false }
      if let result = await store.operation(
        tab, method: "csv_preview",
        params: ["source": .string(frozenSource), "options": frozenOptions])
      {
        guard source == frozenSource, options == frozenOptions else { return }
        let snapshot = CSVImportPreview(
          source: frozenSource, options: frozenOptions, result: result)
        var used: Set<String> = []
        mapping = Dictionary(
          uniqueKeysWithValues: snapshot.headers.enumerated().map { index, header in
            let target = tab.columns.contains(header) && used.insert(header).inserted ? header : ""
            return (index, target)
          })
        preview = snapshot
      }
    } catch { store.report(error) }
  }
  private func exportDisplayed() {
    do {
      let data = try displayedCSVData(
        columns: tab.columns, rows: tab.rows, encoding: encoding, delimiter: delimiter,
        nullValue: nullValue)
      try data.write(to: URL(fileURLWithPath: destination), options: .atomic)
      message = store.t("导出完成", "Export complete")
    } catch { store.report(error) }
  }
  private func exportAll() async {
    guard !busy else { return }
    do { try validateCSVDelimiter(delimiter) } catch {
      store.report(error)
      return
    }
    busy = true
    defer { busy = false }
    let params: [String: WireValue]
    do {
      params = try fullCSVExportParameters(
        tab: tab, destination: destination, encoding: encoding, delimiter: delimiter,
        nullValue: nullValue)
    } catch {
      store.report(error)
      return
    }
    if await store.operation(tab, method: "export_csv", params: params) != nil {
      message = store.t("导出完成", "Export complete")
    }
  }
  private func confirmImport() {
    guard !busy, let snapshot = preview else { return }
    let params: [String: WireValue]
    do {
      params = try snapshot.importParameters(
        source: source, options: options, mapping: mapping, allowedColumns: Set(tab.columns))
    } catch {
      store.report(error)
      return
    }
    let alert = NSAlert()
    alert.messageText = store.t("确认导入到当前表？", "Import into this table?")
    alert.informativeText = store.t(
      "全部记录将在同一事务写入，失败则回滚。", "All rows are written in one transaction, rolled back on failure.")
    alert.addButton(withTitle: store.t("导入", "Import"))
    alert.addButton(withTitle: store.t("取消", "Cancel"))
    guard alert.runModal() == .alertFirstButtonReturn,
      snapshot.matches(source: source, options: options)
    else { return }
    busy = true
    Task { @MainActor in
      defer { busy = false }
      let frozen = tab.base.merging(params) { _, new in new }.merging(["table": tab.object]) {
        _, new in new
      }
      if await store.operation(tab, method: "import_csv", params: frozen) != nil {
        message = store.t("导入完成", "Import complete")
        await store.load(tab)
      }
    }
  }
}
