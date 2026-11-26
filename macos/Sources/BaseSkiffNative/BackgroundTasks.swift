import SwiftUI

struct BackgroundTasks: View {
  @ObservedObject var store: WorkbenchStore
  var body: some View {
    VStack(alignment: .leading, spacing: 14) {
      Text(store.t("后台任务", "Background tasks")).font(.headline)
      if store.tabs.allSatisfy({ !$0.busy }) {
        Text(store.t("暂无运行中的任务", "No active tasks")).foregroundStyle(Color.muted)
      }
      ForEach(store.tabs) { tab in BackgroundTaskRow(store: store, tab: tab) }
    }.padding(18).frame(width: 390)
  }
}
struct BackgroundTaskRow: View {
  @ObservedObject var store: WorkbenchStore
  @ObservedObject var tab: WorkTab
  @ObservedObject var rpc: RPCClient
  init(store: WorkbenchStore, tab: WorkTab) {
    self.store = store
    self.tab = tab
    rpc = store.rpc
  }
  var body: some View {
    if tab.busy {
      VStack(alignment: .leading, spacing: 8) {
        HStack {
          ProgressView().controlSize(.small)
          Text(text(tab.connection["name"]) + " / " + tab.title).lineLimit(1)
          Spacer()
        }
        HStack {
          Text(tab.operationName).font(.system(size: 10))
          Spacer()
          if let start = tab.startedAt {
            TimelineView(.periodic(from: .now, by: 1)) { context in
              Text("\(Int(context.date.timeIntervalSince(start))) s").font(
                .system(size: 10, design: .monospaced))
            }
          }
        }
        if let id = tab.requestID, let p = rpc.progress[id] {
          Text(p.displayText).font(.system(size: 10)).foregroundStyle(Color.muted)
        }
        Button(store.t("请求中断", "Request cancellation")) { Task { await store.cancel(tab) } }
          .disabled(
            !["execute", "explain", "fetch_rows", "export_csv", "import_csv"].contains(
              tab.operationName) || tab.operationName == "execute" && !sqlKinds.contains(tab.kind))
      }.padding(10).background(Color.sidebar).clipShape(RoundedRectangle(cornerRadius: 6))
    }
  }
}
struct QueryPlanView: View {
  var plan: [WireValue]
  @State private var selected: String?
  var nodes: [SidebarNode] {
    var map: [String: SidebarNode] = [:]
    for value in plan {
      let id = text(value["id"])
      map[id] = SidebarNode(
        id: id, label: text(value["label"]),
        detail: fields(value["estimates"]).sorted(by: { $0.key < $1.key }).map {
          "\($0.key): \($0.value.displayText)"
        }.joined(separator: " · "), kind: "plan")
    }
    var roots: [SidebarNode] = []
    for value in plan {
      guard let node = map[text(value["id"])] else { continue }
      if let parent = map[text(value["parent"])], parent !== node {
        parent.children.append(node)
      } else {
        roots.append(node)
      }
    }
    return roots
  }
  var body: some View { NativeOutline(nodes: nodes, selectedID: $selected, onSelect: { _ in }) }
}
