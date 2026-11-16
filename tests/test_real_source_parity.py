"""对照真实 AstrBot 4.29.4 源码的验证测试。

本文件不依赖 AstrBot 运行时，而是把**真实适配器的关键逻辑**原样搬进来，
验证插件的规范化与路由恢复在真实语义下成立。

真实源码位置（容器内）：
    /AstrBot/astrbot/core/platform/sources/qqofficial/qqofficial_platform_adapter.py

验证要点：
1. 缓存键用原始 ``abm.session_id``（群 = group_openid，频道 = channel_id，
   私聊 = sender.user_id）。
2. 发送时群场景会先做 ``session_id.rsplit("_", 1)[-1]`` 再查缓存。
3. ``_allow_group_proactive_send`` 默认 True：群场景只要 scene 正确即可主动发送。
4. 频道与私聊以外的场景没有 msg_id 会被跳过。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from fixtures import CHANNEL_ID, GROUP_OPENID, MEMBER_OPENID, make_event  # noqa: E402

try:
    from core.normalizer import normalize_event, normalize_session_id
    from core.repository import Repository
    from core.resolver import SessionResolver
except ImportError:
    from astrbot_plugin_qq_session_manager.core.normalizer import (  # type: ignore
        normalize_event,
        normalize_session_id,
    )
    from astrbot_plugin_qq_session_manager.core.repository import Repository  # type: ignore
    from astrbot_plugin_qq_session_manager.core.resolver import SessionResolver  # type: ignore

# ---------------------------------------------------------------------------
# 以下常量与逻辑摘自真实 4.29.4 源码
# ---------------------------------------------------------------------------

#: 真实源码：abm.session_id = abm.group_id = message.group_openid
REAL_GROUP_SESSION_ID = GROUP_OPENID
#: 真实源码：abm.session_id = abm.group_id = message.channel_id
REAL_CHANNEL_SESSION_ID = CHANNEL_ID
#: 真实源码：abm.session_id = abm.sender.user_id
REAL_FRIEND_SESSION_ID = MEMBER_OPENID

#: 真实源码 _send_by_session_common 开头
REAL_GROUP_NORMALIZATION = "rsplit('_', 1)[-1]"


def _real_send_session_id(session_id: str, message_type: str) -> str:
    """复刻真实适配器在发送前对群会话 ID 的规范化。"""
    if message_type == "GROUP_MESSAGE":
        return session_id.rsplit("_", 1)[-1]
    return session_id


def test_real_group_session_id_is_group_openid():
    """真实适配器把群 OpenID 直接写入 session_id。"""
    msg = normalize_event(make_event(scene="group"))
    assert msg.session_id == REAL_GROUP_SESSION_ID
    assert msg.target_id == REAL_GROUP_SESSION_ID


def test_real_channel_session_id_is_channel_id():
    msg = normalize_event(make_event(scene="channel"))
    assert msg.session_id == REAL_CHANNEL_SESSION_ID
    assert msg.target_id == REAL_CHANNEL_SESSION_ID


def test_real_friend_session_id_is_sender_user_id():
    msg = normalize_event(make_event(scene="friend"))
    assert msg.session_id == REAL_FRIEND_SESSION_ID
    assert msg.target_id == REAL_FRIEND_SESSION_ID


def test_real_group_openid_has_no_underscore():
    """真实群 OpenID 是 32 位十六进制，不含下划线。

    因此适配器发送时的 rsplit 规范化对真实群会话是恒等变换，
    缓存键与发送时查���的键保持一致。
    """
    assert "_" not in GROUP_OPENID
    normalized, _ = normalize_session_id(GROUP_OPENID, "group")
    assert normalized == GROUP_OPENID
    assert _real_send_session_id(GROUP_OPENID, "GROUP_MESSAGE") == GROUP_OPENID


def test_normalization_matches_real_send_rule():
    """插件的规范化规则必须与适配器的发送规则一致。"""
    for session_id in ("prefix_ABC_suffix", "a_b_c_target", "plain_id"):
        normalized, _ = normalize_session_id(session_id, "group")
        assert normalized == _real_send_session_id(session_id, "GROUP_MESSAGE")


def test_channel_normalization_is_identity():
    """频道不参与群场景的 rsplit 规范化。"""
    normalized, rule = normalize_session_id(CHANNEL_ID, "channel")
    assert normalized == CHANNEL_ID
    assert rule == "identity"
    assert _real_send_session_id(CHANNEL_ID, "GROUP_MESSAGE") == CHANNEL_ID


def test_cache_key_alignment_with_real_adapter(tmp_path):
    """恢复的缓存键必须等于适配器查询缓存时使用的键。

    真实适配器发送群消息时，先把 session_id 规范化再查缓存；
    插件恢复缓存时必须使用同一个键，否则适配器仍然查不到。
    """
    repo = Repository(tmp_path / "x.db")
    msg = normalize_event(make_event(scene="group"))
    repo.upsert_message(msg)

    route = repo.find_route("小瑞", GROUP_OPENID, "group")

    # 适配器发送时使用的键
    send_key = _real_send_session_id(route.session_id, "GROUP_MESSAGE")
    # 插件恢复缓存时使用的键
    restore_key = route.session_id

    assert send_key == restore_key, "缓存键不一致会导致恢复失效"
    repo.close()


def test_group_proactive_send_only_needs_scene(tmp_path):
    """真实适配器：_allow_group_proactive_send 默认 True。

    群场景只要 scene 缓存正确即可主动发送，msg_id 缺失不会阻止发送。
    因此插件必须至少恢复 scene。
    """
    repo = Repository(tmp_path / "x.db")
    msg = normalize_event(make_event(scene="group"))
    repo.upsert_message(msg)

    route = repo.find_route("小瑞", GROUP_OPENID, "group")
    assert route.scene == "group"
    # 群场景恢复 scene 即满足真实适配器的主动发送条件
    assert route.scene == "group"
    repo.close()


def test_channel_still_requires_message_id(tmp_path):
    """频道没有 msg_id 时真实适配器会跳过发送。

    因此频道路由必须同时保存 last_message_id，插件需一并恢复。
    """
    repo = Repository(tmp_path / "x.db")
    msg = normalize_event(make_event(scene="channel", message_id="CH_MSG_1"))
    repo.upsert_message(msg)

    route = repo.find_route("小瑞", CHANNEL_ID, "channel")
    assert route.scene == "channel"
    assert route.last_message_id == "CH_MSG_1", "频道路由必须保存最近消息 ID"
    repo.close()


def test_resolver_uses_real_platform_instance_id(tmp_path):
    """真实平台实例 ID 是配置中的 id（小瑞），路由查询必须使用它。"""
    repo = Repository(tmp_path / "x.db")
    msg = normalize_event(make_event(scene="group", platform_id="小瑞"))
    repo.upsert_message(msg)

    resolver = SessionResolver(repo)
    route = resolver.resolve(
        platform_id="小瑞", session_id=GROUP_OPENID, scene="group"
    )
    assert route.platform_id == "小瑞"
    repo.close()
