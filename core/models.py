from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

Scene = Literal["friend", "group", "channel"]
Direction = Literal["inbound", "outbound"]

@dataclass(frozen=True)
class SessionRoute:
    platform_id: str
    bot_id: str | None
    platform: str
    session_id: str
    normalized_session_id: str
    target_id: str
    scene: Scene
    message_type: str | None = None
    last_message_id: str | None = None
    last_active_at: int = 0
    expires_at: int | None = None
    status: int = 1
    id_normalization_rule: str | None = None
    group_name: str | None = None

@dataclass(frozen=True)
class NormalizedMessage:
    platform_id: str
    bot_id: str | None
    session_id: str
    normalized_session_id: str
    target_id: str
    scene: Scene
    message_id: str | None
    sender_id: str | None
    sender_name: str | None
    direction: Direction
    message_type: str | None
    content: str
    raw_message_json: str | None
    timestamp: int
    imported: int = 0
    id_normalization_rule: str | None = None

@dataclass(frozen=True)
class AdapterResult:
    success: bool
    message_id: str | None = None
    raw: Any = None
