from __future__ import annotations

try:
    from .exceptions import UnsupportedScene
except ImportError:
    from exceptions import UnsupportedScene

SUPPORTED_SCENES = {"friend", "group", "channel"}


class SessionResolver:
    """把路由查询请求交给仓储，并做场景合法性校验。"""

    def __init__(self, repository):
        self.repository = repository

    def resolve(
        self,
        *,
        platform_id: str,
        session_id: str,
        scene: str,
        target_id: str | None = None,
    ):
        """查询可用路由。

        所有查询都必须带 ``platform_id``，避免多 Bot 之间串线。
        未找到 / 过期 / 禁用 / 歧义由仓储抛出对应的明确异常。
        """
        if scene not in SUPPORTED_SCENES:
            raise UnsupportedScene(f"不支持的场景: {scene}")
        if not platform_id:
            raise UnsupportedScene("缺少平台实例 ID，无法安全查询路由")
        return self.repository.find_route(platform_id, session_id, scene, target_id)
