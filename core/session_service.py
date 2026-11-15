from __future__ import annotations

import time

from .models import NormalizedMessage, SessionRoute
from .normalizer import normalize_event, normalize_session_id
from .repository import Repository


class SessionService:
    """把规范化消息写入数据库，并提供路由查询入口。"""

    def __init__(self, repository: Repository, config: dict | None = None):
        self.repository = repository
        self.config = config or {}

    def collect(self, event, group_name: str = "") -> NormalizedMessage | None:
        """采集一条入站消息；非目标平台或场景被禁用时返回 None。

        ``group_name`` 是反查到的群名称；为空时保留数据库中原有值，
        不会把已知的群名清空。
        """
        if not self.config.get("enable_collection", True):
            return None
        message = normalize_event(
            event, store_raw=self.config.get("store_raw_message_json", True)
        )
        if message is None:
            return None
        if not self.config.get(f"support_{message.scene}", True):
            return None

        _, rule = normalize_session_id(message.session_id, message.scene)
        message = NormalizedMessage(
            **{**message.__dict__, "id_normalization_rule": rule}
        )

        retention = int(self.config.get("session_retention_days", 180) or 0)
        # 过期时间必须以"采集时刻"为基准，而不是消息时间戳：
        # 消息时间戳来自适配器解析时间，可能是历史时间，
        # 若以其为基准会让新写入的路由立即处于过期状态。
        expires = int(time.time()) + retention * 86400 if retention > 0 else None
        self.repository.upsert_message(
            message, expires_at=expires, group_name=group_name or None
        )
        return message

    def resolve(
        self, platform_id: str, session_id: str, scene: str, target_id: str | None = None
    ) -> SessionRoute:
        return self.repository.find_route(platform_id, session_id, scene, target_id)
