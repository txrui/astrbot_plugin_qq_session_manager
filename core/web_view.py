"""插件视图的 Web API 与管理服务。

官方插件视图通过 ``context.register_web_api`` 暴露接口。本模块只负责：
- 汇总运行状态、数据量和平台实例；
- 分页查询会话路由与聊天记录；
- 启用、禁用或删除路由；
- 触发过期数据清理。

仓储层仍以 :class:`Repository` 为唯一数据库入口，视图层不直接拼接 SQL。
"""
from __future__ import annotations

import time
from typing import Any

try:
    from astrbot.api import logger
    from astrbot.api.web import error_response, json_response, request
except ImportError:  # 允许在无 AstrBot 宿主的测试环境导入管理服务
    logger = None
    error_response = json_response = request = None

try:
    from .cleaner import Cleaner
    from .group_name import GroupNameResolver
    from .repository import Repository
except ImportError:  # 顶层模块方式加载时的回退
    from cleaner import Cleaner
    from group_name import GroupNameResolver
    from repository import Repository

PLUGIN_NAME = "astrbot_plugin_qq_session_manager"
API_PREFIX = f"/{PLUGIN_NAME}/view"

SCENES = {"friend", "group", "channel"}
STATUSES = {"enabled", "disabled", "expired"}


class ManagementService:
    """把仓储能力转换为官方插件视图可消费的数据。"""

    def __init__(
        self,
        repository: Repository,
        config: dict[str, Any] | None = None,
        group_name_resolver: GroupNameResolver | None = None,
    ):
        self.repository = repository
        self.config = dict(config or {})
        self.cleaner = Cleaner(repository)
        self.group_name_resolver = group_name_resolver or GroupNameResolver()

    @staticmethod
    def _int_arg(value: Any, default: int, *, minimum: int, maximum: int) -> int:
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            parsed = default
        return max(minimum, min(parsed, maximum))

    @staticmethod
    def _text_arg(value: Any, default: str = "") -> str:
        return str(value or default).strip()

    def _session_status(self, row: dict[str, Any], now: int) -> str:
        if not int(row.get("status") or 0):
            return "disabled"
        expires_at = row.get("expires_at")
        if expires_at is not None and int(expires_at) < now:
            return "expired"
        return "enabled"

    def _decorate_session(self, row: dict[str, Any], now: int) -> dict[str, Any]:
        item = dict(row)
        item["status_text"] = self._session_status(item, now)
        item["expired"] = item["status_text"] == "expired"
        return item

    def list_sessions(
        self,
        *,
        platform_id: str = "",
        scene: str = "",
        status: str = "",
        keyword: str = "",
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, Any]:
        """查询会话路由。

        分页在 SQL 层完成：先用同一组过滤条件统计总数，再按 offset/limit 取当前页。
        不能改成"先取全表再切片"——那样总数会被行数上限截断，超过上限的会话永远翻不到。
        """
        limit = self._int_arg(limit, 50, minimum=1, maximum=200)
        offset = self._int_arg(offset, 0, minimum=0, maximum=1_000_000)
        now = int(time.time())
        filters = {
            "platform_id": platform_id or None,
            "scene": scene or None,
            "keyword": keyword or None,
        }
        # 状态过滤下推到 SQL：否则分页会先切片再过滤，导致某一页的条数少于 limit，
        # 且翻页会漏掉本来符合条件的会话。
        rows = self.repository.list_sessions(
            limit=limit,
            offset=offset,
            status=None,
            view_status=status or None,
            now=now,
            **filters,
        )
        decorated = [self._decorate_session(row, now) for row in rows]
        total = self.repository.count_sessions(
            status=status or None, now=now, **filters
        )
        return {
            "items": decorated,
            "total": total,
            "limit": limit,
            "offset": offset,
            "has_more": offset + len(decorated) < total,
        }

    def list_messages(
        self,
        *,
        platform_id: str = "",
        scene: str = "",
        session_id: str = "",
        sender_id: str = "",
        keyword: str = "",
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, Any]:
        """分页查询聊天记录。"""
        limit = self._int_arg(limit, 50, minimum=1, maximum=200)
        offset = self._int_arg(offset, 0, minimum=0, maximum=1_000_000)
        items, total = self.repository.list_messages(
            platform_id=platform_id or None,
            scene=scene or None,
            session_id=session_id or None,
            sender_id=sender_id or None,
            keyword=keyword or None,
            limit=limit,
            offset=offset,
            count_total=True,
        )
        return {
            "items": items,
            "total": total,
            "limit": limit,
            "offset": offset,
            "has_more": offset + len(items) < total,
        }

    def set_route_status(
        self, *, platform_id: str, session_id: str, scene: str, enabled: bool
    ) -> bool:
        if enabled:
            return self.repository.enable_route(platform_id, session_id, scene)
        return self.repository.disable_route(platform_id, session_id, scene)

    def delete_route(self, *, platform_id: str, session_id: str, scene: str) -> bool:
        return self.repository.delete_route(platform_id, session_id, scene)

    def cleanup(self) -> dict[str, int]:
        return self.cleaner.cleanup(
            message_retention_days=int(self.config.get("message_retention_days", 0) or 0)
        )

    async def backfill_group_names(
        self, *, context: Any = None, limit: int = 50
    ) -> dict[str, Any]:
        """为缺少群名称的历史群会话路由反查并回填群名。

        反查结果由 :class:`GroupNameResolver` 缓存，因此重复调用不会反复
        请求 QQ 开放平台。查不到的会话保持原样，下次调用时重试。
        """
        rows = self.repository.list_sessions_missing_group_name(limit=limit)
        updated = 0
        failed = 0
        for row in rows:
            group_name = (
                row.get("target_id") or row.get("session_id") or ""
                if row.get("platform") == "txrui"
                else await self.group_name_resolver.resolve(
                    row.get("target_id") or row.get("session_id") or "",
                    context=context,
                )
            )
            if not group_name:
                failed += 1
                continue
            if self.repository.update_group_name(
                row.get("platform_id") or "",
                row.get("session_id") or "",
                row.get("scene") or "group",
                group_name,
            ):
                updated += 1
            else:
                failed += 1
        return {
            "checked": len(rows),
            "updated": updated,
            "failed": failed,
            "remaining": len(
                self.repository.list_sessions_missing_group_name(limit=1000)
            ),
        }

    def status(self) -> dict[str, Any]:
        counts = self.repository.counts()
        return {
            "counts": counts,
            "health": self.repository.health_check(),
            "collection_enabled": bool(self.config.get("enable_collection", True)),
            "route_recovery_enabled": bool(self.config.get("enable_route_recovery", True)),
            "message_retention_days": int(self.config.get("message_retention_days", 0) or 0),
            "session_retention_days": int(self.config.get("session_retention_days", 180) or 0),
        }


class WebViewAPI:
    """向 AstrBot 注册官方插件视图接口。"""

    def __init__(self, plugin: Any, service: ManagementService):
        self.plugin = plugin
        self.service = service

    def register(self) -> None:
        routes = [
            (f"{API_PREFIX}/status", self.get_status, ["GET"], "查询会话管理器运行状态"),
            (f"{API_PREFIX}/sessions", self.get_sessions, ["GET"], "分页查询会话路由"),
            (f"{API_PREFIX}/messages", self.get_messages, ["GET"], "分页查询聊天记录"),
            (f"{API_PREFIX}/route/status", self.post_route_status, ["POST"], "启用或禁用会话路由"),
            (f"{API_PREFIX}/route/delete", self.post_route_delete, ["POST"], "删除会话路由及消息"),
            (f"{API_PREFIX}/cleanup", self.post_cleanup, ["POST"], "清理过期会话和消息"),
            (
                f"{API_PREFIX}/group_names/backfill",
                self.post_group_names_backfill,
                ["POST"],
                "反查并回填缺失的群名称",
            ),
        ]
        for route, handler, methods, description in routes:
            try:
                self.plugin.context.register_web_api(
                    route, handler, methods, description
                )
            except Exception as exc:
                if logger is not None:
                    logger.error(
                        "[QQ会话管理器] 注册视图 API %s 失败: %s", route, exc
                    )

    @staticmethod
    def _query() -> dict[str, Any]:
        if request is None:
            return {}
        try:
            return dict(request.query or {})
        except Exception:
            return {}

    @staticmethod
    async def _body() -> dict[str, Any]:
        if request is None:
            return {}
        try:
            data = await request.json(default={})
        except Exception:
            return {}
        return data if isinstance(data, dict) else {}

    async def get_status(self):
        return json_response(self.service.status())

    async def get_sessions(self):
        query = self._query()
        scene = self.service._text_arg(query.get("scene"))
        status = self.service._text_arg(query.get("status"))
        if scene and scene not in SCENES:
            return error_response("scene 只能是 friend/group/channel")
        if status and status not in STATUSES:
            return error_response("status 只能是 enabled/disabled/expired")
        return json_response(
            self.service.list_sessions(
                platform_id=self.service._text_arg(query.get("platform_id")),
                scene=scene,
                status=status,
                keyword=self.service._text_arg(query.get("keyword")),
                limit=query.get("limit", 50),
                offset=query.get("offset", 0),
            )
        )

    async def get_messages(self):
        query = self._query()
        scene = self.service._text_arg(query.get("scene"))
        if scene and scene not in SCENES:
            return error_response("scene 只能是 friend/group/channel")
        return json_response(
            self.service.list_messages(
                platform_id=self.service._text_arg(query.get("platform_id")),
                scene=scene,
                session_id=self.service._text_arg(query.get("session_id")),
                sender_id=self.service._text_arg(query.get("sender_id")),
                keyword=self.service._text_arg(query.get("keyword")),
                limit=query.get("limit", 50),
                offset=query.get("offset", 0),
            )
        )

    async def post_route_status(self):
        payload = await self._body()
        platform_id = self.service._text_arg(payload.get("platform_id"))
        session_id = self.service._text_arg(payload.get("session_id"))
        scene = self.service._text_arg(payload.get("scene"))
        enabled = bool(payload.get("enabled", True))
        if not platform_id or not session_id:
            return error_response("platform_id 与 session_id 不能为空")
        if scene not in SCENES:
            return error_response("scene 只能是 friend/group/channel")
        changed = self.service.set_route_status(
            platform_id=platform_id,
            session_id=session_id,
            scene=scene,
            enabled=enabled,
        )
        return json_response({"changed": changed, "enabled": enabled})

    async def post_route_delete(self):
        payload = await self._body()
        platform_id = self.service._text_arg(payload.get("platform_id"))
        session_id = self.service._text_arg(payload.get("session_id"))
        scene = self.service._text_arg(payload.get("scene"))
        if not platform_id or not session_id:
            return error_response("platform_id 与 session_id 不能为空")
        if scene not in SCENES:
            return error_response("scene 只能是 friend/group/channel")
        deleted = self.service.delete_route(
            platform_id=platform_id, session_id=session_id, scene=scene
        )
        return json_response({"deleted": deleted})

    async def post_cleanup(self):
        return json_response(self.service.cleanup())

    async def post_group_names_backfill(self):
        """反查并回填缺失的群名称（历史会话路由在首次采集后才会有群名）。"""
        payload = await self._body()
        limit = self.service._int_arg(payload.get("limit", 50), 50, minimum=1, maximum=200)
        context = getattr(self.plugin, "context", None)
        try:
            result = await self.service.backfill_group_names(
                context=context, limit=limit
            )
        except Exception as exc:
            return error_response(f"群名称回填失败: {exc}")
        return json_response(result)
