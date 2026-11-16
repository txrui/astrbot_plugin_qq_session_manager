"""采集器测试：异步入口、异常隔离、配置开关。"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from fixtures import GROUP_OPENID, make_event  # noqa: E402

try:
    from core.collector import EventCollector
    from core.repository import Repository
    from core.session_service import SessionService
except ImportError:
    from astrbot_plugin_qq_session_manager.core.collector import EventCollector  # type: ignore
    from astrbot_plugin_qq_session_manager.core.repository import Repository  # type: ignore
    from astrbot_plugin_qq_session_manager.core.session_service import SessionService  # type: ignore


def test_collect_returns_normalized_message(tmp_path):
    repo = Repository(tmp_path / "x.db")
    collector = EventCollector(SessionService(repo, {}))
    message = asyncio.run(collector.handle(make_event(scene="group")))
    assert message is not None
    assert message.scene == "group"
    assert message.target_id == GROUP_OPENID
    repo.close()


def test_collector_swallows_errors(tmp_path):
    """采集失败不应让异常冒泡到事件处理流程。"""

    class Boom:
        def collect(self, event):
            raise RuntimeError("数据库炸了")

    collector = EventCollector(Boom())
    assert asyncio.run(collector.handle(make_event())) is None


def test_collector_ignores_other_platforms(tmp_path):
    repo = Repository(tmp_path / "x.db")
    collector = EventCollector(SessionService(repo, {}))
    result = asyncio.run(collector.handle(make_event(platform_type="aiocqhttp")))
    assert result is None
    assert repo.counts()["messages"] == 0
    repo.close()
