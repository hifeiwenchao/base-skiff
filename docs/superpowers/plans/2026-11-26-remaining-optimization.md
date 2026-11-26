# Remaining Optimizations Implementation Plan

**Goal:** 完成已批准的 Redis/SQL/数据交换与构建优化。
**Architecture:** 独立纯逻辑模块 + 后台适配器操作 + 来源工作区 Qt 接线。
**Spec:** docs/superpowers/specs/2026-11-26-remaining-optimization-design.md

- [x] Redis incremental tree and bounded collection continuation, scoped binary identities, UI load-more.
- [x] SQL scoped completion/metadata-on-demand and diagnostic highlights.
- [x] SQL keyset ordering, full MySQL column metadata, constraints, Oracle plans.
- [x] CSV codec/null options, import mapping/preview/transaction/cancel, execution plan UI.
- [x] Atomic dist/work reservation.
- [x] Integrate, independent review, full suite and real services, native macOS build smoke, commit/push.

## 验证

最终本机：1292 passed, 10 skipped in 127.87s；包含真实 MySQL/PostgreSQL/Oracle/MongoDB/Redis 与真实 SSH。10 项为当前参数化方言不适用。Ruff、compileall、git diff --check 通过。源码无测试凭据；私有配置在仓库外。三平台 CI 在提交后核验，Windows/Linux 交互实机不作已验证声明。
