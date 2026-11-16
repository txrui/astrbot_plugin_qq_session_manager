"""仓储层测试：去重、重启、多 Bot 隔离、过期与禁用。"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from fixtures import GROUP_OPENID, make_event  # noqa: E402

try:
    from core.exceptions import (
        AmbiguousSessionRoute,
        RouteDisabled,
        RouteExpired,
        RouteNotFound,
    )
    from core.normalizer import normalize_event
    from core.repository import Repository
except ImportError:
    from astrbot_plugin_qq_session_manager.core.exceptions import (  # type: ignore
        AmbiguousSessionRoute,
        RouteDisabled,
        RouteExpired,
        RouteNotFound,
    )
    from astrbot_plugin_qq_session_manager.core.normalizer import (  # type: ignore
        normalize_event,
    )
    from astrbot_plugin_qq_session_manager.core.repository import Repository  # type: ignore


def _msg(**kwargs):
    msg = normalize_event(make_event(**kwargs))
    assert msg is not None
    return msg


def test_upsert_and_dedup(tmp_path):
    repo = Repository(tmp_path / "sessions.db")
    msg = _msg()
    assert repo.upsert_message(msg) is not None
    # 同一条消息重复到达不应重复插入
    assert repo.upsert_message(msg) is None
    assert repo.counts()["messages"] == 1
    repo.close()


def test_different_message_ids_both_stored(tmp_path):
    repo = Repository(tmp_path / "sessions.db")
    repo.upsert_message(_msg(message_id="MSG_A"))
    repo.upsert_message(_msg(message_id="MSG_B"))
    assert repo.counts()["messages"] == 2
    repo.close()


def test_route_survives_restart(tmp_path):
    db = tmp_path / "sessions.db"
    repo = Repository(db)
    repo.upsert_message(_msg())
    repo.close()

    repo2 = Repository(db)
    route = repo2.find_route("小瑞", GROUP_OPENID, "group")
    assert route.target_id == GROUP_OPENID
    assert route.last_message_id == "MSG0000000000000000000000000001"
    repo2.close()


def test_multi_bot_isolated(tmp_path):
    repo = Repository(tmp_path / "x.db")
    repo.upsert_message(_msg(platform_id="bot-a"))
    repo.upsert_message(_msg(platform_id="bot-b", message_id="MSG_B"))

    assert repo.find_route("bot-b", GROUP_OPENID, "group").platform_id == "bot-b"
    assert repo.find_route("bot-a", GROUP_OPENID, "group").platform_id == "bot-a"

    # 另一个 Bot 实例的路由不应被当前实例看到
    try:
        repo.find_route("bot-c", GROUP_OPENID, "group")
    except RouteNotFound:
        pass
    else:
        raise AssertionError("不同 Bot 实例之间不应串线")
    repo.close()


def test_scene_isolation(tmp_path):
    """群、频道、私聊不得互相匹配。"""
    repo = Repository(tmp_path / "x.db")
    repo.upsert_message(_msg(scene="group"))
    try:
        repo.find_route("小瑞", GROUP_OPENID, "channel")
    except RouteNotFound:
        pass
    else:
        raise AssertionError("场景不匹配时不应命中路由")
    repo.close()


def test_expired_route_raises(tmp_path):
    repo = Repository(tmp_path / "x.db")
    msg = _msg()
    repo.upsert_message(msg, expires_at=msg.timestamp - 1)
    try:
        repo.find_route("小瑞", GROUP_OPENID, "group")
    except RouteExpired:
        pass
    else:
        raise AssertionError("过期路由不应被恢复")
    repo.close()


def test_disabled_route_raises(tmp_path):
    repo = Repository(tmp_path / "x.db")
    repo.upsert_message(_msg())
    repo.disable_route("小瑞", GROUP_OPENID, "group")
    try:
        repo.find_route("小瑞", GROUP_OPENID, "group")
    except RouteDisabled:
        pass
    else:
        raise AssertionError("被禁用的路由不应被恢复")
    repo.close()


def test_missing_route_raises(tmp_path):
    repo = Repository(tmp_path / "x.db")
    try:
        repo.find_route("小瑞", "不存在的会话", "group")
    except RouteNotFound:
        pass
    else:
        raise AssertionError("无历史路由时必须明确报错")
    repo.close()


def test_ambiguous_route_raises(tmp_path):
    """同一会话 ID 在同场景下存在多条路由时必须报歧义，而不是取最新一条。"""
    repo = Repository(tmp_path / "x.db")
    repo.upsert_message(_msg(scene="group", session_id="S"))
    # 直接插入第二条同会话 ID 的路由，绕过唯一键约束
    conn = repo._connection()
    with conn:
        conn.execute(
            """INSERT INTO sessions(platform_id,bot_id,platform,session_id,normalized_session_id,
               target_id,scene,message_type,last_message_id,last_active_at,created_at,updated_at,
               expires_at,status,id_normalization_rule)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            ("小瑞", None, "qq_official", "S", "S", "T2", "group", None, None,
             1, 1, 1, None, 1, "identity"),
        )
    try:
        repo.find_route("小瑞", "S", "group")
    except AmbiguousSessionRoute:
        pass
    else:
        raise AssertionError("多条候选路由时必须抛歧义错误")
    repo.close()


def test_wal_and_integrity(tmp_path):
    repo = Repository(tmp_path / "x.db")
    health = repo.health_check()
    assert health["integrity"] == "ok"
    assert health["schema_version"] >= 1
    repo.close()


def test_pagination_limit(tmp_path):
    repo = Repository(tmp_path / "x.db")
    for i in range(5):
        repo.upsert_message(_msg(message_id=f"MSG_{i}", session_id=f"S{i}"))
    assert len(repo.list_sessions(limit=2)) == 2
    assert len(repo.list_sessions(limit=100)) == 5
    repo.close()


def test_normalized_lookup_fallback(tmp_path):
    """没有精确命中时，才按规范化会话 ID 匹配。"""
    repo = Repository(tmp_path / "x.db")
    repo.upsert_message(_msg(scene="group", session_id="prefix_TARGET"))
    # 以原始 ID 查询不到（数据库中存的是规范化形式之外的另一个 ID）
    route = repo.find_route("小瑞", "TARGET", "group")
    assert route.normalized_session_id == "TARGET"
    repo.close()


def test_exact_match_preferred_over_normalized(tmp_path):
    """原始 ID 与规范化 ID 同时存在时，优先返回原始 ID 的路由，且不误报歧义。"""
    repo = Repository(tmp_path / "x.db")
    repo.upsert_message(_msg(scene="group", session_id="A_B", message_id="MSG_1"))
    repo.upsert_message(_msg(scene="group", session_id="B", message_id="MSG_2"))

    route = repo.find_route("小瑞", "A_B", "group")
    assert route.session_id == "A_B"
    assert route.last_message_id == "MSG_1"
    repo.close()
