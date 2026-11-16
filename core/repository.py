from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path
from typing import Iterable

from .models import NormalizedMessage, SessionRoute

SCHEMA_VERSION = 2

class Repository:
    """线程专属连接的 SQLite 仓储；所有 public 方法在当前线程打开连接。"""
    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        self.initialize()
    
    def _connection(self) -> sqlite3.Connection:
        conn = getattr(self._local, "connection", None)
        if conn is None:
            conn = sqlite3.connect(self.db_path, timeout=10)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("PRAGMA busy_timeout=10000")
            self._local.connection = conn
        return conn

    def initialize(self) -> None:
        """加载 schema 并记录迁移版本。

        schema.sql 是 DDL 的唯一事实来源，这里直接加载它，
        避免代码与 SQL 文件之间出现漂移。
        """
        conn = self._connection()
        schema_path = Path(__file__).resolve().parent.parent / "database" / "schema.sql"
        ddl = schema_path.read_text(encoding="utf-8")
        conn.executescript(ddl)
        self._migrate_add_group_name(conn)
        with conn:
            conn.execute(
                "INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES (?, strftime('%s','now'))",
                (SCHEMA_VERSION,),
            )
        conn.commit()

    @staticmethod
    def _migrate_add_group_name(conn: sqlite3.Connection) -> None:
        """为 v1 旧库补上 sessions.group_name 列。

        ``ALTER TABLE ... ADD COLUMN`` 在 SQLite 中不是幂等的，重复执行会报
        "duplicate column name"，因此必须先查列是否存在再决定是否执行。
        新建的库由 schema.sql 的 CREATE TABLE 直接带上该列，不走这里。
        """
        columns = {
            row[1] for row in conn.execute("PRAGMA table_info(sessions)").fetchall()
        }
        if not columns or "group_name" in columns:
            return
        with conn:
            conn.execute("ALTER TABLE sessions ADD COLUMN group_name TEXT")

    def upsert_message(self, message: NormalizedMessage, *, expires_at: int | None = None, group_name: str | None = None) -> int | None:
        """写入一条消息及其会话路由。

        ``group_name`` 为反查到的群名称；为 None 时保持数据库中原有值不变
        （已知的群名不应被后续查不到群名的消息清空）。
        """
        now = int(time.time())
        conn = self._connection()
        try:
            with conn:
                conn.execute("""INSERT INTO sessions(platform_id,bot_id,platform,session_id,normalized_session_id,target_id,scene,message_type,last_message_id,last_active_at,created_at,updated_at,expires_at,status,id_normalization_rule,group_name)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(platform_id,session_id,scene,target_id) DO UPDATE SET bot_id=excluded.bot_id,normalized_session_id=excluded.normalized_session_id,message_type=excluded.message_type,last_message_id=excluded.last_message_id,last_active_at=excluded.last_active_at,updated_at=excluded.updated_at,expires_at=excluded.expires_at,status=1,id_normalization_rule=excluded.id_normalization_rule,group_name=COALESCE(excluded.group_name,sessions.group_name)""",
                (message.platform_id,message.bot_id,message.platform,message.session_id,message.normalized_session_id,message.target_id,message.scene,message.message_type,message.message_id,now,now,now,expires_at,1,message.id_normalization_rule or "identity",group_name))
                cur = conn.execute("""INSERT OR IGNORE INTO session_messages(platform_id,session_id,target_id,scene,message_id,sender_id,sender_name,direction,message_type,content,raw_message_json,timestamp,imported,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (message.platform_id,message.session_id,message.target_id,message.scene,message.message_id,message.sender_id,message.sender_name,message.direction,message.message_type,message.content,message.raw_message_json,message.timestamp,message.imported,now))
                # INSERT OR IGNORE 命中唯一索引时 rowcount 为 0，lastrowid 会保留上一次插入的值，必须以 rowcount 判定是否真的写入
                if cur.rowcount == 0:
                    return None
                return cur.lastrowid or None
        except sqlite3.IntegrityError:
            return None

    def update_group_name(self, platform_id: str, session_id: str, scene: str, group_name: str) -> bool:
        """回填会话路由的群名称；返回是否确实改动了一行。"""
        group_name = str(group_name or "").strip()
        if not group_name:
            return False
        conn = self._connection()
        with conn:
            cur = conn.execute(
                "UPDATE sessions SET group_name=?, updated_at=? WHERE platform_id=? AND session_id=? AND scene=?",
                (group_name, int(time.time()), platform_id, session_id, scene),
            )
        return cur.rowcount > 0

    def list_sessions_missing_group_name(self, limit: int = 100) -> list[dict]:
        """列出还没有群名称的群会话路由，用于历史数据回填。

        只返回 ``scene='group'`` 且 ``group_name`` 为空的行；私聊与频道
        没有群名概念。
        """
        rows = self._connection().execute(
            "SELECT platform_id, session_id, target_id, scene FROM sessions "
            "WHERE scene='group' AND (group_name IS NULL OR group_name='') "
            "ORDER BY last_active_at DESC LIMIT ?",
            (max(1, min(int(limit), 1000)),),
        ).fetchall()
        return [dict(row) for row in rows]

    def find_route(self, platform_id: str, session_id: str, scene: str, target_id: str | None = None) -> SessionRoute:
        """按平台实例 + 会话 ID + 场景查询可用路由。

        分两级查询（3.md 第 20.3 节）：
        1. 先精确匹配 ``session_id``；
        2. 没有精确命中时，才按 ``normalized_session_id`` 匹配。

        这样可以避免原始 ID 与规范化 ID 同时存在时被误判为歧义。
        命中多条时抛歧义错误，不做"取最新一条"的隐式选择。
        """
        from .exceptions import AmbiguousSessionRoute, RouteDisabled, RouteExpired, RouteNotFound

        conn = self._connection()
        now = int(time.time())

        target_clause = ""
        target_args: list[object] = []
        if target_id is not None:
            target_clause = " AND target_id=?"
            target_args.append(target_id)

        def _query(column: str, status: int | None) -> list:
            query = (
                f"SELECT * FROM sessions WHERE platform_id=? AND {column}=? AND scene=?"
                + target_clause
            )
            args: list[object] = [platform_id, session_id, scene] + target_args
            if status is not None:
                query += " AND status=?"
                args.append(status)
            return conn.execute(query, args).fetchall()

        for column in ("session_id", "normalized_session_id"):
            rows = _query(column, 1)
            if len(rows) > 1:
                raise AmbiguousSessionRoute(
                    f"匹配到多个会话路由: platform={platform_id} scene={scene} session={session_id}"
                )
            if not rows:
                continue

            row = rows[0]
            if row["expires_at"] is not None and row["expires_at"] < now:
                raise RouteExpired(
                    f"会话路由已过期: platform={platform_id} scene={scene} session={session_id}"
                )
            return SessionRoute(
                **{k: row[k] for k in SessionRoute.__dataclass_fields__ if k in row.keys()}
            )

        # 没有可用路由时，区分"被禁用"和"确实不存在"以便诊断
        for column in ("session_id", "normalized_session_id"):
            if _query(column, 0):
                raise RouteDisabled(
                    f"会话路由已被管理员禁用: platform={platform_id} scene={scene} session={session_id}"
                )
        raise RouteNotFound(f"未找到会话路由: platform={platform_id} scene={scene} session={session_id}")

    def list_sessions(
        self,
        limit: int = 100,
        offset: int = 0,
        *,
        platform_id: str | None = None,
        scene: str | None = None,
        status: int | None = None,
        keyword: str | None = None,
        view_status: str | None = None,
        now: int | None = None,
    ) -> list[dict]:
        """分页查询会话路由。

        ``platform_id``、``scene``、``status`` 与 ``keyword`` 可选。未传过滤条件时
        保持原有行为，供仓储层测试和内部调用直接使用。

        ``view_status`` 是视图层语义（``enabled`` / ``disabled`` / ``expired``），
        与数据库列 ``status``（0/1）不是一回事，因此单独提供。
        """
        query = "SELECT * FROM sessions WHERE 1=1"
        args: list[object] = []
        if platform_id:
            query += " AND platform_id=?"
            args.append(platform_id)
        if scene:
            query += " AND scene=?"
            args.append(scene)
        if status is not None:
            query += " AND status=?"
            args.append(int(status))
        if view_status == "disabled":
            query += " AND status=0"
        elif view_status == "enabled":
            query += " AND status=1 AND (expires_at IS NULL OR expires_at >= ?)"
            args.append(int(now if now is not None else time.time()))
        elif view_status == "expired":
            query += " AND expires_at IS NOT NULL AND expires_at < ?"
            args.append(int(now if now is not None else time.time()))
        if keyword:
            query += (
                " AND (platform_id LIKE ? OR bot_id LIKE ? OR session_id LIKE ?"
                " OR normalized_session_id LIKE ? OR target_id LIKE ? OR group_name LIKE ?)"
            )
            needle = f"%{keyword}%"
            args.extend([needle] * 6)
        query += " ORDER BY last_active_at DESC LIMIT ? OFFSET ?"
        args.extend([max(1, min(limit, 1000)), max(0, offset)])
        rows = self._connection().execute(query, args).fetchall()
        return [dict(row) for row in rows]

    def count_sessions(
        self,
        *,
        platform_id: str | None = None,
        scene: str | None = None,
        status: str | None = None,
        now: int | None = None,
        keyword: str | None = None,
    ) -> int:
        """统计会话路由总数，供分页显示准确的总数。

        ``status`` 接受 ``enabled`` / ``disabled`` / ``expired`` 三种视图状态。
        ``expired`` 不能直接写成等值条件，因为它由 ``expires_at`` 与当前时间比较得出。
        """
        query = "SELECT count(*) FROM sessions WHERE 1=1"
        args: list[object] = []
        if platform_id:
            query += " AND platform_id=?"
            args.append(platform_id)
        if scene:
            query += " AND scene=?"
            args.append(scene)
        if keyword:
            query += (
                " AND (platform_id LIKE ? OR bot_id LIKE ? OR session_id LIKE ?"
                " OR normalized_session_id LIKE ? OR target_id LIKE ? OR group_name LIKE ?)"
            )
            needle = f"%{keyword}%"
            args.extend([needle] * 6)
        if status == "disabled":
            query += " AND status=0"
        elif status == "enabled":
            query += " AND status=1 AND (expires_at IS NULL OR expires_at >= ?)"
            args.append(int(now if now is not None else time.time()))
        elif status == "expired":
            query += " AND expires_at IS NOT NULL AND expires_at < ?"
            args.append(int(now if now is not None else time.time()))
        return int(self._connection().execute(query, args).fetchone()[0])

    def disable_route(self, platform_id: str, session_id: str, scene: str) -> bool:
        """禁用一条路由；返回是否确实改动了一行。"""
        conn = self._connection()
        with conn:
            cur = conn.execute(
                "UPDATE sessions SET status=0, updated_at=? WHERE platform_id=? AND session_id=? AND scene=?",
                (int(time.time()), platform_id, session_id, scene),
            )
        return cur.rowcount > 0

    def enable_route(self, platform_id: str, session_id: str, scene: str) -> bool:
        """重新启用一条路由；返回是否确实改动了一行。"""
        conn = self._connection()
        with conn:
            cur = conn.execute(
                "UPDATE sessions SET status=1, updated_at=? WHERE platform_id=? AND session_id=? AND scene=?",
                (int(time.time()), platform_id, session_id, scene),
            )
        return cur.rowcount > 0

    def delete_route(self, platform_id: str, session_id: str, scene: str) -> bool:
        """删除一条路由及其消息；返回是否确实删除了路由。"""
        conn = self._connection()
        with conn:
            cur = conn.execute(
                "DELETE FROM sessions WHERE platform_id=? AND session_id=? AND scene=?",
                (platform_id, session_id, scene),
            )
            deleted = cur.rowcount > 0
            conn.execute(
                "DELETE FROM session_messages WHERE platform_id=? AND session_id=? AND scene=?",
                (platform_id, session_id, scene),
            )
        return deleted

    def list_messages(
        self,
        *,
        platform_id: str | None = None,
        scene: str | None = None,
        session_id: str | None = None,
        target_id: str | None = None,
        sender_id: str | None = None,
        keyword: str | None = None,
        since: int | None = None,
        until: int | None = None,
        limit: int = 50,
        offset: int = 0,
        count_total: bool = False,
    ) -> list[dict] | tuple[list[dict], int]:
        """分页查询消息记录。禁止一次性读取全部数据。

        设置 ``count_total`` 时返回 ``(消息列表, 准确总数)``；普通调用不执行
        额外计数查询。
        """
        query = "SELECT * FROM session_messages WHERE 1=1"
        args: list[object] = []
        if platform_id:
            query += " AND platform_id=?"
            args.append(platform_id)
        if scene:
            query += " AND scene=?"
            args.append(scene)
        if session_id:
            query += " AND session_id=?"
            args.append(session_id)
        if target_id:
            query += " AND target_id=?"
            args.append(target_id)
        if sender_id:
            query += " AND sender_id=?"
            args.append(sender_id)
        if keyword:
            query += " AND content LIKE ?"
            args.append(f"%{keyword}%")
        if since is not None:
            query += " AND timestamp>=?"
            args.append(int(since))
        if until is not None:
            query += " AND timestamp<=?"
            args.append(int(until))
        query += " ORDER BY timestamp DESC, id DESC LIMIT ? OFFSET ?"
        args.extend([max(1, min(limit, 500)), max(0, offset)])
        rows = self._connection().execute(query, args).fetchall()
        items = [dict(row) for row in rows]
        if not count_total:
            return items
        count_sql = "SELECT count(*) FROM session_messages WHERE 1=1"
        count_args: list[object] = []
        if platform_id:
            count_sql += " AND platform_id=?"
            count_args.append(platform_id)
        if scene:
            count_sql += " AND scene=?"
            count_args.append(scene)
        if session_id:
            count_sql += " AND session_id=?"
            count_args.append(session_id)
        if target_id:
            count_sql += " AND target_id=?"
            count_args.append(target_id)
        if sender_id:
            count_sql += " AND sender_id=?"
            count_args.append(sender_id)
        if keyword:
            count_sql += " AND content LIKE ?"
            count_args.append(f"%{keyword}%")
        if since is not None:
            count_sql += " AND timestamp>=?"
            count_args.append(int(since))
        if until is not None:
            count_sql += " AND timestamp<=?"
            count_args.append(int(until))
        total = self._connection().execute(count_sql, count_args).fetchone()[0]
        return items, int(total)

    def counts(self) -> dict[str, int]:
        conn = self._connection()
        return {"sessions": conn.execute("SELECT count(*) FROM sessions").fetchone()[0], "messages": conn.execute("SELECT count(*) FROM session_messages").fetchone()[0]}

    def diagnostic(self, platform_id: str | None, category: str, detail: str = "") -> None:
        with self._connection(): self._connection().execute("INSERT INTO diagnostic_events(platform_id,category,detail,created_at) VALUES(?,?,?,?)", (platform_id,category,detail[:1000],int(time.time())))

    def health_check(self) -> dict[str, object]:
        conn = self._connection(); result = conn.execute("PRAGMA integrity_check").fetchone()[0]
        version = conn.execute("SELECT max(version) FROM schema_migrations").fetchone()[0] or 0
        return {"integrity": result, "schema_version": version, "wal": True}

    def close(self) -> None:
        conn = getattr(self._local, "connection", None)
        if conn is not None: conn.close(); self._local.connection = None
