"""群名称反查。

QQ 官方普通群的消息负载只带 ``group_openid``，不带群名；聊天记录页面若只
显示 openid（例如 ``762146F1DBB849090BCC80B6A667253F``）完全无法辨认是哪个群。
群名需要反查 QQ 开放平台的群管理接口：

    GET /v2/groups/{group_openid}/info

该接口属于群管理能力，未开通的 bot 会被拒绝，因此**成功与失败都要进缓存**，
避免每条消息都打一次请求。

本模块只负责"拿到群名"，落库由仓储层负责，采集流程由
:class:`core.session_service.SessionService` 编排。
"""
from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace
from typing import Any

try:
    from astrbot.api import logger
except ImportError:  # 允许在无 AstrBot 宿主的测试环境导入
    logger = None

try:
    from ..adapters.version_4294 import PLATFORM_TYPE
except ImportError:  # 顶层模块方式加载时的回退
    from adapters.version_4294 import PLATFORM_TYPE

__all__ = ["GroupNameResolver", "is_official_group_openid"]

# 群管理接口路径
QQ_OFFICIAL_GROUP_INFO_PATH = "/v2/groups/{group_openid}/info"

# 群名缓存有效期：群改名最多延迟这么久
GROUP_NAME_TTL = 3600
# 网络/服务端故障后的短退避
GROUP_NAME_RETRY_TTL = 300
# 未开通群管理 API 的 bot 会被一直拒，长退避
GROUP_NAME_DENIED_TTL = 86400
# 单次请求超时（秒）
GROUP_NAME_TIMEOUT = 5
# 缓存条目上限
GROUP_NAME_CACHE_MAX = 500

# QQ 官方开放平台的用户/群 openid 为 24-64 位十六进制大写串；
# 频道 ID 是纯数字，不适用群接口。
_OPENID_LENGTH_RANGE = (24, 64)


def is_official_group_openid(value: str) -> bool:
    """判断字符串是否是 QQ 官方开放平台的 openid（而非频道 ID 等）。"""
    text = str(value or "").strip()
    if not text:
        return False
    low, high = _OPENID_LENGTH_RANGE
    if not low <= len(text) <= high:
        return False
    return all(char in "0123456789ABCDEFabcdef" for char in text)


def _log_debug(message: str, *args: Any) -> None:
    if logger is not None:
        try:
            logger.debug(message, *args)
        except Exception:
            pass


class GroupNameResolver:
    """按 ``group_openid`` 反查群名称，带缓存与退避。

    缓存条目形如 ``(群名, 过期时间戳)``；群名为空串表示上一次查询失败，
    处于退避期内，此时不再发请求。
    """

    def __init__(self) -> None:
        # group_openid -> (群名, 过期时间戳)
        self._cache: dict[str, tuple[str, float]] = {}
        # group_openid -> 锁，避免同群消息并发时打出多个重复请求
        self._locks: dict[str, asyncio.Lock] = {}

    # ------------------------------------------------------------- 缓存

    def _cached(self, group_openid: str) -> str | None:
        """返回缓存中的群名；未缓存或已过期返回 None。

        空串代表"上次失败且仍在退避期"，与"未缓存"必须区分：
        前者直接复用，后者才会发起请求。
        """
        entry = self._cache.get(group_openid)
        if not entry:
            return None
        group_name, expire_at = entry
        if expire_at > time.time():
            return group_name
        self._cache.pop(group_openid, None)
        return None

    def _remember(self, group_openid: str, group_name: str, ttl: int) -> None:
        """写入群名缓存（FIFO 淘汰，限制容量）。"""
        if group_openid not in self._cache and len(self._cache) >= GROUP_NAME_CACHE_MAX:
            self._cache.pop(next(iter(self._cache)), None)
        self._cache[group_openid] = (group_name, time.time() + ttl)

    def _lock_for(self, group_openid: str) -> asyncio.Lock:
        """返回该群的查询锁，顺带清掉空闲的旧锁。"""
        lock = self._locks.get(group_openid)
        if lock is not None:
            return lock
        if len(self._locks) >= GROUP_NAME_CACHE_MAX:
            for cached_id, cached_lock in list(self._locks.items()):
                if not cached_lock.locked():
                    self._locks.pop(cached_id, None)
        return self._locks.setdefault(group_openid, asyncio.Lock())

    # ------------------------------------------------------------- 客户端定位

    @staticmethod
    def _platform_client(event: Any) -> Any:
        """返回事件底层的 botpy client。"""
        return getattr(event, "client", None) or getattr(event, "bot", None)

    @staticmethod
    def _platform_type(event: Any) -> str:
        """返回事件所属平台的规范化名称（小写）。"""
        getter = getattr(event, "get_platform_name", None)
        if callable(getter):
            try:
                name = str(getter() or "").strip().lower()
                if name:
                    return name
            except Exception:
                pass
        meta = getattr(event, "platform_meta", None)
        return str(getattr(meta, "name", "") or "").strip().lower()

    @staticmethod
    def _botpy_http(client: Any) -> Any:
        """返回 botpy 已鉴权的 HTTP 客户端；未登录完成时返回 None。

        只在调用时读 ``client.api``：webhook 适配器登录后会整体替换
        ``client.api`` / ``client.http``，提前缓存会拿到未鉴权的旧实例。
        """
        api = getattr(client, "api", None)
        return getattr(api, "_http", None)

    @staticmethod
    def _clients_from_context(context: Any) -> list[Any]:
        """从平台管理器取所有官方 bot 的 botpy client（无事件对象时用）。"""
        clients: list[Any] = []
        manager = getattr(context, "platform_manager", None)
        get_insts = getattr(manager, "get_insts", None)
        if not callable(get_insts):
            return clients

        try:
            platforms = get_insts() or []
        except Exception:
            return clients

        for platform in platforms:
            get_client = getattr(platform, "get_client", None)
            if not callable(get_client):
                continue
            try:
                meta = platform.meta()
                name = str(getattr(meta, "name", "") or "").strip().lower()
                if name != PLATFORM_TYPE:
                    continue
                client = get_client()
            except Exception:
                continue
            if client is not None:
                clients.append(client)
        return clients

    # ------------------------------------------------------------- 查询

    @staticmethod
    def _denied_errors() -> tuple:
        """代表"群管理 API 用不了"的异常类型，命中后长退避。

        延迟导入 botpy：非 QQ 官方部署可能没装，此时无法区分具体的拒绝原因，
        退化为只认 ImportError —— 拿不到 botpy 本身就属于"用不了"。
        """
        try:
            from botpy import errors
        except ImportError:
            return (ImportError,)
        return (
            errors.AuthenticationFailedError,
            errors.ForbiddenError,
            errors.NotFoundError,
        )

    async def _request_group_info(self, client: Any, group_openid: str) -> Any:
        """请求 /v2/groups/{group_openid}/info；拿不到 HTTP 客户端时返回 None。

        botpy 的 ``Route`` 是标准库之外的依赖，缺失时退化为自建的轻量对象，
        只要 botpy 的 ``BotHttp.request`` 接受带 ``url`` 属性的路由即可。
        """
        http = self._botpy_http(client)
        if http is None:
            return None
        route = self._build_route(group_openid)
        return await asyncio.wait_for(http.request(route), timeout=GROUP_NAME_TIMEOUT)

    @staticmethod
    def _build_route(group_openid: str) -> Any:
        """构造请求路由对象；优先用 botpy 的 Route，缺失时用等价实现。"""
        try:
            from botpy.http import Route
        except ImportError:
            pass
        else:
            return Route("GET", QQ_OFFICIAL_GROUP_INFO_PATH, group_openid=group_openid)

        url = QQ_OFFICIAL_GROUP_INFO_PATH.format(group_openid=group_openid)
        return SimpleNamespace(
            method="GET",
            url=f"https://api.sgroup.qq.com{url}",
            path=url,
            params={},
            headers={},
        )

    async def _resolve_with_clients(self, group_openid: str, clients: list[Any]) -> str:
        """逐个 client 查询群名并写入缓存；查不到返回空串。"""
        denied_errors = self._denied_errors()
        denied_only = True

        for client in clients:
            try:
                info = await self._request_group_info(client, group_openid)
            except Exception as exc:
                if isinstance(exc, denied_errors):
                    _log_debug(
                        "[QQ会话管理器] 群 %s 群名查询被拒绝（群管理 API 未开通？）: %s",
                        group_openid,
                        exc,
                    )
                else:
                    denied_only = False
                    _log_debug("[QQ会话管理器] 群 %s 群名查询失败: %s", group_openid, exc)
                continue

            group_name = ""
            if isinstance(info, dict):
                group_name = str(info.get("group_name") or "").strip()
            if group_name:
                self._remember(group_openid, group_name, GROUP_NAME_TTL)
                return group_name

            # 请求通了但没群名（botpy 超时会返回 None），按可重试处理
            denied_only = False

        ttl = GROUP_NAME_DENIED_TTL if denied_only else GROUP_NAME_RETRY_TTL
        self._remember(group_openid, "", ttl)
        return ""

    async def resolve(
        self,
        group_openid: str,
        *,
        event: Any = None,
        context: Any = None,
    ) -> str:
        """查询群名称；不适用或查不到时返回空串。

        Args:
            group_openid: 群 openid（即官方群消息的 group_openid）
            event: 消息事件对象，可为 None
            context: 插件 Context，用于在没有事件对象时找官方 bot 客户端

        Returns:
            群名称，取不到时为空串
        """
        group_openid = str(group_openid or "").strip()
        # 频道消息的 group_id 是纯数字 channel_id，不适用群接口
        if not is_official_group_openid(group_openid):
            return ""
        # 有事件对象时以事件所属平台为准，别拿别的平台的 ID 去查官方接口
        if event is not None and self._platform_type(event) != PLATFORM_TYPE:
            return ""

        cached = self._cached(group_openid)
        if cached is not None:
            return cached

        clients: list[Any] = []
        if event is not None:
            client = self._platform_client(event)
            if client is not None:
                clients.append(client)
        if context is not None:
            for client in self._clients_from_context(context):
                if client not in clients:
                    clients.append(client)
        if not clients:
            return ""

        async with self._lock_for(group_openid):
            # 等锁期间可能已被同群的其他消息填好
            cached = self._cached(group_openid)
            if cached is not None:
                return cached
            return await self._resolve_with_clients(group_openid, clients)

    def peek(self, group_openid: str) -> str:
        """只读缓存，不发起请求；未缓存时返回空串。"""
        cached = self._cached(str(group_openid or "").strip())
        return cached or ""
