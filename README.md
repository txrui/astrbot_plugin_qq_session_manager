# QQ 会话与聊天记录管理器

持久化 QQ 官方适配器的会话路由和聊天记录，并在适配器缓存丢失时尝试恢复路由。

## 功能

- 采集 QQ 官方私聊、群聊和频道消息，可按配置启用或停用。
- 将会话路由与消息保存到插件目录下的 SQLite 数据库，支持重启后查询。
- 缓存缺失时恢复 QQ 官方适配器所需的会话场景和最近消息 ID。
- 在支持的情况下查询并保存 QQ 群名称；也可在插件视图中回填历史群名。
- 在 AstrBot 插件视图中查询路由和聊天记录、管理路由及清理过期数据。
- 支持 txrui 微信实例采集私聊和群聊记录。txrui 需单独安装并配置；微信记录按平台实例隔离。

路由记录只能帮助恢复适配器所需的信息，不能绕过 QQ 的主动发送权限。发送时仍要求相应平台和客户端在线。

## 兼容性

- AstrBot：`>=4.29.4,<4.30.0`
- QQ 平台：`qq_official`，支持私聊、群聊和频道
- txrui：支持私聊和群聊采集；不适用 QQ 路由恢复补丁
- 外部 Python 依赖：无

## 配置

| 配置项 | 默认值 | 说明 |
|---|---:|---|
| `enable_collection` | `true` | 采集入站消息 |
| `enable_route_recovery` | `true` | 启用 QQ 路由恢复 |
| `enable_outbound_recording` | `true` | 记录成功发送的出站消息 |
| `support_friend` | `true` | 处理私聊 |
| `support_group` | `true` | 处理普通群 |
| `support_channel` | `true` | 处理频道 |
| `session_retention_days` | `180` | 路由保留天数 |
| `message_retention_days` | `0` | 消息保留天数；`0` 表示不清理 |
| `store_raw_message_json` | `true` | 保存原始消息结构 |
| `route_missing_behavior` | `diagnose` | 路由缺失时记录诊断，或设为 `raise` 抛出异常 |
| `disable_patch_on_error` | `true` | 连续异常时停用恢复补丁 |
| `max_route_restore_retries` | `1` | 单次发送的恢复重试次数 |
| `enable_debug_log` | `false` | 输出详细日志 |

## 插件视图

入口：`WebUI → 插件 → QQ 会话与聊天记录管理器 → 视图 → dashboard`

可查看和搜索会话路由、浏览聊天记录、管理路由、回填群名称及清理过期数据。群名称查询需要 QQ 开放平台的群管理权限；没有权限时页面会显示群号。

## 数据与测试

数据库文件：`astrbot_plugin_qq_session_manager/sessions.db`。

运行自带测试：

```bash
cd plugins/astrbot_plugin_qq_session_manager
python3 tests/run_tests.py
```

也可使用 pytest：

```bash
python3 -m pytest tests -q
```

## 限制

- 仅承诺支持 `qq_official`；不支持 `aiocqhttp`、QQ 官方 Webhook 等其他 QQ 适配器。
- QQ 主动发送能力受 QQ 平台权限限制，保存路由不代表可以永久主动发送。
- 路由恢复依赖 AstrBot 适配器的内部实现，升级 AstrBot 后应重新验证。
- 媒体附件不归档；聊天记录保存消息正文、占位内容和可选的原始消息数据。
