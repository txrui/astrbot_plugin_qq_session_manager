"""入站消息规范化测试。"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from fixtures import (  # noqa: E402
    CHANNEL_ID,
    GROUP_OPENID,
    MEMBER_OPENID,
    make_event,
)

try:
    from core.normalizer import normalize_event, normalize_session_id
except ImportError:
    from astrbot_plugin_qq_session_manager.core.normalizer import (  # type: ignore
        normalize_event,
        normalize_session_id,
    )


def test_group_scene_fields():
    msg = normalize_event(make_event(scene="group"))
    assert msg is not None
    assert msg.scene == "group"
    assert msg.target_id == GROUP_OPENID
    assert msg.session_id == GROUP_OPENID
    assert msg.sender_id == MEMBER_OPENID
    assert msg.sender_name == "田小瑞"
    assert msg.message_id == "MSG0000000000000000000000000001"
    assert msg.platform_id == "小瑞"
    assert msg.direction == "inbound"


def test_channel_scene_fields():
    msg = normalize_event(make_event(scene="channel"))
    assert msg is not None
    assert msg.scene == "channel"
    assert msg.target_id == CHANNEL_ID


def test_friend_scene_fields():
    msg = normalize_event(make_event(scene="friend"))
    assert msg is not None
    assert msg.scene == "friend"
    assert msg.target_id == MEMBER_OPENID
    assert msg.session_id == MEMBER_OPENID


def test_other_platform_ignored():
    assert normalize_event(make_event(platform_type="telegram")) is None
    assert normalize_event(make_event(platform_type="aiocqhttp")) is None


def test_platform_instance_id_not_type():
    """platform_id 必须是实例 ID（小瑞），不是平台类型（qq_official）。"""
    msg = normalize_event(make_event(platform_id="小瑞"))
    assert msg.platform_id == "小瑞"


def test_session_id_and_target_id_are_separate():
    """session_id 与 target_id 必须独立保存，不能互相推导。"""
    event = make_event(scene="group", session_id="custom_session_123")
    msg = normalize_event(event)
    assert msg.session_id == "custom_session_123"
    assert msg.target_id == GROUP_OPENID


def test_group_normalization_keeps_original():
    """规范化值不能覆盖原始会话 ID。"""
    normalized, rule = normalize_session_id("prefix_ABCDEF_suffix", "group")
    assert normalized == "suffix"
    assert rule == "qq_official_4_29_4_group_suffix"


def test_non_group_normalization_is_identity():
    normalized, rule = normalize_session_id("abc_def", "friend")
    assert normalized == "abc_def"
    assert rule == "identity"


def test_id_with_multiple_underscores():
    """包含多个下划线的 ID 只截断最后一段。"""
    normalized, _ = normalize_session_id("a_b_c_target", "group")
    assert normalized == "target"


def test_raw_json_can_be_disabled():
    msg = normalize_event(make_event(), store_raw=False)
    assert msg.raw_message_json is None
    msg = normalize_event(make_event(), store_raw=True)
    assert msg.raw_message_json is not None
    assert "MSG0000000000000000000000000001" in msg.raw_message_json
