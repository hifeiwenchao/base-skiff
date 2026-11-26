import Foundation

/// A bounded assembler per request. Interleaving never combines two operation results.
struct ResponseChunks {
  private struct Partial {
    var next = 0
    var data = Data()
  }
  private var partial: [String: Partial] = [:]
  private var totalBytes = 0
  mutating func accept(_ envelope: WireValue) throws -> WireValue? {
    guard let chunk = envelope["chunk"] else { return envelope }
    guard let id = envelope["id"]?.string, let index = chunk["index"]?.integer,
      let encoded = chunk["data"]?.string, let final = chunk["final"]?.bool,
      let bytes = Data(base64Encoded: encoded), bytes.count <= 524288
    else { throw RPCError.protocolError("Invalid response chunk") }
    var state = partial[id] ?? Partial()
    guard index == state.next, partial.count < 32 || partial[id] != nil,
      totalBytes + bytes.count <= 256 * 1024 * 1024
    else { throw RPCError.protocolError("Response chunks out of sequence or above memory limit") }
    state.next += 1
    state.data.append(bytes)
    totalBytes += bytes.count
    if final {
      partial.removeValue(forKey: id)
      totalBytes -= state.data.count
      let value = try JSONDecoder().decode(WireValue.self, from: state.data)
      guard value["id"]?.string == id, value["chunk"] == nil else {
        throw RPCError.protocolError("Mismatched chunk response")
      }
      return value
    }
    partial[id] = state
    return nil
  }
  mutating func discard(_ id: String) {
    if let old = partial.removeValue(forKey: id) { totalBytes -= old.data.count }
  }
}
