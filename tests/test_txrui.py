"""txrui 会话采集、平台隔离和持久化回归。"""
import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core.normalizer import normalize_event
from core.repository import Repository
from core.session_service import SessionService
from core.collector import EventCollector
from adapters.platform_discovery import find_txrui_instances, find_qq_official_instances


def make_txrui(scene="group", platform_id="wx_bot", message_id="evt1"):
    raw = {"chat_id": "工作_交流群", "chat_type": scene,
           "account_id": "wx_001", "event_id": message_id,
           "sender": {"platform_user_id": "wx_member", "display_name": "成员"}}
    message = SimpleNamespace(raw_message=raw, timestamp=1794835864,
                              message_id=message_id, message_str="你好[图片]")
    return SimpleNamespace(
        message_obj=message,
        get_platform_name=lambda: "txrui",
        get_platform_id=lambda: platform_id,
        get_session_id=lambda: raw["chat_id"],
        get_self_id=lambda: "wx_001",
        get_message_type=lambda: {"group": "GroupMessage", "private": "FriendMessage"}.get(scene, "OtherMessage"),
    )


def test_txrui_group_normalization_preserves_chat_name():
    msg = normalize_event(make_txrui())
    assert msg.platform == "txrui"
    assert msg.scene == "group"
    assert msg.normalized_session_id == msg.session_id == "工作_交流群"
    assert msg.sender_id == "wx_member"
    assert msg.sender_name == "成员"
    assert msg.bot_id == "wx_001"
    assert msg.id_normalization_rule == "identity"


def test_txrui_private_and_unknown_type():
    assert normalize_event(make_txrui("private")).scene == "friend"
    assert normalize_event(make_txrui("unknown")) is None
    assert normalize_event(make_txrui(platform_id="")) is None
    assert normalize_event(make_txrui(), store_raw=False).raw_message_json is None


def test_txrui_storage_restart_and_platform_isolation(tmp_path):
    db = tmp_path / "txrui.db"
    repo = Repository(db)
    service = SessionService(repo)
    service.collect(make_txrui())
    service.collect(make_txrui())
    service.collect(make_txrui(platform_id="wx_bot_2"))
    assert repo.counts()["messages"] == 2
    repo.close()
    repo = Repository(db)
    route = repo.find_route("wx_bot", "工作_交流群", "group")
    assert route.platform == "txrui"
    assert route.target_id == "工作_交流群"
    assert route.id_normalization_rule == "identity"
    assert repo.find_route("wx_bot_2", "工作_交流群", "group").platform_id == "wx_bot_2"
    repo.close()


def test_txrui_collector_uses_chat_name_without_qq_lookup(tmp_path):
    async def scenario():
        repo = Repository(tmp_path / "collector.db")
        names = SimpleNamespace(resolve=AsyncMock(side_effect=AssertionError("QQ lookup")))
        collector = EventCollector(SessionService(repo), names)
        await collector.handle(make_txrui())
        assert repo.find_route("wx_bot", "工作_交流群", "group").group_name == "工作_交流群"
        names.resolve.assert_not_awaited()
        repo.close()
    asyncio.run(scenario())


def test_txrui_collection_toggles(tmp_path):
    repo = Repository(tmp_path / "disabled.db")
    assert SessionService(repo, {"support_group": False}).collect(make_txrui()) is None
    assert SessionService(repo, {"enable_collection": False}).collect(make_txrui()) is None
    assert repo.counts()["messages"] == 0
    repo.close()


def test_txrui_discovery_never_installs_qq_cache_patch():
    adapter = SimpleNamespace(meta=lambda: SimpleNamespace(name="txrui", id="wx_bot"),
                              send_by_session=AsyncMock())
    context = SimpleNamespace(platform_manager=SimpleNamespace(get_insts=lambda: [adapter]))
    assert find_txrui_instances(context) == [adapter]
    assert find_qq_official_instances(context) == []
    assert not hasattr(adapter, "_session_last_message_id")
