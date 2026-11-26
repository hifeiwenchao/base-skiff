import AppKit
import SwiftUI

extension Color {
  static let skiff = Color(red: 0.224, green: 0.482, blue: 0.376)
  static let sidebar = Color(red: 0.961, green: 0.969, blue: 0.957)
  static let ink = Color(red: 0.188, green: 0.235, blue: 0.212)
  static let muted = Color(red: 0.392, green: 0.451, blue: 0.416)
  static let line = Color(red: 0.89, green: 0.914, blue: 0.89)
}
struct SkiffButtonStyle: ButtonStyle {
  var primary = false
  func makeBody(configuration: Configuration) -> some View {
    configuration.label.font(.system(size: 12, weight: primary ? .semibold : .regular)).padding(
      .horizontal, 11
    ).padding(.vertical, 7)
      .foregroundStyle(primary ? Color.white : Color.ink)
      .background(primary ? Color.skiff.opacity(configuration.isPressed ? 0.8 : 1) : Color.white)
      .clipShape(RoundedRectangle(cornerRadius: 6)).overlay(
        RoundedRectangle(cornerRadius: 6).stroke(primary ? Color.skiff : Color.line, lineWidth: 1))
  }
}

struct WorkbenchView: View {
  @ObservedObject var store: WorkbenchStore
  @State private var connectionSheet = false
  @State private var editingConnection: WireValue?
  @State private var help = false
  @State private var tasks = false
  @State private var newRedisKey = false
  @State private var redisTarget: TreeTarget?
  @State private var dragWidth: Double?
  @AppStorage("native.sidebarWidth") private var sidebarWidth = 240.0
  @AppStorage("native.fontSize") private var fontSize = 12.0
  @AppStorage("native.rowDensity") private var density = "compact"
  var body: some View {
    GeometryReader { geometry in
      HStack(spacing: 0) {
        sidebar.frame(width: min(sidebarWidth, max(200, geometry.size.width - 645))).background(
          Color.sidebar)
        Rectangle().fill(Color.line).frame(width: 5).contentShape(Rectangle()).gesture(
          DragGesture(minimumDistance: 0).onChanged { value in
            if dragWidth == nil { dragWidth = sidebarWidth }
            sidebarWidth = max(200, min(560, (dragWidth ?? sidebarWidth) + value.translation.width))
          }.onEnded { _ in dragWidth = nil })
        VStack(spacing: 0) {
          topbar
          if !store.tabs.isEmpty { tabbar }
          if let tab = store.current {
            TabWorkspace(store: store, tab: tab).id(tab.id)
          } else {
            empty
          }
          if !store.error.isEmpty { feedback }
          footer
        }.frame(minWidth: 640).background(.white)
      }
    }
    .font(.system(size: fontSize)).foregroundStyle(Color.ink).tint(.skiff)
    .sheet(isPresented: $connectionSheet) {
      ConnectionEditor(store: store, existing: editingConnection)
    }
    .sheet(isPresented: $newRedisKey) {
      if let target = redisTarget {
        RedisNewKey(
          store: store, target: target,
          onCreated: {
            let db = text(target.connection["id"]) + ":db:" + target.database
            Task { await store.loadKeys(parentID: db, append: false) }
          })
      }
    }
    .onReceive(NotificationCenter.default.publisher(for: .newConnection)) { _ in
      editingConnection = nil
      connectionSheet = true
    }
    .alert(store.t("关于 BaseSkiff 原生版", "About BaseSkiff Native"), isPresented: $help) {
      Button("OK", role: .cancel) {}
    } message: {
      Text(
        store.t(
          "SwiftUI + AppKit 界面，复用本地 Python 数据服务。此迁移版本仍需按功能清单完成验收，现有版本继续保留。",
          "SwiftUI + AppKit interface with a bundled Python data service. Migration parity verification is still in progress; the existing version remains available."
        ))
    }
  }
  var sidebar: some View {
    VStack(alignment: .leading, spacing: 14) {
      HStack(spacing: 9) {
        Image(systemName: "sailboat.fill").font(.system(size: 23)).foregroundStyle(Color.skiff)
        Text("BaseSkiff").font(.system(size: 23, weight: .bold)).foregroundStyle(
          Color(red: 0.14, green: 0.31, blue: 0.25))
        Spacer()
      }.padding(.top, 18)
      Text("YOUR DATA, IN GOOD HANDS").font(.system(size: 9, weight: .semibold)).tracking(1.3)
        .foregroundStyle(Color.muted)
      TextField(store.t("搜索连接", "Search connections"), text: $store.sidebarSearch).textFieldStyle(
        .roundedBorder)
      HStack {
        Text(store.t("数据源", "CONNECTIONS")).font(.system(size: 10, weight: .semibold))
          .foregroundStyle(Color.muted)
        Spacer()
        Button {
          editingConnection = nil
          connectionSheet = true
        } label: {
          Image(systemName: "plus")
        }.buttonStyle(.plain).help(store.t("新建连接", "New connection"))
      }
      NativeOutline(
        nodes: store.nodes.filter {
          store.sidebarSearch.isEmpty
            || $0.label.localizedCaseInsensitiveContains(store.sidebarSearch)
        }, selectedID: $store.selectedNode, onSelect: store.select,
        onExpand: { node, state in Task { await store.expand(node, state) } },
        searchContent: { node in
          NSHostingView(
            rootView: EmbeddedRedisSearch(store: store, parentID: String(node.id.dropLast(7))))
        }, revision: store.treeRevision
      )
      .contextMenu {
        if let id = store.selectedNode, let target = store.targets[id] {
          if target.action == "connection" {
            Button(store.t("编辑连接…", "Edit connection…")) {
              editingConnection = target.connection
              connectionSheet = true
            }
            Button(store.t("刷新", "Refresh")) {
              store.loaded.remove(id)
              if let node = store.findNode(id) { Task { await store.expand(node, true) } }
            }
            Button(store.t("删除连接…", "Delete connection…"), role: .destructive) {
              deleteConnection(target.connection)
            }
          }
          if text(target.connection["kind"]) == "Redis" {
            Button(store.t("新建 Key…", "New key…")) {
              redisTarget = target
              newRedisKey = true
            }.disabled(target.connection["read_only"]?.bool == true)
            Button("Console") {
              Task {
                await store.open(
                  TreeTarget(
                    connection: target.connection, database: target.database, action: "console"))
              }
            }
            Button(store.t("选择其他 DB…", "Choose another DB…")) { chooseRedisDB(target.connection) }
          }
          if target.action == "table" && sqlKinds.contains(text(target.connection["kind"])) {
            Button(store.t("打开结构", "Open structure")) {
              Task {
                await store.open(target)
                store.current?.inner = "structure"
              }
            }
          }
        }
      }

      HStack(alignment: .top, spacing: 8) {
        Image(systemName: "lock.shield")
        Text(
          store.t(
            "密码加密保存在本机\n不访问系统钥匙串", "Credentials stay encrypted locally\nNo system keychain access")
        ).font(.system(size: 10)).lineSpacing(3)
      }.foregroundStyle(Color.muted).padding(.vertical, 8)
    }.padding(.horizontal, 16)
  }
  var topbar: some View {
    HStack(spacing: 12) {
      Text(
        store.current.map { text($0.connection["name"]) + "  /  " + $0.title }
          ?? store.t("工作台", "Workspace")
      ).lineLimit(1).truncationMode(.middle).foregroundStyle(Color.muted)
      Spacer()
      Menu {
        Picker(store.t("字号", "Font size"), selection: $fontSize) {
          ForEach(11...16, id: \.self) { Text("\($0)").tag(Double($0)) }
        }
        Picker(store.t("行高", "Density"), selection: $density) {
          Text(store.t("紧凑", "Compact")).tag("compact")
          Text(store.t("舒适", "Comfortable")).tag("comfortable")
        }
      } label: {
        Image(systemName: "textformat.size")
      }
      Button(store.language == "zh" ? "EN" : "中文") { store.toggleLanguage() }.buttonStyle(.plain)
      Button {
        help = true
      } label: {
        Image(systemName: "questionmark.circle")
      }.buttonStyle(.plain)
    }.padding(.horizontal, 22).frame(height: 52).overlay(alignment: .bottom) { Divider() }
  }
  var tabbar: some View {
    ScrollView(.horizontal, showsIndicators: false) {
      HStack(spacing: 3) { ForEach(store.tabs) { tab in TabButton(store: store, tab: tab) } }
        .padding(.horizontal, 12).padding(.top, 5)
    }.frame(height: 42).background(Color.sidebar).overlay(alignment: .bottom) { Divider() }
  }
  var empty: some View {
    VStack(spacing: 15) {
      Spacer()
      Image(systemName: "sailboat").font(.system(size: 58, weight: .light)).foregroundStyle(
        Color.skiff.opacity(0.65))
      Text(store.t("让数据工作更从容", "A calmer place for your data")).font(
        .system(size: 23, weight: .medium))
      Text(store.t("从左侧打开一张表、一个 Key 或 Console", "Open a table, key, or Console from the sidebar"))
        .foregroundStyle(Color.muted)
      Button(store.t("新建连接", "New connection")) {
        editingConnection = nil
        connectionSheet = true
      }.buttonStyle(SkiffButtonStyle(primary: true))
      Spacer()
    }.frame(maxWidth: .infinity)
  }
  var feedback: some View {
    HStack(alignment: .top) {
      Image(systemName: "exclamationmark.circle")
      Text(store.error).textSelection(.enabled).lineLimit(4)
      Spacer()
      Button {
        NSPasteboard.general.clearContents()
        NSPasteboard.general.setString(store.error, forType: .string)
      } label: {
        Image(systemName: "doc.on.doc")
      }
      Button {
        store.error = ""
      } label: {
        Image(systemName: "xmark")
      }
    }.font(.system(size: 11)).foregroundStyle(Color(red: 0.65, green: 0.29, blue: 0.25)).padding(12)
      .background(Color(red: 1, green: 0.96, blue: 0.94))
  }
  var footer: some View {
    HStack {
      Circle().fill(store.connected ? Color.skiff : Color.orange).frame(width: 6, height: 6)
      Text(
        store.connected
          ? store.t("本地服务已连接", "Local service connected") : store.t("正在连接服务…", "Connecting…"))
      Spacer()
      Button {
        tasks.toggle()
      } label: {
        Label(store.t("后台任务", "Tasks"), systemImage: "list.bullet.rectangle")
      }.buttonStyle(.plain).popover(isPresented: $tasks) { BackgroundTasks(store: store) }
      Text("BaseSkiff · macOS Native Preview")
    }.font(.system(size: 10)).foregroundStyle(Color.muted).padding(.horizontal, 18).frame(
      height: 28
    ).background(Color.sidebar)
  }
  func chooseRedisDB(_ connection: WireValue) {
    let alert = NSAlert()
    alert.messageText = store.t("选择 Redis 数据库", "Choose Redis database")
    let field = NSTextField(string: "")
    field.placeholderString = "DB index"
    field.frame = NSRect(x: 0, y: 0, width: 260, height: 25)
    alert.accessoryView = field
    alert.addButton(withTitle: store.t("选择", "Choose"))
    alert.addButton(withTitle: store.t("取消", "Cancel"))
    guard alert.runModal() == .alertFirstButtonReturn, let index = Int(field.stringValue),
      index >= 0
    else { return }
    Task {
      do {
        let id = try await store.chooseRedisDatabase(connection, index: index)
        store.selectedNode = id
        if let node = store.findNode(id) { await store.expand(node, true) }
      } catch { store.report(error) }
    }
  }
  func deleteConnection(_ connection: WireValue) {
    let a = NSAlert()
    a.messageText = store.t("删除连接？", "Delete connection?")
    a.informativeText = text(connection["name"])
    a.addButton(withTitle: store.t("删除", "Delete"))
    a.addButton(withTitle: store.t("取消", "Cancel"))
    if a.runModal() == .alertFirstButtonReturn {
      Task {
        do { try await store.removeConnection(text(connection["id"])) } catch {
          store.report(error)
        }
      }
    }
  }
}

struct TabButton: View {
  @ObservedObject var store: WorkbenchStore
  @ObservedObject var tab: WorkTab
  var body: some View {
    HStack(spacing: 9) {
      Image(
        systemName: tab.mode == "console" ? "terminal" : tab.mode == "redis" ? "key" : "tablecells"
      ).foregroundStyle(Color.skiff)
      Text(tab.title).lineLimit(1).frame(maxWidth: 190)
      if tab.pending > 0 { Circle().fill(Color.orange).frame(width: 5, height: 5) }
      Button {
        store.close(tab)
      } label: {
        Image(systemName: "xmark").font(.system(size: 9))
      }.buttonStyle(.plain)
    }
    .padding(.horizontal, 12).frame(height: 36).background(
      store.activeID == tab.id ? Color.white : Color.sidebar
    )
    .clipShape(UnevenRoundedRectangle(topLeadingRadius: 6, topTrailingRadius: 6)).overlay(
      alignment: .top
    ) { if store.activeID == tab.id { Rectangle().fill(Color.skiff).frame(height: 2) } }
    .contentShape(Rectangle()).onTapGesture { store.activeID = tab.id }
    .draggable(tab.id.uuidString)
    .dropDestination(for: String.self) { ids, _ in
      guard let first = ids.first, let id = UUID(uuidString: first),
        let from = store.tabs.firstIndex(where: { $0.id == id }),
        let to = store.tabs.firstIndex(where: { $0.id == tab.id })
      else { return false }
      let moving = store.tabs.remove(at: from)
      store.tabs.insert(moving, at: min(to, store.tabs.count))
      return true
    }
    .contextMenu {
      Button(store.t("关闭当前标签页", "Close tab")) { store.close(tab) }
      Button(store.t("关闭其他标签页", "Close other tabs")) {
        for other in store.tabs where other.id != tab.id { store.close(other) }
      }
      Button(store.t("关闭所有标签页", "Close all tabs")) {
        for other in store.tabs { store.close(other) }
      }
    }
  }
}
