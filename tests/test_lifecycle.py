"""端到端生命周期验证：模拟 AstrBot 加载插件 -> 采集 -> 路由恢复 -> 卸载。

该脚本不依赖 AstrBot 运行时，用假的 Context / 适配器走通插件主流程，
用于在真实环境之外验证 main.py 的编排逻辑。
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PLUGIN_ROOT.parent))
sys.path.insert(0, str(PLUGIN_ROOT))
sys.path.insert(0, str(PLUGIN_ROOT / "tests"))

from fixtures import GROUP_OPENID, make_event  # noqa: E402

from adapters.compatibility import CompatibilityManager  # noqa: E402
from adapters.platform_discovery import find_qq_official_instances  # noqa: E402
from core.repository import Repository  # noqa: E402
from core.resolver import SessionResolver  # noqa: E402
from core.session_service import SessionService  # noqa: E402

ADAPTER_MODULE = (
    "astrbot.core.platform.sources.qqofficial.qqofficial_platform_adapter"
)


class FakeAdapter:
    """模拟 QQ 官方适配器的关键结构。"""

    def __init__(self, platform_id="小瑞"):
        self._session_last_message_id = {}
        self._session_scene = {}
        self.config = {"id": platform_id}
        self.sent = []

    async def _send_by_session_common(self, session, message_chain):
        # 复刻适配器真实行为：没有 msg_id 就跳过发送
        scene = self._session_scene.get(session.session_id)
        msg_id = self._session_last_message_id.get(session.session_id)
        if scene != "friend" and not msg_id:
            return "skipped"
        self.sent.append(session.session_id)
        return "sent"


class FakeMeta:
    def __init__(self, id_, name):
        self.id = id_
        self.name = name


class FakePlatformInstance(FakeAdapter):
    def __init__(self, platform_id="小瑞"):
        super().__init__(platform_id)
        self._meta = FakeMeta(platform_id, "qq_official")

    def meta(self):
        return self._meta


FakePlatformInstance.__module__ = ADAPTER_MODULE
FakePlatformInstance.__qualname__ = "QQOfficialPlatformAdapter"
FakePlatformInstance.__name__ = "QQOfficialPlatformAdapter"


class FakePlatformManager:
    def __init__(self, instances):
        self._instances = instances

    def get_insts(self):
        return list(self._instances)


class FakeContext:
    def __init__(self, instances):
        self.platform_manager = FakePlatformManager(instances)


class FakeSession:
    def __init__(self, session_id, message_type="GroupMessage"):
        self.session_id = session_id
        self.message_type = message_type


def main() -> int:
    failures: list[str] = []

    def check(label, condition):
        status = "PASS" if condition else "FAIL"
        print(f"[{status}] {label}")
        if not condition:
            failures.append(label)

    adapter = FakePlatformInstance("小瑞")
    context = FakeContext([adapter])

    # 1. 平台实例发现
    found = find_qq_official_instances(context)
    check("发现 QQ 官方适配器实例", len(found) == 1)

    # 2. 初始化仓储与服务
    import tempfile

    tmp = tempfile.TemporaryDirectory()
    repo = Repository(Path(tmp.name) / "sessions.db")
    service = SessionService(repo, {})
    resolver = SessionResolver(repo)

    # 3. 采集入站消息
    message = service.collect(make_event(scene="group"))
    check("采集到群消息", message is not None and message.scene == "group")
    check("platform_id 是实例 ID", message is not None and message.platform_id == "小瑞")
    check("target_id 是群 OpenID", message is not None and message.target_id == GROUP_OPENID)

    # 4. 安装兼容层之前：内存缓存为空，发送会被适配器跳过
    result = asyncio.run(adapter._send_by_session_common(FakeSession(GROUP_OPENID), "hi"))
    check("安装前发送被跳过", result == "skipped")

    # 5. 安装兼容层
    manager = CompatibilityManager(resolver, config={})
    installed = manager.install(adapter)
    check("兼容层安装成功", installed and manager.status == "installed")

    # 6. 安装兼容层后：首次发送即触发路由恢复并成功
    result = asyncio.run(adapter._send_by_session_common(FakeSession(GROUP_OPENID), "hi"))
    check("恢复后发送成功", result == "sent")
    check("场景缓存已恢复", adapter._session_scene.get(GROUP_OPENID) == "group")
    check("消息 ID 缓存已恢复", bool(adapter._session_last_message_id.get(GROUP_OPENID)))

    # 7. 缓存命中时不再查库
    calls_before = len(manager._originals)
    asyncio.run(adapter._send_by_session_common(FakeSession(GROUP_OPENID), "hi"))
    check("缓存命中时包装关系未变化", len(manager._originals) == calls_before)

    # 8. 卸载后恢复原始方法
    manager.uninstall()
    check("卸载后状态为 restored", manager.status == "restored")
    check(
        "卸载后恢复原始方法",
        adapter._send_by_session_common.__func__ is FakeAdapter._send_by_session_common,
    )

    # 9. 卸载后行为与原始适配器一致（无缓存则跳过）
    adapter._session_last_message_id.clear()
    adapter._session_scene.clear()
    result = asyncio.run(adapter._send_by_session_common(FakeSession(GROUP_OPENID), "hi"))
    check("卸载后行为回到原始适配器", result == "skipped")

    repo.close()
    tmp.cleanup()

    print()
    if failures:
        print(f"失败 {len(failures)} 项: {failures}")
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
