# Workbench Optimization Implementation Plan

> **For agentic workers:** Use parallel focused tasks and review each integration.

**Goal:** 改善查询可靠性、大数据响应和 SQL 工作流。
**Architecture:** 现有适配器与 service 接口向后兼容；新 UI 功能拆为专用模块，window 负责接线。后台任务绑定来源工作区。
**Tech Stack:** Python, PySide6, existing database drivers, Conda base-skiff.
**Spec:** docs/superpowers/specs/2026-11-24-workbench-optimization-design.md

## Global Constraints
- 中文/英文、现有凭据方式及只读保护保持可用；不可把测试密码写入仓库。
- 每次导出分批读取；取消与错误必须清理资源；本地打包。

## Review Focus
- SSH 中断、提交前取消、多个标签并行运行。
- 无主键/复合主键/自定义排序不能错误套用游标。
- SQL 中的注释/字符串/带引号标识符不能被补全或格式化破坏。
- DDL 各方言与部分失败，不承诺无法提供的事务原子性。
- 恢复已删除连接、二进制 Redis key、导出中断不覆盖原文件。

## Tasks
- [x] Backend: tests first for SSH cancellation, commit cancellation, optional count/pagination; implement in adapters/service/models with compatible defaults.
- [x] SQL editor: tests first for lexical contexts/current statement and metadata completion; new editor helpers and widget; connect to window after backend tasks.
- [x] Structure dialog: tests first for draft SQL and apply; reusable dialog/module and SQLite verification, protect readonly/constraint indexes.
- [x] Root: per-workspace cancel UI, full export with progress/cancel, restart state, explain/timeout integration.
- [x] Integration: full pytest, Ruff, smoke, review changes, local build --smoke-test, commit and push.

## Delivered and verified

2026-11-24：1089 passed, 101 skipped in 48.29s；Ruff、compileall、diff 检查通过。本机 macOS arm64 构建和 frozen smoke 通过。真实服务未提供本轮配置，101 项跳过。

包：dist/build-20261124-143611-_4tofk19/BaseSkiff.app。Redis 仅测量未做模型重写；语法错误精确定位、Oracle 写计划表和完整 SQL 解析器的限制见 docs/WORKBENCH_OPTIMIZATION.md。
