import AppKit
import SwiftUI

struct NativeEditor: NSViewRepresentable {
  @Environment(\.isEnabled) private var isEnabled
  @Binding var text: String
  var focusToken: Int = 0
  var runToken: Int = 0
  var errorPosition: Int? = nil
  var completionWords: [String] = []
  var usesDefaultCompletions: Bool = true
  var usesCurrentStatement: Bool = true
  var onCompletionRequest: ((String, Int) -> Void)? = nil
  var onRun: (String) -> Void
  func makeCoordinator() -> Coordinator { Coordinator(self) }
  func makeNSView(context: Context) -> NSScrollView {
    let scroll = NSScrollView()
    let editor = QueryTextView(frame: .zero)
    editor.isRichText = false
    editor.isAutomaticQuoteSubstitutionEnabled = false
    editor.isAutomaticDashSubstitutionEnabled = false
    editor.isAutomaticTextReplacementEnabled = false
    editor.isAutomaticSpellingCorrectionEnabled = false
    editor.isContinuousSpellCheckingEnabled = false
    editor.font = .monospacedSystemFont(ofSize: 13, weight: .regular)
    editor.backgroundColor = NSColor(calibratedRed: 0.985, green: 0.98, blue: 0.96, alpha: 1)
    editor.textContainerInset = NSSize(width: 12, height: 12)
    editor.minSize = NSSize(width: 0, height: 0)
    editor.maxSize = NSSize(
      width: CGFloat.greatestFiniteMagnitude, height: CGFloat.greatestFiniteMagnitude)
    editor.isVerticallyResizable = true
    editor.isHorizontallyResizable = false
    editor.autoresizingMask = [.width]
    editor.textContainer?.widthTracksTextView = true
    editor.allowsUndo = true
    editor.delegate = context.coordinator
    editor.isEditable = isEnabled
    editor.run = { [weak coordinator = context.coordinator] in coordinator?.run() }
    editor.completionWords = completionWords
    editor.usesDefaultCompletions = usesDefaultCompletions
    editor.completionRequest = { [weak coordinator = context.coordinator] source, caret in
      guard let coordinator else { return }
      coordinator.awaitingCompletion = true
      coordinator.completionSource = source
      coordinator.completionCaret = caret
      coordinator.parent.onCompletionRequest?(source, caret)
    }
    scroll.documentView = editor
    scroll.hasVerticalScroller = true
    scroll.hasHorizontalScroller = false
    scroll.autohidesScrollers = true
    let ruler = LineNumberRuler(editor: editor, scroll: scroll)
    scroll.verticalRulerView = ruler
    scroll.hasVerticalRuler = true
    scroll.rulersVisible = true
    context.coordinator.editor = editor
    context.coordinator.ruler = ruler
    editor.string = text
    context.coordinator.highlight()
    context.coordinator.markError(errorPosition, scroll: errorPosition != nil)
    return scroll
  }
  func updateNSView(_ scroll: NSScrollView, context: Context) {
    let coordinator = context.coordinator
    let shouldFocus = coordinator.parent.focusToken != focusToken
    let shouldRun = coordinator.parent.runToken != runToken
    let errorChanged = coordinator.parent.errorPosition != errorPosition
    let completionsChanged = coordinator.parent.completionWords != completionWords
    coordinator.parent = self
    coordinator.editor?.completionWords = completionWords
    coordinator.editor?.usesDefaultCompletions = usesDefaultCompletions
    coordinator.editor?.isEditable = isEnabled
    if completionsChanged && coordinator.awaitingCompletion {
      coordinator.awaitingCompletion = false
      if coordinator.editor?.string == coordinator.completionSource
        && coordinator.editor?.selectedRange().location == coordinator.completionCaret
      {
        coordinator.editor?.complete(nil)
      }
    }
    if let editor = coordinator.editor, editor.string != text {
      let selection = editor.selectedRanges
      let origin = scroll.contentView.bounds.origin
      coordinator.updating = true
      editor.string = text
      coordinator.highlight()
      coordinator.clearError()
      let length = (text as NSString).length
      editor.selectedRanges = selection.map { value in
        let range = value.rangeValue
        return NSValue(
          range: NSRange(
            location: min(range.location, length),
            length: min(range.length, max(0, length - min(range.location, length)))))
      }
      scroll.contentView.scroll(to: origin)
      coordinator.updating = false
    }
    if shouldFocus { scroll.window?.makeFirstResponder(coordinator.editor) }
    if errorChanged { coordinator.markError(errorPosition, scroll: errorPosition != nil) }
    if shouldRun { DispatchQueue.main.async { coordinator.run() } }
  }
  final class Coordinator: NSObject, NSTextViewDelegate {
    var parent: NativeEditor
    weak var editor: QueryTextView?
    weak var ruler: LineNumberRuler?
    var updating = false
    var awaitingCompletion = false
    var completionSource = ""
    var completionCaret = 0
    init(_ parent: NativeEditor) { self.parent = parent }
    func textDidChange(_ notification: Notification) {
      guard !updating, let editor else { return }
      clearError()
      parent.text = editor.string
      highlight()
      ruler?.needsDisplay = true
    }
    func clearError() {
      guard let editor, let layout = editor.layoutManager else { return }
      let full = NSRange(location: 0, length: (editor.string as NSString).length)
      for attribute in [NSAttributedString.Key.backgroundColor, .underlineColor, .underlineStyle] {
        layout.removeTemporaryAttribute(attribute, forCharacterRange: full)
      }
    }
    func markError(_ position: Int?, scroll: Bool) {
      clearError()
      guard let editor, let position,
        let range = sqlErrorRange(in: editor.string, position: position)
      else { return }
      editor.layoutManager?.addTemporaryAttributes(
        [
          .backgroundColor: NSColor.systemRed.withAlphaComponent(0.2),
          .underlineColor: NSColor.systemRed, .underlineStyle: NSUnderlineStyle.single.rawValue,
        ], forCharacterRange: range)
      if scroll { editor.scrollRangeToVisible(range) }
    }
    func run() {
      guard parent.isEnabled, let editor else { return }
      let range = editor.selectedRange()
      parent.onRun(
        range.length > 0
          ? (editor.string as NSString).substring(with: range)
          : parent.usesCurrentStatement
            ? currentSQLStatement(in: editor.string, at: range.location) : editor.string)
    }
    func highlight() {
      guard let editor, let storage = editor.textStorage else { return }
      let full = NSRange(location: 0, length: storage.length)
      storage.beginEditing()
      storage.addAttributes(
        [
          .foregroundColor: NSColor.labelColor,
          .font: NSFont.monospacedSystemFont(ofSize: 13, weight: .regular),
        ], range: full)
      // Bound highlighting work for large pasted statements; the text itself remains intact.
      if full.length <= 200_000 {
        let patterns: [(String, NSColor)] = [
          (
            #"\b(SELECT|FROM|WHERE|ORDER|BY|GROUP|LIMIT|OFFSET|INSERT|INTO|VALUES|UPDATE|SET|DELETE|JOIN|LEFT|RIGHT|INNER|OUTER|ON|AS|AND|OR|NOT|NULL|IS|CREATE|TABLE|DROP|ALTER|INDEX|BEGIN|COMMIT|ROLLBACK|EXPLAIN|ASC|DESC|TRUE|FALSE)\b"#,
            NSColor(calibratedRed: 0.29, green: 0.43, blue: 0.33, alpha: 1)
          ),
          (#"\b\d+(?:\.\d+)?\b"#, .systemOrange),
          (#"'(?:''|[^'])*'|"(?:\\.|[^"\\])*""#, .systemBrown),
          (#"--[^\n]*|/\*[\s\S]*?\*/"#, .secondaryLabelColor),
        ]
        for (pattern, color) in patterns {
          guard
            let expression = try? NSRegularExpression(pattern: pattern, options: [.caseInsensitive])
          else { continue }
          for match in expression.matches(in: editor.string, range: full) {
            storage.addAttribute(.foregroundColor, value: color, range: match.range)
          }
        }
      }
      storage.endEditing()
      ruler?.needsDisplay = true
    }
  }
}

func sqlErrorRange(in text: String, position: Int) -> NSRange? {
  let text = text as NSString
  guard text.length > 0, position >= 0 else { return nil }
  let index = min(position, text.length - 1)
  return text.rangeOfComposedCharacterSequence(at: index)
}

final class QueryTextView: NSTextView {
  var run: (() -> Void)?
  var completionWords: [String] = []
  var usesDefaultCompletions = true
  var completionRequest: ((String, Int) -> Void)?
  static let sqlKeywords =
    "SELECT FROM WHERE ORDER BY GROUP LIMIT OFFSET INSERT INTO VALUES UPDATE SET DELETE JOIN LEFT RIGHT INNER OUTER ON AS AND OR NOT NULL IS CREATE TABLE DROP ALTER INDEX BEGIN COMMIT ROLLBACK EXPLAIN ASC DESC TRUE FALSE DISTINCT HAVING UNION ALL CASE WHEN THEN ELSE END WITH RETURNING COUNT SUM MIN MAX AVG"
    .components(separatedBy: " ")
  override func completions(
    forPartialWordRange charRange: NSRange, indexOfSelectedItem index: UnsafeMutablePointer<Int>
  ) -> [String]? {
    guard NSMaxRange(charRange) <= (string as NSString).length else { return nil }
    let prefix = (string as NSString).substring(with: charRange)
    index.pointee = 0
    return Array(Set((usesDefaultCompletions ? Self.sqlKeywords : []) + completionWords)).filter {
      prefix.isEmpty || $0.lowercased().hasPrefix(prefix.lowercased())
    }.sorted { $0.localizedCaseInsensitiveCompare($1) == .orderedAscending }
  }
  override func keyDown(with event: NSEvent) {
    let modifiers = event.modifierFlags.intersection(.deviceIndependentFlagsMask)
    if event.keyCode == 49 && modifiers.contains(.command) {
      requestCompletions()
      return
    }
    if (event.keyCode == 36 || event.keyCode == 76)
      && (modifiers.contains(.command) || modifiers.contains(.shift))
    {
      run?()
      return
    }
    super.keyDown(with: event)
  }
  override func performKeyEquivalent(with event: NSEvent) -> Bool {
    if event.keyCode == 49 && event.modifierFlags.contains(.command) {
      requestCompletions()
      return true
    }
    if (event.keyCode == 36 || event.keyCode == 76) && event.modifierFlags.contains(.command) {
      run?()
      return true
    }
    return super.performKeyEquivalent(with: event)
  }
  private func requestCompletions() {
    completionRequest?(string, selectedRange().location)
    complete(nil)
  }
}

/// Uses NSTextView's UTF-16 caret offsets. Delimiters inside quotes/comments never split SQL.
func currentSQLStatement(in text: String, at caret: Int) -> String {
  let units = Array(text.utf16)
  let caret = max(0, min(caret, units.count))
  var start = 0
  var index = 0
  var quote: UInt16? = nil
  var commentDepth = 0
  var lineComment = false
  var dollarQuote: [UInt16]? = nil
  var statements: [(Range<Int>, String)] = []
  func source(_ range: Range<Int>) -> String {
    String(decoding: units[range], as: UTF16.self).trimmingCharacters(in: .whitespacesAndNewlines)
  }
  while index < units.count {
    let char = units[index]
    let next = index + 1 < units.count ? units[index + 1] : 0
    if lineComment {
      if char == 10 || char == 13 { lineComment = false }
      index += 1
      continue
    }
    if commentDepth > 0 {
      if char == 47 && next == 42 {
        commentDepth += 1
        index += 2
      } else if char == 42 && next == 47 {
        commentDepth -= 1
        index += 2
      } else {
        index += 1
      }
      continue
    }
    if let tag = dollarQuote {
      if index + tag.count <= units.count && Array(units[index..<(index + tag.count)]) == tag {
        dollarQuote = nil
        index += tag.count
      } else {
        index += 1
      }
      continue
    }
    if let closing = quote {
      if char == 92 && closing != 93 {
        index = min(index + 2, units.count)
        continue
      }
      if char == closing {
        if next == closing {
          index += 2
          continue
        }
        quote = nil
      }
      index += 1
      continue
    }
    if char == 45 && next == 45 {
      lineComment = true
      index += 2
      continue
    }
    if char == 47 && next == 42 {
      commentDepth = 1
      index += 2
      continue
    }
    if char == 39 || char == 34 || char == 96 || char == 91 {
      quote = char == 91 ? 93 : char
      index += 1
      continue
    }
    if char == 36 {
      var end = index + 1
      while end < units.count
        && ((65...90).contains(units[end]) || (97...122).contains(units[end])
          || (48...57).contains(units[end]) || units[end] == 95)
      { end += 1 }
      if end < units.count && units[end] == 36 {
        dollarQuote = Array(units[index...end])
        index = end + 1
        continue
      }
    }
    if char == 59 {
      let range = start..<(index + 1)
      statements.append((range, source(start..<index)))
      start = index + 1
    }
    index += 1
  }
  statements.append((start..<units.count, source(start..<units.count)))
  if let statement = statements.first(where: {
    $0.0.contains(caret) || (caret == units.count && $0.0.upperBound == caret)
  }), !statement.1.isEmpty {
    return statement.1
  }
  return statements.last(where: { !$0.1.isEmpty && $0.0.lowerBound <= caret })?.1 ?? ""
}

final class LineNumberRuler: NSRulerView {
  weak var editor: NSTextView?
  private var observer: NSObjectProtocol?
  init(editor: NSTextView, scroll: NSScrollView) {
    self.editor = editor
    super.init(scrollView: scroll, orientation: .verticalRuler)
    clientView = editor
    ruleThickness = 44
    scroll.contentView.postsBoundsChangedNotifications = true
    observer = NotificationCenter.default.addObserver(
      forName: NSView.boundsDidChangeNotification, object: scroll.contentView, queue: .main
    ) { [weak self] _ in self?.needsDisplay = true }
  }
  required init(coder: NSCoder) { fatalError("init(coder:) has not been implemented") }
  deinit { if let observer { NotificationCenter.default.removeObserver(observer) } }
  override func drawHashMarksAndLabels(in rect: NSRect) {
    NSColor(calibratedWhite: 0.95, alpha: 1).setFill()
    rect.fill()
    guard let editor, let layout = editor.layoutManager, let container = editor.textContainer else {
      return
    }
    let visible = editor.visibleRect
    let glyphs = layout.glyphRange(forBoundingRect: visible, in: container)
    let characters = layout.characterRange(forGlyphRange: glyphs, actualGlyphRange: nil)
    let text = editor.string as NSString
    let prefix = text.substring(to: min(characters.location, text.length))
    var number = prefix.reduce(1) { $1 == "\n" ? $0 + 1 : $0 }
    var position = min(characters.location, text.length)
    if position < text.length {
      position = text.lineRange(for: NSRange(location: position, length: 0)).location
    }
    let attributes: [NSAttributedString.Key: Any] = [
      .font: NSFont.monospacedDigitSystemFont(ofSize: 11, weight: .regular),
      .foregroundColor: NSColor.secondaryLabelColor,
    ]
    while position <= min(NSMaxRange(characters), text.length) {
      let line: NSRect
      if position >= text.length {
        line = layout.extraLineFragmentRect
      } else {
        let glyph = layout.glyphIndexForCharacter(at: position)
        line = layout.lineFragmentRect(
          forGlyphAt: glyph, effectiveRange: nil, withoutAdditionalLayout: true)
      }
      let label = String(number) as NSString
      let y = line.minY + editor.textContainerOrigin.y - visible.minY
      label.draw(
        at: NSPoint(x: ruleThickness - label.size(withAttributes: attributes).width - 8, y: y),
        withAttributes: attributes)
      if position >= text.length { break }
      let next = NSMaxRange(text.lineRange(for: NSRange(location: position, length: 0)))
      if next <= position { break }
      position = next
      number += 1
    }
  }
}
