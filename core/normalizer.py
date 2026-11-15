from __future__ import annotations

import json
import time
from typing import Any

from .models import NormalizedMessage, Scene

SUPPORTED_SCENES = {"friend", "group", "channel"}

# QQ 官方开放平台用户/群 OpenID 为 24-64 位十六进制大写串；频道 ID 为纯数字。
_OFFICIAL_OPENID_LEN = 24


def _text(value: Any, limit: int = 512) -> str | None:
    if value is None:
        return None
    value = str(value).strip()
    return value[:limit] if value else None


def normalize_session_id(session_id: str, scene: str) -> tuple[str, str]:
    """按 4.29.4 群发送规则生成规范化值，但不修改原始值。

    适配器在发送 GROUP_MESSAGE 前会执行 ``session_id.rsplit("_", 1)[-1]``，
    因此群场景的规范化 ID 是去掉最后一个下划线之后的部分。
    """
    if scene == "group":
        return session_id.rsplit("_", 1)[-1], "qq_official_4_29_4_group_suffix"
    return session_id, "identity"


def _call(event: Any, name: str, default: Any = None) -> Any:
    """调用事件上的可选方法，失败时返回默认值。"""
    getter = getattr(event, name, None)
    if not callable(getter):
        return default
    try:
        return getter()
    except Exception:
        return default


def _platform_type(event: Any) -> str:
    """返回平台类型（qq_official），而不是平台实例 ID。"""
    name = _call(event, "get_platform_name")
    if not name:
        meta = getattr(event, "platform_meta", None)
        name = getattr(meta, "name", None)
    return str(name or "").strip().lower()


def _platform_instance_id(event: Any) -> str | None:
    """返回平台实例 ID（配置中的 id），用于隔离多 Bot。"""
    value = _call(event, "get_platform_id")
    if not value:
        meta = getattr(event, "platform_meta", None)
        value = getattr(meta, "id", None)
    return _text(value)


def _raw(event: Any) -> Any:
    """定位原始 QQ 消息对象。

    event.message_obj 是 AstrBotMessage，其 raw_message 才是 botpy 原始消息。
    """
    message_obj = getattr(event, "message_obj", None)
    candidates = (
        getattr(message_obj, "raw_message", None),
        getattr(message_obj, "raw_event", None),
        getattr(event, "raw_message", None),
        getattr(event, "raw_event", None),
        message_obj,
    )
    for candidate in candidates:
        if candidate is not None:
            return candidate
    return None


def _field(obj: Any, name: str) -> Any:
    if obj is None:
        return None
    if isinstance(obj, dict):
        return obj.get(name)
    return getattr(obj, name, None)


def _author(event: Any) -> Any:
    """定位原始消息中的 author 对象。"""
    raw = _raw(event)
    author = _field(raw, "author")
    if author is not None:
        return author
    if isinstance(raw, dict):
        data = raw.get("d") if isinstance(raw.get("d"), dict) else raw
        if isinstance(data, dict):
            return data.get("author")
    return getattr(getattr(event, "message_obj", None), "sender", None)


def _detect_scene(event: Any, raw: Any) -> Scene:
    """判定 friend / group / channel。

    频道消息由 ``on_at_message_create`` 回调进入，原始负载带 ``channel_id``；
    普通群消息带 ``group_openid``；私聊/C2C 两者都没有。
    """
    if _field(raw, "channel_id"):
        return "channel"
    if _field(raw, "group_openid"):
        return "group"

    event_type = str(_call(event, "get_message_type", "") or "").upper()
    if "FRIEND" in event_type or "PRIVATE" in event_type or "C2C" in event_type:
        return "friend"

    # 事件类型不可用时回退到 sender/group 组件判断
    message_obj = getattr(event, "message_obj", None)
    if getattr(message_obj, "group", None) is not None and _field(raw, "channel_id"):
        return "channel"
    if getattr(message_obj, "sender", None) is not None and _field(raw, "group_openid") is None:
        return "friend"
    return "group"


def _target_id(raw: Any, scene: Scene, session_id: str) -> str | None:
    """提取 QQ 原生真实发送目标 ID。"""
    if scene == "group":
        return _text(_field(raw, "group_openid"))
    if scene == "channel":
        return _text(_field(raw, "channel_id"))
    # 私聊：优先 user_openid（C2C），其次 author.id（频道私聊），最后退回会话 ID
    author = _field(raw, "author")
    return _text(_field(author, "user_openid") or _field(author, "id")) or session_id


def _sender_info(raw: Any, event: Any) -> tuple[str | None, str | None]:
    author = _author(event)
    sender_id = _text(
        _field(author, "member_openid")
        or _field(author, "user_openid")
        or _field(author, "id")
    )
    sender_name = _text(_field(author, "username") or _field(author, "nickname"))
    return sender_id, sender_name


def _message_id(raw: Any, event: Any) -> str | None:
    """提取 QQ 消息 ID。

    AstrBotMessage.message_id 由适配器从 QQ payload 填充；
    event.message_obj.message_id 是兄弟插件使用的可靠入口。
    """
    message_obj = getattr(event, "message_obj", None)
    return _text(
        _field(message_obj, "message_id")
        or _field(raw, "id")
        or _call(event, "get_message_id")
    )


def _content(event: Any, raw: Any) -> str:
    message_obj = getattr(event, "message_obj", None)
    value = (
        _field(message_obj, "message_str")
        or _call(event, "get_message_str")
        or _field(raw, "content")
    )
    return _text(value) or ""


def _raw_json(raw: Any) -> str | None:
    if raw is None:
        return None
    try:
        return json.dumps(raw, ensure_ascii=False, default=lambda x: getattr(x, "__dict__", str(x)))
    except (TypeError, ValueError):
        return None


def _timestamp(event: Any) -> int:
    """取得消息时间戳。

    4.29.4 的适配器在解析时用 ``int(time.time())`` 填充 ``abm.timestamp``，
    因此该值是**适配器解析时间**，不是 QQ 原始发送时间。
    事件对象上另有 ``created_at``（事件创建时间），二者都不保证等于 QQ 侧时间。
    """
    message_obj = getattr(event, "message_obj", None)
    for value in (
        getattr(message_obj, "timestamp", None),
        getattr(event, "created_at", None),
    ):
        try:
            if value is not None:
                return int(value)
        except (TypeError, ValueError):
            continue
    return int(time.time())


def normalize_event(event: Any, *, store_raw: bool = True) -> NormalizedMessage | None:
    """把 AstrBot 事件规范化为内部消息模型；非 QQ 官方平台返回 None。"""
    if _platform_type(event) != "qq_official":
        return None

    session_id = _text(_call(event, "get_session_id") or getattr(event, "session_id", None))
    if not session_id:
        return None

    raw = _raw(event)
    scene = _detect_scene(event, raw)
    target_id = _target_id(raw, scene, session_id) or session_id
    normalized, rule = normalize_session_id(session_id, scene)
    sender_id, sender_name = _sender_info(raw, event)
    message_id = _message_id(raw, event)

    platform_id = _platform_instance_id(event) or _platform_type(event)

    return NormalizedMessage(
        platform_id=platform_id,
        bot_id=_text(_call(event, "get_self_id")),
        session_id=session_id,
        normalized_session_id=normalized,
        target_id=target_id,
        scene=scene,
        message_id=message_id,
        sender_id=sender_id,
        sender_name=sender_name,
        direction="inbound",
        message_type=_text(_call(event, "get_message_type"), 64),
        content=_content(event, raw),
        raw_message_json=_raw_json(raw) if store_raw else None,
        timestamp=_timestamp(event),
    )
