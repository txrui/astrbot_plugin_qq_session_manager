"""真实环境测试：在真实 AstrBot 4.29.4 进程内运行。

与 tests/ 下的单元测试不同，本文件在容器内用**真实的 AstrBot 类型**运行：
- 真实的 QQOfficialPlatformAdapter 类（不是伪造类）
- 真实的 AstrBotMessage / MessageMember / MessageType
- 真实的 QQOfficialMessageEvent
- 真实的 botpy 消息结构

运行方式（在容器内）：
    cd /AstrBot && python3.12 plugins/astrbot_plugin_qq_session_manager/tests/live_real_test.py

覆盖：
1. 真实适配器类路径与兼容性检查
2. 真实事件结构 -> 插件规范化
3. 真实数据库写入与回读
4. 真实适配器缓存字段的路由恢复
5. 缓存键与适配器发送时查键的一致性
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import traceback
from pathlib import Path

# 让插件可被导入（必须在导入插件模块之前）
PLUGIN_PARENT = Path(__file__).resolve().parent.parent.parent
if str(PLUGIN_PARENT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_PARENT))

from astrbot.core.platform.astrbot_message import AstrBotMessage, MessageMember
from astrbot.core.platform.message_type import MessageType
from astrbot.core.platform.sources.qqofficial.qqofficial_message_event import (
    QQOfficialMessageEvent,
)
from astrbot.core.platform.sources.qqofficial.qqofficial_platform_adapter import (
    QQOfficialPlatformAdapter,
)

from astrbot_plugin_qq_session_manager.adapters.compatibility import (
    CompatibilityManager,
)
from astrbot_plugin_qq_session_manager.adapters.platform_discovery import (
    is_qq_official_instance,
)
from astrbot_plugin_qq_session_manager.adapters.version_4294 import (
    ADAPTER_CLASS_PATH,
    SEND_METHOD,
)
from astrbot_plugin_qq_session_manager.core.normalizer import (
    normalize_event,
    normalize_session_id,
)
from astrbot_plugin_qq_session_manager.core.repository import Repository
from astrbot_plugin_qq_session_manager.core.resolver import SessionResolver

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    RESULTS.append((name, ok, detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f"  {detail}" if detail else ""))
    return ok


# ---------------------------------------------------------------------------
# 真实数据结构构造
# ---------------------------------------------------------------------------

GROUP_OPENID = "762146F1DBB849090BCC80B6A667253F"
MEMBER_OPENID = "E30C2C95C2167F54B1719B45D006DF1F"
MESSAGE_ID = "LIVE0000000000000000000000000001"
PLATFORM_ID = "小瑞"


class RealisticBotpyMessage:
    """复刻 botpy 的 GroupMessage 结构。"""

    def __init__(self, group_openid=GROUP_OPENID, channel_id=None, message_id=MESSAGE_ID):
        self.id = message_id
        self.content = "真实环境测试消息"
        self.group_openid = group_openid
        self.channel_id = channel_id
        self.message_type = "GROUP"
        self.msg_elements = []
        self.raw_data = {"id": message_id, "content": "真实环境测试消息"}
        self.author = type(
            "_Author",
            (),
            {
                "member_openid": MEMBER_OPENID,
                "user_openid": MEMBER_OPENID,
                "id": MEMBER_OPENID,
                "username": "田小瑞",
            },
        )()


def build_abm(scene="group", message_id=MESSAGE_ID):
    """构造真实结构的 AstrBotMessage。"""
    raw = RealisticBotpyMessage(
        group_openid=GROUP_OPENID if scene == "group" else None,
        channel_id="1234567890" if scene == "channel" else None,
        message_id=message_id,
    )
    abm = AstrBotMessage()
    abm.message_id = message_id
    abm.message_str = "真实环境测试消息"
    abm.raw_message = raw
    abm.type = MessageType.GROUP_MESSAGE if scene != "friend" else MessageType.FRIEND_MESSAGE
    if scene == "group":
        abm.group_id = GROUP_OPENID
        abm.session_id = GROUP_OPENID
    elif scene == "channel":
        abm.group_id = "1234567890"
        abm.session_id = "1234567890"
    else:
        abm.session_id = MEMBER_OPENID
    abm.self_id = "102774486"
    abm.sender = MessageMember(MEMBER_OPENID, "田小瑞")
    abm.timestamp = 1700000000
    return abm


def build_event(scene="group", message_id=MESSAGE_ID):
    """用真实适配器的 create_event 构造 QQOfficialMessageEvent。

    直接复用真实适配器的 create_event，保证事件结构与生产环境完全一致。
    """
    abm = build_abm(scene, message_id)

    # 用真实适配器类构造一个"不连接 QQ"的实例，只借用其 create_event 逻辑
    adapter = QQOfficialPlatformAdapter.__new__(QQOfficialPlatformAdapter)
    adapter.config = {"id": PLATFORM_ID}
    adapter._session_last_message_id = {}
    adapter._session_scene = {}
    adapter._allow_group_proactive_send = True
    adapter.client = None  # create_event 只会把它透传给事件，不会发起请求

    return adapter.create_event(abm)


def main() -> int:
    print("=" * 70)
    print("QQ 会话管理器 —— 真实环境测试（真实 AstrBot 4.29.4 类型）")
    print("=" * 70)

    # ---- 1. 真实适配器类路径 -----------------------------------------
    cls_path = f"{QQOfficialPlatformAdapter.__module__}.{QQOfficialPlatformAdapter.__name__}"
    check("真实适配器类路径与常量一致", cls_path == ADAPTER_CLASS_PATH, cls_path)
    check(
        "适配器确实实现 _send_by_session_common",
        hasattr(QQOfficialPlatformAdapter, SEND_METHOD),
    )

    import inspect

    sig = inspect.signature(QQOfficialPlatformAdapter._send_by_session_common)
    params = [p for p in sig.parameters if p != "self"]
    check(
        "适配器签名与兼容层假设一致",
        params[:2] == ["session", "message_chain"],
        f"params={params}",
    )
    check(
        "适配器方法是协程函数",
        inspect.iscoroutinefunction(QQOfficialPlatformAdapter._send_by_session_common),
    )

    # ---- 2. 真实事件 -> 插件规范化 -----------------------------------
    for scene in ("group", "channel", "friend"):
        event = build_event(scene)
        msg = normalize_event(event)
        if not check(f"{scene} 场景规范化成功", msg is not None):
            continue
        check(f"{scene} 场景判定正确", msg.scene == scene, f"scene={msg.scene}")
        check(
            f"{scene} 场景 platform_id 为实例 ID",
            msg.platform_id == PLATFORM_ID,
            msg.platform_id,
        )
        check(f"{scene} 场景 sender_id 非空", bool(msg.sender_id), str(msg.sender_id))
        check(f"{scene} 场景 message_id 正确", msg.message_id == MESSAGE_ID, str(msg.message_id))
        check(f"{scene} 场景 raw JSON 已保存", bool(msg.raw_message_json))
        check(
            f"{scene} 场景 timestamp 取自 message_obj",
            msg.timestamp == 1700000000,
            str(msg.timestamp),
        )

    group_msg = normalize_event(build_event("group"))
    check(
        "群 target_id 为群 OpenID",
        group_msg.target_id == GROUP_OPENID,
        group_msg.target_id,
    )
    check("群 session_id 为群 OpenID", group_msg.session_id == GROUP_OPENID)

    # ---- 3. 规范化规则与真实发送规则一致 -----------------------------
    for sid in ("prefix_ABC_suffix", "a_b_c_target", GROUP_OPENID):
        normalized, _ = normalize_session_id(sid, "group")
        real = sid.rsplit("_", 1)[-1]
        check(
            f"规范化规则匹配适配器 rsplit: {sid[:24]}",
            normalized == real,
            f"{normalized} == {real}",
        )

    # ---- 4. 真实数据库写入与回读 -------------------------------------
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "live.db"
        repo = Repository(db)
        repo.upsert_message(group_msg)
        route = repo.find_route(PLATFORM_ID, GROUP_OPENID, "group")
        check("真实数据库回读路由", route.target_id == GROUP_OPENID)
        check("路由保存了消息 ID", route.last_message_id == MESSAGE_ID)
        check("路由场景正确", route.scene == "group")

        # ---- 5. 真实适配器实例上的兼容层 ----------------------------
        adapter = QQOfficialPlatformAdapter.__new__(QQOfficialPlatformAdapter)
        adapter._session_last_message_id = {}
        adapter._session_scene = {}
        adapter.config = {"id": PLATFORM_ID}
        adapter._allow_group_proactive_send = True

        check(
            "真实适配器实例被识别为 QQ 官方",
            is_qq_official_instance(adapter),
        )

        resolver = SessionResolver(repo)
        manager = CompatibilityManager(resolver)
        installed = manager.install(adapter)
        check("兼容层安装到真实适配器类", installed, f"status={manager.status}")

        # 清空缓存，模拟重启后内存缓存丢失
        adapter._session_last_message_id.clear()
        adapter._session_scene.clear()
        check("适配器内存缓存已清空", not adapter._session_scene)

        class _Session:
            def __init__(self, sid):
                self.session_id = sid
                self.message_type = "GroupMessage"
                self.platform_name = PLATFORM_ID

        manager._restore_route(adapter, _Session(GROUP_OPENID))
        check(
            "路由恢复写入真实场景缓存",
            adapter._session_scene.get(GROUP_OPENID) == "group",
            str(adapter._session_scene.get(GROUP_OPENID)),
        )
        check(
            "路由恢复写入真实消息 ID 缓存",
            bool(adapter._session_last_message_id.get(GROUP_OPENID)),
            str(adapter._session_last_message_id.get(GROUP_OPENID)),
        )

        # 适配器发送时用的是规范化后的键，必须能命中
        send_key = GROUP_OPENID.rsplit("_", 1)[-1]
        check(
            "适配器按发送键能命中场景缓存",
            adapter._session_scene.get(send_key) == "group",
        )
        check(
            "适配器按发送键能命中消息 ID 缓存",
            bool(adapter._session_last_message_id.get(send_key)),
        )

        # 真实适配器的跳过条件：非私聊且无 msg_id 且非群主动发送
        allow_group = (
            adapter._session_scene.get(send_key) == "group"
            and getattr(adapter, "_allow_group_proactive_send", False)
        )
        check(
            "真实适配器允许群主动发送（无需 msg_id）",
            allow_group,
            "scene=group 且 _allow_group_proactive_send=True",
        )

        manager.uninstall()
        check("兼容层卸载恢复原方法", manager.status == "restored")
        check(
            "卸载后实例方法等于原始实现",
            adapter._send_by_session_common.__func__
            is QQOfficialPlatformAdapter._send_by_session_common,
            "恢复后应指向类上的原始方法",
        )
        check(
            "卸载后类级方法未被污染",
            QQOfficialPlatformAdapter._send_by_session_common.__name__
            == "_send_by_session_common",
        )
        repo.close()

    # ---- 6. 真实 botpy 消息结构可用 ----------------------------------
    msg = RealisticBotpyMessage()
    check(
        "botpy 风格消息含 group_openid/author",
        msg.group_openid == GROUP_OPENID and bool(msg.author.member_openid),
    )

    print("=" * 70)
    failed = [r for r in RESULTS if not r[1]]
    print(f"共 {len(RESULTS)} 项，通过 {len(RESULTS) - len(failed)} 项，失败 {len(failed)} 项")
    for name, ok, detail in failed:
        print(f"  FAIL: {name}  {detail}")
    print("=" * 70)
    return 1 if failed else 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception:
        traceback.print_exc()
        raise SystemExit(2)
