# Redis browser implementation

The user requested server key search above the Redis key tree and data views appropriate to each Redis type, including editing individual hash fields. This extends the existing native Redis workspace and keeps Chinese/English UI, SSH, local credentials and history.

- [x] Confirm existing tree, bounded previews, mutation and async request behavior; inspect the supplied reference images.
- [x] Add per-connection key search controls: literal prefix, fuzzy substring/glob and exact full key. Search Redis instead of filtering only the first loaded keys. Preserve tree hierarchy and expansion; discard stale replies.
- [x] Add bounded typed reads and atomic individual-entry mutations. Preserve TTL and unrelated fields, reject stale edits and respect read-only connections. Keep whole-key APIs compatible.
- [x] Add String editor, Hash field/value table, List index/value table, Set member table, ZSet member/score table and read-only Stream entries. Integrate native dialogs and per-row controls.
- [x] Verify through fakeredis, native Qt and local Redis/SSH integration; inspect both UI languages, review the implementation, build to a unique directory and validate the frozen package.

Backend and standalone value-widget changes are delegated to separate agents with exclusive file ownership. The parent owns MainWindow integration, docs and end-to-end verification. There is no Git repository in this workspace, so no worktree or commit steps apply. Test credentials stay in temporary private configuration and all verification blocks system-keyring imports.

Search semantics: prefix and exact preserve spaces/case; fuzzy supports `*`, `?`, `[]`, or literal substring without a pattern. At most 1,000 matching keys/previews are loaded, with an explicit truncation notice. Individual hash/set/zset operations remain usable on larger collections; list removal is restricted when a complete safe snapshot is unavailable. Stream editing and TTL editing are outside this request.

Completed verification: 540 tests passed, including 70 live integrations; another 36 tests passed against Redis 5.0.14. Two independent reviews resolved TTL, ZSet score and asynchronous UI/draft issues. The uniquely built `dist/build-20261111-142849-mmpg97gq/BaseSkiff.app` passed the frozen smoke test, all 1,204 archive modules extracted, code-signature and ZIP integrity checks passed, and a native packaged workflow searched a prefix and edited one Hash field while retaining its other field and recording success in history. Temporary data, keys and the Redis 5 validation container were cleaned up. See [verification](../../VERIFICATION.md).
