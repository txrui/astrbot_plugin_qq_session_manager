"""QQ 官方适配器识别辅助函数。"""
from __future__ import annotations

try:
    from .version_4294 import ADAPTER_IMPORT_PATH, is_supported_adapter
except ImportError:
    from version_4294 import ADAPTER_IMPORT_PATH, is_supported_adapter

__all__ = ["ADAPTER_IMPORT_PATH", "is_supported_adapter"]
