# QQ 会话与聊天记录管理器

持久化 QQ 官方适配器的会话路由，并采集基础聊天记录。

插件目录：`astrbot_plugin_qq_session_manager`，实现计划见仓库根目录 `3.md`。

## 解决的问题

QQ 官方适配器把会话路由保存在**实例内存**中（`_session_last_message_id` 与
`_session_scene` 两个字典）。AstrBot 重启或适配器重载后缓存清空，主动发送就会
被跳过，真实日志表现为：

```text
[QQOfficial] No cached msg_id for session: 762146F1DBB849090BCC80B6A667253F, skip send_by_session
```

本插件把入站消息中出现过的会话路由与最近消息 ID 持久化到 SQLite，并在适配器
缓存缺失时恢复它们。

## 当前版本（0.4.0）

已实现：

- 基于标准库 `sqlite3` 的仓储层，启用 WAL、外键、线程专属连接与短事务。
- 采集 QQ 官方私聊、普通群、频道事件。
- 独立保存 `platform_id`（平台实例 ID）、`session_id`、`normalized_session_id`、
  `target_id` 与 `scene`，不做跨场景推断。
- **群名称自动识别**：QQ 官方群消息负载只带 `group_openid`，插件会反查
  `/v2/groups/{group_openid}/info` 取得可读群名并持久化，视图里直接显示群名
  而不是一串 openid。
- 消息去重（按 `platform_id + target_id + message_id` 唯一索引）。
- 路由查询的明确错误分类：`RouteNotFound` / `RouteExpired` / `RouteDisabled` /
  `AmbiguousSessionRoute` / `UnsupportedScene`。
- 多 Bot 实例隔离：所有查询都带 `platform_id`。
- 面向 `4.29.4` 的适配器兼容层：包装 `_send_by_session_common()`，仅在缓存缺失
  时恢复场景与最近消息 ID，其余行为完全透传。
- AstrBot 官方插件视图：查看运行概览、搜索会话路由、浏览聊天记录、启用/禁用/
  删除路由、回填群名称，以及清理过期数据。

尚未实现（按 `3.md` 的阶段划分排在后续版本）：

- 出站消息记录与附件归档。
- 导入 / 导出 / 备份。
- AstrBot 历史数据库迁移。
- AstrBot 历史数据库迁移。

> 保存消息 ID **不等于** QQ 永久允许主动发送。真实发送权限始终由 QQ 平台决定。

## 兼容性

| 项目 | 值 |
|---|---|
| AstrBot 版本 | `>=4.29.4,<4.30.0` |
| 平台类型 | `qq_official`（QQ 官方 WebSocket 适配器） |
| 场景 | `friend` / `group` / `channel` |
| 外部依赖 | 无（仅使用 Python 标准库） |

## 数据目录

```text
astrbot_plugin_qq_session_manager/
└── sessions.db
```

## 适配器兼容层的工作方式

1. `initialize()` 时通过 `context.platform_manager` 枚举平台实例，只挑选类型为
   `qq_official` 且类路径匹配真实适配器的实例。
2. 逐个检查结构兼容性：`_send_by_session_common` 存在、签名为
   `(session, message_chain)`、是协程函数、且实例上有两个缓存字典。
3. 全部通过才安装实例级包装；任一不通过则**不安装**，插件保持采集与查询能力
   并在日志中说明降级原因。
4. 包装器只在 `session_id` 未同时命中场景缓存与消息 ID 缓存时查库；
   缓存命中时不产生任何额外查询。
5. `terminate()` 恢复所有被包装的原始方法。

失败场景一律不伪造发送成功：

- 路由不存在 → 记录诊断并保持适配器原有行为（可用 `route_missing_behavior=raise`
  改为抛出异常）。
- 路由过期 / 被禁用 → 同上。
- 多条路由同时匹配 → 抛 `AmbiguousSessionRoute`，不"取最新一条"。

## 群名称自动识别

QQ 官方普通群的消息负载只带 `group_openid`（一串 32 位十六进制），不带群名。
聊天记录页面若只显示 openid（例如 `762146F1DBB849090BCC80B6A667253F`），
人根本认不出是哪个群。因此插件会反查 QQ 开放平台的群管理接口取得群名：

```text
GET /v2/groups/{group_openid}/info  ->  { "group_name": "小瑞", ... }
```

行为要点：

- **按需触发**：只有普通群（`scene='group'`）才反查；私聊与频道不适用。
- **结果持久化**：群名写入 `sessions.group_name`，视图直接读库，不会每次刷新
  都重新请求。
- **带缓存与退避**：成功结果缓存 1 小时（群改名最多延迟这么久）；网络故障
  退避 5 分钟；未开通群管理 API 的 bot 会被拒绝，退避 24 小时。成功与失败
  都进缓存，避免每条消息打一次请求。
- **查不到就退回群号**：标题显示 openid，页面仍然可用，不会报错。
- **历史数据可回填**：旧会话的 `group_name` 为空，在视图里点「⟳」按钮即可
  批量反查回填。

> 群管理接口属于 QQ 开放平台的**群管理能力**，未给 bot 开通该权限的部署
> 始终查不到群名，页面会一直显示群号。这是权限限制，不是插件故障。

## 配置项

| 配置项 | 默认值 | 说明 |
|---|---:|---|
| `enable_collection` | `true` | 采集 QQ 入站消息 |
| `enable_route_recovery` | `true` | 启用路由恢复 |
| `enable_outbound_recording` | `true` | 记录发送成功的出站消息 |
| `support_friend` | `true` | 支持私聊 |
| `support_group` | `true` | 支持普通群 |
| `support_channel` | `true` | 支持频道 |
| `session_retention_days` | `180` | 路由保留天数 |
| `message_retention_days` | `0` | 消息保留天数（0 = 不清理） |
| `store_raw_message_json` | `true` | 保存原始消息结构 |
| `route_missing_behavior` | `diagnose` | 路由缺失时 `diagnose` 或 `raise` |
| `disable_patch_on_error` | `true` | 连续异常时自动降级 |
| `max_route_restore_retries` | `1` | 单次发送的恢复重试次数 |
| `enable_debug_log` | `false` | 输出详细日志 |

## 测试

插件不依赖 pytest 即可运行自带用例：

```bash
cd plugins/astrbot_plugin_qq_session_manager
python3 tests/run_tests.py
```

若环境中有 pytest，也可以直接运行：

```bash
python3 -m pytest tests -q
```

测试覆盖：消息规范化（三种场景）、`session_id` 与 `target_id` 分离、会话 ID
规范化规则、去重、重启后路由保留、多 Bot 隔离、`unique_session` 场景、
路由过期 / 禁用 / 歧义 / 缺失的错误分类，以及兼容层的安装、重复安装保护、
卸载恢复、缓存命中不查库、原始返回值与异常透传；另有群名称反查的 openid
判定、缓存命中、失败退避、并发去重、旧库列迁移与历史数据回填。

## 插件视图

AstrBot `4.29+` 中，插件视图入口为：

`WebUI → 插件 → QQ 会话与聊天记录管理器 → 视图 → dashboard`

视图提供：

- 运行概览：路由数量、消息数量、数据库完整性与路由恢复状态。
- 会话路由：按平台实例、场景、状态和关键词筛选，启用/禁用或删除路由。
- 聊天记录：按场景、会话 ID 和正文关键词浏览；页头显示**群名称**，
  副标题显示 `群聊 · 群号 762146F1DBB849090BCC80B6A667253F`。
- 群名称回填：一键为缺失群名的历史会话反查并回填。
- 数据清理：删除过期路由以及超过消息保留期的聊天记录。

后端 API 由 `core/web_view.py` 通过 `context.register_web_api` 注册，前端位于
`views/dashboard/`。页面使用 AstrBot 官方插件页桥接，不要求插件另起 Web 服务。
前端传给桥接 SDK 的 endpoint 使用插件内相对路径（如 `view/status`）；桥接层会自动
拼接插件名前缀并转发到 `/api/v1/plugins/extensions/<插件名>/...`。

## 已知限制

- 仅支持 `qq_official`，不承诺支持 `aiocqhttp`、QQ 官方 Webhook 或其他平台。
- 群名称依赖 QQ 开放平台的群管理权限；未开通该权限的 bot 查不到群名，
  页面会退回显示群号。
- 兼容层依赖适配器内部实现，属于版本敏感的补丁；升级 AstrBot 后必须重新验证。
- 未经真实测试 Bot 验证的功能不会标记为已支持。
- 官方插件视图用于管理与查询；它不能绕过 QQ 平台对主动发送权限的限制。
