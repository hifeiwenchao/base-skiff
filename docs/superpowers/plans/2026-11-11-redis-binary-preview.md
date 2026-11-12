# Redis binary values and serialization previews

**Goal:** Open Redis values without forced UTF-8 decoding and inspect JSON,
MessagePack and arbitrary binary payloads without modifying stored bytes.

**Design:** Keep valid UTF-8 responses as strings and undecodable responses as
bytes. Retain raw keys/fields/members as identities throughout transport, Qt item
data and mutations. Separate display formatting from these identities. Native
String/Hash/List/Set/ZSet/Stream views remain; unsupported module types use a
read-only DUMP fallback, with JSON.GET for RedisJSON types. Decoding a preview
never supplies replacement values to existing editors.

**Constraints:** Python >=3.11 and PySide6; Chinese/English; local encrypted
credentials only, no keyring. Keep the 500-key catalog cap, 1,000-member previews,
DB snapshots, optimistic concurrency and expiration preservation. Unknown
serialization has Hex/Base64 and byte export, without executing object loaders.

- [x] Adapter: reproduce byte-response failures; use binary transport and
      recursive lossless normalization for catalog/value/query/transaction paths.
      Verify all native types, mixed binary identities and direct/SSH routes.
- [x] Codec: reproduce preview cases; implement bounded JSON/MessagePack/text/
      Hex/Base64 rendering and JSON-safe binary structures. Include MessagePack
      runtime dependency and frozen package collection.
- [x] UI: reuse the existing native views with read-only format previews and raw
      byte export; prevent binary or Qt-normalized content from raw-text saves.
      Retain text editing and binary field/member identity for safe operations.
- [x] Workspace: test and fix binary-key tree grouping/navigation, headings,
      nested query rendering, CSV display and history descriptions.
- [x] Review integration boundaries; run focused tests, guarded regression
      and authorized live Redis tests with owned-key cleanup. Inspect actual Qt
      screenshots for both languages.
- [x] Build in a fresh output directory, run frozen decoder/driver/credential
      smoke checks, verify signature and ZIP CRC, update current user guidance
      and verification record, and deliver the macOS artifact.

This workspace is not a Git repository; no commit/worktree steps apply.
MongoDB live tests were excluded after confirming its local container was
unhealthy; the remaining 703 tests passed. See VERIFICATION.md for scope and
the additional focused checks after the shared-display compatibility fix.
