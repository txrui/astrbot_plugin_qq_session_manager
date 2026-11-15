"""QQ 官方适配器兼容层。

只在确认目标实例确实是 AstrBot 4.29.4 的 QQ 官方适配器、且方法签名符合约定时
才安装包装。包装器只在内存缓存缺失时从数据库恢复路由，其余行为完全透传。
"""
from __future__ import annotations

import inspect
from functools import wraps
from typing import Any

try:  # 以包方式加载时使用包内相对导入
    from ..core.exceptions import (
        AdapterCompatibilityError,
        AmbiguousSessionRoute,
        RouteDisabled,
        RouteExpired,
        RouteNotFound,
    )
except ImportError:  # 以顶层模块方式加载时回退到绝对导入
    from core.exceptions import (
        AdapterCompatibilityError,
        AmbiguousSessionRoute,
        RouteDisabled,
        RouteExpired,
        RouteNotFound,
    )

try:
    from .version_4294 import ADAPTER_CLASS_PATH, SEND_METHOD
except ImportError:
    from version_4294 import ADAPTER_CLASS_PATH, SEND_METHOD

# 适配器真实签名：async def _send_by_session_common(self, session, message_chain)
EXPECTED_PARAMS = ("session", "message_chain")

# 需要被恢复的实例级缓存字段
CACHE_FIELDS = ("_session_last_message_id", "_session_scene")


class CompatibilityManager:
    """安装/恢复 QQ 官方适配器发送方法的路由恢复包装。"""

    def __init__(self, resolver, logger=None, config: dict | None = None):
        self.resolver = resolver
        self.logger = logger
        self.config = config or {}
        self._originals: dict[int, Any] = {}
        self._adapters: dict[int, Any] = {}
        self.status = "unchecked"
        self.reason = ""
        self._error_streak = 0

    # ------------------------------------------------------------------ 工具

    def _log(self, level: str, message: str, *args) -> None:
        if self.logger is None:
            return
        getattr(self.logger, level, self.logger.info)("[QQ会话管理器] " + message, *args)

    @staticmethod
    def _class_path(adapter) -> str:
        cls = type(adapter)
        return f"{cls.__module__}.{cls.__name__}"

    def _degrade(self, reason: str) -> bool:
        self.status = "degraded"
        self.reason = reason
        self._log("warning", "兼容层降级: %s", reason)
        return False

    # -------------------------------------------------------------- 安装流程

    def check_compatibility(self, adapter) -> bool:
        """确认目标适配器结构符合 4.29.4 约定。"""
        if self._class_path(adapter) != ADAPTER_CLASS_PATH:
            return self._degrade(
                f"适配器类型不匹配: {self._class_path(adapter)} != {ADAPTER_CLASS_PATH}"
            )

        method = getattr(adapter, SEND_METHOD, None)
        if method is None or not callable(method):
            return self._degrade(f"缺少 {SEND_METHOD} 方法")

        for field in CACHE_FIELDS:
            if not hasattr(adapter, field):
                return self._degrade(f"缺少会话缓存字段 {field}")

        try:
            signature = inspect.signature(method)
        except (TypeError, ValueError):
            return self._degrade(f"无法读取 {SEND_METHOD} 签名")

        params = [p for p in signature.parameters if p != "self"]
        if params[:2] != list(EXPECTED_PARAMS):
            return self._degrade(
                f"{SEND_METHOD} 签名不兼容: {params[:2]} != {list(EXPECTED_PARAMS)}"
            )

        if not inspect.iscoroutinefunction(method):
            return self._degrade(f"{SEND_METHOD} 不是协程函数")

        return True

    def install(self, adapter) -> bool:
        """为单个适配器实例安装包装；重复安装不会重复包装。"""
        if id(adapter) in self._originals:
            return True

        if self.status == "unchecked":
            if not self.check_compatibility(adapter):
                return False

        original = getattr(adapter, SEND_METHOD)
        manager = self

        @wraps(original)
        async def wrapped(session, message_chain, *args, **kwargs):
            try:
                manager._restore_route(adapter, session)
            except (RouteNotFound, RouteDisabled, RouteExpired, AmbiguousSessionRoute) as exc:
                manager._error_streak += 1
                manager._log("warning", "路由恢复失败: %s", exc)
                if manager.config.get("route_missing_behavior", "diagnose") == "raise":
                    raise
            except Exception as exc:  # noqa: BLE001 - 兼容层异常不得影响原始发送
                manager._error_streak += 1
                manager._log("error", "路由恢复异常: %s", exc)
                if manager.config.get("route_missing_behavior", "diagnose") == "raise":
                    raise
            else:
                manager._error_streak = 0
            return await original(session, message_chain, *args, **kwargs)

        adapter._send_by_session_common = wrapped
        self._originals[id(adapter)] = original
        self._adapters[id(adapter)] = adapter
        self.status = "installed"
        self._log("info", "兼容层已安装于 %s", self._class_path(adapter))
        return True

    def _restore_route(self, adapter, session) -> None:
        """内存缓存缺失时从数据库恢复路由。

        恢复场景缓存和最近消息 ID 两者；只恢复其中一个会让适配器仍然跳过发送。
        该方法不执行 I/O，因此保持同步，避免未 await 的协程导致异常被静默吞掉。
        """
        session_id = getattr(session, "session_id", None)
        if not session_id:
            return

        last_ids = getattr(adapter, "_session_last_message_id", {})
        scenes = getattr(adapter, "_session_scene", {})
        if session_id in last_ids and session_id in scenes:
            return  # 缓存命中，保持适配器原有行为

        platform_id = self._platform_id(adapter)
        scene = scenes.get(session_id)

        if scene is not None:
            # 场景已知：直接按平台实例 + 会话 ID + 场景精确查询
            route = self.resolver.resolve(
                platform_id=platform_id, session_id=session_id, scene=scene
            )
        else:
            # 场景未知：先尝试用消息类型推断私聊，否则在三种场景中查找
            message_type = str(getattr(session, "message_type", "") or "").upper()
            if "FRIEND" in message_type or "PRIVATE" in message_type:
                route = self.resolver.resolve(
                    platform_id=platform_id, session_id=session_id, scene="friend"
                )
            else:
                route = self._lookup_route(platform_id, session_id)

        scenes[route.session_id] = route.scene
        if route.last_message_id:
            last_ids[route.session_id] = route.last_message_id
        self._log(
            "info",
            "已恢复路由: platform=%s scene=%s session=%s",
            platform_id,
            route.scene,
            route.session_id,
        )

    def _lookup_route(self, platform_id: str, session_id: str):
        """会话 ID 已知但场景未知时，在三种场景中依次查找路由。

        只返回第一个可用路由，不做"取最新一条"的隐式选择。
        """
        for scene in ("group", "channel", "friend"):
            try:
                return self.resolver.resolve(
                    platform_id=platform_id, session_id=session_id, scene=scene
                )
            except (RouteNotFound, RouteDisabled, RouteExpired):
                continue
            except AmbiguousSessionRoute:
                raise
        raise RouteNotFound(f"未找到会话路由: platform={platform_id} session={session_id}")

    @staticmethod
    def _platform_id(adapter) -> str:
        config = getattr(adapter, "config", None)
        if isinstance(config, dict):
            value = config.get("id")
            if value:
                return str(value)
        meta = getattr(adapter, "meta", None)
        if callable(meta):
            try:
                name = getattr(meta(), "name", None)
                if name:
                    return str(name)
            except Exception:
                pass
        return "qq_official"

    # -------------------------------------------------------------- 卸载流程

    def uninstall(self) -> None:
        """恢复所有被包装的原始方法。"""
        for key, original in list(self._originals.items()):
            adapter = self._adapters.get(key)
            if adapter is not None:
                try:
                    setattr(adapter, SEND_METHOD, original)
                except Exception as exc:  # noqa: BLE001
                    self._log("error", "恢复原始方法失败: %s", exc)
                    self.status = "safe"
                    self.reason = f"恢复原始方法失败: {exc}"
        self._originals.clear()
        self._adapters.clear()
        if self.status != "safe":
            self.status = "restored"
            self.reason = ""
        self._log("info", "兼容层已卸载，原始方法已恢复")
