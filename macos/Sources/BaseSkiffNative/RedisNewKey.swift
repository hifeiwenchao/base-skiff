import SwiftUI

struct RedisDraftRow: Identifiable {
  let id = UUID()
  var field = ""
  var value = ""
  var score = "0"
}

/// Keeps every user value as literal text; only sorted-set scores and TTL are numeric.
func newRedisKeyValues(
  key: String, kind: String, string: String, rows: [RedisDraftRow], ttl: String
) throws -> WireValue {
  func invalid(_ message: String) -> NSError {
    NSError(domain: "BaseSkiff.RedisKey", code: 1, userInfo: [NSLocalizedDescriptionKey: message])
  }
  guard !key.isEmpty else { throw invalid("Enter a key name.") }
  let ttlText = ttl.trimmingCharacters(in: .whitespacesAndNewlines)
  let expiry: WireValue
  if ttlText.isEmpty {
    expiry = .null
  } else {
    guard ttlText.allSatisfy({ $0.isASCII && $0.isNumber }), let seconds = Int64(ttlText),
      (1...2_147_483_647).contains(seconds)
    else { throw invalid("TTL must be an integer from 1 to 2,147,483,647 seconds.") }
    expiry = .int(seconds)
  }
  let value: WireValue
  switch kind {
  case "string": value = .string(string)
  case "hash", "zset":
    guard !rows.isEmpty, rows.count <= 1000 else {
      throw invalid("Add between 1 and 1,000 entries.")
    }
    var entries: [String: WireValue] = [:]
    for row in rows {
      let name = kind == "hash" ? row.field : row.value
      if kind == "hash", name.isEmpty { throw invalid("Hash fields cannot be empty.") }
      guard entries[name] == nil else {
        throw invalid(
          kind == "hash" ? "Hash fields must be unique." : "Sorted-set members must be unique.")
      }
      if kind == "hash" {
        entries[name] = .string(row.value)
      } else {
        guard let score = Double(row.score), score.isFinite else {
          throw invalid("Every sorted-set score must be a finite number.")
        }
        entries[name] = .double(score)
      }
    }
    value = .userObject(entries)
  case "list", "set":
    guard !rows.isEmpty, rows.count <= 1000 else {
      throw invalid("Add between 1 and 1,000 entries.")
    }
    value = .array(rows.map { .string($0.value) })
  default: throw invalid("Choose a supported Redis value type.")
  }
  return .object(["key": .string(key), "type": .string(kind), "value": value, "ttl": expiry])
}

struct RedisNewKey: View {
  @ObservedObject var store: WorkbenchStore
  var target: TreeTarget
  var onCreated: () -> Void
  @Environment(\.dismiss) private var dismiss
  @State private var key = ""
  @State private var kind = "string"
  @State private var ttl = ""
  @State private var stringValue = ""
  @State private var entries: [String: [RedisDraftRow]] = [
    "hash": [RedisDraftRow()], "list": [RedisDraftRow()], "set": [RedisDraftRow()],
    "zset": [RedisDraftRow()],
  ]
  @State private var busy = false
  @State private var error = ""
  private func t(_ zh: String, _ en: String) -> String { store.t(zh, en) }
  private var rows: Binding<[RedisDraftRow]> {
    Binding(get: { entries[kind] ?? [] }, set: { entries[kind] = $0 })
  }
  var body: some View {
    VStack(alignment: .leading, spacing: 16) {
      HStack {
        VStack(alignment: .leading, spacing: 5) {
          Text(t("新建 Redis 键", "New Redis key")).font(.title2.weight(.semibold))
          Text(text(target.connection["name"]) + " · DB" + target.database).foregroundStyle(
            .secondary)
        }
        Spacer()
        Image(systemName: "key").font(.title).foregroundStyle(
          Color(red: 0.36, green: 0.49, blue: 0.39))
      }
      Divider()
      Grid(alignment: .leading, horizontalSpacing: 18, verticalSpacing: 12) {
        GridRow {
          Text(t("键名", "Key"))
          TextField(t("保留空格和大小写", "Spaces and case are preserved"), text: $key)
        }
        GridRow {
          Text(t("类型", "Type"))
          Picker("", selection: $kind) {
            Text("String").tag("string")
            Text("Hash").tag("hash")
            Text("List").tag("list")
            Text("Set").tag("set")
            Text("ZSet").tag("zset")
          }.labelsHidden().pickerStyle(.segmented)
        }
        GridRow {
          Text("TTL")
          TextField(t("留空表示不过期（秒）", "Leave empty for no expiry (seconds)"), text: $ttl)
        }
      }.textFieldStyle(.roundedBorder)
      if kind == "string" {
        Text(t("原始文本", "Raw text")).font(.headline)
        TextEditor(text: $stringValue).font(.system(.body, design: .monospaced)).padding(6)
          .background(.background).clipShape(RoundedRectangle(cornerRadius: 6)).overlay(
            RoundedRectangle(cornerRadius: 6).stroke(.quaternary))
      } else {
        HStack {
          Text(
            kind == "hash"
              ? t("字段和值", "Fields and values")
              : kind == "zset" ? t("成员和分数", "Members and scores") : t("成员", "Members")
          ).font(.headline)
          Spacer()
          Text("\(rows.wrappedValue.count) / 1,000").font(.caption).foregroundStyle(.secondary)
        }
        ScrollView {
          LazyVStack(spacing: 9) {
            ForEach(rows) { $entry in
              HStack(spacing: 9) {
                if kind == "hash" { TextField(t("字段", "Field"), text: $entry.field) }
                TextField(kind == "hash" ? t("值", "Value") : t("成员", "Member"), text: $entry.value)
                if kind == "zset" {
                  TextField(t("分数", "Score"), text: $entry.score).frame(width: 140)
                }
                Button {
                  rows.wrappedValue.removeAll { $0.id == entry.id }
                } label: {
                  Image(systemName: "minus.circle")
                }.buttonStyle(.borderless).accessibilityLabel(t("删除此行", "Remove row"))
              }.textFieldStyle(.roundedBorder)
            }
          }.padding(2)
        }
        Button {
          rows.wrappedValue.append(RedisDraftRow())
        } label: {
          Label(t("添加一行", "Add row"), systemImage: "plus")
        }.disabled(rows.wrappedValue.count >= 1000)
      }
      Text(
        t(
          "文本保持原样，数字和 JSON 文本不会自动转换。",
          "Text is preserved literally; numeric and JSON text is not converted automatically.")
      ).font(.caption).foregroundStyle(.secondary)
      if !error.isEmpty { Text(error).font(.callout).foregroundStyle(.red).textSelection(.enabled) }
      Divider()
      HStack {
        if busy { ProgressView().controlSize(.small) }
        Spacer()
        Button(t("取消", "Cancel")) { dismiss() }.keyboardShortcut(.cancelAction)
        Button(t("创建", "Create")) { create() }.keyboardShortcut(.defaultAction).buttonStyle(
          .borderedProminent)
      }
    }.padding(24).frame(width: 720, height: 580)
      .background(Color(red: 0.985, green: 0.98, blue: 0.96)).tint(
        Color(red: 0.36, green: 0.49, blue: 0.39)
      ).disabled(busy)
  }
  private func create() {
    guard target.connection["read_only"]?.bool != true else {
      error = t("此连接为只读。", "This connection is read-only.")
      return
    }
    do {
      let payload = try newRedisKeyValues(
        key: key, kind: kind, string: stringValue, rows: rows.wrappedValue, ttl: ttl)
      busy = true
      error = ""
      Task { @MainActor in
        defer { busy = false }
        do {
          _ = try await store.rpc.call(
            "insert_row",
            params: [
              "connection_id": target.connection["id"] ?? .null,
              "database": .string(target.database), "table": .string(key), "values": payload,
            ])
          onCreated()
          dismiss()
        } catch { self.error = error.localizedDescription }
      }
    } catch { self.error = error.localizedDescription }
  }
}
