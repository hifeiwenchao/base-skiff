import Combine
import Foundation

enum RPCError: LocalizedError {
  case unavailable(String)
  case protocolError(String)
  case remote(String)
  case cancelled
  case response(WireValue)
  var errorDescription: String? {
    switch self {
    case .unavailable(let text), .protocolError(let text), .remote(let text): return text
    case .cancelled: return "Operation cancelled"
    case .response(let details):
      let message = details["message"]?.string ?? "Database operation failed"
      if let line = details["line"]?.integer {
        return message + " (line \(line), column \(details["column"]?.integer ?? 1))"
      }
      return message
    }
  }
}

/// The reader and writer run on dedicated queues; only response delivery touches MainActor.
@MainActor final class RPCClient: ObservableObject {
  @Published private(set) var isRunning = false
  @Published private(set) var progress: [String: WireValue] = [:]
  private let executable: URL?
  private let arguments: [String]
  private var process: Process?
  private var input: FileHandle?
  private var output: FileHandle?
  private var pending: [String: CheckedContinuation<WireValue, Error>] = [:]
  private var timeouts: [String: Task<Void, Never>] = [:]
  private var session = UUID()
  private let writer = DispatchQueue(label: "baseskiff.rpc.writer")
  nonisolated private static let maximumFrame = 16 * 1024 * 1024

  nonisolated init(workerExecutable: URL? = nil, arguments: [String] = []) {
    executable = workerExecutable
    self.arguments = arguments
  }

  func start() throws {
    if isRunning { return }
    let task = Process()
    let environment = ProcessInfo.processInfo.environment
    if let executable {
      task.executableURL = executable
      task.arguments = arguments
    } else if let bundled = Bundle.main.url(forAuxiliaryExecutable: "BaseSkiffWorker") {
      task.executableURL = bundled
      task.arguments = arguments
    } else if let python = environment["BASESKIFF_PYTHON"], !python.isEmpty {
      task.executableURL = URL(fileURLWithPath: python)
      task.arguments = ["-m", "baseskiff.native.worker"] + arguments
      if let source = environment["BASESKIFF_SOURCE"] {
        task.currentDirectoryURL = URL(fileURLWithPath: source)
      }
    } else {
      throw RPCError.unavailable(
        "Database worker was not found. Set BASESKIFF_PYTHON and BASESKIFF_SOURCE for development.")
    }
    let permitted = Set([
      "HOME", "PATH", "LANG", "LC_ALL", "TMPDIR", "SSH_AUTH_SOCK", "SSL_CERT_FILE", "SSL_CERT_DIR",
      "SYSTEMROOT",
    ])
    task.environment = environment.filter {
      permitted.contains($0.key) || $0.key.hasPrefix("BASESKIFF_")
    }
    if let source = environment["BASESKIFF_SOURCE"], executable == nil {
      task.environment?["PYTHONPATH"] = source
    }
    if let raw = getenv("BASESKIFF_DATA_DIR"), let data = String(validatingCString: raw),
      !data.isEmpty,
      !(task.arguments ?? []).contains("--data-dir")
    {
      task.arguments = (task.arguments ?? []) + ["--data-dir", data]
    }
    if let source = environment["BASESKIFF_SOURCE"] { task.environment?["PYTHONPATH"] = source }
    let stdin = Pipe()
    let stdout = Pipe()
    task.standardInput = stdin
    task.standardOutput = stdout
    task.standardError = FileHandle.standardError
    rpcDiagnostic("launch " + (task.executableURL?.lastPathComponent ?? "unknown"))
    try task.run()
    process = task
    input = stdin.fileHandleForWriting
    output = stdout.fileHandleForReading
    isRunning = true
    session = UUID()
    let currentSession = session
    let reader = stdout.fileHandleForReading
    DispatchQueue(label: "baseskiff.rpc.reader").async { [weak self] in
      var buffer = Data()
      var chunks = ResponseChunks()
      do {
        while true {
          let bytes = reader.availableData
          rpcDiagnostic("read \(bytes.count) bytes")
          if bytes.isEmpty { break }
          buffer.append(bytes)
          while let newline = buffer.firstIndex(of: 10) {
            let frame = Data(buffer[..<newline])
            buffer.removeSubrange(...newline)
            guard frame.count <= Self.maximumFrame else {
              throw RPCError.protocolError("Database worker response exceeds size limit")
            }
            if !frame.isEmpty {
              let envelope = try JSONDecoder().decode(WireValue.self, from: frame)
              if let value = try chunks.accept(envelope) {
                Task { @MainActor [weak self] in self?.receive(value, session: currentSession) }
              }
            }
          }
          guard buffer.count <= Self.maximumFrame else {
            throw RPCError.protocolError("Database worker response exceeds size limit")
          }
        }
        Task { @MainActor [weak self] in
          self?.ended(
            RPCError.unavailable(
              "Database worker closed its output; refresh before retrying. A write may have completed."
            ), session: currentSession)
        }
      } catch {
        Task { @MainActor [weak self] in self?.ended(error, session: currentSession) }
      }
    }
  }

  func hello() async throws -> WireValue {
    let result = try await call("hello", timeout: 45)
    guard result["version"]?.integer == 1 else {
      throw RPCError.protocolError("Incompatible database worker handshake")
    }
    return result
  }

  func call(
    _ method: String, params: [String: WireValue] = [:], requestID: String = UUID().uuidString,
    timeout: TimeInterval = 120
  ) async throws -> WireValue {
    if !isRunning { try start() }
    guard pending.count < 32 else { throw RPCError.unavailable("Database worker queue is full") }
    guard pending[requestID] == nil else { throw RPCError.protocolError("Duplicate request ID") }
    let request: WireValue = .object([
      "version": .int(1), "id": .string(requestID), "method": .string(method),
      "params": .object(params),
    ])
    var bytes = try JSONEncoder().encode(request)
    guard bytes.count <= Self.maximumFrame else {
      throw RPCError.protocolError("Request exceeds size limit")
    }
    bytes.append(10)
    let frame = bytes
    return try await withTaskCancellationHandler(
      operation: {
        try Task.checkCancellation()
        return try await withCheckedThrowingContinuation { continuation in
          pending[requestID] = continuation
          timeouts[requestID] = Task { [weak self] in
            try? await Task.sleep(nanoseconds: UInt64(max(0.01, timeout) * 1_000_000_000))
            guard !Task.isCancelled else { return }
            self?.finish(
              requestID,
              result: .failure(
                RPCError.unavailable(
                  "Database worker request timed out; verify writes before retrying.")))
          }
          let target = input
          let currentSession = session
          writer.async { [weak self] in
            do {
              try target?.write(contentsOf: frame)
              rpcDiagnostic("sent \(frame.count) bytes")
            } catch {
              Task { @MainActor [weak self] in self?.ended(error, session: currentSession) }
            }
          }
        }
      },
      onCancel: {
        Task { @MainActor [weak self] in
          self?.finish(requestID, result: .failure(CancellationError()))
          _ = try? await self?.cancel(requestID: requestID)
        }
      })
  }

  func cancel(requestID: String) async throws -> WireValue {
    try await call("cancel", params: ["request_id": .string(requestID)], timeout: 10)
  }

  func cancelAll() {
    let ids = Array(pending.keys)
    for id in ids { Task { _ = try? await cancel(requestID: id) } }
  }

  func stop() {
    let old = session
    ended(RPCError.unavailable("Database worker stopped"), session: old)
  }

  private func receive(_ value: WireValue, session: UUID) {
    rpcDiagnostic("delivering response")
    guard session == self.session else { return }
    guard value["version"]?.integer == 1, let id = value["id"]?.string else {
      ended(RPCError.protocolError("Incompatible database worker response"), session: session)
      return
    }
    if let update = value["progress"] {
      progress[id] = update
      return
    }
    if let error = value["error"], error != .null {
      finish(
        id,
        result: .failure(
          error["cancelled"]?.bool == true ? RPCError.cancelled : RPCError.response(error)))
    } else if let result = value["result"] {
      finish(id, result: .success(result))
    }
  }

  private func finish(_ id: String, result: Result<WireValue, Error>) {
    progress.removeValue(forKey: id)
    timeouts.removeValue(forKey: id)?.cancel()
    pending.removeValue(forKey: id)?.resume(with: result)
  }

  private func ended(_ error: Error, session: UUID) {
    guard session == self.session else { return }
    self.session = UUID()
    isRunning = false
    try? input?.close()
    input = nil
    if let process, process.isRunning { process.terminate() }
    process = nil
    output = nil
    for id in Array(pending.keys) { finish(id, result: .failure(error)) }
  }
}

private func rpcDiagnostic(_ message: String) {
  guard ProcessInfo.processInfo.environment["BASESKIFF_RPC_DIAGNOSTICS"] == "1" else { return }
  try? FileHandle.standardError.write(contentsOf: Data(("Native RPC: " + message + "\n").utf8))
}
