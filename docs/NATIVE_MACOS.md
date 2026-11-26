# macOS 原生版本

macOS 新增 SwiftUI + AppKit 界面，数据库工作通过应用内的独立 Python 服务进程完成；没有浏览器、HTTP 服务或 Qt 界面。Windows / Linux 继续使用现有 Python / PySide6 应用。

迁移代码位于 `macos/`，服务协议与实现位于 `baseskiff/native/`。原有 `python -m baseskiff` 和 `scripts/build.py` 保持不变，原生版通过独立构建命令生成，不覆盖旧构建目录。

## 从源码运行

需要 macOS、Xcode Command Line Tools、Swift 6，以及已安装项目 requirements.txt 的 Python 环境。遵循项目 Conda 环境偏好，先选择已有的 `base-skiff` 环境。示例使用当前环境解释器：

```sh
swift build --package-path macos
BASESKIFF_PYTHON="$(command -v python)" BASESKIFF_SOURCE="$PWD" macos/.build/debug/BaseSkiffNative
```

运行测试或试用新界面时，可以指定独立数据目录，避免改写日常连接与历史：

```sh
BASESKIFF_DATA_DIR=/absolute/path/native-test-data \
BASESKIFF_PYTHON="$(command -v python)" BASESKIFF_SOURCE="$PWD" \
macos/.build/debug/BaseSkiffNative
```

不指定目录时使用现有 `~/Library/Application Support/BaseSkiff/`。连接、加密密码、查询历史和 Console 内容兼容；原生显示设置在独立命名空间中保存，首次读取已有 Qt 显示偏好与工作区描述。应用不访问钥匙串。备份时同时保留 `metadata.sqlite` 和 `credentials.key`，并退出所有 BaseSkiff 进程。

## 验证

```sh
python -m pytest -q
BASESKIFF_PYTHON="$(command -v python)" BASESKIFF_SOURCE="$PWD" swift test --package-path macos
python scripts/build_native.py --smoke-test
```

真实服务验证使用仓库外的 `BASESKIFF_LIVE_CONFIG`，格式与现有集成测试一致。不要把密码写进仓库。打包启动检查与截图使用临时数据目录，不修改日常应用数据。

```sh
/path/BaseSkiff.app/Contents/MacOS/BaseSkiffNative --smoke-test
/path/BaseSkiff.app/Contents/MacOS/BaseSkiffNative --screenshot /absolute/path/native.png --language zh
```

## 打包

`python scripts/build_native.py --smoke-test` 生成 `dist/build-YYYYMMDDHHMMSS/BaseSkiff.app`。应用包含 Swift 可执行程序和独立打包的 Python worker，用户不需要另装 Python / Conda。worker 不包含 PySide6、Qt 或钥匙串组件；Oracle Thin、PostgreSQL libpq、SSH 加密依赖随包提供。

本机产物为 Apple Silicon arm64，最低部署目标设为 macOS 14。最低系统版本和 Intel 架构需要分别在对应系统实测；本机编译通过不代表其他系统已验证。代码签名仅为本地 ad-hoc，未作 Developer ID 签名或公证。

原生构建保持独立入口与 bundle identifier，现有 macOS 版本继续可用。迁移验收与已知边界见 [NATIVE_PARITY.md](NATIVE_PARITY.md)，协议见 [PROTOCOL.md](../macos/PROTOCOL.md)。新增原生 CI 为独立工作流，现有三平台测试与构建不受影响。

## 数据与并发边界

- 原生表格修改保持在标签内，只有点击提交才写入。提交失败保留草稿，退出或关闭待提交工作区仍需确认。
- 正在提交或有待提交修改时，禁止改写连接端点，避免旧视图中的行写入新数据库。
- worker 异常退出、超时或连接中断后，不自动重试写操作。应先核实数据库结果再决定重试。
- 取消是请求而不是回滚承诺；服务已进入提交阶段时不能取消。MongoDB / Redis 普通命令沿用现有非取消执行语义。
- 每条请求最大 16 MiB；大响应以分块传输，重组上限 256 MiB。超过限制会明确报错，SQL 全量导出仍在服务端分批写入文件。Redis 编解码沿用既有 1 MiB 限制。
