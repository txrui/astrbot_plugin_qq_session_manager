"""适配器兼容层测试。

按 3.md 第 20.10 节的"补丁生命周期测试"要求，覆盖：
- 安装 / 重复安装 / 卸载恢复
- 兼容性检查失败时不安装
- 缓存命中不查库
- 缓存缺失时恢复场景与消息 ID
- 路由缺失时报错但不影响原始发送
- 原始方法的返回值与异常透传
- 其他平台适配器不受影响
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

try:
    from adapters.compatibility import CompatibilityManager
    from core.exceptions import RouteNotFound
except ImportError:
    from astrbot_plugin_qq_session_manager.adapters.compatibility import (  # type: ignore
        CompatibilityManager,
    )
    from astrbot_plugin_qq_session_manager.core.exceptions import RouteNotFound  # type: ignore

FAKE_ADAPTER_PATH = (
    "astrbot.core.platform.sources.qqofficial.qqofficial_platform_adapter."
    "QQOfficialPlatformAdapter"
)

# 哨兵：表示"使用默认路由"，与显式传入 None（表示没有路由）区分开
_DEFAULT_ROUTE = object()


class FakeSession:
    def __init__(self, session_id="s1", message_type="GroupMessage"):
        self.session_id = session_id
        self.message_type = message_type
        self.platform_name = "qq_official"


class FakeRoute:
    def __init__(self, session_id="s1", scene="group", last_message_id="m1"):
        self.session_id = session_id
        self.scene = scene
        self.last_message_id = last_message_id


class FakeResolver:
    """模拟 SessionResolver。

    使用哨兵值区分"没有路由"与"未显式传入"，避免 route=None 被当成默认值。
    """

    def __init__(self, route=_DEFAULT_ROUTE):
        self.route = FakeRoute() if route is _DEFAULT_ROUTE else route
        self.calls = []

    def resolve(self, **kwargs):
        self.calls.append(kwargs)
        if self.route is None:
            raise RouteNotFound("没有路由")
        return self.route


class FakeAdapter:
    """模拟 QQ 官方适配器的最小结构。"""

    def __init__(self, platform_id="bot-a"):
        self._session_last_message_id = {}
        self._session_scene = {}
        self.config = {"id": platform_id}
        self.sent = []

    async def _send_by_session_common(self, session, message_chain):
        self.sent.append((session.session_id, message_chain))
        return "sent"

    async def _boom(self, session, message_chain):
        raise RuntimeError("原始异常")


def _install(adapter, resolver, config=None):
    """把假适配器伪装成真实类路径，以便通过兼容性检查。"""
    adapter.__class__ = type(
        "QQOfficialPlatformAdapter",
        (FakeAdapter,),
        {"__module__": "astrbot.core.platform.sources.qqofficial.qqofficial_platform_adapter"},
    )
    manager = CompatibilityManager(resolver, config=config)
    return manager


def test_install_and_uninstall():
    adapter = FakeAdapter()
    manager = _install(adapter, FakeResolver())

    assert manager.install(adapter) is True
    assert manager.status == "installed"

    # 重复安装不得重复包装
    wrapped = adapter._send_by_session_common
    assert manager.install(adapter) is True
    assert adapter._send_by_session_common is wrapped

    manager.uninstall()
    assert adapter._send_by_session_common.__func__ is FakeAdapter._send_by_session_common
    assert manager.status == "restored"


def test_incompatible_adapter_not_patched():
    """非 QQ 官方适配器不得被包装。"""

    class OtherAdapter:
        def __init__(self):
            self._session_last_message_id = {}
            self._session_scene = {}
            self.config = {"id": "x"}

        async def _send_by_session_common(self, session, message_chain):
            return "sent"

    adapter = OtherAdapter()
    manager = CompatibilityManager(FakeResolver())
    assert manager.install(adapter) is False
    assert manager.status == "degraded"
    assert "适配器类型不匹配" in manager.reason
    assert "_send_by_session_common" not in vars(adapter)


def test_missing_cache_fields_not_patched():
    class Broken:
        config = {"id": "x"}

        async def _send_by_session_common(self, session, message_chain):
            return "sent"

    Broken.__module__ = (
        "astrbot.core.platform.sources.qqofficial.qqofficial_platform_adapter"
    )
    Broken.__qualname__ = "QQOfficialPlatformAdapter"
    adapter = Broken()
    # 让类路径匹配真实适配器
    adapter.__class__ = type(
        "QQOfficialPlatformAdapter",
        (Broken,),
        {"__module__": Broken.__module__},
    )
    manager = CompatibilityManager(FakeResolver())
    assert manager.install(adapter) is False
    assert manager.status == "degraded"


def test_cache_hit_does_not_query_db():
    adapter = FakeAdapter()
    adapter._session_last_message_id["s1"] = "cached"
    adapter._session_scene["s1"] = "group"
    resolver = FakeResolver()
    manager = _install(adapter, resolver)
    manager.install(adapter)

    asyncio.run(adapter._send_by_session_common(FakeSession(), "chain"))

    assert resolver.calls == []
    assert adapter.sent == [("s1", "chain")]


def test_cache_miss_restores_scene_and_message_id():
    adapter = FakeAdapter()
    resolver = FakeResolver(FakeRoute(session_id="s1", scene="group", last_message_id="m1"))
    manager = _install(adapter, resolver)
    manager.install(adapter)

    asyncio.run(adapter._send_by_session_common(FakeSession("s1"), "chain"))

    assert len(resolver.calls) == 1
    assert resolver.calls[0]["platform_id"] == "bot-a"
    assert adapter._session_scene["s1"] == "group"
    assert adapter._session_last_message_id["s1"] == "m1"
    assert adapter.sent == [("s1", "chain")]


def test_route_missing_still_calls_original():
    """路由缺失时保持兼容行为：记录诊断但不伪造失败。"""
    adapter = FakeAdapter()
    resolver = FakeResolver(route=None)
    manager = _install(adapter, resolver)
    manager.install(adapter)

    result = asyncio.run(adapter._send_by_session_common(FakeSession("s1"), "chain"))

    assert result == "sent"
    assert adapter.sent == [("s1", "chain")]


def test_route_missing_can_raise():
    adapter = FakeAdapter()
    resolver = FakeResolver(route=None)
    manager = _install(adapter, resolver, config={"route_missing_behavior": "raise"})
    manager.install(adapter)

    try:
        asyncio.run(adapter._send_by_session_common(FakeSession("s1"), "chain"))
    except RouteNotFound:
        pass
    else:
        raise AssertionError("route_missing_behavior=raise 时应抛出异常")


def test_original_exception_propagates():
    """原始方法自身抛出的异常必须原样传播。"""

    class BoomAdapter(FakeAdapter):
        async def _send_by_session_common(self, session, message_chain):
            raise RuntimeError("原始异常")

    adapter = BoomAdapter()
    # 伪装成真实适配器类路径，以便通过兼容性检查
    adapter.__class__ = type(
        "QQOfficialPlatformAdapter",
        (BoomAdapter,),
        {
            "__module__": "astrbot.core.platform.sources.qqofficial.qqofficial_platform_adapter"
        },
    )
    manager = CompatibilityManager(FakeResolver())
    assert manager.install(adapter) is True

    try:
        asyncio.run(adapter._send_by_session_common(FakeSession("s1"), "chain"))
    except RuntimeError as exc:
        assert str(exc) == "原始异常"
    else:
        raise AssertionError("原始异常必须透传")


def test_return_value_preserved():
    adapter = FakeAdapter()
    resolver = FakeResolver()
    manager = _install(adapter, resolver)
    manager.install(adapter)

    result = asyncio.run(adapter._send_by_session_common(FakeSession("s1"), "chain"))
    assert result == "sent"


def test_scene_looked_up_when_unknown():
    """会话 ID 已知但场景未知时，应在三种场景中查找路由。"""
    adapter = FakeAdapter()
    resolver = FakeResolver(FakeRoute(session_id="s1", scene="channel", last_message_id="m9"))
    manager = _install(adapter, resolver)
    manager.install(adapter)

    asyncio.run(adapter._send_by_session_common(FakeSession("s1"), "chain"))

    assert adapter._session_scene["s1"] == "channel"
    assert adapter._session_last_message_id["s1"] == "m9"
    assert resolver.calls[0]["scene"] == "group"


def test_multiple_instances_installed_independently():
    a = FakeAdapter(platform_id="bot-a")
    b = FakeAdapter(platform_id="bot-b")
    resolver = FakeResolver()
    manager = CompatibilityManager(resolver)
    # 两个实例使用同一类但不同 platform_id
    for inst in (a, b):
        inst.__class__ = type(
            "QQOfficialPlatformAdapter",
            (FakeAdapter,),
            {"__module__": "astrbot.core.platform.sources.qqofficial.qqofficial_platform_adapter"},
        )
    assert manager.install(a) is True
    assert manager.install(b) is True

    manager.uninstall()
    assert a._send_by_session_common.__func__ is FakeAdapter._send_by_session_common
    assert b._send_by_session_common.__func__ is FakeAdapter._send_by_session_common
