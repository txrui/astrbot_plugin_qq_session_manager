from __future__ import annotations

from pathlib import Path

import astrbot.api.star as star
from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter

try:
    from .adapters.compatibility import CompatibilityManager
    from .adapters.platform_discovery import (
        describe_instances,
        find_qq_official_instances,
        find_txrui_instances,
    )
    from .core.collector import EventCollector
    from .core.group_name import GroupNameResolver
    from .core.repository import Repository
    from .core.resolver import SessionResolver
    from .core.session_service import SessionService
    from .core.web_view import ManagementService, WebViewAPI
except ImportError:  # 顶层模块方式加载时的回退
    from adapters.compatibility import CompatibilityManager
    from adapters.platform_discovery import describe_instances, find_qq_official_instances, find_txrui_instances
    from core.collector import EventCollector
    from core.group_name import GroupNameResolver
    from core.repository import Repository
    from core.resolver import SessionResolver
    from core.session_service import SessionService
    from core.web_view import ManagementService, WebViewAPI

PLUGIN_NAME = "astrbot_plugin_qq_session_manager"


class QQSessionManagerPlugin(star.Star):
    """QQ 官方会话路由持久化与聊天记录采集插件。

    首版范围（对应 3.md 第 20.1 节）：
    - 入站消息采集（私聊 / 普通群 / 频道）
    - 会话路由持久化与重启后恢复
    - 适配器兼容层（在缓存缺失时恢复场景与最近消息 ID）
    """

    def __init__(self, context: star.Context, config):
        super().__init__(context)
        self.config = dict(config or {})
        self.data_dir = Path(star.StarTools.get_data_dir(PLUGIN_NAME))
        self.repository = Repository(self.data_dir / "sessions.db")
        self.session_service = SessionService(self.repository, self.config)
        self.group_name_resolver = GroupNameResolver()
        self.collector = EventCollector(self.session_service, self.group_name_resolver)
        self.resolver = SessionResolver(self.repository)
        self.compatibility = CompatibilityManager(self.resolver, logger, self.config)
        self.management_service = ManagementService(
            self.repository, self.config, self.group_name_resolver
        )
        self.web_view_api = WebViewAPI(self, self.management_service)
        self.web_view_api.register()
        self._patched_instances: list[str] = []

    # ------------------------------------------------------------- 生命周期

    async def initialize(self) -> None:
        health = self.repository.health_check()
        logger.info(
            "[QQ会话管理器] 已初始化 | 数据目录=%s | 完整性=%s | schema版本=%s",
            self.data_dir,
            health.get("integrity"),
            health.get("schema_version"),
        )

        if not self.config.get("enable_collection", True):
            logger.info("[QQ会话管理器] 入站采集已关闭")

        if not self.config.get("enable_route_recovery", True):
            logger.info("[QQ会话管理器] 路由恢复已关闭，进入降级模式")
            return

        # 插件加载时平台适配器通常还没创建（实测冷启动时相差十几秒），
        # 这里先尝试一次；真正的安装时机是 on_platform_loaded 钩子。
        self._install_compatibility()

    @filter.on_platform_loaded()
    async def on_platform_loaded(self) -> None:
        """平台适配器加载完成后安装兼容层。

        插件的 initialize() 早于平台适配器创建，必须依赖这个钩子才能在
        冷启动时真正装上补丁，否则路由恢复永远不会生效。
        """
        if not self.config.get("enable_route_recovery", True):
            return
        logger.info("[QQ会话管理器] 平台加载完成，检查是否需要安装兼容层")
        self._install_compatibility()

    @filter.on_astrbot_loaded()
    async def on_astrbot_loaded(self) -> None:
        """兜底：AstrBot 全部就绪后再确认一次兼容层状态。"""
        if not self.config.get("enable_route_recovery", True):
            return
        if self.compatibility.status not in {"installed", "native"}:
            logger.info(
                "[QQ会话管理器] AstrBot 就绪时兼容层仍未安装（状态=%s），再次尝试",
                self.compatibility.status,
            )
            self._install_compatibility()

    def _install_compatibility(self) -> None:
        """发现 QQ 官方适配器实例并安装兼容层。"""
        instances = find_qq_official_instances(self.context)
        if not instances:
            txrui_instances = find_txrui_instances(self.context)
            if txrui_instances:
                self.compatibility.status = "native"
                self.compatibility.reason = "txrui 原生支持按会话发送，无需 QQ 缓存恢复补丁"
                logger.info("[QQ会话管理器] txrui 会话采集已就绪: %s", describe_instances(txrui_instances))
                return
            if self.compatibility.status != "degraded":
                self.compatibility.status = "degraded"
                self.compatibility.reason = "未找到 QQ 官方适配器实例"
            logger.warning(
                "[QQ会话管理器] 当前没有 QQ 官方适配器实例，"
                "将在平台加载完成后重试（采集功能不受影响）"
            )
            return

        installed = 0
        for inst in instances:
            if self.compatibility.install(inst):
                installed += 1
        self._patched_instances = [
            str((getattr(inst, "config", {}) or {}).get("id", ""))
            for inst in instances
        ]
        logger.info(
            "[QQ会话管理器] 兼容层状态=%s，已安装 %d/%d 个实例: %s",
            self.compatibility.status,
            installed,
            len(instances),
            describe_instances(instances),
        )
        if installed < len(instances):
            logger.warning(
                "[QQ会话管理器] 部分实例未安装兼容层，原因: %s",
                self.compatibility.reason or "未知",
            )

    async def terminate(self) -> None:
        self.compatibility.uninstall()
        self.repository.close()
        logger.info("[QQ会话管理器] 已停止，兼容层已恢复原始方法")

    # ------------------------------------------------------------- 事件监听

    @filter.event_message_type(filter.EventMessageType.PRIVATE_MESSAGE, priority=999)
    async def on_private_message(self, event: AstrMessageEvent):
        await self._collect(event)

    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE, priority=999)
    async def on_group_message(self, event: AstrMessageEvent):
        await self._collect(event)

    async def _collect(self, event: AstrMessageEvent) -> None:
        if event.get_platform_name() not in {"qq_official", "txrui"}:
            return
        try:
            message = await self.collector.handle(event)
        except Exception as exc:
            logger.exception("[QQ会话管理器] 入站消息采集失败: %s", exc)
            return
        if message is not None and self.config.get("enable_debug_log", False):
            logger.debug(
                "[QQ会话管理器] 已采集 %s 场景消息 | platform=%s scene=%s session=%s target=%s",
                message.scene,
                message.platform_id,
                message.scene,
                message.session_id,
                message.target_id,
            )
