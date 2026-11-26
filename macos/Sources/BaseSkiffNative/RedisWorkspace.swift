import AppKit
import SwiftUI

struct RedisEntry: Identifiable {
  let id: Int
  let entry: WireValue
  let value: WireValue
}
struct RedisWorkspace: View {
  @ObservedObject var store: WorkbenchStore
  @ObservedObject var tab: WorkTab
  @State private var selected: Int?
  @State private var localSearch = ""
  @State private var valueSheet = false
  @State private var editing = false
  @State private var original: WireValue = .null
  @State private var entry: WireValue = .null
  @State private var entryText = ""
  @State private var draft = ""
  @State private var format = "auto"
  @State private var formatChain: WireValue = .null
  @State private var notice = ""
  @State private var ttl = ""
  @State private var permanent = false
  @State private var ttlSheet = false
  var kind: String { text(tab.redis["kind"]) }
  var entries: [RedisEntry] {
    let value = tab.redis["value"] ?? .null
    if kind == "hash" || kind == "zset" {
      if text(value["$type"]) == "map" {
        return values(value["value"]).enumerated().map { i, pair in
          let p = values(pair)
          return RedisEntry(id: i, entry: p.first ?? .null, value: p.count > 1 ? p[1] : .null)
        }
      }
      return fields(value).sorted { $0.key < $1.key }.enumerated().map { i, pair in
        RedisEntry(id: i, entry: .string(pair.key), value: pair.value)
      }
    }
    return values(value).enumerated().map { i, item in
      RedisEntry(id: i, entry: kind == "list" ? .int(Int64(i)) : item, value: item)
    }
  }
  var filtered: [RedisEntry] {
    entries.filter {
      localSearch.isEmpty || $0.entry.displayText.localizedCaseInsensitiveContains(localSearch)
        || $0.value.displayText.localizedCaseInsensitiveContains(localSearch)
    }
  }
  var selectedEntry: RedisEntry? {
    selected.flatMap { i in filtered.indices.contains(i) ? filtered[i] : nil }
  }
  var ttlLabel: String {
    let ms = number(tab.redis["ttl_ms"], -2)
    return ms == -1
      ? store.t("永久", "No expiry") : ms == -2 ? store.t("已过期", "Expired") : "\(max(0,ms)/1000) s"
  }
  var body: some View {
    VStack(spacing: 0) {
      HStack(spacing: 12) {
        Image(systemName: "key").foregroundStyle(Color.skiff)
        Text(tab.object.displayText).font(.system(size: 13, weight: .medium)).lineLimit(1)
          .truncationMode(.middle).textSelection(.enabled)
        Text(kind.uppercased()).font(.system(size: 10, weight: .semibold)).padding(5).background(
          Color.sidebar
        ).clipShape(RoundedRectangle(cornerRadius: 4))
        Spacer()
        Text("TTL · " + ttlLabel).foregroundStyle(Color.muted)
        Button(store.t("设置 TTL", "Set TTL")) {
          permanent = number(tab.redis["ttl_ms"]) == -1
          ttl = String(max(1, number(tab.redis["ttl_ms"]) / 1000))
          ttlSheet = true
        }.disabled(tab.readOnly || tab.busy)
      }.padding(18).overlay(alignment: .bottom) { Divider() }
      HStack(spacing: 8) {
        Button {
          Task { await store.load(tab) }
        } label: {
          Image(systemName: "arrow.clockwise")
        }
        Button(store.t("查看", "View")) { Task { await openValue(edit: false, add: false) } }
          .disabled(
            ["hash", "list", "set", "zset", "stream"].contains(kind) && selectedEntry == nil)
        if ["string", "hash", "list", "set", "zset"].contains(kind) {
          Button(store.t("编辑", "Edit")) { Task { await openValue(edit: true, add: false) } }
            .disabled(tab.readOnly || (kind != "string" && selectedEntry == nil))
          if kind != "string" {
            Button(store.t("添加", "Add")) { Task { await openValue(edit: true, add: true) } }
              .disabled(tab.readOnly)
          }
          Button(store.t("删除项", "Delete entry")) { deleteEntry() }.disabled(
            tab.readOnly || selectedEntry == nil || kind == "string"
              || (kind == "list"
                && (tab.redis["truncated"]?.bool == true || number(tab.redis["size"]) > 1000))
          )
        }
        if tab.busy { ProgressView().controlSize(.small) }
        Spacer()
        if ["hash", "list", "set", "zset", "stream"].contains(kind) {
          TextField(store.t("筛选已载入数据", "Filter loaded entries"), text: $localSearch).textFieldStyle(
            .roundedBorder
          ).frame(maxWidth: 230)
        }
      }.buttonStyle(SkiffButtonStyle()).padding(.horizontal, 18).frame(height: 46)
      if kind == "string" || tab.redis["value"]?.array == nil && !["hash", "zset"].contains(kind) {
        ScrollView {
          Text(tab.redis["value"]?.displayText ?? "").font(.system(size: 12, design: .monospaced))
            .textSelection(.enabled).frame(maxWidth: .infinity, alignment: .topLeading).padding(20)
        }.background(Color.sidebar.opacity(0.4))
      } else {
        NativeGrid(
          columns: kind == "set"
            ? ["member"]
            : kind == "list"
              ? ["index", "value"] : kind == "zset" ? ["member", "score"] : ["field", "value"],
          rows: filtered.map { item in
            kind == "set"
              ? ["member": item.entry]
              : kind == "list"
                ? ["index": item.entry, "value": item.value]
                : kind == "zset"
                  ? ["member": item.entry, "score": item.value]
                  : ["field": item.entry, "value": item.value]
          }, selectedRow: $selected)
      }
      HStack {
        Text("\(number(tab.redis["size"])) " + store.t("项 / 字节", "items / bytes"))
        Spacer()
        if tab.redis["truncated"]?.bool == true {
          Text(store.t("已载入 \(entries.count) 项", "\(entries.count) entries loaded"))
          if tab.redis["continuation"] != .null {
            Button(store.t("加载更多", "Load more")) { Task { await continueValue() } }.disabled(
              tab.busy)
          }
        }
      }.font(.system(size: 11)).foregroundStyle(Color.muted).padding(12).background(Color.sidebar)
    }
    .sheet(isPresented: $valueSheet) { valueEditor }
    .sheet(isPresented: $ttlSheet) { ttlEditor }
  }
  var valueEditor: some View {
    VStack(alignment: .leading, spacing: 12) {
      Text(editing ? store.t("编辑 Redis 值", "Edit Redis value") : store.t("值预览", "Value preview"))
        .font(.system(size: 22, weight: .semibold))
      if kind == "hash" || kind == "set" || kind == "zset" {
        HStack {
          Text(kind == "hash" ? store.t("字段", "Field") : store.t("成员", "Member"))
          TextField("", text: $entryText).textFieldStyle(.roundedBorder).disabled(
            original != .null || !editing)
        }
      }
      HStack {
        Text(store.t("格式", "Format"))
        Picker("", selection: $format) {
          ForEach(
            ["auto", "text", "json", "msgpack", "bson", "cbor", "gzip", "zlib", "hex", "base64"],
            id: \.self
          ) { Text($0).tag($0) }
        }.labelsHidden().frame(width: 180).disabled(editing).onChange(of: format) { _, new in
          if !editing { Task { await preview(original, format: new) } }
        }
        Spacer()
        Button(store.t("导出原始值…", "Export original…")) { exportRaw(original) }.disabled(
          original.string == nil && text(original["$type"]) != "bytes")
      }
      if !notice.isEmpty { Text(notice).foregroundStyle(Color.muted).font(.system(size: 11)) }
      if kind == "stream", let item = selectedEntry, let pair = item.value.array, pair.count > 1 {
        Menu(store.t("查看消息字段…", "Inspect message field…")) {
          ForEach(Array(fields(pair[1]).sorted(by: { $0.key < $1.key }).enumerated()), id: \.offset)
          { _, field in
            Button(field.key) {
              original = field.value
              Task { await preview(field.value) }
            }
          }
          if text(pair[1]["$type"]) == "map" {
            ForEach(Array(values(pair[1]["value"]).enumerated()), id: \.offset) { _, field in
              Button(values(field).first?.displayText ?? "field") {
                let pair = values(field)
                if pair.count > 1 {
                  original = pair[1]
                  Task { await preview(pair[1]) }
                }
              }
            }
          }
        }
      }
      TextEditor(text: $draft).font(.system(size: 12, design: .monospaced)).disabled(!editing)
        .overlay(RoundedRectangle(cornerRadius: 5).stroke(Color.line))
      Text(
        editing
          ? store.t(
            "点击保存后写入数据库；保留过期时间并检查并发修改。",
            "Save writes to Redis, preserving expiry and checking concurrent changes.")
          : store.t("解码仅用于展示，不会自动写回。", "Decoding only changes the preview.")
      ).font(.system(size: 11)).foregroundStyle(Color.muted)
      HStack {
        Button(store.t("取消", "Cancel")) { valueSheet = false }
        Spacer()
        if editing {
          Button(store.t("保存", "Save")) { Task { await saveValue() } }.buttonStyle(
            SkiffButtonStyle(primary: true))
        } else {
          Button(store.t("关闭", "Close")) { valueSheet = false }.buttonStyle(
            SkiffButtonStyle(primary: true))
        }
      }
    }.padding(24).frame(width: 720, height: 590)
  }
  var ttlEditor: some View {
    VStack(alignment: .leading, spacing: 16) {
      Text(store.t("设置 TTL", "Set TTL")).font(.title2)
      Toggle(store.t("永久保存", "No expiry"), isOn: $permanent)
      HStack {
        TextField(store.t("秒数", "Seconds"), text: $ttl).textFieldStyle(.roundedBorder).disabled(
          permanent)
        Text(store.t("秒", "seconds"))
      }
      HStack {
        Button(store.t("取消", "Cancel")) { ttlSheet = false }
        Spacer()
        Button(store.t("保存", "Save")) {
          Task {
            guard permanent || (Int(ttl) ?? 0) > 0 else {
              store.error = store.t("请输入正整数秒数。", "Enter a positive number of seconds.")
              return
            }
            var p = tab.base
            p["key"] = tab.object
            p["seconds"] = permanent ? .null : .int(Int64(ttl) ?? 0)
            p["expected_ttl_ms"] = tab.redis["ttl_ms"]
            if await store.operation(tab, method: "set_redis_ttl", params: p) != nil {
              ttlSheet = false
              await store.load(tab)
            }
          }
        }.buttonStyle(SkiffButtonStyle(primary: true))
      }
    }.padding(24).frame(width: 380)
  }
  func preview(_ value: WireValue, format requested: String = "auto") async {
    do {
      let result = try await store.rpc.call(
        "preview_value", params: ["value": value, "format": .string(requested)])
      draft = text(result["text"])
      format = text(result["format"], "text")
      formatChain = result["chain"] ?? .null
      notice = text(result["notice"])
    } catch { store.report(error) }
  }
  func openValue(edit: Bool, add: Bool) async {
    editing = edit
    if add {
      original = .null
      entry = .null
      entryText = ""
      draft = ""
      format = "text"
      formatChain = .null
      notice = ""
    } else {
      original =
        !["hash", "list", "set", "zset", "stream"].contains(kind)
        ? tab.redis["value"] ?? .null
        : kind == "zset" && !edit ? selectedEntry?.entry ?? .null : selectedEntry?.value ?? .null
      entry = selectedEntry?.entry ?? .null
      entryText = entry.displayText
      if kind == "zset" && edit {
        draft = original.displayText
        format = "text"
        formatChain = .null
        notice = ""
      } else if original.string != nil || text(original["$type"]) == "bytes" {
        await preview(original)
      } else {
        draft = pretty(original)
        format = "json"
        notice = store.t(
          "结构预览 · 选择字段可查看原值", "Structure preview · choose a field to inspect its raw value")
        editing = false
      }
      if edit
        && !redisPreviewCanEdit(
          format: format, chain: values(formatChain).map(\.displayText), notice: notice)
      {
        editing = false
        notice = store.t(
          "无法无损编辑此格式，可预览或导出。", "This format is available for preview and export only.")
      }
    }
    valueSheet = true
  }
  func bytes(_ value: WireValue) -> WireValue {
    if text(value["$type"]) == "bytes" { return value }
    return .object([
      "$type": .string("bytes"),
      "value": .string(Data(value.displayText.utf8).base64EncodedString()),
    ])
  }
  func saveValue() async {
    guard editing, !tab.busy, tab.alive, !tab.readOnly else { return }
    do {
      var payload: [String: WireValue] = [:]
      let add = original == .null
      if kind == "zset" {
        guard let score = Double(draft), score.isFinite else { throw CocoaError(.formatting) }
        payload["value"] = .double(score)
        if !add { payload["expected"] = original }
      } else {
        if !add && !["text", "auto"].contains(format) {
          let encoded = try await store.rpc.call(
            "encode_preview_value",
            params: [
              "original": original,
              "format": formatChain.array?.isEmpty == false ? formatChain : .string(format),
              "text": .string(draft),
            ])
          payload["value_bytes"] = encoded
          payload["expected_bytes"] = bytes(original)
        } else {
          payload["value"] = .string(draft)
          if !add {
            if text(original["$type"]) == "bytes" {
              payload["expected_bytes"] = original
            } else {
              payload["expected"] = original
            }
          }
        }
      }
      if kind != "string" {
        if kind == "list" {
          if !add { payload["entry"] = entry }
        } else {
          let actual = add ? WireValue.string(entryText) : entry
          payload[text(actual["$type"]) == "bytes" ? "entry_bytes" : "entry"] = actual
        }
      }
      var p = tab.base
      p["key"] = tab.object
      p["kind"] = .string(kind)
      p["action"] = .string(add ? "add" : "update")
      p["payload"] = .object(payload)
      if await store.operation(tab, method: "mutate_redis_entry", params: p) != nil {
        valueSheet = false
        await store.load(tab)
      }
    } catch { store.report(error) }
  }
  func deleteEntry() {
    guard let item = selectedEntry else { return }
    let a = NSAlert()
    a.messageText = store.t("删除选中项？", "Delete selected entry?")
    a.addButton(withTitle: store.t("删除", "Delete"))
    a.addButton(withTitle: store.t("取消", "Cancel"))
    guard a.runModal() == .alertFirstButtonReturn else { return }
    Task {
      var payload: [String: WireValue] = [
        text(item.entry["$type"]) == "bytes" ? "entry_bytes" : "entry": item.entry
      ]
      if kind != "set" {
        payload[text(item.value["$type"]) == "bytes" ? "expected_bytes" : "expected"] = item.value
      }
      if kind == "list" { payload["length"] = tab.redis["size"] }
      var p = tab.base
      p["key"] = tab.object
      p["kind"] = .string(kind)
      p["action"] = .string("delete")
      p["payload"] = .object(payload)
      if await store.operation(tab, method: "mutate_redis_entry", params: p) != nil {
        await store.load(tab)
      }
    }
  }
  func continueValue() async {
    let selectedIdentity = selectedEntry?.entry
    var p = tab.base
    p["snapshot"] = tab.redis["snapshot"]
    if let result = await store.operation(tab, method: "continue_redis_value", params: p) {
      let old = SessionPersistence.handles(in: tab.redis)
      tab.redis = result
      if let selectedIdentity {
        selected = filtered.firstIndex(where: { $0.entry == selectedIdentity })
      }
      let releasing = old.subtracting(SessionPersistence.handles(in: result))
      if !releasing.isEmpty {
        _ = try? await store.rpc.call(
          "release", params: ["handles": .array(releasing.map(WireValue.string))])
      }
    }
  }
  func exportRaw(_ value: WireValue) {
    let panel = NSSavePanel()
    panel.nameFieldStringValue = "redis-value.bin"
    if panel.runModal() == .OK, let url = panel.url {
      do {
        let data =
          text(value["$type"]) == "bytes"
          ? Data(base64Encoded: text(value["value"])) ?? Data() : Data(value.displayText.utf8)
        try data.write(to: url, options: .atomic)
      } catch { store.report(error) }
    }
  }
}

func redisPreviewCanEdit(format: String, chain: [String], notice: String) -> Bool {
  guard notice.isEmpty else { return false }
  let allowed: Set<String> = ["text", "json", "msgpack", "bson", "cbor", "gzip", "zlib"]
  return allowed.contains(format) && (chain.isEmpty || chain.allSatisfy(allowed.contains))
}
