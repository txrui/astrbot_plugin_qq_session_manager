"""端到端集成测试：采集 -> 持久化 -> 重启 -> 路由恢复。

对应 3.md 第 14.3 节的集成测试矩阵。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from fixtures import CHANNEL_ID, GROUP_OPENID, MEMBER_OPENID, make_event  # noqa: E402

try:
    from core.exceptions import RouteNotFound, UnsupportedScene
    from core.repository import Repository
    from core.resolver import SessionResolver
    from core.session_service import SessionService
except ImportError:
    from astrbot_plugin_qq_session_manager.core.exceptions import (  # type: ignore
        RouteNotFound,
        UnsupportedScene,
    )
    from astrbot_plugin_qq_session_manager.core.repository import Repository  # type: ignore
    from astrbot_plugin_qq_session_manager.core.resolver import SessionResolver  # type: ignore
    from astrbot_plugin_qq_session_manager.core.session_service import SessionService  # type: ignore


def _service(db, config=None):
    repo = Repository(db)
    return repo, SessionService(repo, config or {})


def test_collect_three_scenes(tmp_path):
    """私聊、普通群、频道三种场景都能正确入库。"""
    repo, service = _service(tmp_path / "x.db")

    service.collect(make_event(scene="group"))
    service.collect(make_event(scene="channel"))
    service.collect(make_event(scene="friend"))

    assert repo.counts()["sessions"] == 3
    assert repo.counts()["messages"] == 3

    assert repo.find_route("小瑞", GROUP_OPENID, "group").target_id == GROUP_OPENID
    assert repo.find_route("小瑞", CHANNEL_ID, "channel").target_id == CHANNEL_ID
    assert repo.find_route("小瑞", MEMBER_OPENID, "friend").target_id == MEMBER_OPENID
    repo.close()


def test_collect_is_idempotent(tmp_path):
    """同一条消息重复到达不会重复插入。"""
    repo, service = _service(tmp_path / "x.db")
    event = make_event(scene="group")
    service.collect(event)
    service.collect(event)
    assert repo.counts()["messages"] == 1
    repo.close()


def test_other_platform_not_stored(tmp_path):
    """其他平台消息不会写入本插件表。"""
    repo, service = _service(tmp_path / "x.db")
    service.collect(make_event(platform_type="aiocqhttp"))
    service.collect(make_event(platform_type="telegram"))
    assert repo.counts()["messages"] == 0
    assert repo.counts()["sessions"] == 0
    repo.close()


def test_scene_toggle_disables_collection(tmp_path):
    repo, service = _service(tmp_path / "x.db", {"support_channel": False})
    service.collect(make_event(scene="channel"))
    service.collect(make_event(scene="group"))
    assert repo.counts()["sessions"] == 1
    repo.close()


def test_collection_can_be_disabled(tmp_path):
    repo, service = _service(tmp_path / "x.db", {"enable_collection": False})
    assert service.collect(make_event(scene="group")) is None
    assert repo.counts()["messages"] == 0
    repo.close()


def test_restart_recovery_preserves_routes(tmp_path):
    """AstrBot 重启后路由记录保留并可查询。"""
    db = tmp_path / "x.db"
    repo, service = _service(db)
    service.collect(make_event(scene="group"))
    service.collect(make_event(scene="channel"))
    service.collect(make_event(scene="friend"))
    repo.close()

    repo2 = Repository(db)
    resolver = SessionResolver(repo2)
    assert resolver.resolve(platform_id="小瑞", session_id=GROUP_OPENID, scene="group")
    assert resolver.resolve(platform_id="小瑞", session_id=CHANNEL_ID, scene="channel")
    assert resolver.resolve(platform_id="小瑞", session_id=MEMBER_OPENID, scene="friend")
    repo2.close()


def test_unique_session_does_not_confuse_target(tmp_path):
    """开启 unique_session 时不能把用户 ID 当成群目标 ID。

    模拟 unique_session 场景：会话 ID 是用户 ID，但真实群目标仍是群 OpenID。
    """
    repo, service = _service(tmp_path / "x.db")
    event = make_event(scene="group", session_id=MEMBER_OPENID)
    service.collect(event)

    route = repo.find_route("小瑞", MEMBER_OPENID, "group")
    assert route.session_id == MEMBER_OPENID
    assert route.target_id == GROUP_OPENID
    assert route.session_id != route.target_id
    repo.close()


def test_multi_bot_no_crosstalk(tmp_path):
    """两个 QQ Bot 同时运行时数据和路由互不干扰。"""
    repo, service = _service(tmp_path / "x.db")
    service.collect(make_event(platform_id="bot-a", scene="group"))
    service.collect(make_event(platform_id="bot-b", scene="group", message_id="MSG_B"))

    resolver = SessionResolver(repo)
    a = resolver.resolve(platform_id="bot-a", session_id=GROUP_OPENID, scene="group")
    b = resolver.resolve(platform_id="bot-b", session_id=GROUP_OPENID, scene="group")

    assert a.platform_id == "bot-a"
    assert b.platform_id == "bot-b"
    assert a.last_message_id != b.last_message_id

    # bot-c 没有数据，不应命中任何路由
    try:
        resolver.resolve(platform_id="bot-c", session_id=GROUP_OPENID, scene="group")
    except RouteNotFound:
        pass
    else:
        raise AssertionError("未登记的 Bot 实例不应命中路由")
    repo.close()


def test_no_history_route_is_explicit_error(tmp_path):
    """没有历史消息时明确报告路由不存在。"""
    repo = Repository(tmp_path / "x.db")
    resolver = SessionResolver(repo)
    try:
        resolver.resolve(platform_id="小瑞", session_id="从未出现过的会话", scene="group")
    except RouteNotFound:
        pass
    else:
        raise AssertionError("无历史路由必须抛 RouteNotFound")
    repo.close()


def test_resolver_rejects_unknown_scene(tmp_path):
    repo = Repository(tmp_path / "x.db")
    resolver = SessionResolver(repo)
    try:
        try:
            resolver.resolve(platform_id="小瑞", session_id="s", scene="unknown")
        except UnsupportedScene:
            pass
        else:
            raise AssertionError("未知场景必须抛 UnsupportedScene")
    finally:
        repo.close()


def test_message_listing_and_pagination(tmp_path):
    repo, service = _service(tmp_path / "x.db")
    for i in range(5):
        service.collect(make_event(scene="group", message_id=f"MSG_{i}"))
    assert len(repo.list_messages(limit=2)) == 2
    assert len(repo.list_messages(limit=50)) == 5
    assert len(repo.list_messages(scene="channel")) == 0
    repo.close()


def test_retention_sets_expiry(tmp_path):
    repo, service = _service(tmp_path / "x.db", {"session_retention_days": 1})
    message = service.collect(make_event(scene="group"))
    assert message is not None
    row = repo.list_sessions()[0]
    assert row["expires_at"] is not None
    assert row["expires_at"] > message.timestamp
    repo.close()
