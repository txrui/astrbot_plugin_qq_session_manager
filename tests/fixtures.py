"""构造与真实 AstrBot 事件 API 一致的测试夹具。

真实事件对象提供 ``get_platform_name()`` / ``get_platform_id()`` /
``get_session_id()`` 等方法，``message_obj.raw_message`` 是 botpy 原始消息。
测试夹具必须忠实还原这一结构，否则测试通过也无法证明真实行为正确。
"""
from __future__ import annotations

from types import SimpleNamespace

GROUP_OPENID = "762146F1DBB849090BCC80B6A667253F"
CHANNEL_ID = "1234567890"
MEMBER_OPENID = "E30C2C95C2167F54B1719B45D006DF1F"


def make_raw(
    *,
    scene: str = "group",
    message_id: str = "MSG0000000000000000000000000001",
    content: str = "hello",
    author_id: str = MEMBER_OPENID,
    author_name: str = "田小瑞",
):
    """构造 botpy 原始消息对象。"""
    author = SimpleNamespace(
        member_openid=author_id,
        user_openid=author_id,
        id=author_id,
        username=author_name,
    )
    raw = SimpleNamespace(
        id=message_id,
        content=content,
        channel_id=None,
        group_openid=None,
        author=author,
        message_type="GROUP",
        msg_elements=[],
        raw_data={"id": message_id, "content": content},
    )
    if scene == "group":
        raw.group_openid = GROUP_OPENID
    elif scene == "channel":
        raw.channel_id = CHANNEL_ID
    return raw


def make_event(
    *,
    scene: str = "group",
    platform_id: str = "小瑞",
    platform_type: str = "qq_official",
    session_id: str | None = None,
    message_id: str = "MSG0000000000000000000000000001",
    content: str = "hello",
    author_id: str = MEMBER_OPENID,
    author_name: str = "田小瑞",
    timestamp: int = 1_700_000_000,
):
    """构造一个与真实事件结构一致的 QQ 官方消息事件。"""
    raw = make_raw(
        scene=scene,
        message_id=message_id,
        content=content,
        author_id=author_id,
        author_name=author_name,
    )

    if session_id is None:
        if scene == "group":
            session_id = GROUP_OPENID
        elif scene == "channel":
            session_id = CHANNEL_ID
        else:
            session_id = author_id

    message_type = "FRIEND_MESSAGE" if scene == "friend" else "GROUP_MESSAGE"

    message_obj = SimpleNamespace(
        message_id=message_id,
        message_str=content,
        raw_message=raw,
        sender=SimpleNamespace(user_id=author_id, nickname=author_name),
        group=None if scene == "friend" else SimpleNamespace(group_id=session_id),
    )

    return SimpleNamespace(
        get_platform_name=lambda: platform_type,
        get_platform_id=lambda: platform_id,
        get_session_id=lambda: session_id,
        get_message_id=lambda: message_id,
        get_message_str=lambda: content,
        get_message_type=lambda: message_type,
        get_self_id=lambda: "102774486",
        get_timestamp=lambda: timestamp,
        platform_name=platform_type,
        platform_id=platform_id,
        session_id=session_id,
        message_type=message_type,
        message_obj=message_obj,
        message_str=content,
        self_id="102774486",
        timestamp=timestamp,
    )
