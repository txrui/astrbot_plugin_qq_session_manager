"""平台实例发现。

集中处理 AstrBot 平台实例的枚举与 QQ 官方适配器识别，避免在 main 里散落
对 platform_manager 内部 API 的假设。
"""
from __future__ import annotations

from typing import Any

try:
    from .version_4294 import ADAPTER_CLASS_PATH, PLATFORM_TYPE, SEND_METHOD
except ImportError:
    from version_4294 import ADAPTER_CLASS_PATH, PLATFORM_TYPE, SEND_METHOD


def _iter_platform_instances(platform_manager: Any) -> list[Any]:
    """枚举平台实例，兼容 get_insts / platform_insts 两种 API。"""
    if platform_manager is None:
        return []
    try:
        platforms = platform_manager.get_insts()
    except Exception:
        try:
            platforms = platform_manager.platform_insts
        except Exception:
            return []
    try:
        return list(platforms or [])
    except TypeError:
        return []


def _platform_type(inst: Any) -> str:
    try:
        meta = inst.meta()
    except Exception:
        return ""
    return str(getattr(meta, "name", "") or "").strip().lower()


def _platform_id(inst: Any) -> str:
    try:
        meta = inst.meta()
    except Exception:
        return ""
    return str(getattr(meta, "id", "") or "").strip()


def is_qq_official_instance(inst: Any) -> bool:
    """判断平台实例是否为 QQ 官方（WebSocket）适配器。"""
    if _platform_type(inst) != PLATFORM_TYPE:
        return False
    cls = type(inst)
    return f"{cls.__module__}.{cls.__name__}" == ADAPTER_CLASS_PATH


def find_qq_official_instances(context: Any) -> list[Any]:
    """返回当前已加载的所有 QQ 官方适配器实例。"""
    manager = getattr(context, "platform_manager", None)
    return [inst for inst in _iter_platform_instances(manager) if is_qq_official_instance(inst)]


def platform_id_of(inst: Any) -> str:
    """返回平台实例 ID（配置中的 id），用于多 Bot 隔离。"""
    return _platform_id(inst)


def describe_instances(instances: list[Any]) -> str:
    """生成用于日志的实例摘要。"""
    parts = []
    for inst in instances:
        parts.append(f"{_platform_id(inst) or '(无ID)'}({_platform_type(inst)})")
    return ", ".join(parts) if parts else "(无)"
