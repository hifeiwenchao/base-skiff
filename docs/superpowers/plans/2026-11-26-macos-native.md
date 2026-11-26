# macOS Native Implementation Plan

> **For agentic workers:** Use executing-plans with parallel component implementation and a final independent review.

**Goal:** Preserve BaseSkiff's current UI and features in a SwiftUI / AppKit macOS app.
**Architecture:** Native UI with a bundled, Qt-free Python service worker using versioned, typed stdin/stdout RPC. Existing cross-platform app remains available until parity validation.
**Tech Stack:** Swift 6, SwiftUI, AppKit, Python 3.11+, existing database drivers, PyInstaller.
**Spec:** docs/superpowers/specs/2026-11-26-macos-native-design.md (approved by user)

## Global Constraints

- Warm white / sage UI; Chinese and English; preserve existing functionality.
- No keychain; compatible BaseSkiff metadata and Fernet credentials.
- Redis 500 keys per load, cumulative; query only on submitted search.
- Never replace old bundles; dist/build-YYYYMMDDHHMMSS.
- No default app replacement before full feature verification.

## Review Focus

- Binary / precise / nested values must round trip without coercion (Task 1).
- Late results after tab close/reopen cannot overwrite new state (Task 2).
- Cancel cannot wait behind the operation it cancels (Task 1).
- Incomplete migration must not be represented as production parity (Task 4).
- A frozen worker must run without a development Python environment (Task 4).

## Task 1: Headless worker and wire contract

Files: baseskiff/native/{wire,worker}.py, tests/test_native_worker.py; macos/PROTOCOL.md.
Interface: version 1 JSONL envelopes {version,id,method,params}; response {version,id,result|error}. Ordinary dictionaries and arrays remain JSON; special scalar values use {$type,value}; dataclasses serialize as fields. requests are concurrent; cancellation uses target request id. No arbitrary getattr dispatch. Opaque continuation handles stay worker-owned.
- [ ] Test real temporary SQLite CRUD, history, precision/binary roundtrip, whitelist, cancel and clean EOF without Qt imports; observe RED.
- [ ] Implement explicit operation registry and bounded concurrent dispatch with separate cancellation; GREEN.
- [ ] Document protocol and run worker tests.

## Task 2: Swift core and native widgets

Files: macos/Package.swift, Sources/BaseSkiffNative/{WireValue,RPCClient,WorkspaceStore,NativeGrid,NativeEditor,NativeOutline}.swift, Tests.
Interfaces: WireValue lossless Codable enum; RPCClient.call(method:params:) async throws -> WireValue; stable per-workspace UUID lifetime plus generation. AppKit grid/editor/outline exposed to SwiftUI.
- [ ] Test tagged values, typed editing and lifetime/generation rejection; observe RED.
- [ ] Implement async subprocess client with EOF/crash/timeout handling and bounded messages.
- [ ] Implement AppKit views; preserve selection and scroll on value updates.
- [ ] Run swift test and build.

## Task 3: Native workspace interactions

Files: Sources/BaseSkiffNative/{App,WorkbenchView,ConnectionEditor,RedisWorkspace,StructureWorkspace,TransferWorkspace}.swift.
- [ ] Connect real service to native connection tree / tabs / SQL Console and staged grid.
- [ ] Migrate Redis type-specific view/edit/TTL/search/history and serialized preview, Mongo BSON editing.
- [ ] Migrate DDL draft / preview, typed delete confirmation, indexes/constraints, plans, CSV import/export, persistent display/session settings.
- [ ] Verify actual operations against temporary SQLite plus explicitly configured local services; test late responses and staged edit guards.

## Task 4: Packaging and parity gate

Files: scripts/build_native.py, BaseSkiffWorker.spec, docs/NATIVE_MACOS.md, docs/NATIVE_PARITY.md.
- [ ] Bundle native executable + standalone worker without PySide6; keep existing scripts/build.py unchanged.
- [ ] Launch packaged smoke against temporary data with external Python disabled.
- [ ] Run Python regression, Swift tests, UI screenshots, signing verification, feature matrix review.
- [ ] Record supported / missing / unverified capabilities truthfully; do not replace default before all pass.

## Execution ledger

- User approved design, 2026-11-26. Isolated worktree codex/macos-native.
- Execution continues directly under existing implementation authorization; no extra plan approval needed.

### 实施结果

- 服务通信、原生控件、工作区模块和独立打包入口已接入；默认发行入口保持原版，原生验收状态见 docs/NATIVE_PARITY.md。
- Python 全量1350通过、10跳过；最终 Swift55通过（含本地Redis与SQLite），Ruff/compileall/diff检查通过。
- 关键修复：SQL之外命令取消参数、Oracle动态加密模块打包、CSV精确指纹、用户字典$type、连接端点切换/提交草稿并发、Console未加载关闭、Redis搜索迟到结果与大值预览保护。
- 用户新增约束：测试不得干扰工作桌面。之后只允许后台测试、编译、打包、静态校验，不再启动 GUI。
- Ruling: 原生版本保持独立预览入口，完整功能清单中未逐项实机验证的路径不宣称已通过；成本是本轮不自动替换默认应用。
- Ruling: 大响应分块重组上限256MiB、请求16MiB，保留明确错误和流式导出；成本是超过限制的单次网格请求需缩小结果或导出。
- 独立最终审查3项全部修复并回归；最终版本保留现有Windows/Linux和macOS原版构建。
