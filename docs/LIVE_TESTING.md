# BaseSkiff 本地真实服务测试

## 最新验证边界（2026-11-11）

Redis 累计分批加载和标签右键菜单的最终本地回归 **751 项通过（35.41 秒）**，系统凭据导入尝试为 0；日志为 `build/redis-key-batches-unit-validation.log`。本机 Docker Unix socket 的 `/_ping` 在有界只读探针中仍超时，因此本轮排除 **101 项真实服务用例**，未重启或修改服务，也未宣称新版通过真实数据库复验。分批 SCAN 的后端、真实 Qt 界面及 fakeredis 累计 1,203 键工作流均在本地回归中覆盖。构建结果见 [验证记录](VERIFICATION.md)。

## 此前真实服务验证（2026-11-11）

本轮在 macOS arm64 上连接用户提供的 PostgreSQL、Oracle 及恢复后的 MongoDB 本地测试环境。测试通过 `127.0.0.1` 直连实际数据库，也通过临时本机 SSH 服务进行真实 TCP 转发；使用临时应用元数据、PostgreSQL 独立 schema、Oracle 唯一命名对象或 MongoDB 独立测试数据库，结束后清理测试创建的资源。

| 测试范围 | 服务版本与目标 | 测试数 | 本轮结果 |
| --- | --- | ---: | --- |
| PostgreSQL 直连、Qt 多标签与真实 SSH | PostgreSQL 18.6 / 5432 / `dev` | 9 | 通过 |
| Oracle Thin 直连、Qt 多标签与真实 SSH | Oracle AI Database 26ai Free 23.26.3 / 1521 / `FREEPDB1` | 9 | 通过 |
| MongoDB 服务 / CRUD、Qt、真实 SSH 与集合标签页 | MongoDB 8.3.8 / 27017 | 16 | 已恢复，全部通过 |

Redis DB 树调整阶段，最后一处标签导航保护修复前，完整回归 **813 项通过（100.09 秒）**，其中 **101 项**为真实 MySQL、Redis、MongoDB、PostgreSQL 和 Oracle 集成测试，系统凭据导入尝试为 0。该保护随后通过 43 项定向验证；该阶段最终本地回归为 **713 项通过（31.76 秒）**，明确排除了 101 项真实服务用例。最后一次完整实机复验在 MySQL 原生测试连接处阻塞：只读诊断显示 Docker Unix socket 的 `/_ping` 和 MySQL 初始握手均超时，已中断测试，未重启或修改服务。因此不能将修复前的 813 项完整结果视为当前源码的完整实机结果。该阶段本地日志为 `build/redis-database-tree-unit-validation.log`，中断的实机日志为 `build/redis-database-tree-validation.log`。

此前 MongoDB 恢复后的完整回归为 **783 项通过（89.87 秒）**，包含相同的 101 项真实服务测试。日志保留在 `build/mongodb-restored-validation.log`；测试后审计确认 MongoDB 的 `baseskiff_it_` 前缀测试数据库数量为 0，之前的 PostgreSQL 临时 schema 与 Oracle 临时对象审计也均为 0。Redis DB 树阶段还通过了真实 direct / SSH 浏览和逐字段编辑，保留 TTL、历史和中英切换断言；Oracle 新建表的原生只读快照准备检查修正后，9 项 Oracle 测试通过，自有对象审计为 0。

MongoDB 的 16 项复验由 **9 项服务 / CRUD、3 项真实 Qt、2 项真实 SSH、2 项集合标签页测试**组成。新增 `tests/integration/test_mongodb_tabs_live.py` 独立运行 2 项通过（1.81 秒），验证集合切换时查询草稿与结果保留，以及后台编辑仍写入发起操作的集合；独立审阅未发现重要问题。

两个新增套件分别为 `tests/integration/test_postgresql.py` 和 `tests/integration/test_oracle.py`，均已通过 9 项测试。覆盖连接认证、表与视图目录、字段与复合主键、真实 CRUD、数据类型、分页排序、字面量筛选、主键保护、只读事务、失败回滚与历史、结果上限，以及实际 Qt 多标签切换、原表编辑、新增精确数值与日期。SSH 测试核对了服务端收到的转发目标，覆盖认证、浏览、CRUD、查询与只读操作。

本轮修复并通过真实服务验证的行为包括：PostgreSQL 无参数查询中的 `%` 字符和取模表达式；Oracle 视图目录、`NUMBER` 的 Decimal 精度、完整 CLOB / BLOB 读取、直接在 CLOB 上执行字面量搜索、文本筛选排除二进制列，以及时间戳微秒绑定。Qt 记录对话框的新增和编辑保留精确数值，并解析日期 / 日期时间，不经浮点数转换。

此前 MongoDB 曾因运行环境兼容性未能启动：arm64 主机上的 amd64 容器镜像通过 QEMU 执行，所需 AVX 指令不可用，进程报 `Illegal instruction`；当时容器内 27017 未监听，健康检查返回 `ECONNREFUSED`，尚未进入账号认证阶段。此前诊断仅读取架构、日志、端口与健康状态，没有修改用户的 MongoDB 服务配置。用户随后修复环境，现已实际连接 MongoDB 8.3.8 并通过认证 ping，`depends-mongo-1` 健康状态为 healthy，全部 16 项 MongoDB 复验通过。先前故障仅作为历史诊断记录保留。

本轮 Oracle 日期测试覆盖 `DATE` 和无时区 `TIMESTAMP`。独立探针发现原生驱动取回 `TIMESTAMP WITH TIME ZONE` 时没有保留 `tzinfo`；该类型的完整编辑往返不在当前验证保证内。SQLite 的 NUMERIC 存储精度由 SQLite 类型亲和性决定，应用适配 Decimal 输入并不改变其底层存储规则。

本轮数据库服务连接使用明文 TCP 或真实本机 SSH 转发，未验证 PostgreSQL / Oracle 的数据库服务端 TLS。PostgreSQL 的 `sslmode=require` 仍只要求加密，不验证证书身份。未使用外部堡垒机，本轮未在 Windows / Linux 上构建或验证产物。完整回归和构建结果见 [验证记录](VERIFICATION.md)。

MongoDB 恢复后的此次复验没有修改生产源码或重新构建应用，继续使用已有 `dist/build-20261111-170603-7k8aoy3x/BaseSkiff-macOS-arm64.zip`。该次审计将包内 23 个 BaseSkiff 模块与当时的生产源码编译代码对象逐一比对，结果一致；已有包的启动、临时签名和 ZIP 完整性验证结果保留。

## 历史验证：289 项通过

以下为此前同日在 macOS arm64 上完成的验证记录，保留其原有数量和结果，不作为本轮服务可用性或完整回归总数。该轮通过 `127.0.0.1` 和默认端口连接，使用临时元数据、仅会话凭据及独立数据库/唯一前缀键。

| 测试范围 | 服务版本 | 测试数 | 结果 |
| --- | --- | ---: | --- |
| MySQL 实际服务与数据访问层 | 9.7.2 / 3306 | 12 | 通过 |
| Redis 实际服务与数据访问层 | 8.10.1 / 6379 | 31 | 通过 |
| MongoDB 实际服务与数据访问层 | 8.3.8 / 27017 | 9 | 通过 |
| 三类服务的真实 Qt 窗口操作 | 同上 | 7 | 通过 |
| 三类服务通过真实 SSH 转发的 CRUD / Qt 操作 | 同上 | 6 | 通过 |
| Redis 真实树形目录（直连 `:` / SSH `::`） | 同上 | 2 | 通过 |
| SQLite、对话框、Qt 回归、SSH / TLS、Redis 树与驱动边界 | 本机 | 222 | 通过 |
| **合计** | | **289** | **全部通过** |

MySQL 验证了实际认证、字段/主键/视图、增删改查、Decimal/日期/JSON/二进制/NULL/Unicode、筛选转义、分页排序、约束错误、批量操作回滚、只读事务以及历史记录。提供的 dev 账号可以认证，但没有可写的业务库权限；完整数据测试使用 root 创建独立测试库，没有更改 dev 权限。详见 [MySQL 结果](mysql-live-results.md)。

Redis 验证了 string/hash/list/set/zset 的实际读写，编辑保留 TTL，非法数据不覆盖原值，大集合截断保护，真实 WATCH 并发冲突，命令执行、历史和只读限制。测试只删除自己创建的唯一前缀键，没有执行 FLUSHDB 或服务配置变更。详见 [Redis 结果](redis-live-results.md)。

MongoDB 验证了认证、集合/字段、CRUD、分页排序、字面量搜索、find/aggregate、Extended JSON、写命令、结果截断、重复键错误和只读限制。Qt 窗口测试验证了实际数据加载、查询/历史、编辑、异步连接测试和空白密码保留。

SSH 测试通过临时的真实 Paramiko 服务转发到这三个数据库，验证连接测试、结构、CRUD、命令、只读和历史，以及 Qt 窗口的连接测试、浏览和查询。服务端同时核对每个转发目标。222 项本地测试包含 33 项 SSH 传输、59 项路由 / TLS、17 项 SSH 对话框、11 项 SSH 凭据服务和 10 项 Redis 协议 / 认证 / 错误脱敏测试；Redis / MongoDB 另有本机 TLS 握手验证。未完成的代理握手并发关闭也有回归测试。详见 [SSH 说明](SSH_TESTING.md)。

Redis 树形验收测试直接连接本机 Redis 8.10.1，并通过临时 SSH 服务连接同一服务；测试使用 `:` 和 `::` 分隔符，验证目录、计数、搜索、精确叶节点、查询历史和平铺切换。Redis 树与分隔符相关的本地回归共有 33 项，包含 UI、持久化和服务边界。

额外使用独立临时容器 Redis 5.0.14 验证旧版兼容：修复前实际复现 `HELLO` 不支持错误；修复后空用户名密码认证通过全部 31 项 Redis 服务测试（4.60 秒），并通过直接 / SSH 的 Qt 连接测试、浏览、查询、CRUD 与历史验证。该额外运行重用相同 31 项测试，不重复计入上表。服务只绑定本机回环端口，测试完成移除，不改用户的 Redis 服务配置。

该轮实机测试发现并修复：

1. 查询编辑器提示的 `insertOne`、`updateOne`、`deleteOne` 简写没有接入。现通过 PyMongo 执行实际单条操作；`updateOne` 支持 upsert，并正确计算 `_id: null` 的插入数量。
2. MongoDB 原生命令返回 `ok: 1` 和 `writeErrors` 时被误记成功。现传播写入错误并在历史中标记失败。
3. 异构文档的同名字段沿用了其他文档的类型。现按当前文档实际值编辑，保留字符串和前导零，避免给缺失字段添加 NULL。
4. 编辑嵌套字段时，BSON ObjectId、Decimal128、日期等会因 JSON 格式化变为字符串。MongoDB 文档编辑现使用 Extended JSON 保留类型；SQL JSON 字段继续按普通 JSON 处理。

## 运行实机测试

测试默认不访问外部服务。提供环境变量后才运行。将下列格式的私有 JSON 放在仓库外，填写自己的测试环境凭据：

```json
{
  "host": "127.0.0.1",
  "postgresql": {"port": 5432, "username": "<postgresql username>", "password": "<postgresql password>", "database": "dev"},
  "oracle": {"port": 1521, "username": "<oracle username>", "password": "<oracle password>", "database": "FREEPDB1"},
  "mysql": {"port": 3306, "root_password": "<root password>", "username": "dev", "password": "<dev password>"},
  "redis": {"port": 6379, "username": "default", "password": "<redis password>"},
  "mongo": {"port": 27017, "username": "admin", "password": "<mongo password>"}
}
```

```sh
BASESKIFF_LIVE_CONFIG=/absolute/private/local-services.json .venv/bin/python -m pytest -m integration -q
BASESKIFF_LIVE_CONFIG=/absolute/private/local-services.json .venv/bin/python -m pytest -q
BASESKIFF_LIVE_CONFIG=/absolute/private/local-services.json .venv/bin/python -m pytest tests/integration/test_postgresql.py tests/integration/test_oracle.py -q
```

测试配置和密码不打包，凭据在临时测试数据目录中本地加密保存，不访问系统钥匙串。每次使用随机资源前缀，`finally` 清理自己创建的资源。该测试套件应仅对明确允许修改的测试环境运行。

macOS 应用通过 PyInstaller 打包，并运行包内原生窗口 / SQLite / 驱动与 SSH 加密库检查。实机数据库验证使用本机明文 TCP，覆盖直接连接和临时 SSH 转发；本轮新增 PostgreSQL 18.6 与 Oracle AI Database 26ai Free 23.26.3，数据库服务端 TLS、其他服务版本或认证方式没有覆盖。SSH 的密码、RSA / Ed25519 私钥、加密私钥和 Agent 认证使用临时 SSH 服务验证。未使用外部堡垒机，本轮未在 Windows / Linux 上构建或验证产物。
