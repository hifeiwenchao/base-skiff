import AppKit
import SwiftUI

/// Connection secrets stay in local view state until sent to the worker over its private pipe.
struct ConnectionEditor: View {
  @ObservedObject var store: WorkbenchStore
  let existing: WireValue?
  @Environment(\.dismiss) private var dismiss
  @State private var draft: [String: WireValue]
  @State private var choosingType: Bool
  @State private var busy = false
  @State private var status = ""
  @State private var failed = false
  @State private var tab = "database"
  private let kinds = ["PostgreSQL", "MySQL", "SQLite", "MongoDB", "Redis", "Oracle"]
  private static let ports = [
    "PostgreSQL": 5432, "MySQL": 3306, "MongoDB": 27017, "Redis": 6379, "Oracle": 1521,
  ]

  init(store: WorkbenchStore, existing: WireValue?) {
    self.store = store
    self.existing = existing
    let defaults: [String: WireValue] = [
      "id": .string(UUID().uuidString.replacingOccurrences(of: "-", with: "").lowercased()),
      "name": .string(""), "kind": .string("PostgreSQL"), "host": .string("localhost"),
      "port": .int(5432),
      "database": .string(""), "username": .string(""), "password": .string(""),
      "path": .string(""), "group": .string("Local"),
      "ssl": .bool(false), "read_only": .bool(false), "is_demo": .bool(false),
      "query_timeout": .double(10), "redis_separator": .string(":"),
      "ssh_enabled": .bool(false), "ssh_host": .string(""), "ssh_port": .int(22),
      "ssh_username": .string(""),
      "ssh_auth_method": .string("password"), "ssh_password": .string(""),
      "ssh_private_key": .string(""),
      "ssh_passphrase": .string(""), "ssh_host_key": .string(""), "ssh_known_hosts": .string(""),
    ]
    var values = defaults.merging(existing?.object ?? [:]) { _, saved in saved }
    // Public metadata never provides secrets; do not accidentally display or echo a secret.
    for key in ["password", "ssh_password", "ssh_passphrase"] { values[key] = .string("") }
    if let kind = values["kind"]?.string, values["port"]?.integer == 0, let port = Self.ports[kind]
    {
      values["port"] = .int(Int64(port))
    }
    _draft = State(initialValue: values)
    _choosingType = State(initialValue: existing == nil)
  }
  private var kind: String { draft["kind"]?.string ?? "PostgreSQL" }
  private func t(_ zh: String, _ en: String) -> String { store.t(zh, en) }
  private func binding(_ key: String) -> Binding<String> {
    Binding(
      get: { draft[key]?.displayText ?? "" },
      set: {
        draft[key] = .string($0)
        status = ""
      })
  }
  private func flag(_ key: String) -> Binding<Bool> {
    Binding(
      get: { draft[key]?.bool ?? false },
      set: {
        draft[key] = .bool($0)
        status = ""
      })
  }
  var body: some View {
    VStack(spacing: 0) {
      HStack {
        VStack(alignment: .leading, spacing: 5) {
          Text(
            choosingType
              ? t("选择数据库类型", "Choose database type")
              : existing == nil ? t("新建连接", "New connection") : t("编辑连接", "Edit connection")
          ).font(.title2.weight(.semibold))
          Text(choosingType ? t("选择要连接的数据库", "Select the database you want to connect to") : kind)
            .foregroundStyle(.secondary)
        }
        Spacer()
        Image(systemName: choosingType ? "externaldrive.connected.to.line.below" : "network").font(
          .system(size: 27)
        ).foregroundStyle(Color(red: 0.36, green: 0.49, blue: 0.39))
      }.padding(24)
      Divider()
      if choosingType {
        typePicker
      } else {
        TabView(selection: $tab) {
          databaseForm.tabItem { Label(t("数据库", "Database"), systemImage: "externaldrive") }.tag(
            "database")
          if kind != "SQLite" {
            sshForm.tabItem { Label(t("SSH 隧道", "SSH tunnel"), systemImage: "lock.shield") }.tag(
              "ssh")
          }
        }.padding(.horizontal, 18).padding(.top, 12).disabled(busy)
      }
      if !status.isEmpty {
        HStack(alignment: .top, spacing: 8) {
          Image(systemName: failed ? "exclamationmark.circle" : "checkmark.circle")
          Text(status).textSelection(.enabled).frame(maxWidth: .infinity, alignment: .leading)
        }.font(.callout).foregroundStyle(
          failed ? Color.red : Color(red: 0.27, green: 0.43, blue: 0.31)
        )
        .padding(12).background(.background.opacity(0.7)).padding(.horizontal, 24).padding(
          .vertical, 8)
      }
      Divider()
      HStack {
        if !choosingType {
          Button(t("测试连接", "Test connection")) { perform(save: false) }.disabled(busy)
          if busy { ProgressView().controlSize(.small).padding(.leading, 4) }
        }
        Spacer()
        Button(t("取消", "Cancel")) { dismiss() }.keyboardShortcut(.cancelAction).disabled(busy)
        if !choosingType {
          Button(t("保存", "Save")) { perform(save: true) }.keyboardShortcut(.defaultAction)
            .buttonStyle(.borderedProminent).disabled(busy)
        }
      }.padding(20)
    }
    .frame(width: 800, height: 650)
    .background(Color(red: 0.985, green: 0.98, blue: 0.96))
    .tint(Color(red: 0.36, green: 0.49, blue: 0.39))
  }
  private var typePicker: some View {
    LazyVGrid(columns: [GridItem(.flexible()), GridItem(.flexible())], spacing: 16) {
      ForEach(kinds, id: \.self) { value in
        Button {
          draft["kind"] = .string(value)
          draft["port"] = .int(Int64(Self.ports[value] ?? 0))
          if value == "Redis" { draft["database"] = .string("0") }
          choosingType = false
        } label: {
          HStack(spacing: 14) {
            Image(
              systemName: value == "SQLite"
                ? "doc.zipper" : value == "Redis" ? "key" : "externaldrive"
            ).font(.title2).frame(width: 30)
            Text(value).font(.headline)
            Spacer()
            Image(systemName: "chevron.right").foregroundStyle(.secondary)
          }.padding(20).frame(maxWidth: .infinity, minHeight: 57)
        }.buttonStyle(.bordered).accessibilityLabel(value)
      }
    }.padding(30).frame(maxHeight: .infinity)
  }
  private var databaseForm: some View {
    Form {
      Section {
        LabeledContent(t("数据库类型", "Database type")) {
          Text(kind).frame(maxWidth: .infinity, alignment: .leading)
        }
        TextField(t("连接名称", "Connection name"), text: binding("name"))
        TextField(t("分组", "Group"), text: binding("group"))
      }
      Section {
        if kind == "SQLite" {
          fileField(t("数据库文件", "Database file"), key: "path", canCreate: true)
        } else {
          TextField(t("主机", "Host"), text: binding("host"))
          numericField(t("端口", "Port"), key: "port")
          if kind != "Redis" {
            TextField(
              kind == "Oracle" ? t("服务名", "Service name") : t("数据库", "Database"),
              text: binding("database"))
          }
          TextField(
            kind == "Redis" ? t("用户名（可选）", "Username (optional)") : t("用户名", "Username"),
            text: binding("username"))
          SecureField(t("密码", "Password"), text: binding("password"))
          if existing != nil {
            Text(t("密码留空将保留已保存的密码。", "Leave the password blank to keep the saved password.")).font(
              .caption
            ).foregroundStyle(.secondary)
          }
          if kind == "Redis" {
            TextField(t("键分隔符", "Key separator"), text: binding("redis_separator"))
            Text(
              t(
                "默认使用 : 分组；留空平铺显示。数据库在侧栏选择。",
                "Use : to group keys, or leave empty for a flat list. Select the database in the sidebar."
              )
            ).font(.caption).foregroundStyle(.secondary)
          }
          Toggle(t("启用 TLS", "Enable TLS"), isOn: flag("ssl"))
          if draft["ssl"]?.bool == true && kind == "PostgreSQL" {
            Text(
              t(
                "PostgreSQL 要求加密传输，但不验证服务端证书身份。",
                "PostgreSQL requires encryption but does not verify the server certificate identity."
              )
            ).font(.caption).foregroundStyle(.secondary)
          }
        }
      }
      Section {
        TextField(t("查询超时（秒）", "Query timeout (seconds)"), text: binding("query_timeout"))
        Toggle(t("只读连接", "Read-only connection"), isOn: flag("read_only"))
      }
    }.formStyle(.grouped).scrollContentBackground(.hidden)
  }
  private var sshForm: some View {
    Form {
      Section {
        Toggle(t("启用 SSH 隧道", "Enable SSH tunnel"), isOn: flag("ssh_enabled"))
        Text(
          t(
            "数据库主机和端口由 SSH 服务器访问。本地转发端口自动分配。",
            "The SSH server reaches the database host and port. A local forwarding port is allocated automatically."
          )
        ).font(.caption).foregroundStyle(.secondary)
      }
      Section {
        TextField(t("SSH 主机", "SSH host"), text: binding("ssh_host"))
        numericField(t("SSH 端口", "SSH port"), key: "ssh_port")
        TextField(t("SSH 用户名", "SSH username"), text: binding("ssh_username"))
        Picker(t("认证方式", "Authentication"), selection: binding("ssh_auth_method")) {
          Text(t("密码", "Password")).tag("password")
          Text(t("私钥文件", "Private key file")).tag("key")
          Text("SSH Agent").tag("agent")
        }
        if draft["ssh_auth_method"]?.string == "password" {
          SecureField(t("SSH 密码", "SSH password"), text: binding("ssh_password"))
        } else if draft["ssh_auth_method"]?.string == "key" {
          fileField(t("私钥文件", "Private key file"), key: "ssh_private_key")
          SecureField(t("私钥口令", "Key passphrase"), text: binding("ssh_passphrase"))
        } else {
          Text(
            t(
              "使用当前进程可访问的 SSH Agent，不自动读取默认私钥。",
              "Uses the SSH Agent available to this process without trying default private key files."
            )
          ).font(.caption).foregroundStyle(.secondary)
        }
        if existing != nil {
          Text(
            t(
              "密码与私钥口令留空将保留本机已保存的值。", "Blank passwords and key passphrases keep their saved values."
            )
          ).font(.caption).foregroundStyle(.secondary)
        }
      }.disabled(draft["ssh_enabled"]?.bool != true)
      Section(t("服务器身份验证", "Server identity")) {
        TextField(
          t("SHA256 主机指纹", "SHA256 host fingerprint"), text: binding("ssh_host_key"),
          prompt: Text("SHA256:…"))
        fileField("known_hosts", key: "ssh_known_hosts")
        Text(
          t(
            "留空使用 ~/.ssh/known_hosts。填写指纹时以指纹为准；未知或不匹配的主机将被拒绝。",
            "Uses ~/.ssh/known_hosts when empty. A supplied fingerprint takes precedence; unknown or mismatched hosts are rejected."
          )
        ).font(.caption).foregroundStyle(.secondary)
      }.disabled(draft["ssh_enabled"]?.bool != true)
    }.formStyle(.grouped).scrollContentBackground(.hidden)
  }
  private func numericField(_ title: String, key: String) -> some View {
    TextField(
      title,
      text: Binding(
        get: { binding(key).wrappedValue },
        set: { value in
          binding(key).wrappedValue = String(value.filter { $0.isASCII && $0.isNumber })
        }))
  }
  private func fileField(_ title: String, key: String, canCreate: Bool = false) -> some View {
    LabeledContent(title) {
      HStack {
        TextField("", text: binding(key)).accessibilityLabel(title)
        Button(t("浏览…", "Browse…")) { chooseFile(key: key, create: false) }
        if canCreate { Button(t("新建…", "Create…")) { chooseFile(key: key, create: true) } }
      }
    }
  }
  private func chooseFile(key: String, create: Bool) {
    if create {
      let panel = NSSavePanel()
      panel.nameFieldStringValue = "database.sqlite"
      panel.canCreateDirectories = true
      if panel.runModal() == .OK, let url = panel.url { draft[key] = .string(url.path) }
    } else {
      let panel = NSOpenPanel()
      panel.canChooseDirectories = false
      panel.canChooseFiles = true
      panel.allowsMultipleSelection = false
      panel.showsHiddenFiles = true
      if panel.runModal() == .OK, let url = panel.url { draft[key] = .string(url.path) }
    }
  }
  private func validatedConnection() throws -> WireValue {
    func reject(_ zh: String, _ en: String) -> NSError {
      NSError(
        domain: "BaseSkiff.Connection", code: 1, userInfo: [NSLocalizedDescriptionKey: t(zh, en)])
    }
    var value = draft
    for key in [
      "name", "host", "username", "group", "database", "ssh_host", "ssh_username", "ssh_host_key",
      "ssh_known_hosts", "ssh_private_key", "path",
    ] {
      value[key] = .string(
        (value[key]?.string ?? "").trimmingCharacters(in: .whitespacesAndNewlines))
    }
    guard !(value["name"]?.string ?? "").isEmpty else {
      throw reject("请填写连接名称。", "Enter a connection name.")
    }
    if value["group"]?.string == "" { value["group"] = .string("Local") }
    if kind == "SQLite" {
      guard let path = value["path"]?.string, !path.isEmpty else {
        throw reject("请选择 SQLite 数据库文件。", "Choose a SQLite database file.")
      }
      let expanded = (path as NSString).expandingTildeInPath
      if expanded != ":memory:" {
        var directory: ObjCBool = false
        if FileManager.default.fileExists(atPath: expanded, isDirectory: &directory),
          directory.boolValue
        {
          throw reject("请选择文件，而非目录。", "Choose a file, not a directory.")
        }
        guard
          FileManager.default.fileExists(
            atPath: (expanded as NSString).deletingLastPathComponent, isDirectory: &directory),
          directory.boolValue
        else {
          throw reject("数据库文件的父目录不存在。", "The database file's parent directory does not exist.")
        }
      }
      value["path"] = .string(expanded)
      value["port"] = .int(0)
      value["ssl"] = .bool(false)
      value["ssh_enabled"] = .bool(false)
    } else {
      guard !(value["host"]?.string ?? "").isEmpty else {
        throw reject("请填写数据库主机。", "Enter the database host.")
      }
      guard let port = Int(draft["port"]?.displayText ?? ""), (1...65535).contains(port) else {
        throw reject("数据库端口必须在 1–65535 之间。", "The database port must be between 1 and 65535.")
      }
      value["port"] = .int(Int64(port))
      if kind == "Oracle", value["database"]?.string == "" {
        throw reject("请填写 Oracle 服务名。", "Enter the Oracle service name.")
      }
    }
    guard let timeout = Double(draft["query_timeout"]?.displayText ?? ""), timeout.isFinite,
      timeout > 0
    else { throw reject("查询超时必须为正数。", "Query timeout must be a positive number.") }
    value["query_timeout"] = .double(timeout)
    guard let sshPort = Int(draft["ssh_port"]?.displayText ?? ""), (1...65535).contains(sshPort)
    else { throw reject("SSH 端口必须在 1–65535 之间。", "The SSH port must be between 1 and 65535.") }
    value["ssh_port"] = .int(Int64(sshPort))
    if value["ssh_enabled"]?.bool == true {
      guard value["ssh_host"]?.string != "", value["ssh_username"]?.string != "" else {
        throw reject("请填写 SSH 主机与用户名。", "Enter the SSH host and username.")
      }
    }
    if kind == "Redis" { value["database"] = existing?["database"] ?? .string("0") }
    return .object(value)
  }
  private func perform(save: Bool) {
    do {
      let connection = try validatedConnection()
      busy = true
      status = ""
      failed = false
      Task { @MainActor in
        defer { busy = false }
        do {
          if save {
            try await store.saveConnection(connection)
            dismiss()
          } else {
            let result = try await store.rpc.call(
              "test_connection", params: ["connection": connection])
            status = result.string ?? t("连接成功。", "Connection successful.")
          }
        } catch {
          failed = true
          status = error.localizedDescription
        }
      }
    } catch {
      failed = true
      status = error.localizedDescription
    }
  }
}
