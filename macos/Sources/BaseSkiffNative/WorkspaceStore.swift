import Foundation

struct WorkspaceToken: Equatable, Sendable {
  let id: UUID
  let lifetime: UUID
  let generation: UInt64
}

/// A lifetime changes even when the same object is closed then opened again.
@MainActor final class WorkspaceStore {
  private var workspaces: [UUID: WorkspaceToken] = [:]

  @discardableResult func open(_ id: UUID) -> WorkspaceToken {
    if let current = workspaces[id] { return current }
    let token = WorkspaceToken(id: id, lifetime: UUID(), generation: 0)
    workspaces[id] = token
    return token
  }

  @discardableResult func advance(_ id: UUID) -> WorkspaceToken {
    let previous = open(id)
    let token = WorkspaceToken(
      id: id, lifetime: previous.lifetime, generation: previous.generation &+ 1)
    workspaces[id] = token
    return token
  }

  func close(_ id: UUID) { workspaces.removeValue(forKey: id) }
  func accepts(_ token: WorkspaceToken) -> Bool { workspaces[token.id] == token }
}
