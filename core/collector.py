"""事件采集入口。

把 AstrBot 事件交给 SessionService 采集。数据库写入放到线程中执行，
避免阻塞事件循环（3.md 第 20.6 节）。

群名称反查是异步的（要走 QQ 开放平台 HTTP 接口），因此在进入线程之前
先完成解析，再把结果交给同步的落库流程。
"""
from __future__ import annotations

import asyncio
import inspect

try:
    from .group_name import GroupNameResolver
except ImportError:
    from group_name import GroupNameResolver

try:
    from .normalizer import normalize_event
except ImportError:
    from normalizer import normalize_event


class EventCollector:
    """AstrBot 事件到统一模型的薄适配层。"""

    def __init__(self, session_service, group_name_resolver: GroupNameResolver | None = None):
        self.session_service = session_service
        self.group_name_resolver = group_name_resolver or GroupNameResolver()

    async def handle(self, event):
        """采集一条事件，返回规范化后的消息或 None。"""
        group_name = await self._resolve_group_name(event)
        loop = asyncio.get_running_loop()
        # SQLite 写入是同步阻塞操作，放到线程池执行，避免拖慢事件循环
        return await loop.run_in_executor(
            None, self._collect_sync, event, group_name
        )

    async def _resolve_group_name(self, event) -> str:
        """解析群名称；失败不影响消息采集主流程。

        群名只是展示用的增强信息，拿不到时退回到群号显示，
        因此这里的任何异常都必须被吞掉。
        """
        try:
            message = normalize_event(event)
            if message is None or message.scene != "group":
                return ""
            return await self.group_name_resolver.resolve(
                message.target_id, event=event
            )
        except Exception:
            import logging

            logging.getLogger("astrbot").exception(
                "[QQ会话管理器] 群名称解析失败"
            )
            return ""

    def _collect_sync(self, event, group_name: str = ""):
        try:
            collect = self.session_service.collect
            if self._accepts_group_name(collect):
                return collect(event, group_name=group_name)
            return collect(event)
        except Exception:
            # 采集失败不应影响消息处理主流程
            import logging

            logging.getLogger("astrbot").exception(
                "[QQ会话管理器] 入站消息采集失败"
            )
            return None

    @staticmethod
    def _accepts_group_name(collect) -> bool:
        """判断会话服务的 collect 是否接受 ``group_name`` 关键字参数。

        用签名而不是捕获 TypeError 来判断：collect 内部抛出的 TypeError
        属于真正的采集失败，不应被误当成"参数不匹配"而重复调用一次。
        """
        try:
            signature = inspect.signature(collect)
        except (TypeError, ValueError):
            return False
        try:
            signature.bind(None, group_name="")
        except TypeError:
            return False
        return True
