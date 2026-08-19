/* 管理端逻辑：Agents CRUD + 群组管理 */
const $ = (id) => document.getElementById(id);
let allAgents = [];

async function api(path, opts) {
  const res = await fetch(path, opts);
  if (res.status === 401) {
    location.href = "/login.html";
    throw new Error("未登录");
  }
  if (!res.ok) throw new Error((await res.text()) || res.status);
  return res.json();
}

function csv(v) { return (v || "").split(/[,，]/).map((s) => s.trim()).filter(Boolean); }

async function init() {
  const sys = await api("/api/system");
  const badge = $("modeBadge");
  badge.textContent = `LLM: ${sys.llm_model}（${sys.llm_mode === "mock" ? "模拟模式" : "真实模型"}）`;
  badge.className = "badge " + sys.llm_mode;
  await loadAgents();
  await loadRooms();
}

// ---------- Agents ----------
async function loadAgents() {
  allAgents = await api("/api/agents");
  renderAgentWall();
}

function renderAgentWall() {
  const wall = $("agentWall");
  if (!allAgents.length) { wall.innerHTML = '<div class="empty">还没有角色，点击下方表单创建</div>'; return; }
  wall.innerHTML = allAgents.map((a) => `
    <div class="agent-card" onclick="editAgent(${a.id})">
      <div class="agent-avatar">${a.avatar ? `<img src="${esc(a.avatar)}" alt="">` : `<span>${esc(a.name.slice(0, 1))}</span>`}</div>
      <div class="agent-name">${esc(a.name)}${a.created_by === "god" ? ' <span class="chip" style="color:var(--accent)">铸造</span>' : ""}</div>
      <div class="agent-aliases">${(a.aliases || []).map(esc).join("、") || "无别名"}</div>
      <div class="agent-keywords">${(a.trigger_keywords || []).slice(0, 4).map((k) => `<span class="chip">${esc(k)}</span>`).join("")}</div>
      <div class="agent-stats">话痨 ${a.base_freq} · 冷却 ${a.base_cd}s${a.reply_len ? ` · 💬 ${({ short: "短", normal: "中", long: "长" })[a.reply_len] || a.reply_len}` : ""}${a.model ? ` · ${esc(a.model)}` : ""}</div>
      <div class="agent-actions">
        <button onclick="event.stopPropagation();editAgent(${a.id})">✏️ 编辑</button>
        <button class="danger" onclick="event.stopPropagation();delAgent(${a.id})">🗑 删除</button>
      </div>
    </div>`).join("") + `
    <div class="agent-card new" onclick="clearAgentForm()">
      <div class="agent-avatar"><span>＋</span></div>
      <div class="agent-name">新建角色</div>
      <div class="agent-stats">点击创建新人格</div>
    </div>`;
}

async function restoreDemo() {
  if (!confirm("恢复示例人格（毒舌老王 / 温柔小晴 / 技术极客阿睿）？已存在的不会重复创建。")) return;
  try {
    const r = await api("/api/system/restore-demo", { method: "POST" });
    alert(r.created.length ? `已恢复：${r.created.join("、")}` : "示例人格都已存在，无需恢复");
    await loadAgents();
    await loadRooms();
  } catch (e) { alert("恢复失败：" + e.message); }
}

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

function editAgent(id) {
  const a = allAgents.find((x) => x.id === id);
  if (!a) return;
  $("agentId").value = a.id;
  $("agentFormTitle").textContent = `编辑：${a.name}`;
  $("f_name").value = a.name;
  $("f_aliases").value = (a.aliases || []).join(",");
  $("f_prompt").value = a.system_prompt;
  $("f_memory").value = a.memory || "";
  $("f_fewshot").value = (a.few_shot || []).join("\n");
  $("f_keywords").value = (a.trigger_keywords || []).join(",");
  $("f_freq").value = a.base_freq;
  $("f_cd").value = a.base_cd;
  $("f_len").value = a.reply_len || "normal";
  $("f_model").value = a.model;
  $("f_avatar").value = a.avatar || "";
  renderAvatarPreview(a.avatar || "");
  loadVersions(a.id);
}

let dayVersionId = null;

async function loadVersions(agentId) {
  dayVersionId = null;
  const versions = await api(`/api/agents/${agentId}/versions`);
  const box = $("versionList");
  if (!versions.length) {
    box.innerHTML = '<span class="hint">暂无历史版本（记忆/人格变更时自动记录）</span>';
    return;
  }
  // 找"一天前或更早"里最近的版本
  const dayAgo = Date.now() / 1000 - 86400;
  const candidates = versions.filter((v) => v.ts <= dayAgo);
  if (candidates.length) dayVersionId = candidates[candidates.length - 1].id;
  box.innerHTML = `
    <table>
      <tr><th>时间</th><th>记忆条数</th><th></th></tr>
      ${versions.map((v) => `
        <tr>
          <td>${new Date(v.ts * 1000).toLocaleString("zh-CN", { hour12: false })}</td>
          <td>${v.memory ? v.memory.split("\n").filter(Boolean).length : 0}</td>
          <td class="td-actions"><button onclick="rollbackVersion(${agentId}, ${v.id})">回滚</button></td>
        </tr>`).join("")}
    </table>`;
  $("rollbackDayBtn").disabled = !dayVersionId;
  $("rollbackDayBtn").textContent = dayVersionId
    ? "⏪ 回滚到一天前"
    : "回滚到一天前（暂无一天前版本）";
}

async function rollbackVersion(agentId, versionId) {
  if (!confirm("回滚到该版本？当前人格/记忆会先存为新版本（可再回滚回来）。")) return;
  try {
    await api(`/api/agents/${agentId}/versions/${versionId}/rollback`, { method: "POST" });
    alert("已回滚到该版本");
    editAgent(agentId);
    await loadAgents();
  } catch (e) {
    alert("回滚失败：" + e.message);
  }
}

async function rollbackToDay() {
  const id = $("agentId").value;
  if (!id || !dayVersionId) { alert("没有一天前的版本"); return; }
  if (!confirm("把人格/记忆回滚到一天前的状态？")) return;
  try {
    await api(`/api/agents/${id}/versions/${dayVersionId}/rollback`, { method: "POST" });
    alert("已回滚到一天前");
    editAgent(+id);
    await loadAgents();
  } catch (e) {
    alert("回滚失败：" + e.message);
  }
}

function clearAgentForm() {
  $("agentId").value = "";
  $("agentFormTitle").textContent = "新建 Agent";
  ["f_name", "f_aliases", "f_prompt", "f_memory", "f_fewshot", "f_keywords", "f_model", "f_avatar"].forEach((id) => ($(id).value = ""));
  $("f_freq").value = 0.3;
  $("f_cd").value = 30;
  $("f_len").value = "normal";
  renderAvatarPreview("");
  $("versionList").innerHTML = '<span class="hint">选择角色后显示历史版本</span>';
  dayVersionId = null;
}

function renderAvatarPreview(url) {
  $("avatarPreview").innerHTML = url
    ? `<img src="${esc(url)}" style="width:40px;height:40px;border-radius:50%;object-fit:cover"> ${esc(url)}`
    : "未设置头像";
}

async function saveAgent() {
  const body = {
    name: $("f_name").value.trim(),
    aliases: csv($("f_aliases").value),
    system_prompt: $("f_prompt").value,
    few_shot: $("f_fewshot").value.split("\n").map((s) => s.trim()).filter(Boolean),
    trigger_keywords: csv($("f_keywords").value),
    base_freq: parseFloat($("f_freq").value) || 0.3,
    base_cd: parseFloat($("f_cd").value) || 30,
    reply_len: $("f_len").value || "normal",
    model: $("f_model").value.trim(),
    avatar: $("f_avatar").value.trim(),
    memory: $("f_memory").value,
  };
  if (!body.name) { alert("名字不能为空"); return; }
  const id = $("agentId").value;
  try {
    if (id) await api(`/api/agents/${id}`, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    else await api("/api/agents", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    clearAgentForm();
    await loadAgents();
    await loadRooms();
  } catch (e) { alert("保存失败：" + e.message); }
}

function uploadAvatar() {
  const f = $("avatarFile").files[0];
  const id = $("agentId").value;
  if (!f) { alert("请选择图片"); return; }
  if (!id) { alert("请先保存 agent 再上传头像（上传前需已有 agent id）"); return; }
  const reader = new FileReader();
  reader.onload = async () => {
    try {
      const a = await api(`/api/agents/${id}/avatar`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ data_url: reader.result }),
      });
      $("f_avatar").value = a.avatar;
      renderAvatarPreview(a.avatar);
      await loadAgents();
    } catch (e) { alert("上传失败：" + e.message); }
  };
  reader.readAsDataURL(f);
}

async function delAgent(id) {
  if (!confirm("确认删除该 agent？")) return;
  try {
    await api(`/api/agents/${id}`, { method: "DELETE" });
    await loadAgents();
    await loadRooms();
  } catch (e) { alert("删除失败：" + e.message); }
}

// ---------- Rooms ----------
async function loadRooms() {
  const rooms = await api("/api/rooms");
  const box = $("roomList");
  if (!rooms.length) { box.innerHTML = '<div class="empty">还没有群组</div>'; return; }
  box.innerHTML = `
    <table>
      <tr><th>群名</th><th>渠道</th><th>启用的 Agents</th><th>状态</th><th></th></tr>
      ${rooms.map((r) => `
        <tr>
          <td><strong>${esc(r.name)}</strong></td>
          <td>${r.channel}</td>
          <td>${r.agents.length ? r.agents.map((a) => `<span class="chip">${esc(a.name)}</span>`).join("") : '<span class="hint">（空）</span>'}</td>
          <td>${r.paused ? '<span class="chip" style="color:var(--warn);border-color:var(--warn)">⏸ 已暂停</span>' : '<span class="chip">正常</span>'}</td>
          <td class="td-actions">
            <button onclick="editRoom(${r.id})">编辑</button>
            <button class="danger" onclick="delRoom(${r.id})">删除</button>
          </td>
        </tr>`).join("")}
    </table>`;
  renderRoomAgentChecks();
  // 浓度复盘房间选择器
  const sel = $("concRoom");
  const keep = sel.value;
  sel.innerHTML = rooms.map((r) => `<option value="${r.id}">${esc(r.name)}</option>`).join("");
  if (keep && rooms.some((r) => r.id == keep)) sel.value = keep;
  if (rooms.length) loadConc();
}

async function loadConc() {
  const roomId = $("concRoom").value;
  if (!roomId) return;
  const rows = await api(`/api/rooms/${roomId}/concentration?limit=20`);
  const box = $("concLog");
  if (!rows.length) { box.innerHTML = '<div class="empty">还没有消息</div>'; return; }
  const agentCols = new Set();
  for (const r of rows) for (const k of Object.keys(r.scores)) agentCols.add(k);
  box.innerHTML = `
    <table>
      <tr><th>#</th><th>发言者</th><th>内容</th>${[...agentCols].map((n) => `<th>${esc(n)}</th>`).join("")}</tr>
      ${rows.map((r) => `
        <tr>
          <td>${r.id}</td>
          <td>${esc(r.sender)}</td>
          <td style="max-width:280px">${esc(r.content)}</td>
          ${[...agentCols].map((n) => {
            const v = r.scores[n];
            const color = v == null ? "var(--muted)" : v >= 0.7 ? "var(--ok)" : v >= 0.4 ? "var(--warn)" : "var(--muted)";
            return `<td style="color:${color}">${v == null ? "—" : v}</td>`;
          }).join("")}
        </tr>`).join("")}
    </table>`;
}

function renderRoomAgentChecks() {
  const box = $("r_agents");
  box.innerHTML = allAgents.length
    ? allAgents.map((a) => `<label><input type="checkbox" value="${a.id}"> ${esc(a.name)}</label>`).join("")
    : '<span class="hint">先创建 agent</span>';
}

function editRoom(id) {
  api("/api/rooms").then((rooms) => {
    const r = rooms.find((x) => x.id === id);
    if (!r) return;
    $("roomId").value = r.id;
    $("r_name").value = r.name;
    $("r_paused").checked = !!r.paused;
    $("r_search").checked = r.allow_search !== false;
    renderRoomAgentChecks();
    $("r_agents").querySelectorAll("input").forEach((cb) => (cb.checked = r.agent_ids.includes(+cb.value)));
  });
}

function clearRoomForm() {
  $("roomId").value = "";
  $("r_name").value = "";
  $("r_paused").checked = false;
  $("r_search").checked = true;
  renderRoomAgentChecks();
}

async function saveRoom() {
  const name = $("r_name").value.trim();
  if (!name) { alert("群名不能为空"); return; }
  const agent_ids = [...$("r_agents").querySelectorAll("input:checked")].map((cb) => +cb.value);
  const paused = $("r_paused").checked;
  const allow_search = $("r_search").checked;
  const id = $("roomId").value;
  try {
    if (id) await api(`/api/rooms/${id}`, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ name, agent_ids, paused, allow_search }) });
    else await api("/api/rooms", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ name, agent_ids, paused, allow_search }) });
    clearRoomForm();
    await loadRooms();
  } catch (e) { alert("保存失败：" + e.message); }
}

async function delRoom(id) {
  if (!confirm("确认删除该群组？消息记录会保留但不再显示。")) return;
  try {
    await api(`/api/rooms/${id}`, { method: "DELETE" });
    await loadRooms();
  } catch (e) { alert("删除失败：" + e.message); }
}

// ---------- Knowledge ----------
function fillKbAgentSelect(selectedId) {
  const sel = $("kbAgent");
  const keep = sel.value || selectedId;
  sel.innerHTML = allAgents.map((a) => `<option value="${a.id}">${esc(a.name)}</option>`).join("");
  if (keep && allAgents.some((a) => a.id == keep)) sel.value = keep;
  if (allAgents.length) loadKb();
}

async function loadKb() {
  const agentId = $("kbAgent").value;
  if (!agentId) { $("kbList").innerHTML = '<div class="empty">先创建 agent</div>'; $("kbStats").textContent = ""; return; }
  const entries = await api(`/api/agents/${agentId}/knowledge`);
  const chars = entries.reduce((s, e) => s + e.content.length, 0);
  $("kbStats").textContent = `共 ${entries.length} 块，${chars} 字`;
  const box = $("kbList");
  if (!entries.length) { box.innerHTML = '<div class="empty">还没有知识，右侧添加</div>'; return; }
  box.innerHTML = `
    <table>
      <tr><th>标题</th><th>内容预览</th><th>来源</th><th></th></tr>
      ${entries.map((e) => `
        <tr>
          <td>${esc(e.title)}</td>
          <td style="max-width:280px">${esc(e.content.slice(0, 60))}${e.content.length > 60 ? "…" : ""}</td>
          <td>${esc(e.source) || "—"}</td>
          <td class="td-actions"><button class="danger" onclick="delKb(${e.id})">删除</button></td>
        </tr>`).join("")}
    </table>`;
}

async function saveKbText() {
  const content = $("kbContent").value.trim();
  if (!content) { alert("内容不能为空"); return; }
  try {
    const created = await api(`/api/agents/${$("kbAgent").value}/knowledge/text`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ title: $("kbTitle").value.trim() || "未命名", content, source: $("kbSource").value.trim() }),
    });
    $("kbContent").value = "";
    $("kbUploadInfo").textContent = `已入库 ${created.length} 块`;
    await loadKb();
  } catch (e) { alert("入库失败：" + e.message); }
}

function uploadKbFile() {
  const f = $("kbFile").files[0];
  if (!f) return;
  const reader = new FileReader();
  reader.onload = async () => {
    try {
      const created = await api(`/api/agents/${$("kbAgent").value}/knowledge/text`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ title: f.name, content: reader.result, source: f.name }),
      });
      $("kbUploadInfo").textContent = `「${f.name}」已入库 ${created.length} 块`;
      $("kbFile").value = "";
      await loadKb();
    } catch (e) { alert("上传失败：" + e.message); }
  };
  reader.readAsText(f, "utf-8");
}

async function delKb(id) {
  if (!confirm("删除该知识块？")) return;
  try {
    await api(`/api/knowledge/${id}`, { method: "DELETE" });
    await loadKb();
  } catch (e) { alert("删除失败：" + e.message); }
}

async function searchKb() {
  const q = $("kbQuery").value.trim();
  const agentId = $("kbAgent").value;
  if (!q || !agentId) return;
  const results = await api(`/api/agents/${agentId}/knowledge/search?q=${encodeURIComponent(q)}`);
  $("kbResults").innerHTML = results.length
    ? results.map((r) => `<div class="hint" style="margin:6px 0">🎯 ${esc(r.title)}（${r.score}）<br>${esc(r.content.slice(0, 120))}</div>`).join("")
    : '<div class="hint">无命中</div>';
}

// ---------- Forge：铸造师待确认档案 ----------
async function loadDrafts() {
  const drafts = await api("/api/god/drafts");
  const box = $("draftList");
  if (!drafts.length) { box.innerHTML = '<div class="empty">暂无档案，去聊天室的【铸造室】让铸造师生成</div>'; return; }
  box.innerHTML = `
    <table>
      <tr><th>名字</th><th>状态</th><th>知识条数</th><th></th></tr>
      ${drafts.map((d) => `
        <tr>
          <td><strong>${esc(d.name)}</strong></td>
          <td>${d.status === "pending" ? '<span class="chip" style="color:var(--warn);border-color:var(--warn)">待确认</span>'
              : d.status === "approved" ? '<span class="chip" style="color:var(--ok)">已创建</span>'
              : '<span class="chip" style="color:var(--muted)">已拒绝</span>'}</td>
          <td>${(d.knowledge_entries || []).length}</td>
          <td class="td-actions">
            <button onclick="showDraftDetail(${d.id})">预览</button>
            ${d.status === "pending" ? `
              <button class="primary" onclick="approveDraft(${d.id})">确认创建</button>
              <button class="danger" onclick="rejectDraft(${d.id})">拒绝</button>` : ""}
          </td>
        </tr>`).join("")}
    </table>`;
}

let currentDraft = null;

async function showDraftDetail(id) {
  currentDraft = await api(`/api/god/drafts/${id}`);
  const d = currentDraft;
  $("draftDetailTitle").textContent = `档案详情：${d.name}`;
  $("draftDetail").innerHTML = `
    <label>名字</label><input id="dd_name" value="${esc(d.name)}">
    <label>别名（逗号分隔）</label><input id="dd_aliases" value="${esc((d.aliases || []).join(","))}">
    <label>触发关键词（逗号分隔）</label><input id="dd_keywords" value="${esc((d.trigger_keywords || []).join(","))}">
    <label>人格提示词（可修改后确认）</label>
    <textarea id="dd_prompt" rows="8">${esc(d.system_prompt)}</textarea>
    <label>语气示例（每行一条）</label>
    <textarea id="dd_fewshot" rows="4">${esc((d.few_shot || []).join("\n"))}</textarea>
    <label>知识库（${(d.knowledge_entries || []).length} 条）</label>
    <div class="hint">${(d.knowledge_entries || []).map((e) => `• ${esc(e.title)}（来源：${esc(e.source || "—")}）`).join("<br>") || "无"}</div>
    <label>建议参数</label>
    <div class="hint">话痨度 ${d.suggested_params?.base_freq ?? 0.3} / 冷却 ${d.suggested_params?.base_cd_s ?? 30}s / 发言长度 ${({ short: "短", normal: "中", long: "长" })[d.suggested_params?.reply_len] || "中"}</div>
    <div style="display:flex;gap:10px;margin-top:14px">
      <button id="ddUpdate" class="primary" style="flex:1">保存修改</button>
      <button id="ddApprove" class="primary" style="flex:1">确认创建</button>
      <button id="ddReject" class="danger" style="flex:1">拒绝</button>
    </div>`;
  $("ddUpdate").addEventListener("click", updateDraft);
  $("ddApprove").addEventListener("click", () => approveDraft(d.id));
  $("ddReject").addEventListener("click", () => rejectDraft(d.id));
}

async function updateDraft() {
  if (!currentDraft) return;
  const body = {
    name: $("dd_name").value.trim(),
    aliases: csv($("dd_aliases").value),
    trigger_keywords: csv($("dd_keywords").value),
    system_prompt: $("dd_prompt").value,
    few_shot: $("dd_fewshot").value.split("\n").map((s) => s.trim()).filter(Boolean),
  };
  try {
    currentDraft = await api(`/api/god/drafts/${currentDraft.id}`, {
      method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
    });
    alert("已保存修改");
    await loadDrafts();
    showDraftDetail(currentDraft.id);
  } catch (e) { alert("保存失败：" + e.message); }
}

async function approveDraft(id) {
  if (!confirm("确认创建该 agent？将生成人格 + 知识库（创建后仍可在 Agents 页修改）。")) return;
  try {
    const a = await api(`/api/god/drafts/${id}/approve`, { method: "POST" });
    alert(`已创建 agent「${a.name}」`);
    await loadDrafts();
    await loadAgents();
    await loadRooms();
    currentDraft = null;
  } catch (e) { alert("创建失败：" + e.message); }
}

async function rejectDraft(id) {
  if (!confirm("拒绝该档案？")) return;
  try {
    await api(`/api/god/drafts/${id}`, { method: "DELETE" });
    await loadDrafts();
  } catch (e) { alert("操作失败：" + e.message); }
}

// ---------- Monitor ----------
async function loadMonitor() {
  const [overview, llmRows, rooms] = await Promise.all([
    api("/api/stats/overview"),
    api("/api/stats/llm"),
    api("/api/rooms"),
  ]);
  $("monOverview").innerHTML = `
    <table>
      <tr><td>消息总数</td><td><strong>${overview.total_messages}</strong></td></tr>
      <tr><td>其中 agent 发言</td><td><strong>${overview.total_replies}</strong></td></tr>
      <tr><td>LLM 调用次数</td><td><strong>${overview.total_calls}</strong></td></tr>
      <tr><td>token 总量</td><td><strong>${overview.total_tokens}</strong></td></tr>
      <tr><td>👍 好评</td><td style="color:var(--ok)"><strong>${overview.feedback_up}</strong></td></tr>
      <tr><td>👎 差评</td><td style="color:var(--danger)"><strong>${overview.feedback_down}</strong></td></tr>
    </table>
    <div class="hint" style="margin-top:6px">（mock 模式下 token 为估算值）</div>`;
  $("monLLM").innerHTML = llmRows.length
    ? `<table>
        <tr><th>用途</th><th>模型</th><th>调用</th><th>输入token</th><th>输出token</th></tr>
        ${llmRows.map((r) => `
          <tr>
            <td>${esc(r.purpose)}</td>
            <td>${esc(r.model)}</td>
            <td>${r.calls}</td>
            <td>${r.prompt_tokens}</td>
            <td>${r.completion_tokens}</td>
          </tr>`).join("")}
      </table>`
    : '<div class="empty">还没有调用记录</div>';
  const sel = $("monRoom");
  const keep = sel.value;
  sel.innerHTML = `<option value="">全部群</option>` + rooms.map((r) => `<option value="${r.id}">${esc(r.name)}</option>`).join("");
  if (keep && (keep === "" || rooms.some((r) => r.id == keep))) sel.value = keep;
  await loadDecisions();
}

async function loadDecisions() {
  const roomId = $("monRoom").value;
  const q = roomId ? `?room_id=${roomId}&limit=50` : "?limit=50";
  const rows = await api("/api/logs/decisions" + q);
  const box = $("monDecisions");
  if (!rows.length) { box.innerHTML = '<div class="empty">暂无发言记录</div>'; return; }
  box.innerHTML = rows.map((d) => `
    <details style="border:1px solid var(--border);border-radius:8px;margin-bottom:8px;padding:8px 12px;background:var(--panel)">
      <summary style="cursor:pointer">
        <strong>${esc(d.agent_name)}</strong>
        <span class="chip">浓度 ${d.concentration}</span>
        <span class="chip">概率 ${d.probability}</span>
        ${d.mentioned ? '<span class="chip" style="color:var(--accent)">点名</span>' : ""}
        ${(d.knowledge_hits || []).length ? `<span class="chip" style="color:var(--ok)">知识 ${d.knowledge_hits.length} 条</span>` : ""}
        <span class="hint">${esc(d.reply.slice(0, 40))}${d.reply.length > 40 ? "…" : ""}</span>
      </summary>
      <div style="margin-top:8px">
        <div class="hint">触发消息：${esc(d.trigger_content)}</div>
        ${(d.knowledge_hits || []).length ? `<div class="hint" style="margin-top:4px">知识命中：${d.knowledge_hits.map((h) => `${esc(h.title)}（${h.score}）`).join("、")}</div>` : ""}
        <div style="margin-top:6px;border-top:1px solid var(--border);padding-top:6px;white-space:pre-wrap">${esc(d.reply)}</div>
      </div>
    </details>`).join("");
}

// ---------- Settings（运行时可调参数） ----------
const SETTINGS_META = [
  ["silence_limit", "静默上限", "连续 N 条非真人消息后，agent 暂停发言", "int"],
  ["context_n", "上下文条数", "每个 agent 记住最近多少条消息", "int"],
  ["delay_min", "发言延迟下限（秒）", "延迟越小 agent 接话越快", "float"],
  ["delay_max", "发言延迟上限（秒）", "延迟越大回复越慢", "float"],
  ["cooldown_min", "冷却下限（秒）", "防止刷屏的最低冷却", "float"],
  ["cooldown_max", "冷却上限（秒）", "冷却时间不会超过此值", "float"],
  ["suppress_factor", "抢答降频因子", "高浓度 agent 抢答后，其他人发言概率乘以此值", "float"],
  ["high_conc_threshold", "高浓度阈值", "浓度达到此值视为抢答（0~1）", "float"],
  ["prob_base", "概率基数", "发言概率公式常数项，越大越爱接话", "float"],
  ["prob_factor", "浓度放大系数", "浓度对发言概率的影响强度", "float"],
  ["conc_min", "闲聊基线浓度", "非点名时参与闲聊的最低浓度（调高更爱接话）", "float"],
  ["conc_low", "低浓度阈值", "相似度低于此值直接判低浓度", "float"],
  ["conc_high", "高浓度阈值", "相似度高于此值直接判高浓度", "float"],
  ["repeat_threshold", "防复读阈值", "新回复与自己的上一条相似度超过此值就拦截（调低=更严格，可能误伤正常补充；调高=更宽松）", "float"],
];

let settingsLoaded = null;

async function loadSettings() {
  const s = await api("/api/settings");
  settingsLoaded = { ...s };
  $("settingsForm").innerHTML = SETTINGS_META.map(([key, label, desc, type]) => `
    <label style="margin-top:12px">${label} <span class="hint">— ${desc}</span></label>
    <input id="set_${key}" type="number" step="${type === "int" ? 1 : 0.05}" value="${s[key] ?? ""}">
  `).join("");
}

async function saveSettings() {
  const payload = {};
  for (const [key] of SETTINGS_META) {
    const v = $(`set_${key}`).value;
    if (v !== "" && v !== undefined) payload[key] = v;
  }
  try {
    await api("/api/settings", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ settings: payload }),
    });
    $("settingsMsg").textContent = "✅ 已保存，立即生效";
    await loadSettings();
  } catch (e) {
    $("settingsMsg").textContent = "❌ " + e.message;
  }
}

async function resetSettings() {
  if (!confirm("恢复全部设置为默认值？")) return;
  try {
    await api("/api/settings", { method: "DELETE" });
    $("settingsMsg").textContent = "✅ 已恢复默认";
    await loadSettings();
  } catch (e) {
    $("settingsMsg").textContent = "❌ " + e.message;
  }
}

// ---------- Tabs ----------
function switchTab(tab) {
  $("panelAgents").style.display = tab === "agents" ? "" : "none";
  $("panelRooms").style.display = tab === "rooms" ? "" : "none";
  $("panelKb").style.display = tab === "kb" ? "" : "none";
  $("panelForge").style.display = tab === "forge" ? "" : "none";
  $("panelMonitor").style.display = tab === "monitor" ? "" : "none";
  $("panelSettings").style.display = tab === "settings" ? "" : "none";
  $("tabAgents").classList.toggle("active", tab === "agents");
  $("tabRooms").classList.toggle("active", tab === "rooms");
  $("tabKb").classList.toggle("active", tab === "kb");
  $("tabForge").classList.toggle("active", tab === "forge");
  $("tabMonitor").classList.toggle("active", tab === "monitor");
  $("tabSettings").classList.toggle("active", tab === "settings");
  if (tab === "kb") fillKbAgentSelect();
  if (tab === "forge") loadDrafts();
  if (tab === "monitor") loadMonitor();
  if (tab === "settings") loadSettings();
}
$("tabAgents").addEventListener("click", () => switchTab("agents"));
$("tabRooms").addEventListener("click", () => switchTab("rooms"));
$("tabKb").addEventListener("click", () => switchTab("kb"));
$("tabForge").addEventListener("click", () => switchTab("forge"));
$("tabMonitor").addEventListener("click", () => switchTab("monitor"));
$("tabSettings").addEventListener("click", () => switchTab("settings"));

$("saveAgent").addEventListener("click", saveAgent);
$("resetAgent").addEventListener("click", clearAgentForm);
$("rollbackDayBtn").addEventListener("click", rollbackToDay);
$("restoreDemoBtn").addEventListener("click", restoreDemo);
$("avatarUploadBtn").addEventListener("click", () => $("avatarFile").click());
$("avatarFile").addEventListener("change", uploadAvatar);
$("saveRoom").addEventListener("click", saveRoom);
$("resetRoom").addEventListener("click", clearRoomForm);
$("concRoom").addEventListener("change", loadConc);
$("saveSettings").addEventListener("click", saveSettings);
$("resetSettings").addEventListener("click", resetSettings);
$("kbAgent").addEventListener("change", loadKb);
$("kbSaveText").addEventListener("click", saveKbText);
$("kbUpload").addEventListener("click", () => $("kbFile").click());
$("kbFile").addEventListener("change", uploadKbFile);
$("kbSearchBtn").addEventListener("click", searchKb);
$("kbQuery").addEventListener("keydown", (e) => { if (e.key === "Enter") searchKb(); });

init().catch((e) => alert("初始化失败：" + e.message));
