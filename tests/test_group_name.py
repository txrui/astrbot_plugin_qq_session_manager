"""群名称反查与回填测试。

覆盖：
- openid 判定（频道 ID 等不适用群接口）
- OpenAPI 反查、成功缓存、失败退避
- 仓储层群名写入与"已有群名不被清空"
- 视图层回填
"""
from __future__ import annotations

import asyncio
import sqlite3
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent))

from fixtures import GROUP_OPENID, make_event  # noqa: E402

try:
    from core.group_name import GroupNameResolver, is_official_group_openid
    from core.normalizer import normalize_event
    from core.repository import Repository
    from core.web_view import ManagementService
except ImportError:
    from astrbot_plugin_qq_session_manager.core.group_name import (  # type: ignore
        GroupNameResolver,
        is_official_group_openid,
    )
    from astrbot_plugin_qq_session_manager.core.normalizer import (  # type: ignore
        normalize_event,
    )
    from astrbot_plugin_qq_session_manager.core.repository import Repository  # type: ignore
    from astrbot_plugin_qq_session_manager.core.web_view import (  # type: ignore
        ManagementService,
    )


class _FakeHttp:
    """假的 botpy BotHttp：记录请求次数，按脚本返回结果或抛错。"""

    def __init__(self, results):
        self.results = list(results)
        self.routes = []

    async def request(self, route, **kwargs):
        self.routes.append(route)
        result = self.results.pop(0) if self.results else None
        if isinstance(result, Exception):
            raise result
        return result


def _fake_event(http, platform_name="qq_official"):
    client = SimpleNamespace(api=SimpleNamespace(_http=http))
    return SimpleNamespace(
        get_platform_name=lambda: platform_name,
        client=client,
        platform_meta=SimpleNamespace(name=platform_name),
    )


def _message(**kwargs):
    message = normalize_event(make_event(**kwargs))
    assert message is not None
    return message


# --------------------------------------------------------------- openid 判定


def test_openid_detection():
    assert is_official_group_openid(GROUP_OPENID) is True
    # 频道 ID 是纯数字，不适用群接口
    assert is_official_group_openid("1234567890") is False
    assert is_official_group_openid("") is False
    assert is_official_group_openid("ZZZZZZZZZZZZZZZZZZZZZZZZ") is False


# --------------------------------------------------------------- 反查与缓存


def test_resolve_group_name_and_cache():
    resolver = GroupNameResolver()
    http = _FakeHttp([{"group_name": "机器人测试群"}])
    event = _fake_event(http)

    name = asyncio.run(resolver.resolve(GROUP_OPENID, event=event))
    assert name == "机器人测试群"
    assert len(http.routes) == 1

    # 第二次命中缓存，不再发请求
    again = asyncio.run(resolver.resolve(GROUP_OPENID, event=event))
    assert again == "机器人测试群"
    assert len(http.routes) == 1


def test_resolve_skips_channel_id():
    resolver = GroupNameResolver()
    http = _FakeHttp([{"group_name": "不该被查到"}])
    event = _fake_event(http)

    name = asyncio.run(resolver.resolve("1234567890", event=event))
    assert name == ""
    assert http.routes == []


def test_resolve_skips_non_official_platform():
    resolver = GroupNameResolver()
    http = _FakeHttp([{"group_name": "不该被查到"}])
    event = _fake_event(http, platform_name="aiocqhttp")

    name = asyncio.run(resolver.resolve(GROUP_OPENID, event=event))
    assert name == ""
    assert http.routes == []


def test_resolve_failure_is_cached():
    """失败也要进缓存，避免每条消息都打一次请求。"""
    resolver = GroupNameResolver()
    http = _FakeHttp([RuntimeError("boom"), {"group_name": "机器人测试群"}])
    event = _fake_event(http)

    assert asyncio.run(resolver.resolve(GROUP_OPENID, event=event)) == ""
    assert asyncio.run(resolver.resolve(GROUP_OPENID, event=event)) == ""
    assert len(http.routes) == 1


def test_resolve_without_client_returns_empty():
    resolver = GroupNameResolver()
    event = SimpleNamespace(
        get_platform_name=lambda: "qq_official",
        client=None,
        bot=None,
        platform_meta=SimpleNamespace(name="qq_official"),
    )
    assert asyncio.run(resolver.resolve(GROUP_OPENID, event=event)) == ""


def test_concurrent_lookups_share_one_request():
    resolver = GroupNameResolver()
    http = _FakeHttp([{"group_name": "并发群"}])
    event = _fake_event(http)

    async def race():
        return await asyncio.gather(
            *(resolver.resolve(GROUP_OPENID, event=event) for _ in range(5))
        )

    assert asyncio.run(race()) == ["并发群"] * 5
    assert len(http.routes) == 1


# --------------------------------------------------------------- 仓储层


def test_group_name_is_persisted(tmp_path):
    repo = Repository(tmp_path / "x.db")
    repo.upsert_message(_message(), group_name="机器人测试群")

    rows = repo.list_sessions()
    assert rows[0]["group_name"] == "机器人测试群"
    repo.close()


def test_group_name_is_not_cleared_by_later_message(tmp_path):
    """后续查不到群名时，不应把已知的群名清空。"""
    repo = Repository(tmp_path / "x.db")
    repo.upsert_message(_message(), group_name="机器人测试群")
    repo.upsert_message(_message(message_id="MSG_SECOND"), group_name=None)

    rows = repo.list_sessions()
    assert rows[0]["group_name"] == "机器人测试群"
    repo.close()


def test_update_group_name(tmp_path):
    repo = Repository(tmp_path / "x.db")
    repo.upsert_message(_message())

    assert repo.update_group_name("小瑞", GROUP_OPENID, "group", "机器人测试群") is True
    assert repo.list_sessions()[0]["group_name"] == "机器人测试群"
    # 空群名不写入
    assert repo.update_group_name("小瑞", GROUP_OPENID, "group", "  ") is False
    repo.close()


def test_list_sessions_missing_group_name(tmp_path):
    repo = Repository(tmp_path / "x.db")
    repo.upsert_message(_message(session_id="S_WITH"), group_name="有名字群")
    repo.upsert_message(_message(session_id="S_WITHOUT", message_id="MSG_B"))

    missing = repo.list_sessions_missing_group_name()
    assert len(missing) == 1
    assert missing[0]["session_id"] == "S_WITHOUT"
    repo.close()


def test_search_matches_group_name(tmp_path):
    repo = Repository(tmp_path / "x.db")
    repo.upsert_message(_message(), group_name="机器人测试群")

    assert len(repo.list_sessions(keyword="机器人测试群")) == 1
    assert len(repo.list_sessions(keyword="不存在的群")) == 0
    repo.close()


def test_legacy_database_gets_group_name_column(tmp_path):
    """v1 旧库（没有 group_name 列）升级后应能正常读写。"""
    db = tmp_path / "legacy.db"
    conn = sqlite3.connect(db)
    conn.executescript(
        """CREATE TABLE sessions(
             id INTEGER PRIMARY KEY,
             platform_id TEXT NOT NULL,
             bot_id TEXT,
             platform TEXT NOT NULL,
             session_id TEXT NOT NULL,
             normalized_session_id TEXT NOT NULL,
             target_id TEXT NOT NULL,
             scene TEXT NOT NULL,
             message_type TEXT,
             last_message_id TEXT,
             last_active_at INTEGER NOT NULL,
             created_at INTEGER NOT NULL,
             updated_at INTEGER NOT NULL,
             expires_at INTEGER,
             status INTEGER NOT NULL DEFAULT 1,
             id_normalization_rule TEXT
           )"""
    )
    conn.execute(
        "INSERT INTO sessions(platform_id,bot_id,platform,session_id,normalized_session_id,"
        "target_id,scene,last_active_at,created_at,updated_at,status) "
        "VALUES('小瑞',NULL,'qq_official',?,?,?,'group',1,1,1,1)",
        (GROUP_OPENID, GROUP_OPENID, GROUP_OPENID),
    )
    conn.commit()
    conn.close()

    repo = Repository(db)
    assert repo.update_group_name("小瑞", GROUP_OPENID, "group", "旧库群名") is True
    assert repo.list_sessions()[0]["group_name"] == "旧库群名"
    repo.close()


# --------------------------------------------------------------- 视图层


def test_backfill_group_names(tmp_path):
    repo = Repository(tmp_path / "x.db")
    # 两个会话路由指向同一个群 openid，群名按 target_id 反查
    repo.upsert_message(_message(session_id="S_A", message_id="MSG_A"))
    repo.upsert_message(_message(session_id="S_B", message_id="MSG_B"))

    class FakeResolver:
        async def resolve(self, group_openid, *, event=None, context=None):
            return "机器人测试群" if group_openid == GROUP_OPENID else ""

    service = ManagementService(repo, {}, FakeResolver())
    result = asyncio.run(service.backfill_group_names())

    assert result["checked"] == 2
    assert result["updated"] == 2
    assert result["failed"] == 0
    assert len(repo.list_sessions(keyword="机器人测试群")) == 2
    repo.close()


def test_backfill_reports_failed_lookups(tmp_path):
    """查不到群名的会话保持原样，计入 failed，等待下次重试。"""
    repo = Repository(tmp_path / "x.db")
    repo.upsert_message(_message(session_id="S_A", message_id="MSG_A"))

    class FakeResolver:
        async def resolve(self, group_openid, *, event=None, context=None):
            return ""

    service = ManagementService(repo, {}, FakeResolver())
    result = asyncio.run(service.backfill_group_names())

    assert result["checked"] == 1
    assert result["updated"] == 0
    assert result["failed"] == 1
    assert result["remaining"] == 1
    repo.close()


def test_backfill_skips_sessions_already_named(tmp_path):
    repo = Repository(tmp_path / "x.db")
    repo.upsert_message(_message(), group_name="已有群名")

    class FakeResolver:
        async def resolve(self, group_openid, *, event=None, context=None):
            raise AssertionError("已有群名的会话不应发起反查")

    service = ManagementService(repo, {}, FakeResolver())
    result = asyncio.run(service.backfill_group_names())
    assert result["checked"] == 0
    assert result["updated"] == 0
    repo.close()
