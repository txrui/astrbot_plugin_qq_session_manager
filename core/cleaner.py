"""数据清理。

按 3.md 第 4.1 节的要求，清理过期会话、过期消息与孤立附件索引。
所有删除都是短事务，不阻塞事件循环。
"""
from __future__ import annotations

import time

try:
    from .repository import Repository
except ImportError:
    from repository import Repository


class Cleaner:
    """定期清理过期与冗余数据。"""

    def __init__(self, repository: Repository):
        self.repository = repository

    def remove_expired_sessions(self) -> int:
        """删除已过期的会话路由及其消息，返回删除的路由数。"""
        conn = self.repository._connection()
        with conn:
            expired = conn.execute(
                "SELECT platform_id, session_id, scene FROM sessions "
                "WHERE expires_at IS NOT NULL AND expires_at < ?",
                (int(time.time()),),
            ).fetchall()
            cur = conn.execute(
                "DELETE FROM sessions WHERE expires_at IS NOT NULL AND expires_at < ?",
                (int(time.time()),),
            )
            for row in expired:
                conn.execute(
                    "DELETE FROM session_messages WHERE platform_id=? AND session_id=? AND scene=?",
                    (row["platform_id"], row["session_id"], row["scene"]),
                )
        return cur.rowcount

    def remove_expired_messages(self, retention_days: int) -> int:
        """删除超过保留期的聊天记录，返回删除行数。"""
        if retention_days <= 0:
            return 0
        cutoff = int(time.time()) - retention_days * 86400
        conn = self.repository._connection()
        with conn:
            cur = conn.execute("DELETE FROM session_messages WHERE timestamp < ?", (cutoff,))
            deleted = cur.rowcount
            # 附件索引随消息级联删除，这里清理不再被任何消息引用的行
            conn.execute(
                "DELETE FROM message_attachments WHERE message_row_id NOT IN "
                "(SELECT id FROM session_messages)"
            )
        return deleted

    def remove_orphan_attachments(self) -> int:
        """清理不再被任何消息引用的附件索引。"""
        conn = self.repository._connection()
        with conn:
            cur = conn.execute(
                "DELETE FROM message_attachments WHERE message_row_id NOT IN "
                "(SELECT id FROM session_messages)"
            )
        return cur.rowcount

    def cleanup(self, *, message_retention_days: int = 0) -> dict[str, int]:
        """执行一次完整清理，返回各步骤删除的行数。"""
        return {
            "expired_sessions": self.remove_expired_sessions(),
            "expired_messages": self.remove_expired_messages(message_retention_days),
            "orphan_attachments": self.remove_orphan_attachments(),
        }
