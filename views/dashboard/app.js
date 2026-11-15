const API_BASE = "view";

const state = {
  sessionOffset: 0,
  sessionLimit: 50,
  sessionTotal: 0,
  messageOffset: 0,
  messageLimit: 50,
  messageTotal: 0,
  messageRetentionDays: 0,
  conversationScene: "",
  conversationKeyword: "",
  conversations: [],
  selectedConversation: null,
};

const $ = (id) => document.getElementById(id);
const esc = (value) =>
  String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");

function unwrap(response) {
  const body = response?.data?.status ? response.data : response;
  if (body?.status === "ok") return body.data || {};
  if (body?.status === "error") throw new Error(body.message || "请求失败");
  if (body?.success === false) throw new Error(body.message || body.error || "请求失败");
  return body || {};
}

function getBridge() {
  return window.AstrBotPluginView || window.AstrBotPluginPage;
}

async function waitForBridge(timeout = 5000) {
  const deadline = Date.now() + timeout;
  while (!getBridge()) {
    if (Date.now() >= deadline) {
      throw new Error("插件页面桥接未就绪，请刷新后重试");
    }
    await new Promise((resolve) => setTimeout(resolve, 50));
  }
  const bridge = getBridge();
  await bridge.ready();
  return bridge;
}

async function apiGet(path, params = {}) {
  const bridge = await waitForBridge();
  return unwrap(await bridge.apiGet(`${API_BASE}/${String(path).replace(/^\/+/, "")}`, params));
}

async function apiPost(path, body = {}) {
  const bridge = await waitForBridge();
  return unwrap(await bridge.apiPost(`${API_BASE}/${String(path).replace(/^\/+/, "")}`, body));
}

let toastTimer = null;
function toast(message, error = false) {
  const node = $("toast");
  node.textContent = message;
  node.classList.toggle("error", error);
  node.classList.remove("hidden");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => node.classList.add("hidden"), 3000);
}

let confirmResolver = null;
let confirmPreviousFocus = null;

function closeConfirmDialog(confirmed) {
  const overlay = $("confirm-dialog");
  const submitButton = $("confirm-submit");
  const cancelButton = $("confirm-cancel");
  submitButton.disabled = true;
  cancelButton.disabled = true;
  overlay.classList.add("hidden");
  const resolver = confirmResolver;
  confirmResolver = null;
  if (confirmPreviousFocus instanceof HTMLElement) {
    confirmPreviousFocus.focus();
  }
  confirmPreviousFocus = null;
  if (resolver) resolver(confirmed);
}

function confirmAction({ title, message, submitText = "确认" }) {
  if (confirmResolver) {
    closeConfirmDialog(false);
  }
  $("confirm-title").textContent = title;
  $("confirm-message").textContent = message;
  $("confirm-submit").textContent = submitText;
  $("confirm-submit").disabled = false;
  $("confirm-cancel").disabled = false;
  confirmPreviousFocus = document.activeElement;
  $("confirm-dialog").classList.remove("hidden");
  $("confirm-cancel").focus();
  return new Promise((resolve) => {
    confirmResolver = resolve;
  });
}

function fmtTime(value) {
  if (!value) return "--";
  const date = new Date(Number(value) * 1000);
  return Number.isNaN(date.getTime()) ? String(value) : date.toLocaleString();
}

function fmtConversationTime(value) {
  if (!value) return "";
  const date = new Date(Number(value) * 1000);
  if (Number.isNaN(date.getTime())) return String(value);
  const now = new Date();
  if (date.toDateString() === now.toDateString()) {
    return date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  }
  if (date.getFullYear() === now.getFullYear()) {
    return date.toLocaleDateString([], { month: "2-digit", day: "2-digit" });
  }
  return date.toLocaleDateString([], { year: "numeric", month: "2-digit", day: "2-digit" });
}

function sceneText(scene) {
  return { friend: "私聊", group: "群聊", channel: "频道" }[scene] || scene || "会话";
}

function statusBadge(status) {
  const labels = { enabled: "正常", disabled: "已禁用", expired: "已过期" };
  return `<span class="badge badge-${esc(status)}">${esc(labels[status] || status)}</span>`;
}

async function loadStatus() {
  const data = await apiGet("/status");
  $("stat-sessions").textContent = data.counts?.sessions ?? 0;
  $("stat-messages").textContent = data.counts?.messages ?? 0;
  $("stat-health").textContent = data.health?.integrity === "ok" ? "正常" : (data.health?.integrity || "未知");
  $("stat-recovery").textContent = data.route_recovery_enabled ? "已启用" : "已关闭";
  state.messageRetentionDays = Number(data.message_retention_days) || 0;
}

function renderSessions(data) {
  const rows = $("session-rows");
  const items = data.items || [];
  state.sessionTotal = Number(data.total) || 0;
  renderSessionPagination();
  if (!items.length) {
    rows.innerHTML = '<tr><td colspan="6"><div class="empty">暂无匹配的会话路由</div></td></tr>';
    return;
  }
  rows.innerHTML = items
    .map((item) => {
      const action = item.status_text === "disabled"
        ? '<button class="button button-small" data-action="enable">启用</button>'
        : '<button class="button button-small" data-action="disable">禁用</button>';
      return `<tr>
        <td><span class="badge badge-scene">${esc(sceneText(item.scene))}</span></td>
        <td>
          <div class="mono">${esc(item.session_id)}</div>
          <div class="cell-sub mono">目标 ${esc(item.target_id)}</div>
        </td>
        <td><div class="mono">${esc(item.platform_id)}</div><div class="cell-sub">${esc(item.bot_id || "未知 Bot")}</div></td>
        <td><div class="mono">${esc(item.last_message_id || "--")}</div><div class="cell-sub">${esc(fmtTime(item.last_active_at))}</div></td>
        <td>${statusBadge(item.status_text)}</td>
        <td><div class="actions">${action}<button class="button button-small" data-action="delete">删除</button></div></td>
      </tr>`;
    })
    .join("");

  rows.querySelectorAll("button[data-action]").forEach((button) => {
    button.onclick = () => handleRouteAction(button, button.dataset.action);
  });
}

function renderSessionPagination() {
  const total = state.sessionTotal;
  const start = total === 0 ? 0 : state.sessionOffset + 1;
  const end = Math.min(state.sessionOffset + state.sessionLimit, total);
  $("session-summary").textContent = `共 ${total} 条会话，当前显示 ${start}-${end}`;
  $("session-prev").disabled = state.sessionOffset <= 0;
  $("session-next").disabled = state.sessionOffset + state.sessionLimit >= total;
}

async function loadSessions() {
  const params = {
    keyword: $("session-keyword").value.trim(),
    scene: $("session-scene").value,
    status: $("session-status").value,
    limit: state.sessionLimit,
    offset: state.sessionOffset,
  };
  const data = await apiGet("/sessions", params);
  renderSessions(data);
}

async function handleRouteAction(button, action) {
  const row = button.closest("tr");
  const cells = row.querySelectorAll("td");
  const sessionId = cells[1]?.querySelector(".mono")?.textContent?.trim();
  const platformId = cells[2]?.querySelector(".mono")?.textContent?.trim();
  const scene = row.querySelector(".badge-scene")?.textContent?.trim();
  const sceneValue = { 私聊: "friend", 群聊: "group", 频道: "channel" }[scene];
  if (!sessionId || !platformId || !sceneValue) {
    toast("无法识别该会话，请刷新后重试", true);
    return;
  }

  if (action === "delete") {
    const confirmed = await confirmAction({
      title: "删除会话路由",
      message: "确定删除这条会话路由及其聊天记录吗？此操作无法撤销。",
      submitText: "确认删除",
    });
    if (!confirmed) return;
  }
  button.disabled = true;
  try {
    if (action === "delete") {
      toast("正在删除会话路由及聊天记录...");
      const result = await apiPost("/route/delete", { platform_id: platformId, session_id: sessionId, scene: sceneValue });
      if (result.deleted === false) {
        toast("会话不存在或已被删除", true);
      } else {
        toast("会话路由及聊天记录已删除");
      }
    } else {
      toast(action === "enable" ? "正在启用会话路由..." : "正在禁用会话路由...");
      const result = await apiPost("/route/status", {
        platform_id: platformId,
        session_id: sessionId,
        scene: sceneValue,
        enabled: action === "enable",
      });
      if (result.changed === false) {
        toast("会话路由状态未变化，可能已被删除或不存在", true);
      } else {
        toast(action === "enable" ? "会话路由已启用" : "会话路由已禁用");
      }
    }
    await Promise.all([loadSessions(), loadStatus()]);
    if ($("tab-messages").classList.contains("active")) {
      await loadConversations();
    }
  } catch (error) {
    toast(`操作失败：${error.message}`, true);
  } finally {
    button.disabled = false;
  }
}

function conversationKey(item) {
  return [item.platform_id, item.session_id, item.scene].join("|");
}

function conversationName(item) {
  const groupName = String(item.group_name || "").trim();
  if (groupName) return groupName;
  const target = String(item.target_id || "").trim();
  const session = String(item.session_id || "").trim();
  return target || session || "未知会话";
}

function conversationInitial(item) {
  if (item.scene === "group") return "群";
  if (item.scene === "channel") return "频";
  return "聊";
}

function conversationPreview(item) {
  const groupName = String(item.group_name || "").trim();
  const target = String(item.target_id || "").trim();
  const session = String(item.session_id || "").trim();
  // 有群名时副标题不再重复群号，直接展示群号
  if (groupName) return target || session || "暂无会话标识";
  if (target && session && target !== session) return `群号 ${session}`;
  return session || "暂无会话标识";
}

function renderConversationList() {
  const list = $("conversation-list");
  const items = state.conversations;
  $("conversation-count").textContent = `${items.length} 个会话`;
  if (!items.length) {
    list.innerHTML = '<div class="qq-conversation-empty">暂无匹配的群聊或私聊</div>';
    return;
  }

  list.innerHTML = items
    .map((item) => {
      const key = conversationKey(item);
      const selected = state.selectedConversation && conversationKey(state.selectedConversation) === key;
      const sceneClass = item.scene === "friend" ? "friend" : item.scene === "channel" ? "channel" : "";
      return `<button class="qq-conversation-item${selected ? " active" : ""}" type="button" role="option" aria-selected="${selected}" data-conversation-key="${esc(key)}">
        <span class="qq-conversation-avatar ${sceneClass}">${esc(conversationInitial(item))}</span>
        <span class="qq-conversation-main">
          <span class="qq-conversation-title-row">
            <span class="qq-conversation-name">${esc(conversationName(item))}</span>
            <span class="qq-conversation-time">${esc(fmtConversationTime(item.last_active_at))}</span>
          </span>
          <span class="qq-conversation-preview">${esc(conversationPreview(item))}</span>
        </span>
      </button>`;
    })
    .join("");

  list.querySelectorAll("[data-conversation-key]").forEach((button) => {
    button.onclick = () => {
      const item = state.conversations.find((entry) => conversationKey(entry) === button.dataset.conversationKey);
      if (item) selectConversation(item);
    };
  });
}

async function loadConversations({ preserveSelection = true } = {}) {
  const previousKey = preserveSelection && state.selectedConversation
    ? conversationKey(state.selectedConversation)
    : "";
  const data = await apiGet("/sessions", {
    scene: state.conversationScene,
    keyword: state.conversationKeyword,
    limit: 200,
    offset: 0,
  });
  state.conversations = data.items || [];
  const selected = state.conversations.find((item) => conversationKey(item) === previousKey) || null;
  state.selectedConversation = selected;
  renderConversationList();

  if (selected) {
    updateChatHeader(selected);
    await loadMessages({ reset: true });
  } else if (!preserveSelection || !state.selectedConversation) {
    showChatPlaceholder();
  }
}

function updateChatHeader(item) {
  const avatar = $("chat-avatar");
  avatar.textContent = conversationInitial(item);
  avatar.className = `qq-chat-avatar${item.scene === "friend" ? " friend" : item.scene === "channel" ? " channel" : ""}`;
  $("chat-title").textContent = conversationName(item);
  $("chat-subtitle").textContent = chatSubtitle(item);
  $("chat-status-slot").innerHTML = statusBadge(item.status_text || "enabled");
  $("chat-toolbar").classList.remove("is-hidden");
}

function chatSubtitle(item) {
  const scene = sceneText(item.scene);
  const groupName = String(item.group_name || "").trim();
  const session = String(item.session_id || "").trim();
  // 有群名：群聊 · 群号 762146F1DBB849090BCC80B6A667253F
  // 无群名：退回到群聊 · 平台 · 会话 ID
  if (groupName) return `${scene} · 群号 ${session || "--"}`;
  return `${scene} · ${item.platform_id || "未知平台"} · ${session || "--"}`;
}

function showChatPlaceholder() {
  $("chat-avatar").textContent = "群";
  $("chat-avatar").className = "qq-chat-avatar";
  $("chat-title").textContent = "请选择会话";
  $("chat-subtitle").textContent = "从左侧选择群聊或私聊查看聊天记录";
  $("chat-status-slot").innerHTML = "";
  $("chat-toolbar").classList.add("is-hidden");
  $("message-list").innerHTML = '<div class="qq-chat-placeholder"><div class="qq-placeholder-icon">☏</div><strong>选择一个会话</strong><span>聊天记录将在这里显示</span></div>';
  $("message-summary").textContent = "尚未选择会话";
}

function selectConversation(item) {
  state.selectedConversation = item;
  state.messageOffset = 0;
  $("message-keyword").value = "";
  renderConversationList();
  updateChatHeader(item);
  loadMessages({ reset: true }).catch((error) => {
    $("message-list").innerHTML = `<div class="qq-message-empty">加载失败：${esc(error.message)}</div>`;
  });
}

function messageRowHtml(item) {
  const outbound = item.direction === "outbound";
  const sender = outbound ? "机器人" : (item.sender_name || item.sender_id || "未知发送者");
  const initial = outbound ? "机" : String(sender || "聊").slice(0, 1);
  return `<article class="qq-message-row${outbound ? " outbound" : ""}">
    <span class="qq-message-avatar">${esc(initial)}</span>
    <div class="qq-message-body">
      <span class="qq-message-sender">${esc(sender)}</span>
      <div class="qq-message-bubble">${esc(item.content || item.message_type || "（无正文）")}</div>
      <span class="qq-message-time">${esc(fmtTime(item.timestamp))}</span>
    </div>
  </article>`;
}

function renderMessages(data) {
  const list = $("message-list");
  const items = data.items || [];
  state.messageTotal = data.total ?? items.length;
  if (!items.length) {
    list.innerHTML = '<div class="qq-message-empty">当前会话暂无聊天记录</div>';
    $("message-summary").textContent = "当前会话暂无聊天记录";
    $("message-more").classList.add("is-hidden");
    return;
  }

  list.innerHTML = items.map(messageRowHtml).join("");

  const loaded = state.messageOffset + items.length;
  $("message-summary").textContent = `已显示 ${loaded} / ${state.messageTotal} 条消息`;
  $("message-more").classList.toggle("is-hidden", loaded >= state.messageTotal);
}

async function loadMessages({ reset = false } = {}) {
  const item = state.selectedConversation;
  if (!item) return;
  if (reset) state.messageOffset = 0;
  const params = {
    platform_id: item.platform_id,
    session_id: item.session_id,
    scene: item.scene,
    keyword: $("message-keyword").value.trim(),
    limit: state.messageLimit,
    offset: state.messageOffset,
  };
  const data = await apiGet("/messages", params);
  if (reset) {
    renderMessages(data);
    return;
  }
  // 追加模式：保留已渲染的消息，把新一页接在后面
  const items = data.items || [];
  state.messageTotal = data.total ?? state.messageTotal;
  $("message-list").insertAdjacentHTML("beforeend", items.map(messageRowHtml).join(""));
  state.messageOffset += items.length;
  const loaded = state.messageOffset;
  $("message-summary").textContent = `已显示 ${loaded} / ${state.messageTotal} 条消息`;
  $("message-more").classList.toggle("is-hidden", loaded >= state.messageTotal);
}

document.querySelectorAll(".tab").forEach((tab) => {
  tab.onclick = () => {
    document.querySelectorAll(".tab").forEach((item) => item.classList.toggle("active", item === tab));
    document.querySelectorAll(".tab-panel").forEach((panel) => panel.classList.toggle("active", panel.id === `tab-${tab.dataset.tab}`));
    if (tab.dataset.tab === "messages" && !state.conversations.length) {
      loadConversations({ preserveSelection: false }).catch((error) => {
        $("conversation-list").innerHTML = `<div class="qq-conversation-empty">加载失败：${esc(error.message)}</div>`;
      });
    }
  };
});

document.querySelectorAll(".qq-scene-tab").forEach((tab) => {
  tab.onclick = () => {
    document.querySelectorAll(".qq-scene-tab").forEach((item) => item.classList.toggle("active", item === tab));
    state.conversationScene = tab.dataset.scene || "";
    loadConversations().catch((error) => {
      $("conversation-list").innerHTML = `<div class="qq-conversation-empty">加载失败：${esc(error.message)}</div>`;
    });
  };
});

$("conversation-keyword").addEventListener("keydown", (event) => {
  if (event.key === "Enter") {
    event.preventDefault();
    searchConversations();
  }
});

$("conversation-search").onclick = () => searchConversations();

function searchConversations() {
  state.conversationKeyword = $("conversation-keyword").value.trim();
  return loadConversations().catch((error) => {
    $("conversation-list").innerHTML = `<div class="qq-conversation-empty">加载失败：${esc(error.message)}</div>`;
  });
}

$("conversation-refresh").onclick = async () => {
  const button = $("conversation-refresh");
  button.disabled = true;
  toast("正在刷新会话...");
  try {
    await Promise.all([loadConversations(), loadStatus()]);
    toast("会话刷新完成");
  } catch (error) {
    toast(`刷新会话失败：${error.message}`, true);
  } finally {
    button.disabled = false;
  }
};

$("group-name-backfill").onclick = async () => {
  const button = $("group-name-backfill");
  button.disabled = true;
  toast("正在回填群名称...");
  try {
    const result = await apiPost("/group_names/backfill", { limit: 200 });
    const checked = result.checked || 0;
    const updated = result.updated || 0;
    const failed = result.failed || 0;
    const remaining = result.remaining || 0;
    toast(
      checked === 0
        ? "没有需要回填的群名称"
        : `群名称回填完成：检查 ${checked} 个，成功 ${updated} 个，未查到 ${failed} 个，仍缺少 ${remaining} 个`,
      checked > 0 && updated === 0 && failed > 0
    );
    await loadConversations();
  } catch (error) {
    toast(`群名称回填失败：${error.message}`, true);
  } finally {
    button.disabled = false;
  }
};

$("refresh-btn").onclick = async () => {
  const button = $("refresh-btn");
  button.disabled = true;
  toast("正在刷新数据...");
  try {
    await Promise.all([loadStatus(), loadSessions()]);
    if ($("tab-messages").classList.contains("active")) await loadConversations();
    toast("数据刷新完成");
  } catch (error) {
    toast(`刷新数据失败：${error.message}`, true);
  } finally {
    button.disabled = false;
  }
};
$("session-search").onclick = async () => {
  const button = $("session-search");
  state.sessionOffset = 0;
  button.disabled = true;
  try {
    await loadSessions();
    toast("会话查询完成");
  } catch (error) {
    toast(`会话查询失败：${error.message}`, true);
  } finally {
    button.disabled = false;
  }
};

$("session-prev").onclick = async () => {
  state.sessionOffset = Math.max(0, state.sessionOffset - state.sessionLimit);
  try {
    await loadSessions();
  } catch (error) {
    toast(`加载上一页失败：${error.message}`, true);
  }
};
$("session-next").onclick = async () => {
  state.sessionOffset += state.sessionLimit;
  try {
    await loadSessions();
  } catch (error) {
    toast(`加载下一页失败：${error.message}`, true);
  }
};
$("message-more").onclick = async () => {
  const button = $("message-more");
  button.disabled = true;
  try {
    await loadMessages();
  } catch (error) {
    toast(`加载更多消息失败：${error.message}`, true);
  } finally {
    button.disabled = false;
  }
};
$("message-search").onclick = async () => {
  const button = $("message-search");
  state.messageOffset = 0;
  button.disabled = true;
  try {
    await loadMessages({ reset: true });
    toast("消息搜索完成");
  } catch (error) {
    toast(`消息搜索失败：${error.message}`, true);
  } finally {
    button.disabled = false;
  }
};
$("cleanup-btn").onclick = async () => {
  const button = $("cleanup-btn");
  const confirmed = await confirmAction({
    title: "清理过期数据",
    message: "确定清理已过期的会话路由和超出保留期的聊天记录吗？此操作无法撤销。",
    submitText: "开始清理",
  });
  if (!confirmed) return;
  button.disabled = true;
  toast("正在清理过期数据...");
  try {
    const result = await apiPost("/cleanup", {});
    const sessions = result.expired_sessions || 0;
    const messages = result.expired_messages || 0;
    const attachments = result.orphan_attachments || 0;
    const total = sessions + messages + attachments;
    if (total === 0) {
      // 消息保留期为 0 表示"永不按时间清理"，此处说明原因避免用户误以为功能失效
      const retention = Number(state.messageRetentionDays) || 0;
      toast(
        retention > 0
          ? "清理完成：没有需要清理的数据"
          : "清理完成：没有过期数据（聊天记录保留期为 0，表示不按时间清理）"
      );
    } else {
      toast(`清理完成：会话 ${sessions} 条，消息 ${messages} 条，孤立附件 ${attachments} 条`);
    }
    await Promise.all([loadStatus(), loadSessions(), loadConversations()]);
  } catch (error) {
    toast(`清理失败：${error.message}`, true);
  } finally {
    button.disabled = false;
  }
};

$("confirm-cancel").onclick = () => closeConfirmDialog(false);
$("confirm-submit").onclick = () => closeConfirmDialog(true);
$("confirm-dialog").addEventListener("click", (event) => {
  if (event.target === $("confirm-dialog")) closeConfirmDialog(false);
});
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && confirmResolver) closeConfirmDialog(false);
});

async function init() {
  showChatPlaceholder();
  try {
    await Promise.all([loadStatus(), loadSessions()]);
  } catch (error) {
    toast(`初始化加载失败：${error.message}`, true);
    $("session-rows").innerHTML = `<tr><td colspan="6"><div class="empty">加载失败：${esc(error.message)}</div></td></tr>`;
  }
}

init();
