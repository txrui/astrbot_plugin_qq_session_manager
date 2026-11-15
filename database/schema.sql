-- QQ 会话与聊天记录管理器 —— 数据库 schema v2
--
-- 本文件是 schema 的唯一事实来源，Repository.initialize() 直接加载它，
-- 避免 DDL 在代码与 SQL 文件之间出现漂移。
--
-- 迁移版本记录在 schema_migrations 表中。
--
-- v2 变更：sessions 表新增 group_name 列，用于展示可读的群名称
-- （QQ 官方群消息负载不带群名，需反查 /v2/groups/{group_openid}/info）。

PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS schema_migrations(
  version INTEGER PRIMARY KEY,
  applied_at INTEGER NOT NULL
);

-- 平台实例：避免多个 QQ Bot 之间串线
CREATE TABLE IF NOT EXISTS platform_instances(
  platform_id TEXT PRIMARY KEY,
  bot_id TEXT,
  bot_name TEXT,
  enabled INTEGER NOT NULL DEFAULT 1,
  last_connected_at INTEGER,
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL
);

-- 会话路由：用于适配器缓存缺失时恢复发送目标
-- session_id 与 target_id 必须分开保存：
-- 前者是 AstrBot 会话 ID，后者是 QQ 原生真实目标 ID，二者在 unique_session
-- 等场景下并不相同。
CREATE TABLE IF NOT EXISTS sessions(
  id INTEGER PRIMARY KEY,
  platform_id TEXT NOT NULL,
  bot_id TEXT,
  platform TEXT NOT NULL,
  session_id TEXT NOT NULL,
  normalized_session_id TEXT NOT NULL,
  target_id TEXT NOT NULL,
  scene TEXT NOT NULL CHECK(scene IN ('friend','group','channel')),
  message_type TEXT,
  last_message_id TEXT,
  last_active_at INTEGER NOT NULL,
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL,
  expires_at INTEGER,
  status INTEGER NOT NULL DEFAULT 1,
  id_normalization_rule TEXT,
  -- 群名称（QQ 官方群消息负载不带群名，由 OpenAPI 反查后回填）
  group_name TEXT,
  UNIQUE(platform_id, session_id, scene, target_id)
);

CREATE INDEX IF NOT EXISTS idx_sessions_target ON sessions(platform_id, target_id, scene);
CREATE INDEX IF NOT EXISTS idx_sessions_active ON sessions(last_active_at);
CREATE INDEX IF NOT EXISTS idx_sessions_expire ON sessions(expires_at);
CREATE INDEX IF NOT EXISTS idx_sessions_normalized ON sessions(platform_id, normalized_session_id, scene);

-- 完整聊天记录
CREATE TABLE IF NOT EXISTS session_messages(
  id INTEGER PRIMARY KEY,
  platform_id TEXT NOT NULL,
  session_id TEXT NOT NULL,
  target_id TEXT,
  scene TEXT,
  message_id TEXT,
  sender_id TEXT,
  sender_name TEXT,
  direction TEXT NOT NULL CHECK(direction IN ('inbound','outbound')),
  message_type TEXT,
  content TEXT,
  raw_message_json TEXT,
  timestamp INTEGER NOT NULL,
  imported INTEGER NOT NULL DEFAULT 0,
  created_at INTEGER NOT NULL
);

-- 去重键：同一平台实例 + 同一目标下，QQ 消息 ID 唯一
CREATE UNIQUE INDEX IF NOT EXISTS idx_message_dedup
  ON session_messages(platform_id, target_id, message_id)
  WHERE message_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_messages_session ON session_messages(platform_id, session_id, timestamp);
CREATE INDEX IF NOT EXISTS idx_messages_timestamp ON session_messages(timestamp);

-- 附件索引：只存元数据与归档状态，不把大文件塞进 SQLite
CREATE TABLE IF NOT EXISTS message_attachments(
  id INTEGER PRIMARY KEY,
  message_row_id INTEGER NOT NULL REFERENCES session_messages(id) ON DELETE CASCADE,
  attachment_type TEXT NOT NULL,
  file_name TEXT,
  file_path TEXT,
  source_url TEXT,
  file_size INTEGER,
  sha256 TEXT,
  download_status TEXT NOT NULL DEFAULT 'pending',
  created_at INTEGER NOT NULL
);

-- 运行诊断：用于排查路由命中/未命中/恢复失败
CREATE TABLE IF NOT EXISTS diagnostic_events(
  id INTEGER PRIMARY KEY,
  platform_id TEXT,
  category TEXT NOT NULL,
  detail TEXT,
  created_at INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_diagnostic_created ON diagnostic_events(created_at);
