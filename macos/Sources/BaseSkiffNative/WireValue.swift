import Foundation

/// JSON values retain tagged Python values as objects, never coercing Decimal or bytes.
enum WireValue: Codable, Equatable, Sendable {
  case null
  case bool(Bool)
  case int(Int64)
  case double(Double)
  case string(String)
  case array([WireValue])
  case object([String: WireValue])

  init(from decoder: Decoder) throws {
    let value = try decoder.singleValueContainer()
    if value.decodeNil() {
      self = .null
    } else if let item = try? value.decode(Bool.self) {
      self = .bool(item)
    } else if let item = try? value.decode(Int64.self) {
      self = .int(item)
    } else if let item = try? value.decode(Double.self) {
      self = .double(item)
    } else if let item = try? value.decode(String.self) {
      self = .string(item)
    } else if let item = try? value.decode([WireValue].self) {
      self = .array(item)
    } else {
      self = .object(try value.decode([String: WireValue].self))
    }
  }

  func encode(to encoder: Encoder) throws {
    var value = encoder.singleValueContainer()
    switch self {
    case .null: try value.encodeNil()
    case .bool(let item): try value.encode(item)
    case .int(let item): try value.encode(item)
    case .double(let item): try value.encode(item)
    case .string(let item): try value.encode(item)
    case .array(let item): try value.encode(item)
    case .object(let item): try value.encode(item)
    }
  }

  subscript(key: String) -> WireValue? { object?[key] }
  var string: String? {
    if case .string(let item) = self { return item }
    return nil
  }
  var array: [WireValue]? {
    if case .array(let item) = self { return item }
    return nil
  }
  var object: [String: WireValue]? {
    if case .object(let item) = self { return item }
    return nil
  }
  var integer: Int64? {
    if case .int(let item) = self { return item }
    if self["$type"]?.string == "integer", let value = self["value"]?.string { return Int64(value) }
    return nil
  }
  var bool: Bool? {
    if case .bool(let item) = self { return item }
    return nil
  }
  var displayText: String {
    switch self {
    case .null: return "NULL"
    case .string(let item): return item
    case .bool(let item): return item ? "true" : "false"
    case .int(let item): return String(item)
    case .double(let item): return String(item)
    case .object(let items) where items["$type"] != nil:
      return items["value"]?.displayText ?? jsonText
    default: return jsonText
    }
  }
  private var jsonText: String {
    let encoder = JSONEncoder()
    encoder.outputFormatting = [.sortedKeys, .withoutEscapingSlashes]
    return (try? String(data: encoder.encode(self), encoding: .utf8)) ?? ""
  }
}

extension WireValue {
  /// User field names can legally contain the protocol marker; escape them on writes.
  static func userObject(_ fields: [String: WireValue]) -> WireValue {
    if fields["$type"] != nil {
      return .object([
        "$type": .string("map"),
        "value": .array(
          fields.sorted(by: { $0.key < $1.key }).map { .array([.string($0.key), $0.value]) }),
      ])
    }
    return .object(fields)
  }
  var stringKeyedFields: [String: WireValue]? {
    guard case .object(let object) = self else { return nil }
    guard object["$type"]?.string == "map", let pairs = object["value"]?.array else {
      return object
    }
    var result: [String: WireValue] = [:]
    for pair in pairs {
      guard let values = pair.array, values.count == 2, let key = values[0].string,
        result[key] == nil
      else { return nil }
      result[key] = values[1]
    }
    return result
  }
}

func keyDisplayLabel(_ value: WireValue) -> String {
  if value["$type"]?.string == "bytes", let encoded = value["value"]?.string {
    return "⟨binary · \(Data(base64Encoded:encoded)?.count ?? 0) bytes · \(encoded.prefix(36))⟩"
  }
  return String(
    value.displayText.unicodeScalars.prefix(4096).map { scalar in
      if scalar.value < 32 || scalar.value == 127 { return String(format: "\\u%04x", scalar.value) }
      return String(scalar)
    }.joined())
}
