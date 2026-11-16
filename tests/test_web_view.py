"""官方插件视图后端 API 测试。"""
from __future__ import annotations

import sys
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PLUGIN_ROOT.parent))
sys.path.insert(0, str(PLUGIN_ROOT))
sys.path.insert(0, str(PLUGIN_ROOT / "tests"))

from fixtures import GROUP_OPENID, make_event  # noqa: E402

try:
    from core.normalizer import normalize_event
    from core.repository import Repository
    from core.web_view import API_PREFIX, ManagementService, WebViewAPI
except ImportError:
    from astrbot_plugin_qq_session_manager.core.normalizer import normalize_event  # type: ignore
    from astrbot_plugin_qq_session_manager.core.repository import Repository  # type: ignore
    from astrbot_plugin_qq_session_manager.core.web_view import (  # type: ignore
        API_PREFIX,
        ManagementService,
        WebViewAPI,
    )


def _message(**kwargs):
    message = normalize_event(make_event(**kwargs))
    assert message is not None
    return message


def test_management_service_lists_sessions_and_messages(tmp_path):
    repo = Repository(tmp_path / "sessions.db")
    repo.upsert_message(_message())
    service = ManagementService(repo, {})

    sessions = service.list_sessions()
    assert sessions["total"] == 1
    assert sessions["items"][0]["scene"] == "group"
    assert sessions["items"][0]["status_text"] == "enabled"

    messages = service.list_messages()
    assert messages["total"] == 1
    assert messages["items"][0]["content"] == "hello"
    repo.close()


def test_management_service_filters_and_updates_route(tmp_path):
    repo = Repository(tmp_path / "sessions.db")
    repo.upsert_message(_message())
    service = ManagementService(repo, {})

    assert service.list_sessions(keyword="不存在")["total"] == 0
    assert service.list_sessions(scene="friend")["total"] == 0

    changed = service.set_route_status(
        platform_id="小瑞",
        session_id=GROUP_OPENID,
        scene="group",
        enabled=False,
    )
    assert changed is True
    assert service.list_sessions(status="disabled")["total"] == 1

    changed = service.set_route_status(
        platform_id="小瑞",
        session_id=GROUP_OPENID,
        scene="group",
        enabled=True,
    )
    assert changed is True
    assert service.list_sessions(status="enabled")["total"] == 1
    repo.close()


def test_management_service_deletes_route_and_messages(tmp_path):
    repo = Repository(tmp_path / "sessions.db")
    repo.upsert_message(_message())
    service = ManagementService(repo, {})

    assert service.delete_route(
        platform_id="小瑞", session_id=GROUP_OPENID, scene="group"
    ) is True
    assert service.list_sessions()["total"] == 0
    assert service.list_messages()["total"] == 0
    repo.close()


def test_view_api_prefix():
    assert API_PREFIX == "/astrbot_plugin_qq_session_manager/view"


def test_web_view_api_registers_routes_with_plugin_prefix():
    registered = []

    class FakeContext:
        def register_web_api(self, route, handler, methods, description):
            registered.append((route, handler, methods, description))

    class FakePlugin:
        context = FakeContext()

    plugin = FakePlugin()
    service = ManagementService(Repository(Path("/tmp/astrbot_qq_session_manager_test.db")), {})
    try:
        WebViewAPI(plugin, service).register()
    finally:
        service.repository.close()
        Path("/tmp/astrbot_qq_session_manager_test.db").unlink(missing_ok=True)

    assert [route for route, _, _, _ in registered] == [
        "/astrbot_plugin_qq_session_manager/view/status",
        "/astrbot_plugin_qq_session_manager/view/sessions",
        "/astrbot_plugin_qq_session_manager/view/messages",
        "/astrbot_plugin_qq_session_manager/view/route/status",
        "/astrbot_plugin_qq_session_manager/view/route/delete",
        "/astrbot_plugin_qq_session_manager/view/cleanup",
        "/astrbot_plugin_qq_session_manager/view/group_names/backfill",
    ]


def test_cleanup_removes_expired_route_and_its_messages(tmp_path):
    repo = Repository(tmp_path / "sessions.db")
    repo.upsert_message(_message(), expires_at=1)
    service = ManagementService(repo, {"message_retention_days": 0})

    result = service.cleanup()

    assert result["expired_sessions"] == 1
    assert result["expired_messages"] == 0
    assert service.list_sessions()["total"] == 0
    assert service.list_messages()["total"] == 0
    repo.close()


def test_cleanup_removes_messages_beyond_retention(tmp_path):
    repo = Repository(tmp_path / "sessions.db")
    repo.upsert_message(_message())
    conn = repo._connection()
    with conn:
        conn.execute(
            "UPDATE session_messages SET timestamp=? WHERE message_id=?",
            (1, "MSG0000000000000000000000000001"),
        )
    service = ManagementService(repo, {"message_retention_days": 1})

    result = service.cleanup()

    assert result["expired_sessions"] == 0
    assert result["expired_messages"] == 1
    assert service.list_messages()["total"] == 0
    repo.close()


def test_delete_route_updates_database_and_api_result(tmp_path):
    repo = Repository(tmp_path / "sessions.db")
    repo.upsert_message(_message())
    service = ManagementService(repo, {})

    assert service.delete_route(
        platform_id="小瑞", session_id=GROUP_OPENID, scene="group"
    ) is True
    assert service.list_sessions()["total"] == 0
    assert service.list_messages()["total"] == 0
    assert (
        service.delete_route(
            platform_id="小瑞", session_id=GROUP_OPENID, scene="group"
        )
        is False
    )
    repo.close()


def test_route_status_reports_not_changed_for_missing_route(tmp_path):
    """对不存在的路由启用/禁用应返回 changed=False，供前端区分假成功。"""
    repo = Repository(tmp_path / "sessions.db")
    service = ManagementService(repo, {})

    assert (
        service.set_route_status(
            platform_id="小瑞",
            session_id="NOT_EXIST",
            scene="group",
            enabled=False,
        )
        is False
    )
    repo.close()


def test_list_sessions_paginates_beyond_first_page(tmp_path):
    """超过单页上限时，翻页必须能取到后续会话，不能只返回第一页。"""
    repo = Repository(tmp_path / "sessions.db")
    for index in range(5):
        repo.upsert_message(
            _message(
                session_id=f"SESSION{index:02d}",
                message_id=f"MSG{index:02d}",
                content=f"msg-{index}",
            )
        )
    service = ManagementService(repo, {})

    first = service.list_sessions(limit=2, offset=0)
    second = service.list_sessions(limit=2, offset=2)
    third = service.list_sessions(limit=2, offset=4)

    assert first["total"] == 5
    assert len(first["items"]) == 2
    assert first["has_more"] is True
    assert len(second["items"]) == 2
    assert len(third["items"]) == 1
    assert third["has_more"] is False

    # 三页的会话 ID 不能重复，否则翻页只是在重复同一批数据
    seen = [
        item["session_id"]
        for page in (first, second, third)
        for item in page["items"]
    ]
    assert len(seen) == len(set(seen)) == 5
    repo.close()


def test_list_messages_paginates_beyond_first_page(tmp_path):
    """聊天记录同样需要能翻页查看更早的消息。

    多条消息必须落在同一个会话里，否则测的是过滤而不是分页。
    """
    repo = Repository(tmp_path / "sessions.db")
    for index in range(5):
        repo.upsert_message(
            _message(
                session_id=GROUP_OPENID,
                message_id=f"MSG{index:02d}",
                content=f"msg-{index}",
                timestamp=1_700_000_000 + index,
            )
        )
    service = ManagementService(repo, {})

    first = service.list_messages(session_id=GROUP_OPENID, limit=2, offset=0)
    assert first["total"] == 5
    assert len(first["items"]) == 2
    assert first["has_more"] is True

    second = service.list_messages(session_id=GROUP_OPENID, limit=2, offset=2)
    assert len(second["items"]) == 2
    assert second["has_more"] is True

    third = service.list_messages(session_id=GROUP_OPENID, limit=2, offset=4)
    assert len(third["items"]) == 1
    assert third["has_more"] is False

    # 三页取到的消息 ID 不能重复，否则翻页只是在重复同一批数据
    seen = [
        item["message_id"]
        for page in (first, second, third)
        for item in page["items"]
    ]
    assert len(seen) == len(set(seen)) == 5
    repo.close()


def test_list_sessions_total_is_not_capped_by_internal_row_limit(tmp_path):
    """会话总数不能被仓储层的取行上限截断。

    旧实现先取 1000 行再在 Python 里切片，超过 1000 条的会话永远翻不到，
    且 total 会被固定为 1000。
    """
    repo = Repository(tmp_path / "sessions.db")
    conn = repo._connection()
    with conn:
        conn.executemany(
            "INSERT INTO sessions(platform_id,bot_id,platform,session_id,"
            "normalized_session_id,target_id,scene,last_active_at,created_at,"
            "updated_at,status) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            [
                (
                    "小瑞",
                    "102774486",
                    "qq_official",
                    f"BULK{index:05d}",
                    f"BULK{index:05d}",
                    f"BULK{index:05d}",
                    "group",
                    1_700_000_000 + index,
                    1_700_000_000,
                    1_700_000_000,
                    1,
                )
                for index in range(1200)
            ],
        )
    service = ManagementService(repo, {})

    first = service.list_sessions(limit=200, offset=0)
    assert first["total"] == 1200
    assert len(first["items"]) == 200
    assert first["has_more"] is True

    # 第 1000 条之后的会话必须仍然翻得到
    beyond = service.list_sessions(limit=200, offset=1000)
    assert len(beyond["items"]) == 200
    assert beyond["has_more"] is False
    # 排序为 last_active_at DESC，而夹具时间戳递增，因此第 1000 条之后是时间最早的那些
    assert beyond["items"][0]["session_id"] == "BULK00199"
    assert beyond["items"][-1]["session_id"] == "BULK00000"
    repo.close()


def test_list_sessions_status_filter_is_applied_before_pagination(tmp_path):
    """状态过滤必须在分页之前生效。

    若先切片再过滤，被过滤掉的行会让当前页不足 limit，翻页也会漏行。
    """
    repo = Repository(tmp_path / "sessions.db")
    for index in range(6):
        repo.upsert_message(
            _message(
                session_id=f"SESSION{index:02d}",
                message_id=f"MSG{index:02d}",
                content=f"msg-{index}",
            )
        )
    # 把其中三条禁用
    conn = repo._connection()
    with conn:
        conn.execute(
            "UPDATE sessions SET status=0 WHERE session_id IN "
            "('SESSION00','SESSION02','SESSION04')"
        )
    service = ManagementService(repo, {})

    disabled = service.list_sessions(status="disabled", limit=10, offset=0)
    assert disabled["total"] == 3
    assert len(disabled["items"]) == 3
    assert all(item["status_text"] == "disabled" for item in disabled["items"])

    enabled = service.list_sessions(status="enabled", limit=10, offset=0)
    assert enabled["total"] == 3
    assert len(enabled["items"]) == 3
    assert all(item["status_text"] == "enabled" for item in enabled["items"])
    repo.close()
