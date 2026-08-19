/* 聊天室逻辑：轮询消息 + 状态（正在输入/静默计数） */
const NAME_COLORS = [
  "#4f8cff", "#3ecf8e", "#f0b429", "#f2646a", "#b07cf0",
  "#3ecfc0", "#f07cb0", "#8cff4f", "#ff9f43", "#5f6cff",
];
function nameColor(name) {
  let h = 0;
  for (const ch of name) h = (h * 31 + ch.codePointAt(0)) >>> 0;
  return NAME_COLORS[h % NAME_COLORS.length];
}

const state = { roomId: null, roomName: "", lastId: 0, msgs: new Map(), pending: new Set(), humanNames: new Set(["我"]), avatars: {}, quote: null };
const ROOM_KEY = "dsh_current_room"; // 记住当前群，刷新/从管理端回来不用重选

const $ = (id) => document.getElementById(id);

async function api(path, opts) {
  const res = await fetch(path, opts);
  if (res.status === 401) {
    location.href = "/login.html";
    throw new Error("未登录");
  }
  if (!res.ok) throw new Error((await res.text()) || res.status);
  return res.json();
}

async function init() {
  const sys = await api("/api/system");
  const badge = $("modeBadge");
  badge.textContent = `LLM: ${sys.llm_model}（${sys.llm_mode === "mock" ? "模拟模式，未接真实模型" : "真实模型"}）`;
  badge.className = "badge " + sys.llm_mode;

  const rooms = await api("/api/rooms");
  const sel = $("roomSelect");
  sel.innerHTML = "";
  for (const r of rooms) {
    const opt = document.createElement("option");
    opt.value = r.id;
    opt.textContent = `${r.name}（${r.agents.length} 个 agent）`;
    sel.appendChild(opt);
  }
  // 优先恢复上次看的群（若还在），否则默认第一个
  const saved = parseInt(localStorage.getItem(ROOM_KEY) || "", 10);
  const target = rooms.find((r) => r.id === saved) || rooms[0];
  if (target) {
    sel.value = target.id;
    await selectRoom(target.id);
  }
  sel.addEventListener("change", () => selectRoom(+sel.value));
}

async function selectRoom(id) {
  state.roomId = id;
  localStorage.setItem(ROOM_KEY, String(id)); // 记住当前群
  state.lastId = 0;
  state.msgs.clear();
  $("messages").innerHTML = "";
  const rooms = await api("/api/rooms");
  const room = rooms.find((r) => r.id === id);
  state.roomName = room?.name || "群聊";
  state.avatars = {};
  for (const a of room?.agents || []) if (a.avatar) state.avatars[a.name] = a.avatar;
  $("agentChips").innerHTML = room
    ? room.agents.map((a) => `<span class="chip" style="color:${nameColor(a.name)}">${a.name}</span>`).join("")
    : "";
  await poll(true);
}

async function poll(force) {
  if (!state.roomId) return;
  try {
    const msgs = await api(`/api/rooms/${state.roomId}/messages?after_id=${state.lastId}`);
    for (const m of msgs) {
      state.msgs.set(m.id, m);
      if (m.id > state.lastId) state.lastId = m.id;
    }
    if (msgs.length || force) renderMessages();

    const st = await api(`/api/rooms/${state.roomId}/state`);
    state.pending = new Set(st.pending);
    renderTyping(st);
  } catch (e) {
    console.error("poll failed", e);
  }
}

function renderMessages() {
  const box = $("messages");
  const before = box.scrollHeight - box.scrollTop - box.clientHeight;
  box.innerHTML = "";
  for (const m of [...state.msgs.values()].sort((a, b) => a.id - b.id)) {
    const div = document.createElement("div");
    div.className = "msg" + (m.sender_type === "human" ? " human" : "");
    const meta = document.createElement("div");
    meta.className = "meta";
    const avatar = state.avatars[m.sender];
    if (avatar) {
      const img = document.createElement("img");
      img.className = "avatar";
      img.src = avatar;
      img.alt = m.sender;
      meta.appendChild(img);
    }
    const name = document.createElement("span");
    name.className = "name";
    name.style.color = nameColor(m.sender);
    name.style.borderColor = nameColor(m.sender);
    name.textContent = m.sender;
    const time = document.createElement("span");
    time.className = "time";
    time.textContent = new Date(m.ts * 1000).toLocaleTimeString("zh-CN", { hour12: false });
    meta.append(name, time);
    const bubble = document.createElement("div");
    bubble.className = "bubble";
    if (m.quoted_content) {
      const card = document.createElement("div");
      card.className = "quote-card";
      card.textContent = `↩ ${m.quoted_sender}：${m.quoted_content}`;
      bubble.appendChild(card);
    }
    const text = document.createElement("div");
    text.textContent = m.content;
    bubble.appendChild(text);
    div.append(meta, bubble);
    // 引用按钮 + 反馈按钮
    const actions = document.createElement("div");
    actions.className = "msg-actions";
    const quoteBtn = document.createElement("button");
    quoteBtn.textContent = "↩引用";
    quoteBtn.title = "引用这条消息再回复";
    quoteBtn.addEventListener("click", () => setQuote(m));
    actions.appendChild(quoteBtn);
    if (m.sender_type === "agent") {
      const mk = (emoji, value) => {
        const b = document.createElement("button");
        b.textContent = emoji;
        b.title = value === "up" ? "回答得好" : "回答不好";
        if (m.feedback === value) b.classList.add("active");
        b.addEventListener("click", async () => {
          const next = m.feedback === value ? "" : value;
          try {
            await api(`/api/messages/${m.id}/feedback`, {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ value: next }),
            });
            m.feedback = next;
            renderMessages();
          } catch (e) { /* 忽略 */ }
        });
        return b;
      };
      actions.append(mk("👍", "up"), mk("👎", "down"));
    }
    div.append(actions);
    box.appendChild(div);
  }
  // 保持滚动位置：在底部则跟随，否则不动
  if (before < 80) box.scrollTop = box.scrollHeight;
}

function renderTyping(st) {
  const el = $("typing");
  if (st.paused) {
    el.textContent = "⏸ 已暂停：agent 不会再发言，点「继续」恢复";
    $("pauseBtn").textContent = "▶ 继续";
  } else {
    $("pauseBtn").textContent = "⏸ 暂停";
    if (state.pending.size) {
      const names = [...state.pending];
      el.innerHTML = names.map((n) => `<span style="color:${nameColor(n)}">${n}</span>`).join("、") + " 正在输入…";
    } else {
      el.textContent = st.silence_count > 0
        ? `静默计数：${st.silence_count}/${st.silence_limit}（连续 ${st.silence_limit} 条非真人消息后 agent 暂停）`
        : "";
    }
  }
}

async function togglePause() {
  if (!state.roomId) return;
  const st = await api(`/api/rooms/${state.roomId}/state`);
  const paused = !st.paused;
  try {
    await api(`/api/rooms/${state.roomId}/pause`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ paused }),
    });
    await poll(true);
  } catch (e) {
    alert("操作失败：" + e.message);
  }
}

async function closeConversation() {
  if (!state.roomId) return;
  if (!confirm("结束当前话题？将取消所有待发言、清空 agent 临时记忆（聊天记录保留）。")) return;
  try {
    await api(`/api/rooms/${state.roomId}/close`, { method: "POST" });
    await poll(true);
  } catch (e) {
    alert("操作失败：" + e.message);
  }
}

// 🗑 彻底清空聊天记录（删除记忆）
async function clearRoomHistory() {
  if (!state.roomId) return;
  if (!confirm("⚠️ 彻底清空本群所有聊天记录？\n消息和 agent 的记忆都会被删除，且不可恢复！")) return;
  try {
    await api(`/api/rooms/${state.roomId}/clear`, { method: "POST" });
    state.msgs.clear();
    state.lastId = 0;
    await poll(true);
  } catch (e) {
    alert("操作失败：" + e.message);
  }
}

// ＋ 新建聊天群
async function openCreateRoom() {
  const agents = await api("/api/agents");
  const box = $("nr_agents");
  box.innerHTML = agents.length
    ? agents.map((a) => `<label><input type="checkbox" value="${a.id}"> ${escHtml(a.name)}</label>`).join("")
    : '<span class="hint">还没有角色，先去管理端创建</span>';
  $("nr_name").value = "";
  $("newRoomModal").style.display = "flex";
}

function escHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

async function createRoom() {
  const name = $("nr_name").value.trim();
  if (!name) { alert("请输入群名"); return; }
  const agent_ids = [...$("nr_agents").querySelectorAll("input:checked")].map((cb) => +cb.value);
  try {
    const room = await api("/api/rooms", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name, agent_ids }),
    });
    $("newRoomModal").style.display = "none";
    await refreshRooms(+room.id);
  } catch (e) {
    alert("创建失败：" + e.message);
  }
}

async function refreshRooms(selectId) {
  const rooms = await api("/api/rooms");
  const sel = $("roomSelect");
  sel.innerHTML = "";
  for (const r of rooms) {
    const opt = document.createElement("option");
    opt.value = r.id;
    opt.textContent = `${r.name}（${r.agents.length} 个角色）`;
    sel.appendChild(opt);
  }
  if (selectId) sel.value = selectId;
  await selectRoom(+sel.value);
}

// 👥 管理当前群成员
let currentRoomInfo = null;

async function openMemberModal() {
  if (!state.roomId) return;
  const [rooms, agents] = await Promise.all([api("/api/rooms"), api("/api/agents")]);
  currentRoomInfo = rooms.find((r) => r.id === state.roomId);
  if (!currentRoomInfo) return;
  $("memberTitle").textContent = `👥 群成员管理 · ${currentRoomInfo.name}`;
  const inIds = currentRoomInfo.agent_ids || [];
  $("memberCurrent").innerHTML = `当前成员：${currentRoomInfo.agents.length
    ? currentRoomInfo.agents.map((a) => `<span class="chip">${escHtml(a.name)}</span>`).join("")
    : '<span style="color:var(--warn)">（空群，无人会回复）</span>'}`;
  const box = $("memberAgents");
  box.innerHTML = agents.length
    ? agents.map((a) => {
        const checked = inIds.includes(a.id) ? "checked" : "";
        return `<label><input type="checkbox" value="${a.id}" ${checked}> ${escHtml(a.name)}</label>`;
      }).join("")
    : '<span class="hint">还没有角色，先去管理端创建</span>';
  $("memberModal").style.display = "flex";
}

async function saveMembers() {
  if (!state.roomId) return;
  const agent_ids = [...$("memberAgents").querySelectorAll("input:checked")].map((cb) => +cb.value);
  try {
    await api(`/api/rooms/${state.roomId}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ agent_ids }),
    });
    $("memberModal").style.display = "none";
    await refreshRooms(state.roomId);
  } catch (e) {
    alert("保存失败：" + e.message);
  }
}

async function send() {
  const input = $("input");
  const content = input.value.trim();
  if (!content || !state.roomId) return;
  input.value = "";
  const body = { sender: "我", content };
  if (state.quote) body.quoted_message_id = state.quote.id;
  clearQuote();
  try {
    await api(`/api/rooms/${state.roomId}/messages`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    await poll(true);
  } catch (e) {
    alert("发送失败：" + e.message);
  }
}

// ---------- 分享：最近 20 条消息 → 图片 ----------
function wrapText(ctx, text, maxWidth) {
  const lines = [];
  let line = "";
  for (const ch of String(text)) {
    if (line && ctx.measureText(line + ch).width > maxWidth) {
      lines.push(line);
      line = ch;
    } else {
      line += ch;
    }
  }
  if (line) lines.push(line);
  return lines;
}

function roundRectPath(ctx, x, y, w, h, r) {
  ctx.beginPath();
  ctx.moveTo(x + r, y);
  ctx.arcTo(x + w, y, x + w, y + h, r);
  ctx.arcTo(x + w, y + h, x, y + h, r);
  ctx.arcTo(x, y + h, x, y, r);
  ctx.arcTo(x, y, x + w, y, r);
  ctx.closePath();
}

function renderChatImage(msgs, title) {
  const W = 920;
  const padX = 28;
  const nameSize = 15;
  const bodySize = 14;
  const lineH = 22;
  const bubblePad = 12;
  const bubbleR = 10;
  const msgGap = 16;

  const canvas = document.createElement("canvas");
  const ctx = canvas.getContext("2d");
  ctx.font = `${bodySize}px "Microsoft YaHei", sans-serif`;
  const contentW = W - padX * 2 - bubblePad * 2;

  // 第一遍：测量每条消息占用的行数
  const blocks = msgs.map((m) => {
    const quoteLines = m.quoted_content ? wrapText(ctx, `↩ ${m.quoted_sender}：${m.quoted_content}`, contentW) : [];
    ctx.font = `${bodySize}px "Microsoft YaHei", sans-serif`;
    const bodyLines = wrapText(ctx, m.content, contentW);
    return { m, quoteLines, bodyLines };
  });
  let total = 0;
  for (const b of blocks) {
    total += 1; // 名字行
    total += b.quoteLines.length;
    total += b.bodyLines.length;
    total += msgGap / lineH;
  }

  // 标题区 + 消息区
  const titleH = 96;
  const canvasH = Math.ceil(titleH + total * lineH + 40);
  canvas.width = W;
  canvas.height = canvasH;

  // 背景
  ctx.fillStyle = "#ffffff";
  ctx.fillRect(0, 0, W, canvasH);

  // 标题
  ctx.fillStyle = "#1a1a1a";
  ctx.font = `bold 22px "Microsoft YaHei", sans-serif`;
  ctx.fillText(`🗣 ${title}`, padX, 44);
  ctx.font = `12px "Microsoft YaHei", sans-serif`;
  ctx.fillStyle = "#8b93a3";
  const now = new Date();
  ctx.fillText(
    `最近 ${msgs.length} 条 · 生成于 ${now.toLocaleString("zh-CN", { hour12: false })}`,
    padX,
    70,
  );
  ctx.strokeStyle = "#e8eaef";
  ctx.beginPath();
  ctx.moveTo(padX, 84);
  ctx.lineTo(W - padX, 84);
  ctx.stroke();

  // 消息
  let y = titleH;
  for (const { m, quoteLines, bodyLines } of blocks) {
    const nameColor = m.sender_type === "human" ? "#1a1a1a" : nameColorOf(m.sender);
    // 名字 + 彩色圆点
    ctx.fillStyle = nameColor;
    ctx.font = `bold ${nameSize}px "Microsoft YaHei", sans-serif`;
    ctx.beginPath();
    ctx.arc(padX + 6, y - 4, 5, 0, Math.PI * 2);
    ctx.fill();
    ctx.fillText(m.sender, padX + 18, y);
    y += lineH;

    const allLines = [...quoteLines, ...bodyLines];
    // 气泡
    const bubbleH = allLines.length * lineH + bubblePad;
    roundRectPath(ctx, padX, y - bubblePad / 2, W - padX * 2, bubbleH, bubbleR);
    ctx.fillStyle = m.sender_type === "human" ? "#eef3ff" : "#f2f3f5";
    ctx.fill();

    let ty = y + 2;
    for (const line of quoteLines) {
      ctx.fillStyle = "#8b93a3";
      ctx.font = `${bodySize - 1}px "Microsoft YaHei", sans-serif`;
      ctx.fillText(line, padX + bubblePad, ty);
      ty += lineH;
    }
    ctx.fillStyle = "#333333";
    ctx.font = `${bodySize}px "Microsoft YaHei", sans-serif`;
    for (const line of bodyLines) {
      ctx.fillText(line, padX + bubblePad, ty);
      ty += lineH;
    }
    y += bubbleH + msgGap;
  }
  return canvas.toDataURL("image/png");
}

function nameColorOf(name) {
  let h = 0;
  for (const ch of name) h = (h * 31 + ch.codePointAt(0)) >>> 0;
  return NAME_COLORS[h % NAME_COLORS.length];
}

function shareRecent() {
  const msgs = [...state.msgs.values()].sort((a, b) => a.id - b.id).slice(-20);
  if (!msgs.length) {
    alert("还没有消息可以分享");
    return;
  }
  const dataUrl = renderChatImage(msgs, state.roomName);
  $("shareImg").src = dataUrl;
  $("shareHint").textContent = "点「复制图片」后可直接粘贴到微信/QQ 聊天框";
  $("shareModal").style.display = "flex";
}

async function copyShareImage() {
  const img = $("shareImg");
  try {
    const blob = await (await fetch(img.src)).blob();
    await navigator.clipboard.write([new ClipboardItem({ "image/png": blob })]);
    $("shareHint").textContent = "✅ 已复制到剪贴板，直接粘贴到聊天框即可";
  } catch (e) {
    $("shareHint").textContent = "复制失败（浏览器权限），请改用「下载 PNG」";
  }
}

function downloadShareImage() {
  const a = document.createElement("a");
  a.href = $("shareImg").src;
  a.download = `${state.roomName}-最近${Math.min(state.msgs.size, 20)}条.png`;
  a.click();
}
function setQuote(m) {
  state.quote = { id: m.id, sender: m.sender, content: m.content };
  const bar = $("quoteBar");
  bar.style.display = "flex";
  bar.innerHTML = "";
  const span = document.createElement("span");
  span.textContent = `↩ 引用 ${m.sender}：${m.content.slice(0, 60)}${m.content.length > 60 ? "…" : ""}`;
  const cancel = document.createElement("button");
  cancel.textContent = "✕";
  cancel.title = "取消引用";
  cancel.addEventListener("click", clearQuote);
  bar.append(span, cancel);
  $("input").focus();
}

function clearQuote() {
  state.quote = null;
  const bar = $("quoteBar");
  bar.style.display = "none";
  bar.innerHTML = "";
}

$("sendBtn").addEventListener("click", send);
$("input").addEventListener("keydown", (e) => {
  if (e.key === "Enter") send();
});
$("pauseBtn").addEventListener("click", togglePause);
$("closeBtn").addEventListener("click", closeConversation);
$("clearBtn").addEventListener("click", clearRoomHistory);
$("newRoomBtn").addEventListener("click", openCreateRoom);
$("nrCreate").addEventListener("click", createRoom);
$("nrCancel").addEventListener("click", () => ($("newRoomModal").style.display = "none"));
$("memberBtn").addEventListener("click", openMemberModal);
$("memberSave").addEventListener("click", saveMembers);
$("memberCancel").addEventListener("click", () => ($("memberModal").style.display = "none"));
$("shareBtn").addEventListener("click", shareRecent);
$("copyImgBtn").addEventListener("click", copyShareImage);
$("downloadImgBtn").addEventListener("click", downloadShareImage);
$("closeShareBtn").addEventListener("click", () => ($("shareModal").style.display = "none"));

// 📎 投料：把 txt/md 文件内容作为一条消息发出（铸造室给铸造师投角色资料）
$("attachBtn").addEventListener("click", () => $("attachFile").click());
$("attachFile").addEventListener("change", () => {
  const f = $("attachFile").files[0];
  if (!f || !state.roomId) return;
  const reader = new FileReader();
  reader.onload = async () => {
    try {
      await api(`/api/rooms/${state.roomId}/messages`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ sender: "我", content: `📎 资料：${f.name}\n${reader.result}` }),
      });
      $("attachFile").value = "";
      await poll(true);
    } catch (e) {
      alert("投料失败：" + e.message);
    }
  };
  reader.readAsText(f, "utf-8");
});

init().catch((e) => alert("初始化失败：" + e.message));
setInterval(() => poll(), 1500);
