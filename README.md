# BaseSkiff

用 Python 和 PySide6 编写的原生桌面数据库工作台，支持 macOS、Windows 和 Linux。暖白与鼠尾草绿界面，中文 / English 即时切换；无需浏览器或本地 Web 服务。

## 功能

- 管理 PostgreSQL、MySQL、SQLite、MongoDB、Redis、Oracle 连接，测试连接并浏览数据。
- 新建连接先选择数据库类型，再填写对应表单；编辑时类型固定。
- 为网络数据库配置 SSH 隧道，支持密码、私钥文件和 SSH Agent 认证。
- 表 / 集合 / 键侧栏、搜索、分页、列排序、记录详情与结构视图。
- 右侧多表标签页常驻打开的表、集合和 Redis 键，切换时保留各自的数据与查询状态。
- 数据源连接可通过左侧箭头收起 / 展开；已载入的连接点击名称也可切换，刷新与语言切换保留状态。
- 拖动侧栏右侧分隔线可调整宽度，重启自动恢复上次拖动的宽度。
- Redis 在侧栏选择数据库，按连接中设置的键分隔符显示目录树，支持服务端键搜索、分类型值视图与逐项编辑。
- 新增、编辑、删除记录；SQL 表可直接编辑非主键单元格，所有变更点击「提交」后才写入数据库。SQL 表需要主键才能从网格修改；MongoDB 和 Redis 使用各自的编辑入口。
- SQL / MongoDB JSON / Redis 命令编辑器，运行历史、耗时、错误信息与 CSV 导出。
- 连接只读模式、本地加密保存密码、中文 / 英文设置持久化。
- 首次启动创建本地 SQLite 示例库，包含 `customers`、`products`、`orders`、`order_items`、`categories`、`activity_log` 六张关联表；示例可真实增删改查，后续启动保留修改。

## 从源码运行

需要 **Python 3.11+**。建议使用独立虚拟环境；依赖包括 PySide6 6.9+、各数据库的 Python 驱动和本地加密库。

macOS / Linux：

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m baseskiff
```

Windows（PowerShell）：

```powershell
py -3 -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe -m baseskiff
```

应用运行、测试和打包统一使用 `requirements.txt`；其中同时包含数据库驱动、Qt、pytest、pytest-qt、fakeredis 和 PyInstaller。也可以在虚拟环境中执行 `python -m pip install -e ".[dev]"`，然后使用 `baseskiff` 启动。

Linux 需要可用的桌面图形环境。Ubuntu 22.04 可安装 Qt 所需的系统库：

```sh
sudo apt-get install libegl1 libopengl0 libxkbcommon-x11-0 libxcb-cursor0
```

## 连接与查询

点击「新建连接」，先在类型选择页选择数据库，再打开对应的连接表单填写参数。数据库类型在创建时确定，新建和编辑表单均以静态文字显示类型；编辑已有连接直接进入表单。选择页支持 Tab 切换、Enter 确认与 Esc 取消，取消时不保存连接。查看[类型选择页](docs/connection-type-zh.png)与[固定类型编辑表单](docs/connection-edit-redis-zh.png)。

SQLite 选择已有数据库文件。Oracle 使用 python-oracledb **Thin 模式**，数据库字段填写服务名，不要求安装 Oracle Instant Client。Redis 新建连接默认使用 DB0，创建后通过侧栏选择数据库；新建和编辑表单均不显示数据库索引，编辑保留已选数据库。连接外部服务需要自己的地址、账号、网络访问和服务端权限。

数据库和 SSH 端口均支持直接输入数字；鼠标滚轮用于滚动表单，不会修改端口值。

侧栏点击连接左侧箭头收起 / 展开其表、集合或键，连接名称仍保持可见。已载入且有子项的连接点击名称也可切换展开状态，不重新查询或重置工作区；初次打开或空连接仍点击名称载入。搜索会临时展开匹配的连接，清空搜索后恢复原状态。

从侧栏打开表、集合或 Redis 键后，右侧会保留对应标签页；再次打开同一对象会选中已有标签。点击标签切换，拖动可调整顺序，标签较多时可滚动查看。右击标签可选择「关闭当前标签页」「关闭其他标签页」或「关闭所有标签页」；前两项以右击的标签为操作对象，关闭所有标签页后显示空工作区。各标签分别保留分页、筛选、排序、已加载数据、查询草稿与结果，以及「数据 / 查询 / 结构」内层页签位置；后台任务完成后只更新其来源标签。关闭标签仅关闭视图，关闭最后一个标签后显示空工作区。

不同连接、不同数据库中的同名对象使用独立标签。Redis 标签同时绑定 DB 编号与完整原始键标识，包括二进制键；显示标记不会改变操作目标。操作示例与状态说明见[多表标签页](docs/TABLE_TABS.md)，界面示例：[中文](docs/table-tabs-zh.png) / [English](docs/table-tabs-en.png)。

将鼠标移到侧栏右侧分隔线上，左右拖动即可调宽或调窄；默认 240px，可在 200–560px 范围内调整。工作区保留至少 640px，窗口较窄时会相应限制侧栏宽度。上次拖动的宽度会自动保存，调整布局保留当前数据、查询草稿与连接状态。查看[调整后的侧栏](docs/sidebar-resize-zh.png)。

Redis 用户名可留空，仅填写密码时使用默认用户的密码认证；使用 ACL 账号时填写相应用户名。连接统一使用兼容新旧 Redis 的 RESP2，支持 Redis 5 等不提供 `HELLO` 命令的服务器，直接连接和 SSH 隧道行为一致。

启用 TLS 时，MySQL 使用系统信任证书并校验主机名；PostgreSQL 使用 `sslmode=require`，要求加密传输，**不会验证服务端证书身份**。目前未提供自定义 CA 或客户端证书配置。

### SSH 代理 / 隧道

在「新建连接」或「编辑连接」中，先填写「数据库」页，再切换到「SSH 隧道」启用连接。填写 SSH 主机、端口（默认 `22`）、用户名，并选择密码、私钥文件（支持加密私钥）或 SSH Agent 认证。Agent 方式使用当前进程可访问的 SSH Agent；不会自动尝试磁盘上的默认私钥。

「数据库」页的主机和端口由 **SSH 服务器访问**，例如数据库与 SSH 服务在同一机器时可填 `127.0.0.1` 和数据库端口。本地转发端口自动分配，无需手动运行 `ssh -L`。测试连接、表结构、浏览、查询及增删改查均使用隧道，每次操作结束后关闭连接。

服务器身份通过默认的 `~/.ssh/known_hosts`、指定的 known_hosts 文件，或填写的 `SHA256:` 主机指纹验证。指定指纹时以该指纹为准。未知主机的测试会显示实际指纹；通过可信渠道核实后填入，再次测试即可。指纹不匹配会拒绝连接。

SSH 支持 MySQL、PostgreSQL、Oracle、MongoDB 和 Redis；SQLite 仍使用本地文件。MongoDB 隧道只连接所填目标（`directConnection=True`），不做副本集成员发现或故障转移。SSH 与数据库 TLS 可同时启用。配置示例和验证范围见 [SSH 使用与测试说明](docs/SSH_TESTING.md)。

SQL 数据源在左侧按「Console / History / Tables」组织。打开 Console 后可点击运行，或按 `⌘ / Ctrl + Enter` 执行当前选区（无选区时执行全文）；按 `Shift + Enter` 也可执行选中语句。Console 文本按连接和数据库分别保存在本机，查询结果只保留在当前会话。SQL 每次执行一条语句，成功后提交、出错后回滚；不同运行之间不保留事务。

### SQL

SQLite 示例库可直接运行：

```sql
SELECT id, name, email, plan
FROM customers
WHERE status = 'Active'
ORDER BY id
LIMIT 20;
```

PostgreSQL、MySQL、SQLite、Oracle 使用各自的 SQL 方言。Oracle 限制结果条数可使用 `FETCH FIRST 20 ROWS ONLY`。

SQL 表数据页上方提供 `WHERE` 和 `ORDER BY` 条件栏。输入 SQL 片段后点击「查询」或按 Enter 执行；可省略关键字，刷新和分页会复用已提交的条件。条件禁止分号、注释和多语句，原有全字段筛选仍可作为快捷筛选。

SQL 表支持直接编辑单元格，主键列保持只读。新增、修改和删除先保存在当前标签页的待提交区，点击「提交」才会在一个事务中写入；「撤销修改」会丢弃本地变更。提交失败时整体回滚并保留待提交内容；切换或关闭有未提交修改的标签页会先提示。没有主键的表继续只读，MongoDB 和 Redis 保持各自的编辑流程。

### MongoDB

输入 **JSON 命令对象**；支持 BSON Extended JSON。命令名放在第一个字段。示例中的集合需要在连接的数据库中存在。

```json
{"find":"customers","filter":{"status":"Active"},"sort":{"name":1},"limit":20}
```

```json
{"aggregate":"customers","pipeline":[{"$match":{"status":"Active"}},{"$group":{"_id":"$plan","count":{"$sum":1}}}]}
```

查询编辑器也支持单条写入简写：

```json
{"insertOne":"customers","document":{"name":"BaseSkiff","status":"Active"}}
```

```json
{"updateOne":"customers","filter":{"name":"BaseSkiff"},"update":{"$set":{"status":"Trial"}}}
```

```json
{"deleteOne":"customers","filter":{"name":"BaseSkiff"}}
```

文档字段编辑使用 MongoDB Extended JSON，保留嵌套 ObjectId、Decimal128、日期等 BSON 类型。编辑已有记录时按当前文档的实际字段类型处理；不同文档可以使用不同类型。

### Redis

展开 Redis 连接后，第一层显示 DB，展开具体 DB 后才加载其键目录与键列表，每次最多加载 500 个键，已加载的键全部累计展示；再次展开 DB 或点击末尾「加载更多」继续下一批。可读取服务端统计时，节点显示如 `DB0 [42]` 的键数量，包括空数据库。切换先验证目标 DB，成功后保存选择，重启后恢复；验证失败时保留原数据库、键列表与工作区。

无法通过 `CONFIG` 获取数据库数量（例如 ACL 禁止该命令）时，列表提供 DB0–DB15，以及服务端报告或当前选中的其他 DB；可用「选择其他 DB…」输入编号，是否可用仍由服务端验证。无法读取键数量时不显示计数。识别到 Redis Cluster 时只提供 DB0。

在新建或编辑连接的「数据库」页设置「键分隔符」，默认 `:`。例如 `h:match:cart:stock:1:4205457` 会按 `h → match → cart → stock → 1` 显示目录，叶节点保留完整原始键标识，二进制或特殊文本使用安全显示标记，过长标签会截断。支持 `::`、`/` 等多个字符或其他分隔符；留空则平铺显示。旧连接自动使用 `:`，各连接单独保存设置。

点击目录在本地展开 / 折叠，不重新扫描；点击键查看数据，保留侧栏当前的横向与纵向滚动位置。Redis 键搜索位于当前展开的 DB 分支首行，只搜索该 DB，仅支持前缀和精确两种模式，按连接及 DB 分别保留输入和模式。输入、清空或切换模式均只修改草稿，点击输入框右侧的搜索按钮或按 Enter 才向 Redis 提交搜索；刷新继续使用上次提交的条件，草稿未提交时暂停续载。匹配规则：前缀按字面前缀匹配；精确查找完整字面键名。输入中的 `*`、`?` 或 `[]` 均按普通字符处理。搜索区分大小写，保留输入中的空格。前缀搜索通过服务端 `SCAN MATCH` 获取匹配键并去重，再批量执行 `TYPE`、按分隔符建树，每批最多返回 500 个匹配键，累计展示全部已加载结果；再次展开 DB 或点击「加载更多」续载，精确搜索使用 `EXISTS`。每批最多执行 8 次 SCAN，稀疏搜索可能返回少量或零个匹配键但仍可继续，只有扫描完成时才移除续载入口。刷新或提交新的搜索条件会重新开始加载。匹配结果保留父目录，目录展开状态按 DB 分别保留，刷新不会重置；查询与修改使用原始键名。目录括号中的数量仅是已加载的后代键数量。

值视图按类型展示：String 原始文本、Hash 字段和值、List 索引和值、Set 成员、ZSet 成员与分数，以及只读 Stream 消息。Redis 驱动保留原始字节，只将可无损解码的 UTF-8 转为文本，二进制值不会因 UTF-8 解码错误而无法打开。点击「查看」，或选中集合中的一行后点击「查看」，可打开只读预览，在 Auto / Text / JSON / MessagePack / BSON / CBOR / gzip / zlib / Hex / Base64 间切换。Auto 只接受完整的单个对象，并识别 JSON、MessagePack、BSON、CBOR 以及完整的 gzip / zlib 压缩流；压缩内容会显示解压和内部格式链。BSON 使用 Extended JSON 保留 ObjectId、Decimal128、日期、二进制等类型；CBOR 保留标签、二进制、重复键和非字符串键。所有解码仅用于展示，不会自动重新编码或写回数据库；无法确定的内容回退 Hex。Stream 支持结构预览，再选择单个字段值查看或导出。

预览窗口可导出其原始单值字节；解码和解压最多处理 1 MiB，显示最多 200,000 个字符，结构深度和条目数也有限制，超限会提示或回退，单值原始导出不截断。gzip / zlib 必须是完整的单一流，尾随数据、校验错误和解压炸弹会回退 Hex。RedisJSON 使用 `JSON.GET` 读取；其他未知模块类型使用只读 `DUMP` 回退，导出的是包含类型和校验数据的 Redis 序列化值，并非业务原文。私有编码不保证能够解码，仍可选择 Hex / Base64 查看。

Hash、Set、ZSet 支持逐项添加、编辑和删除；List 支持按索引编辑与追加，只有完整载入且不超过 1,000 项时才允许删除。二进制内容及 Qt 无法无损保存的特殊文本禁止原文编辑，可只读预览、导出，并按现有规则删除条目或整键；ZSet 的分数编辑不改变成员字节。普通文本的增删改查保持原样，数字和 JSON 文本不会自动转换。集合仍最多预览 1,000 项，预览内搜索按字面子串匹配且不区分大小写，不会向服务端查找其他条目。查看[中文](docs/redis-batches-zh.png) / [英文](docs/redis-batches-en.png)界面及 [Redis 浏览与目录树说明](docs/REDIS_TREE.md)。

每次运行一条 Redis 命令，含空格的参数使用引号：

```text
SET greeting "hello world"
```

```text
GET greeting
```

```text
HGETALL customer:1
```

不支持订阅、监控等交互命令，以及阻塞命令。查询输出最多显示 **1,000 行**，SQL / MongoDB 的大结果会显示截断状态。Redis 返回值可能包含嵌套集合；侧栏每批最多加载 500 个匹配键，累计展示全部已加载的键。CSV 导出当前显示的结果 / 当前页，完整导出需在服务端或专用工具中完成。

Redis 集合超过 1,000 项时，Hash / Set / ZSet 的逐项修改和 List 的编辑 / 追加仍可用；整键替换与 List 删除要求完整载入且不超过 1,000 项。修改会保留服务端绝对过期时间，避免延长 TTL；检测到并发冲突时要求刷新后重试。只读保护与操作历史同样适用于这些操作，直接连接和 SSH 隧道行为一致，界面支持中文 / English 即时切换。

切换 DB 后，已排队的读取、写入与命令仍在发起时的数据库执行；不同 DB 的同名键与修改锁分别处理，旧 DB 的迟到结果不会覆盖当前视图。Redis 操作历史保存 DB 编号，双击记录会在原数据库重新打开查询。

只读连接禁止网格修改。SQLite 同时启用数据库只读模式；其他 SQL 使用只读事务并限制查询类型；MongoDB 和 Redis 使用读取命令白名单。服务端账户权限仍由数据库自身管理。

## 本地数据与密码

| 系统 | 应用数据目录 |
| --- | --- |
| macOS | `~/Library/Application Support/BaseSkiff/` |
| Windows | `%LOCALAPPDATA%\BaseSkiff\` |
| Linux | `${XDG_DATA_HOME:-~/.local/share}/baseskiff/` |

目录包含连接元数据、加密凭据与历史的 `metadata.sqlite`，本地加密密钥 `credentials.key`，以及示例库 `playground.sqlite`。语言偏好通过系统 Qt 设置保存。

数据库密码、SSH 密码和私钥口令使用 Fernet 加密后保存在本机，重启应用后仍可使用。应用不访问系统钥匙串，也没有主密码步骤；旧版保存在钥匙串中的凭据需在新版重新输入并保存一次。编辑已有连接时，密码或私钥口令留空表示保留本机已保存的值；移除连接会同时删除对应的本地凭据。私钥文件由用户管理，应用只记录路径。

密钥文件与凭据库都保存在上述应用数据目录，macOS / Linux 上限制为当前用户读写。迁移或备份连接时须同时保留 `metadata.sqlite` 与 `credentials.key`；拥有这两个文件及相应权限的用户可解密密码。

历史会保存查询原文、记录修改值、连接名称、时间与执行结果，可能包含用户输入的敏感数据；可在历史视图清空。应用数据保存在本机。

## 测试与打包

在已安装开发依赖的环境中运行（Windows 使用对应的 `.venv\Scripts\python.exe`）：

```sh
.venv/bin/python -m pytest -q
.venv/bin/python -m baseskiff --smoke-test
.venv/bin/python scripts/build.py --smoke-test
```

`--smoke-test` 进行启动检查并自动退出。无显示服务的 Linux 测试可设置 `QT_QPA_PLATFORM=offscreen`。需要界面截图时：

```sh
.venv/bin/python -m baseskiff --screenshot /absolute/path/baseskiff.png
```

构建脚本调用当前 Python 环境中的 PyInstaller，不会自动安装依赖。`--dist-dir` 和 `--work-dir` 可指定输出与构建缓存的父目录。每次构建生成独立的 `build-日期-随机值` 子目录，并打印应用的完整路径；之前的应用目录会保留，避免覆盖运行中的程序导致驱动解压失败。退出旧版后再打开新目录中的应用；清理旧目录前应先退出对应应用。**必须分别在目标系统上构建**，并匹配目标 CPU 架构；macOS 的本机 arm64 构建不是 Intel / Apple Silicon 通用二进制。

| 平台 | 构建输出 | 运行 |
| --- | --- | --- |
| macOS | `dist/build-<id>/BaseSkiff.app` | Finder 打开应用 |
| Windows | `dist/build-<id>/BaseSkiff/` | `BaseSkiff.exe` |
| Linux | `dist/build-<id>/BaseSkiff/` | `./BaseSkiff` |

Windows / Linux 需要保留整个输出文件夹，包含 `_internal` 中的依赖。数据库驱动和 Qt 原生库随应用打包；外部数据库服务仍由用户提供。QtWebEngine 不属于此应用。

GitHub Actions 提供 macOS、Windows、Ubuntu 22.04 + Python 3.12 的测试、构建和启动检查工作流。产物以 ZIP / tar.gz 上传到 Actions artifacts，不会自动发布 Release。Linux 构建应在不高于目标系统的 glibc 版本上完成；Ubuntu 22.04 产物面向同版本或兼容的更新发行版。

产物未做面向公众分发的代码签名；macOS 未经过 Developer ID 签名和 Apple 公证。下载后若被 Gatekeeper 拦截，在确认来源后先尝试打开，再到「系统设置 → 隐私与安全性」使用「仍要打开」。请勿关闭系统安全检查。Windows 也可能显示未知发布者提示。

全局性能修复的最终本地回归 **970 项通过（63.38 秒）**，包含 **101 项真实数据库集成测试**，系统凭据导入尝试为 0。修复了 Redis 目录重复重建、表格选区恢复与大字段绘制、批量 SQL 删除、历史统计及 SSH 关闭的性能问题。每批最多加载 500 个键，已加载的键全部保留；多标签的选中状态、草稿和完整导出保持可用。性能对比与范围见 [性能检查](docs/PERFORMANCE.md)，测试、构建及三平台 CI 记录见 [验证记录](docs/VERIFICATION.md) 和 [实机测试报告](docs/LIVE_TESTING.md)。

此前 PostgreSQL 18.6 与 Oracle AI Database 26ai Free 23.26.3 的真实验证各 **9 项通过**，覆盖数据类型与 CRUD、字面量搜索和分页、视图与复合主键、只读与回滚历史、结果上限，以及原生 Qt 多标签、精确数值和日期录入、真实本机 SSH 转发。用户修复 MongoDB 部署后，MongoDB 8.3.8 的 **16 项复验通过**：9 项服务 / CRUD、3 项 Qt、2 项真实 SSH、2 项集合标签页。这些历史结果不代表本轮服务可用性。PostgreSQL / Oracle 数据库服务端 TLS 未验证，Windows / Linux 未在本机构建或实机验证。

不提供测试配置时，真实服务测试自动跳过。可通过环境变量 `BASESKIFF_LIVE_CONFIG` 指向仓库外的私有 JSON 配置，再执行 `python -m pytest -m integration -q`。配置格式和运行方式见测试报告。0.1 暂不包含备份、云同步、ER 图或签名安装包。

## English quickstart

BaseSkiff is a native Python / PySide6 desktop database workspace for macOS, Windows, and Linux, with instant Chinese / English switching. It supports PostgreSQL, MySQL, SQLite, MongoDB, Redis, and Oracle through their actual Python drivers. A persistent local SQLite playground is created on first launch.

Create a Python 3.11+ virtual environment, install `requirements.txt`, then run `python -m baseskiff`. Run `python -m pytest -q` for tests and `python scripts/build.py --smoke-test` to build on the target OS. Windows and Linux outputs require the complete application folder.

Each build uses a separate `dist/build-<id>/` directory and prints the full app path. `--dist-dir` and `--work-dir` select parent directories. Existing bundles remain untouched: replacing a running frozen app can break later driver imports with a decompression error. Quit the old app before opening the new build or removing its old directory.

**New connection** opens a database type picker, then the selected type's connection form. The database type is fixed in both new and existing connection forms. Editing opens the form directly. Use Tab and Enter to choose a type or Esc to cancel; cancelling does not save a connection.

New Redis connections start in **DB0**. Both new and edit forms hide the database index; editing retains the selected DB. Expand a connection to see its DB nodes, then expand a DB to load its key folders and keys, up to 500 per load. Loaded keys accumulate; reopen the DB or click **Load more** for another batch. When server statistics are accessible, nodes show key counts such as `DB0 [42]`, including empty databases. A switch is validated before saving and survives a restart; a failed switch keeps the original database and workspace.

Use the arrow beside a connection to collapse or expand its tables, collections or keys. Clicking the name of a loaded, nonempty connection also toggles it without reloading the workspace. Refresh and language changes preserve this state; clearing a search restores the earlier expansion state.

Opening a table, collection or Redis key from the sidebar keeps it in a tab on the right. Opening the same object again selects its existing tab. Click tabs to switch, drag to reorder, and scroll through the bar when many are open. Right-click a tab for **Close tab**, **Close other tabs**, or **Close all tabs**. The first two use the clicked tab as the target; closing all tabs leaves an empty workspace. Table tabs retain their pagination, filters, sorting, loaded data, staged edits and **Data / Structure** page. SQL execution lives in the connection's separate **Console**, whose draft is scoped by connection and database. Background task results update their originating tab or Console. Closing a tab closes its view; closing the last tab leaves an empty workspace.

Identically named objects in different connections or databases have separate tabs. Redis tabs retain the DB index and complete original key identity, including binary keys, so display markers cannot change the operation target. See the [table tabs guide](docs/TABLE_TABS.md) for usage and retained state, or view the [Chinese](docs/table-tabs-zh.png) / [English](docs/table-tabs-en.png) interface.

Drag the divider at the sidebar's right edge to resize it. The default is 240px, with a 200–560px range and at least 640px reserved for the workspace. Narrow windows may limit the available sidebar width. The last dragged width is restored on restart; resizing preserves the open data, query draft and connection state. See the [resized sidebar](docs/sidebar-resize-en.png).

Use SQL in the connection's Console, JSON command objects for MongoDB (for example `{"find":"customers","filter":{}}`), and one shell-style Redis command per run (for example `GET greeting`). Displayed query rows are capped at 1,000; CSV export contains the displayed result or current page. SQL table views provide `WHERE` and `ORDER BY` fragment fields above the grid; click **Apply** or press Enter to execute, and committed conditions are reused for refresh and pagination. Semicolons, comments and multiple statements are rejected. SQL table cells are directly editable except for primary keys. Inserts, edits and deletes stay staged in the tab until **Commit**; **Revert** discards them. A failed commit rolls back the transaction and keeps the staged changes. Tables without a primary key remain read-only, while MongoDB and Redis keep their own editing workflows.

Enable **SSH tunnel** in the connection dialog to use password, private-key or SSH agent authentication. The database address is reached from the SSH server. Verify the server using known_hosts or a trusted SHA256 fingerprint; unknown or changed keys are rejected. All five network databases support tunnels; SQLite uses local files. Database passwords, SSH passwords and key passphrases have separate encrypted local entries. MongoDB tunnels connect to one target without replica discovery or failover.

Passwords persist locally with Fernet encryption in `metadata.sqlite`, using `credentials.key` in the same application data directory. The app never accesses the system keyring and requires no master password. Re-enter credentials previously saved by the old keyring-based version once. Back up both files together; a user who can read both can decrypt credentials. Query history may contain sensitive literals and can be cleared. Builds are unsigned distribution artifacts; macOS builds are not notarized. An earlier local regression passed **618 tests in 52.10 seconds**, including **73 live MySQL, Redis, MongoDB and native Qt integration tests** over direct connections and real SSH forwards. It also covers build directory isolation, port input, local credential recovery, connection type selection, sidebar navigation, Redis key search, typed value operations and sidebar database selection. The new frozen macOS app successfully connected to local MySQL through its connection dialog. An additional **36 Redis tests passed in 8.75 seconds** against Redis 5.0.14, with password-only native Qt and SSH workflows also verified. See [verification](docs/VERIFICATION.md) and [live testing results](docs/LIVE_TESTING.md).

The global performance fixes passed **970 local tests in 63.38 seconds**, including **101 live database integration cases**, with zero system credential import attempts. Redis batches keep every loaded key with a limit of 500 per load. Grid selection/rendering, SQL batch deletion, history and SSH cleanup were also improved. See the [performance audit](docs/PERFORMANCE.md) for comparisons and scope, and [verification](docs/VERIFICATION.md) for builds and three-platform CI results.

Earlier live PostgreSQL 18.6 and Oracle AI Database 26ai Free 23.26.3 validation passed **9 tests each**. Coverage includes typed CRUD, literal search and pagination, views and composite keys, read-only transactions and rollback history, result limits, native Qt table tabs, precise numeric and date entry, and real loopback SSH forwarding. After the user repaired MongoDB, **16 MongoDB 8.3.8 tests passed**: 9 service / CRUD, 3 Qt, 2 real SSH and 2 collection-tab cases. These historical results do not confirm current service availability. Database-server TLS for PostgreSQL / Oracle was not validated, and Windows / Linux artifacts were neither built nor tested here.

Redis usernames are optional for password-only authentication. RESP2 is selected explicitly for compatibility with legacy servers and modern ACL users, including through SSH. Redis errors that echo the connection password are redacted before reaching the UI or operation history.

If `CONFIG` cannot report the database count, for example because of ACL permissions, the tree offers DB0–DB15 plus reported and currently selected databases. Use **Choose another DB…** to enter an index; the server validates availability. Unknown key counts are omitted. A detected Redis Cluster offers DB0 only.

Set **Key separator** in a Redis connection to group keys into folders. The default is `:`; multiple characters are supported, and an empty separator shows a flat list. Leaves retain complete original key identifiers; binary or special text uses safe display markers, and long labels may be shortened. Folders expand and collapse locally without rescanning. Folder counts cover only loaded descendant keys. Search keeps matching ancestors visible; each DB retains its folder expansion state across refreshes.

Redis key search appears as the first row inside the active expanded DB branch and searches only that DB. It supports only **Prefix** and **Exact**. Input and mode are kept separately for each connection and DB. Editing, clearing, or changing the mode only updates a draft; click the search button beside the input or press Enter to submit it to Redis. Refresh uses the last submitted filter, and continuation pauses while a changed draft is pending. Opening a key preserves the sidebar scroll position. Prefix matches a literal prefix; Exact checks the literal full key with `EXISTS`. Characters such as `*`, `?` and `[]` are treated literally. Key search is case sensitive and preserves spaces. Prefix uses server `SCAN MATCH`, deduplicates matches, then batches `TYPE` requests and builds the tree using the separator. Each load returns up to 500 matching keys; the tree keeps every loaded key. Reopen the DB or click **Load more** to continue. Each batch performs at most eight SCAN calls, so a sparse search may return few or no keys with a continuation still available. Refreshing or submitting a new search restarts the scan.

Redis values have native views for String (raw text), Hash (field/value), List (index/value), Set (members), ZSet (member/score) and read-only Stream entries. The driver preserves bytes and converts only losslessly decoded UTF-8 to text, so binary payloads remain readable without UTF-8 errors. Click **View**, or select a collection row and click **View**, for a read-only preview with **Auto / Text / JSON / MessagePack / BSON / CBOR / gzip / zlib / Hex / Base64**. Auto accepts only complete single objects and recognizes JSON, MessagePack, BSON, CBOR, and complete gzip/zlib streams; compressed values show the decompression and inner-format chain. BSON uses Extended JSON to retain ObjectId, Decimal128, dates and binary values. CBOR retains tags, binary data, duplicate keys and non-string keys. Decoding only affects the preview; it never re-encodes or saves data. Stream previews show the structure and let you choose an individual field value to inspect or export.

The preview can export its original scalar bytes. Decoding and decompression are limited to 1 MiB, display to 200,000 characters, and bounded depth and item counts; notices or Hex fallback appear when limits apply. gzip/zlib must contain one complete stream; trailing data, checksum failures and decompression bombs fall back to Hex. Original scalar exports are not truncated. RedisJSON is read with `JSON.GET`. Unknown module types use a read-only `DUMP` fallback: the exported bytes are Redis serialization with type and checksum data, rather than the original application payload. Private encodings may remain undecodable; Hex and Base64 are available for inspection.

Hash, Set and ZSet support individual additions, edits and deletions; List supports index edits and append, with deletion available only for a complete preview of at most 1,000 items. Binary content and special text that Qt cannot save losslessly cannot be edited as raw text. They remain available for read-only preview, export, and entry or whole-key deletion under the existing rules; editing a ZSet score does not change its member bytes. Ordinary text CRUD preserves the text without automatic number or JSON conversion. Collections still show up to 1,000 items. Entry search filters only that loaded preview using case-insensitive literal substrings. See the [Chinese](docs/redis-batches-zh.png) / [English](docs/redis-batches-en.png) views and [Redis browsing guide](docs/REDIS_TREE.md).

Individual Hash, Set and ZSet mutations and List edits/appends remain available above the preview limit; whole-key replacement and List deletion require the complete value within 1,000 items. Mutations preserve the server's absolute expiration time and reject detected concurrent changes until refreshed. Read-only protection, operation history, Chinese/English switching, direct connections and SSH tunnels apply throughout.

Queued reads, writes and commands stay bound to the DB where they started. Identical key names and pending write locks are separate across DBs, and late results from an earlier DB do not replace the current view. Redis history records the DB index; double-clicking reopens a query in its original database.

With TLS enabled, MySQL validates system certificate trust and the hostname. PostgreSQL uses `sslmode=require` for encrypted transport without server certificate identity verification.
