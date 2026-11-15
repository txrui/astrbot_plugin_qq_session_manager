"""AstrBot 4.29.4 QQ 官方适配器兼容性常量。

这些常量来自对 4.29.4 实际运行环境的核对：

- 适配器真实类路径见 ``astrbot/core/platform/sources/qqofficial/qqofficial_platform_adapter.py``。
- ``send_by_session()`` 只负责转发，真正的发送实现是 ``_send_by_session_common()``，
  兼容层必须包装后者。
- 适配器在实例上维护 ``_session_last_message_id`` 与 ``_session_scene`` 两个字典，
  分别缓存 ``session_id -> 最近消息 ID`` 与 ``session_id -> 场景``。
- ``MessageSession.platform_id`` 取自平台配置中的 ``id``，因此同一部署里
  平台实例 ID 是 ``小瑞`` 这类自定义名称，而不是 ``qq_official``。
"""
from __future__ import annotations

ADAPTER_IMPORT_PATH = (
    "astrbot.core.platform.sources.qqofficial.qqofficial_platform_adapter."
    "QQOfficialPlatformAdapter"
)
ADAPTER_CLASS_PATH = ADAPTER_IMPORT_PATH
SEND_METHOD = "_send_by_session_common"
PUBLIC_SEND_METHOD = "send_by_session"
SUPPORTED_VERSION = ">=4.29.4,<4.30.0"

# 平台类型（用于区分 qq_official 与 qq_official_webhook 等）
PLATFORM_TYPE = "qq_official"

# 场景取值，与适配器 ``_session_scene`` 缓存一致
SCENES = ("friend", "group", "channel")

# 会话 ID 规范化规则版本
NORMALIZATION_RULE_GROUP = "qq_official_4_29_4_group_suffix"
NORMALIZATION_RULE_IDENTITY = "identity"


def is_supported_adapter(adapter) -> bool:
    """判断给定对象是否是结构兼容的 QQ 官方适配器实例。"""
    cls = type(adapter)
    path = f"{cls.__module__}.{cls.__name__}"
    return path == ADAPTER_CLASS_PATH and hasattr(adapter, SEND_METHOD)
