import AppKit
import SwiftUI

@main struct BaseSkiffNativeApp: App {
  @StateObject private var store = WorkbenchStore()
  @NSApplicationDelegateAdaptor(AppDelegate.self) private var delegate
  var body: some Scene {
    WindowGroup("BaseSkiff") {
      WorkbenchView(store: store).frame(minWidth: 920, minHeight: 620)
        .preferredColorScheme(.light)
        .task {
          delegate.store = store
          NSApplication.shared.windows.first?.delegate = delegate
          let args = ProcessInfo.processInfo.arguments
          if let i = args.firstIndex(of: "--language"), args.indices.contains(i + 1),
            ["zh", "en"].contains(args[i + 1])
          {
            store.language = args[i + 1]
          }
          await store.start()
          await automation(store)
        }
    }.defaultSize(width: 1440, height: 900)
      .commands {
        CommandGroup(replacing: .newItem) {
          Button(store.t("新建连接…", "New connection…")) {
            NotificationCenter.default.post(name: .newConnection, object: nil)
          }.keyboardShortcut("n")
        }
        CommandGroup(after: .windowArrangement) {
          Button(store.t("关闭标签页", "Close tab")) { if let tab = store.current { store.close(tab) } }
            .keyboardShortcut("w")
        }
      }
  }
  @MainActor func automation(_ store: WorkbenchStore) async {
    let arguments = ProcessInfo.processInfo.arguments
    guard arguments.contains("--smoke-test") || arguments.contains("--screenshot") else { return }
    do {
      guard let connection = store.connections.first(where: { text($0["kind"]) == "SQLite" }) else {
        throw RPCError.unavailable(
          store.error.isEmpty ? "No SQLite connection returned by worker" : store.error)
      }
      if let node = store.nodes.first(where: { $0.id == text(connection["id"]) }) {
        await store.expand(node, true)
      }
      await store.open(
        TreeTarget(
          connection: connection, database: text(connection["database"]),
          object: .string("customers"), action: "table"))
      guard let tab = store.current, tab.rows.count > 0, !tab.columns.isEmpty else {
        throw NSError(
          domain: "BaseSkiff", code: 1, userInfo: [NSLocalizedDescriptionKey: store.error])
      }
      let result = try await store.rpc.call(
        "execute",
        params: [
          "connection_id": .string(text(connection["id"])),
          "query": .string("SELECT COUNT(*) AS total FROM customers"),
        ])
      guard number(values(result["rows"]).first?["total"]) == 128 else {
        throw CocoaError(.coderInvalidValue)
      }
      if let i = arguments.firstIndex(of: "--screenshot"), arguments.indices.contains(i + 1) {
        try await Task.sleep(nanoseconds: 800_000_000)
        if let window = NSApplication.shared.windows.first, let view = window.contentView,
          let bitmap = view.bitmapImageRepForCachingDisplay(in: view.bounds)
        {
          view.cacheDisplay(in: view.bounds, to: bitmap)
          if let data = bitmap.representation(using: .png, properties: [:]) {
            try data.write(to: URL(fileURLWithPath: arguments[i + 1]))
          }
        }
      }
      print("BaseSkiff native smoke passed: SwiftUI/AppKit + worker + SQLite (128 rows)")
      await store.flushForTermination()
      store.rpc.stop()
      if let temporary = delegate.temporary { try? FileManager.default.removeItem(at: temporary) }
      fflush(stdout)
      exit(0)
    } catch {
      fputs("Native smoke failed: \(error)\n", stderr)
      store.rpc.stop()
      exit(1)
    }
  }
}

extension Notification.Name {
  static let newConnection = Notification.Name("BaseSkiff.newConnection")
}
final class AppDelegate: NSObject, NSApplicationDelegate, NSWindowDelegate {
  weak var store: WorkbenchStore?
  var finishing = false
  var discardConfirmed = false
  var temporary: URL?
  func applicationWillFinishLaunching(_ notification: Notification) {
    NSApplication.shared.setActivationPolicy(.regular)
    if ProcessInfo.processInfo.arguments.contains("--smoke-test")
      || ProcessInfo.processInfo.arguments.contains("--screenshot")
    {
      let url = FileManager.default.temporaryDirectory.appendingPathComponent(
        "baseskiff-native-\(UUID().uuidString)")
      try? FileManager.default.createDirectory(at: url, withIntermediateDirectories: true)
      temporary = url
      setenv("BASESKIFF_DATA_DIR", url.path, 1)
    }
  }
  func applicationDidFinishLaunching(_ notification: Notification) {
    NSApplication.shared.activate(ignoringOtherApps: true)
  }
  @MainActor func confirmPending() -> Bool {
    if let store,
      store.tabs.contains(where: {
        $0.busy
          && ![
            "fetch_rows", "read_redis_key", "continue_redis_value", "list_history", "get_schema",
            "explain", "export_csv",
          ].contains($0.operationName)
      })
    {
      let alert = NSAlert()
      alert.messageText = store.t("仍有数据库操作正在执行", "Database operations are still running")
      alert.informativeText = store.t(
        "请先等待操作完成，或在支持中断的查询中请求中断并确认结果后再退出。",
        "Wait for completion, or request cancellation for supported queries and check the result before quitting."
      )
      alert.addButton(withTitle: store.t("返回工作台", "Return to workspace"))
      alert.runModal()
      return false
    }
    guard !discardConfirmed, let store, store.tabs.contains(where: { $0.hasPending }) else {
      return true
    }
    let alert = NSAlert()
    alert.messageText = store.t("退出并丢弃未提交的修改？", "Quit and discard pending changes?")
    alert.addButton(withTitle: store.t("丢弃并退出", "Discard and quit"))
    alert.addButton(withTitle: store.t("取消", "Cancel"))
    discardConfirmed = alert.runModal() == .alertFirstButtonReturn
    return discardConfirmed
  }
  @MainActor func windowShouldClose(_ sender: NSWindow) -> Bool { confirmPending() }
  @MainActor func applicationShouldTerminate(_ sender: NSApplication)
    -> NSApplication.TerminateReply
  {
    appDiagnostic("terminate requested")
    guard !finishing else { return .terminateNow }
    guard confirmPending() else { return .terminateCancel }
    finishing = true
    Task {
      appDiagnostic("flushing")
      await store?.flushForTermination()
      store?.rpc.stop()
      appDiagnostic("reply terminate")
      sender.reply(toApplicationShouldTerminate: true)
    }
    return .terminateLater
  }
  func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { true }
  func applicationWillTerminate(_ notification: Notification) {
    if let temporary { try? FileManager.default.removeItem(at: temporary) }
  }
}

private func appDiagnostic(_ message: String) {
  if ProcessInfo.processInfo.environment["BASESKIFF_RPC_DIAGNOSTICS"] == "1" {
    fputs(("Native lifecycle: " + message + "\n"), stderr)
  }
}
