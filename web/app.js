const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

const state = {
  config: null,
  status: null,
  user: null,
  day: 10,
  custom: false,
  lastTarget: null,
  busy: false,
  blocks: [],
  selected: new Map(),
  blockPage: 1,
  blockPages: 1,
  blockTotal: 0,
  allUsers: null,
  loadingAll: false,
  batchMode: null,
  batchPending: [],
  batchDone: false,
  appeals: [],
  appealSel: new Set(),
  appealMode: null,
  appealPending: [],
  appealDone: false,
};

/* ---------------- 工具 ---------------- */

function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

function fmtTime(ms) {
  if (!ms) return "";
  const d = new Date(ms);
  const p = (n) => String(n).padStart(2, "0");
  return `${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
}

function displayName(t) {
  if (!t) return "";
  return t.nick_name || t.user_name || String(t.user_id || t.key || "");
}

// 头像：图片加载失败时退化成首字占位
function avatarHtml(src, name, cls = "avatar") {
  const ch = esc(Array.from((name || "?").trim())[0] || "?");
  const img = src
    ? `<img src="${esc(src)}" alt="" loading="lazy" onerror="this.remove()">`
    : "";
  return `<span class="${cls}"><span>${ch}</span>${img}</span>`;
}

async function api(path, options = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  let data = {};
  try {
    data = await res.json();
  } catch (_) {
    throw new Error("服务返回异常，请重启程序后重试");
  }
  if (!res.ok || data.ok === false) {
    throw new Error(data.error || data.message || `请求失败（${res.status}）`);
  }
  return data;
}

function toast(message, type = "ok") {
  const box = $("#toasts");
  const el = document.createElement("div");
  el.className = `toast ${type}`;
  el.innerHTML = `<span class="ic">${type === "ok" ? "✓" : "!"}</span><span>${esc(message)}</span>`;
  box.appendChild(el);
  setTimeout(() => {
    el.style.transition = "opacity .3s, transform .3s";
    el.style.opacity = "0";
    el.style.transform = "translateX(10px)";
    setTimeout(() => el.remove(), 320);
  }, type === "ok" ? 3000 : 6000);
}

function setBusy(flag) {
  state.busy = flag;
  ["#btnResolve", "#btnBan", "#btnUnban", "#btnSaveSettings", "#btnConfirmBan"].forEach((s) => {
    const el = $(s);
    if (!el) return;
    if (s === "#btnBan" || s === "#btnUnban") el.disabled = flag || !state.user;
    else el.disabled = flag;
  });
  syncSelectBar();
  syncAppealBar();
}

/* ---------------- 渲染 ---------------- */

function setStatus(status) {
  state.status = status;
  const pill = $("#loginPill");
  const text = $("#loginText");
  pill.classList.remove("ok", "bad");
  if (!status || !state.config?.configured) {
    pill.classList.add("bad");
    text.textContent = "未配置登录凭证";
    return;
  }
  if (status.login) {
    pill.classList.add("ok");
    const name = status.self?.name || status.self?.user_name || "已登录";
    text.textContent = name;
    pill.title = `已登录：${name}（UID ${status.self?.user_id ?? "-"}）`;
  } else {
    pill.classList.add("bad");
    text.textContent = status.error || "登录失效";
    pill.title = status.error || "";
  }
}

function applyConfig(cfg) {
  state.config = cfg;
  $("#fnameLabel").textContent = cfg.fname;
  $("#dayHint").textContent = `默认封禁 ${cfg.default_day} 天`;
  $("#reason").value = cfg.default_reason || "";
  setDay(cfg.default_day, true);
}

function setDay(day, silent = false) {
  const presets = [1, 3, 10];
  const n = Number(day) || 10;
  state.day = n;
  state.custom = !presets.includes(n);
  $$("#daySeg button").forEach((b) => {
    const target = b.dataset.day;
    b.classList.toggle("active", state.custom ? target === "custom" : Number(target) === n);
  });
  const customInput = $("#customDay");
  customInput.hidden = !state.custom;
  if (state.custom) customInput.value = n;
  if (!silent) updateBanLabel();
}

function updateBanLabel() {
  const label = $("#banLabel");
  if (!label) return;
  const d = currentDay();
  label.textContent = state.user ? `封禁 ${d} 天` : "封禁";
  syncSelectBar();
}

function currentDay() {
  if (state.custom) {
    const v = Number($("#customDay").value);
    return Number.isFinite(v) && v > 0 ? Math.min(Math.floor(v), 9999) : 10;
  }
  return state.day;
}

function renderTargets(targets) {
  const box = $("#chips");
  box.innerHTML = "";
  (targets || []).slice(0, 8).forEach((t) => {
    const name = displayName(t);
    const chip = document.createElement("button");
    chip.className = "chip";
    chip.title = `${name} · UID ${t.user_id || t.key}`;
    chip.innerHTML = `
      ${avatarHtml(t.avatar, name, "av-mini")}
      <span class="nm">${esc(name)}</span>`;
    chip.addEventListener("click", () => {
      $("#query").value = t.user_name || String(t.user_id || t.key);
      resolveTarget();
    });
    box.appendChild(chip);
  });
}

function renderUser(user) {
  state.user = user;
  const box = $("#userBox");
  if (!user) {
    box.innerHTML = `<div class="empty-state">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><circle cx="12" cy="8.5" r="3.6"/><path d="M4.5 20c.9-3.4 3.9-5.4 7.5-5.4s6.6 2 7.5 5.4" stroke-linecap="round"/></svg>
      <p>还没有选中用户</p></div>`;
    $("#btnBan").disabled = true;
    $("#btnUnban").disabled = true;
    updateBanLabel();
    return;
  }

  const name = user.nick_name || user.user_name || String(user.user_id);
  const badges = [];
  if (user.bawu_label) badges.push(`<span class="badge bawu">${esc(user.bawu_label)}</span>`);
  if (user.is_vip) badges.push(`<span class="badge vip">会员</span>`);

  const meta = [];
  if (user.user_name) meta.push(`@${esc(user.user_name)}`);
  meta.push(`<code>UID ${user.user_id}</code>`);
  if (user.gender) meta.push(esc(user.gender));
  if (user.follow_days) meta.push(`关注 ${user.follow_days} 天`);
  else if (user.is_follow) meta.push("已关注本吧");
  else meta.push("未关注本吧");

  box.innerHTML = `
    <div class="user-card">
      ${avatarHtml(user.avatar, name)}
      <div class="u-main">
        <div class="u-name"><span class="txt">${esc(name)}</span>${badges.join("")}</div>
        <div class="u-sub">${meta.join("")}</div>
        ${user.sign ? `<div class="u-sub" style="margin-top:5px">${esc(user.sign).slice(0, 60)}</div>` : ""}
      </div>
      <div class="u-stats">
        <div class="u-level">${user.forum_level ?? "-"}<span>级</span></div>
        <div class="u-follow">${esc(state.config?.fname || "")}</div>
      </div>
    </div>`;

  $("#btnBan").disabled = state.busy;
  $("#btnUnban").disabled = state.busy;
  updateBanLabel();
}

const LOG_KINDS = {
  ban: { tag: "封禁", cls: "" },
  unban: { tag: "解封", cls: "unban" },
  appeal_pass: { tag: "申诉通过", cls: "unban" },
  appeal_refuse: { tag: "申诉驳回", cls: "refuse" },
};

function logDetail(l) {
  if (l.action === "ban") {
    return `封禁 ${l.day || "-"} 天 · ${l.reason ? esc(l.reason) : "无理由"}`;
  }
  if (l.action === "unban") return "解除封禁";
  if (l.action === "appeal_pass") {
    return `${l.ok ? "通过申诉并已解封" : "尝试通过申诉"}${l.reason ? ` · 申诉理由：${esc(l.reason)}` : ""}`;
  }
  if (l.action === "appeal_refuse") {
    return `${l.ok ? "驳回申诉" : "尝试驳回申诉"}${l.reason ? ` · 申诉理由：${esc(l.reason)}` : ""}`;
  }
  return esc(l.message || "");
}

function renderLogs(logs) {
  const box = $("#logList");
  if (!logs || !logs.length) {
    box.innerHTML = `<p class="muted small">还没有操作记录</p>`;
    return;
  }
  box.innerHTML = logs
    .map((l) => {
      const name = l.nick_name || l.user_name || l.user_id;
      const kind = LOG_KINDS[l.action] || { tag: l.action, cls: "" };
      return `<div class="log-item">
        <div class="log-dot ${l.ok ? "ok" : "fail"}"></div>
        <div class="log-body">
          <div class="log-title">
            <span class="act-tag ${kind.cls}">${esc(kind.tag)}</span>
            <b>${esc(name)}</b>
            <span class="muted small">${l.ok ? "成功" : "失败"}</span>
          </div>
          <div class="log-meta">${fmtTime(l.ts)} · ${logDetail(l)}${l.ok ? "" : ` · ${esc(l.message || "")}`}</div>
        </div>
      </div>`;
    })
    .join("");
}

function renderBlocks(data) {
  const box = $("#blockList");
  const items = data?.items || [];
  state.blocks = items;

  const page = data?.page || {};
  state.blockPages = page.total || 1;
  state.blockTotal = page.count || items.length;

  const countEl = $("#blockCount");
  countEl.hidden = !items.length && !state.blockTotal;
  countEl.textContent = state.blockTotal ? `${state.blockTotal} 人` : "";

  $("#selectBar").hidden = !items.length;
  const pager = $("#blockPager");
  pager.hidden = state.blockPages <= 1;
  $("#pageInfo").textContent = `第 ${state.blockPage} / ${state.blockPages} 页`;
  $("#btnPrevPage").disabled = state.blockPage <= 1;
  $("#btnNextPage").disabled = state.blockPage >= state.blockPages;

  if (!items.length) {
    box.innerHTML = `<p class="muted small">暂无待解封用户</p>`;
    syncSelectBar();
    return;
  }

  box.innerHTML = items
    .map((it) => {
      const key = String(it.user_id);
      const on = state.selected.has(key);
      const name = it.nick_name || it.user_name || it.user_id;
      return `<label class="row-item selectable${on ? " on" : ""}">
        <input class="row-check" type="checkbox" data-uid="${esc(key)}" ${on ? "checked" : ""} />
        <span class="av">${esc(Array.from(String(name))[0] || "?")}</span>
        <span class="row-main">
          <span class="nm">${esc(name)}</span>
          <span class="sub">UID ${it.user_id}${it.block_time ? ` · ${esc(it.block_time)}` : ""}</span>
        </span>
        <span class="row-day">${it.day} 天</span>
      </label>`;
    })
    .join("");

  $$(".row-check", box).forEach((cb) =>
    cb.addEventListener("change", () => toggleSelect(cb.dataset.uid, cb.checked))
  );
  syncSelectBar();
}

function toggleSelect(uid, on) {
  const key = String(uid);
  const item = state.blocks.find((it) => String(it.user_id) === key);
  if (on && item) state.selected.set(key, item);
  else if (!on) state.selected.delete(key);
  const row = $(`.row-check[data-uid="${key}"]`)?.closest(".row-item");
  if (row) row.classList.toggle("on", on);
  syncSelectBar();
}

function syncSelectBar() {
  const total = state.blocks.length;
  const picked = state.selected.size;
  const grand = Math.max(state.blockTotal, total);
  const multiPage = grand > total;

  $("#selCount").textContent = state.loadingAll
    ? `正在载入全部 ${grand} 人…`
    : picked
      ? `已选 ${picked} 人`
      : "已选 0 人";

  const onPage = state.blocks.filter((it) => state.selected.has(String(it.user_id))).length;
  const pageAll = total > 0 && onPage === total;
  const allSelected = multiPage && picked >= grand;

  const btnAll = $("#btnScopeAll");
  const btnPage = $("#btnScopePage");
  btnAll.hidden = !multiPage;
  btnAll.classList.toggle("active", allSelected);
  btnPage.classList.toggle("active", pageAll && !allSelected);
  btnAll.disabled = state.loadingAll || state.busy;
  btnPage.disabled = state.loadingAll || state.busy;

  const day = currentDay();
  $("#btnBatchBan").textContent = `封禁 ${day} 天`;
  $("#btnBatchBan").disabled = picked === 0 || state.busy;
  $("#btnBatchUnban").disabled = picked === 0 || state.busy;
}

function markVisibleRows() {
  $$(".row-check").forEach((cb) => {
    const on = state.selected.has(String(cb.dataset.uid));
    cb.checked = on;
    const row = cb.closest(".row-item");
    if (row) row.classList.toggle("on", on);
  });
}

function clearSelection() {
  state.selected.clear();
  markVisibleRows();
  syncSelectBar();
}

/* 选中当前页的人；已经是「整页全选」时再点一次则取消 */
function selectCurrentPage() {
  const total = state.blocks.length;
  if (!total) return;
  const pageAll =
    state.selected.size === total &&
    state.blocks.every((it) => state.selected.has(String(it.user_id)));
  if (pageAll) {
    clearSelection();
    return;
  }
  state.selected.clear();
  state.blocks.forEach((it) => state.selected.set(String(it.user_id), it));
  markVisibleRows();
  syncSelectBar();
}

/* 选中吧务后台里的所有封禁用户（含其他页）；已全选时再点一次则取消 */
async function selectAllUsers() {
  const total = state.blocks.length;
  const grand = Math.max(state.blockTotal, total);
  if (!grand) return;

  // 只有一页时「全选」等价于「本页」
  if (grand <= total) {
    selectCurrentPage();
    return;
  }
  if (state.selected.size >= grand && state.allUsers) {
    clearSelection();
    return;
  }
  if (state.allUsers) {
    state.selected.clear();
    state.allUsers.forEach((it) => state.selected.set(String(it.user_id), it));
    markVisibleRows();
    syncSelectBar();
    return;
  }

  state.loadingAll = true;
  syncSelectBar();
  try {
    const data = await api("/api/blocks?all=1");
    const items = data.items || [];
    state.allUsers = items;
    if (data?.page?.count) state.blockTotal = data.page.count;
    state.selected.clear();
    items.forEach((it) => state.selected.set(String(it.user_id), it));
    markVisibleRows();
    if (items.length) toast(`已选中全部 ${items.length} 人（含其他页）`);
  } catch (err) {
    toast(err.message, "err");
  } finally {
    state.loadingAll = false;
    syncSelectBar();
  }
}

function selectedUsers() {
  return [...state.selected.values()];
}

function renderMemory(lastTarget) {
  state.lastTarget = lastTarget;
  const hint = $("#memoryHint");
  const forget = $("#btnForget");
  if (lastTarget) {
    hint.innerHTML = `已记住上次：<b>${esc(displayName(lastTarget))}</b>`;
    forget.hidden = false;
  } else {
    hint.textContent = "支持记忆最近操作的目标";
    forget.hidden = true;
  }
}

function handleFnameCorrection(data) {
  const fix = data.fname_corrected;
  if (!fix) return;
  if (data.config) applyConfig(data.config);
  toast(`目标贴吧已自动修正：「${fix.from}」是一个同名空吧，已改为「${fix.to}」`);
}

/* ---------------- 业务 ---------------- */

async function resolveTarget() {
  const query = $("#query").value.trim();
  if (!query) {
    toast("请输入用户名或 UID", "err");
    $("#query").focus();
    return;
  }
  localStorage.setItem("tb_last_query", query);
  setBusy(true);
  try {
    const data = await api("/api/resolve", {
      method: "POST",
      body: JSON.stringify({ query }),
    });
    renderUser(data.user);
    renderTargets(data.targets);
    renderMemory(data.last_target);
    handleFnameCorrection(data);
    if (data.user.forum_error) {
      toast(`吧内信息读取失败：${data.user.forum_error}`, "err");
    }
  } catch (err) {
    toast(err.message, "err");
  } finally {
    setBusy(false);
  }
}

function openConfirm() {
  if (!state.user) return;
  const u = state.user;
  const day = currentDay();
  const reason = $("#reason").value.trim() || "（未填写）";
  const name = u.nick_name || u.user_name || u.user_id;
  $("#confirmBody").innerHTML = `
    <div class="user-card" style="margin-bottom:10px">
      ${avatarHtml(u.avatar, name)}
      <div class="u-main">
        <div class="u-name"><span class="txt">${esc(name)}</span></div>
        <div class="u-sub"><code>UID ${u.user_id}</code>${u.user_name ? `<span>@${esc(u.user_name)}</span>` : ""}</div>
      </div>
    </div>
    <div class="confirm-line"><span>目标贴吧</span><span>${esc(state.config?.fname || "")}</span></div>
    <div class="confirm-line"><span>封禁时长</span><span>${day} 天</span></div>
    <div class="confirm-line"><span>封禁理由</span><span>${esc(reason)}</span></div>
    <div class="warn-box">该操作会立即生效，如需撤销只能在吧务后台解封。请再次确认目标没有弄错。</div>`;
  $("#confirmModal").hidden = false;
}

async function doBan() {
  if (!state.user) return;
  const day = currentDay();
  const reason = $("#reason").value.trim();
  setBusy(true);
  $("#confirmModal").hidden = true;
  try {
    const data = await api("/api/ban", {
      method: "POST",
      body: JSON.stringify({ query: state.user.user_id, day, reason }),
    });
    renderLogs(data.logs);
    renderTargets(data.targets);
    renderMemory(data.last_target);
    handleFnameCorrection(data);
    if (data.success) {
      const via = data.route ? `（${data.route}）` : "";
      toast(`${data.user.nick_name || data.user.user_id} 已被封禁 ${day} 天${via}`);
    } else {
      state.lastAttempts = data.attempts || [];
      toast(data.message || "封禁失败", "err");
      if (/权限|3150003/.test(data.message || "")) setTimeout(openDiagnose, 500);
    }
    refreshBlocks(true);
  } catch (err) {
    toast(err.message, "err");
  } finally {
    setBusy(false);
  }
}

async function doUnban() {
  if (!state.user) return;
  setBusy(true);
  try {
    const data = await api("/api/unban", {
      method: "POST",
      body: JSON.stringify({ query: state.user.user_id }),
    });
    renderLogs(data.logs);
    if (data.success) toast(`${data.user.nick_name || data.user.user_id} 已解封`);
    else toast(data.message || "解封失败", "err");
    refreshBlocks(true);
  } catch (err) {
    toast(err.message, "err");
  } finally {
    setBusy(false);
  }
}

async function refreshBlocks(silent = false) {
  const btn = $("#btnRefreshBlocks");
  btn.disabled = true;
  // 列表可能已经变了，全量缓存作废
  state.allUsers = null;
  try {
    const data = await api(`/api/blocks?page=${state.blockPage}`);
    const totalPages = Math.max(1, data?.page?.total || 1);
    // 批量操作后当前页可能已经空了，回落到最后一页
    if (!(data.items || []).length && state.blockPage > totalPages) {
      state.blockPage = totalPages;
      return refreshBlocks(silent);
    }
    renderBlocks(data);
  } catch (err) {
    if (!silent) toast(err.message, "err");
    state.blocks = [];
    state.blockTotal = 0;
    state.blockPages = 1;
    $("#blockCount").hidden = true;
    $("#selectBar").hidden = true;
    $("#blockPager").hidden = true;
    $("#blockList").innerHTML = `<p class="muted small">${esc(err.message)}</p>`;
  } finally {
    btn.disabled = false;
  }
}

function gotoBlockPage(delta) {
  const next = Math.min(Math.max(1, state.blockPage + delta), state.blockPages || 1);
  if (next === state.blockPage) return;
  state.blockPage = next;
  refreshBlocks(true);
}

/* ---------------- 批量操作（封禁中列表多选） ---------------- */

function attachFade(list) {
  if (!list) return;
  const sync = () => {
    const more = list.scrollHeight - list.clientHeight - list.scrollTop > 4;
    list.classList.toggle("fade", more);
  };
  list.addEventListener("scroll", sync);
  requestAnimationFrame(sync);
}

const BATCH_CHUNK = 50;

function openBatchConfirm(mode) {
  const users = selectedUsers();
  if (!users.length) {
    toast("请先勾选要处理的用户", "err");
    return;
  }
  const isBan = mode === "ban";
  const day = currentDay();
  const reason = $("#reason").value.trim() || "（未填写）";

  state.batchMode = mode;
  state.batchPending = users;
  state.batchDone = false;
  // 申诉流程也用这个弹窗，进来前先把它清掉
  state.appealMode = null;
  state.appealDone = false;

  $("#batchTitle").textContent = isBan
    ? `确认批量封禁 ${users.length} 人`
    : `确认批量解封 ${users.length} 人`;
  $("#btnBatchConfirm").textContent = isBan
    ? `封禁 ${users.length} 人`
    : `解封 ${users.length} 人`;
  $("#btnBatchConfirm").className = isBan ? "btn danger" : "btn primary";
  $("#btnBatchConfirm").disabled = false;
  $("#btnBatchCancel").hidden = false;

  const rows = users
    .map(
      (u) => `<div class="batch-item">
        <span class="nm">${esc(u.nick_name || u.user_name || u.user_id)}</span>
        <span class="sub">UID ${u.user_id}${u.day ? ` · 此前 ${u.day} 天` : ""}</span>
      </div>`
    )
    .join("");

  const chunks = Math.ceil(users.length / BATCH_CHUNK);
  const chunkHint =
    chunks > 1
      ? `<div class="hint-box">共 ${users.length} 人，会分成 ${chunks} 批依次提交，中途请不要关闭页面。</div>`
      : "";

  $("#batchBody").innerHTML = `
    <div class="batch-list">${rows}</div>
    ${chunkHint}
    <div class="confirm-line"><span>目标贴吧</span><span>${esc(state.config?.fname || "")}</span></div>
    ${
      isBan
        ? `<div class="confirm-line"><span>封禁时长</span><span>${day} 天</span></div>
           <div class="confirm-line"><span>封禁理由</span><span>${esc(reason)}</span></div>
           <div class="warn-box">这些人当前还在封禁中，重新封禁会把到期时间重置为「现在 + ${day} 天」，相当于续封；如果其中有人已经自动到期，也会被重新封上。</div>`
        : `<div class="hint-box">解封后这些人会立刻恢复发言权限，需要时可以在吧务后台重新封禁。</div>`
    }`;
  attachFade($("#batchBody .batch-list"));
  $("#batchModal").hidden = false;
}

async function runBatch() {
  const mode = state.batchMode;
  const users = state.batchPending || [];
  if (!mode || !users.length) return;
  const isBan = mode === "ban";
  const day = currentDay();
  const reason = $("#reason").value.trim();

  const buildPayload = (part) => {
    const payload = {
      users: part.map((u) => ({
        user_id: u.user_id,
        user_name: u.user_name || "",
        nick_name: u.nick_name || "",
      })),
    };
    if (isBan) {
      payload.day = day;
      payload.reason = reason;
    }
    return payload;
  };

  const chunks = [];
  for (let i = 0; i < users.length; i += BATCH_CHUNK) {
    chunks.push(users.slice(i, i + BATCH_CHUNK));
  }

  setBusy(true);
  $("#btnBatchConfirm").disabled = true;
  $("#btnBatchCancel").hidden = true;

  const merged = { results: [], success_count: 0, fail_count: 0, logs: null };
  try {
    for (let i = 0; i < chunks.length; i++) {
      const done = i * BATCH_CHUNK;
      const stage =
        chunks.length > 1
          ? `第 ${i + 1} / ${chunks.length} 批（已完成 ${done} / ${users.length} 人）`
          : `共 ${users.length} 人`;
      $("#batchBody").innerHTML = `<p class="muted small">正在逐个提交：${stage}，过程中请不要关闭页面…</p>`;

      const data = await api(isBan ? "/api/ban_batch" : "/api/unban_batch", {
        method: "POST",
        body: JSON.stringify(buildPayload(chunks[i])),
      });
      merged.results.push(...(data.results || []));
      merged.success_count += data.success_count || 0;
      merged.fail_count += data.fail_count || 0;
      if (data.logs) merged.logs = data.logs;
    }

    if (merged.logs) renderLogs(merged.logs);
    state.allUsers = null;
    state.selected.clear();
    showBatchResult(merged, day);
    refreshBlocks(true);
  } catch (err) {
    $("#batchModal").hidden = true;
    toast(err.message, "err");
    refreshBlocks(true);
  } finally {
    $("#btnBatchConfirm").disabled = false;
    setBusy(false);
  }
}

function showBatchResult(data, day) {
  const failed = (data.results || []).filter((r) => !r.ok);
  const isBan = state.batchMode === "ban";
  state.batchDone = true;

  $("#batchTitle").textContent = isBan ? "批量封禁结果" : "批量解封结果";
  $("#batchBody").innerHTML = `
    <div class="result-sum">
      成功 <b class="ok-text">${data.success_count}</b> 人 ·
      失败 <b class="${failed.length ? "bad-text" : "ok-text"}">${data.fail_count}</b> 人
      ${isBan ? `<span class="muted small">· 时长 ${day} 天</span>` : ""}
    </div>
    ${
      failed.length
        ? `<div class="batch-list err">${failed
            .map(
              (r) => `<div class="batch-item">
                <span class="nm">${esc(r.name)}</span>
                <span class="sub">${esc(r.message)}</span>
              </div>`
            )
            .join("")}</div>`
        : `<div class="hint-box">全部处理成功。</div>`
    }`;
  attachFade($("#batchBody .batch-list"));
  $("#btnBatchConfirm").textContent = "知道了";
  $("#btnBatchConfirm").className = "btn primary";
  $("#btnBatchCancel").hidden = true;
  $("#batchModal").hidden = false;

  toast(
    `${isBan ? "批量封禁" : "批量解封"}完成：成功 ${data.success_count} 人，失败 ${data.fail_count} 人`,
    failed.length ? "err" : "ok"
  );
}

/* ---------------- 解封申诉 ---------------- */

function appealName(a) {
  return a.nick_name || a.user_name || String(a.user_id || "");
}

function renderAppeals(data) {
  const box = $("#appealList");
  const items = (data && data.items) || [];
  state.appeals = items;

  const countEl = $("#appealCount");
  countEl.hidden = !items.length;
  countEl.textContent = items.length ? `${items.length} 条` : "";
  $("#appealBar").hidden = !items.length;

  if (!items.length) {
    box.innerHTML = `<p class="muted small">暂无待处理的解封申诉</p>`;
    syncAppealBar();
    return;
  }

  box.innerHTML = items
    .map((a) => {
      const name = appealName(a);
      const meta = [
        a.punish_day ? `原封禁 ${a.punish_day} 天` : "封禁中",
        a.punish_reason ? esc(a.punish_reason) : "",
        a.punish_time ? esc(a.punish_time) : "",
      ]
        .filter(Boolean)
        .join(" · ");
      const on = state.appealSel.has(String(a.appeal_id));
      return `<div class="row-item appeal-item${on ? " on" : ""}" data-id="${a.appeal_id}">
        <input class="row-check appeal-check" type="checkbox" data-id="${a.appeal_id}" ${on ? "checked" : ""} />
        <span class="av">${esc(Array.from(name)[0] || "?")}</span>
        <div class="row-main">
          <span class="nm">${esc(name)}<span class="muted small"> · UID ${a.user_id}</span></span>
          <span class="sub">${meta}</span>
          <span class="sub appeal-why" title="${esc(a.appeal_reason || "")}">${a.appeal_reason ? esc(a.appeal_reason) : "（未填写申诉理由）"}</span>
        </div>
        <span class="row-ops">
          <button class="mini-btn pass" type="button" data-act="pass" data-id="${a.appeal_id}">通过</button>
          <button class="mini-btn refuse" type="button" data-act="refuse" data-id="${a.appeal_id}">驳回</button>
        </span>
      </div>`;
    })
    .join("");

  if (data && data.truncated) {
    box.insertAdjacentHTML(
      "beforeend",
      `<p class="muted small">待处理申诉超过 100 条，这里只显示最新 100 条；处理完一批后点「刷新」读取剩下的。</p>`
    );
  }

  $$(".appeal-check", box).forEach((cb) =>
    cb.addEventListener("change", () => toggleAppeal(cb.dataset.id, cb.checked))
  );
  $$(".mini-btn", box).forEach((btn) =>
    btn.addEventListener("click", () => {
      const a = state.appeals.find((x) => String(x.appeal_id) === btn.dataset.id);
      if (a) openAppealConfirm(btn.dataset.act === "pass", [a]);
    })
  );
  syncAppealBar();
}

function toggleAppeal(id, on) {
  const key = String(id);
  if (on) state.appealSel.add(key);
  else state.appealSel.delete(key);
  const row = $(`.appeal-item[data-id="${key}"]`);
  if (row) row.classList.toggle("on", on);
  syncAppealBar();
}

function syncAppealBar() {
  const bar = $("#appealBar");
  if (!bar) return;
  const total = state.appeals.length;
  const picked = state.appealSel.size;
  $("#appealSelCount").textContent = picked ? `已选 ${picked} 条` : "已选 0 条";
  const btnAll = $("#btnAppealAll");
  btnAll.classList.toggle("active", total > 0 && picked >= total);
  btnAll.disabled = !total || state.busy;
  $("#btnAppealPass").disabled = !picked || state.busy;
  $("#btnAppealRefuse").disabled = !picked || state.busy;
}

function selectAllAppeals() {
  const total = state.appeals.length;
  if (!total) return;
  const all = state.appealSel.size >= total;
  state.appealSel.clear();
  if (!all) state.appeals.forEach((a) => state.appealSel.add(String(a.appeal_id)));
  $$(".appeal-check").forEach((cb) => {
    const on = state.appealSel.has(String(cb.dataset.id));
    cb.checked = on;
    const row = cb.closest(".appeal-item");
    if (row) row.classList.toggle("on", on);
  });
  syncAppealBar();
}

function selectedAppeals() {
  return state.appeals.filter((a) => state.appealSel.has(String(a.appeal_id)));
}

async function refreshAppeals(silent = false) {
  const btn = $("#btnRefreshAppeals");
  if (btn) btn.disabled = true;
  try {
    const data = await api("/api/appeals");
    state.appealSel.clear();
    renderAppeals(data);
  } catch (err) {
    state.appeals = [];
    state.appealSel.clear();
    $("#appealCount").hidden = true;
    $("#appealBar").hidden = true;
    $("#appealList").innerHTML = `<p class="muted small">${esc(err.message)}</p>`;
    if (!silent) toast(err.message, "err");
  } finally {
    if (btn) btn.disabled = false;
  }
}

function openAppealConfirm(pass, list) {
  if (state.busy) return;
  const items = list && list.length ? list : selectedAppeals();
  if (!items.length) {
    toast("请先勾选要处理的申诉", "err");
    return;
  }

  // 封禁批量和申诉共用同一个弹窗，进来前先清掉另一套流程的状态
  state.batchMode = null;
  state.batchDone = false;
  state.appealMode = pass ? "pass" : "refuse";
  state.appealPending = items;
  state.appealDone = false;

  $("#batchTitle").textContent = pass
    ? `确认通过 ${items.length} 条申诉`
    : `确认驳回 ${items.length} 条申诉`;
  $("#btnBatchConfirm").textContent = pass
    ? `通过并解封 ${items.length} 人`
    : `驳回 ${items.length} 条`;
  $("#btnBatchConfirm").className = pass ? "btn primary" : "btn danger";
  $("#btnBatchConfirm").disabled = false;
  $("#btnBatchCancel").hidden = false;

  const rows = items
    .map(
      (a) => `<div class="batch-item">
        <span class="nm">${esc(appealName(a))}</span>
        <span class="sub">UID ${a.user_id} · ${a.punish_day ? `原封禁 ${a.punish_day} 天` : "封禁中"}${a.punish_reason ? ` · ${esc(a.punish_reason)}` : ""}</span>
        ${a.appeal_reason ? `<span class="sub">申诉：${esc(a.appeal_reason)}</span>` : ""}
      </div>`
    )
    .join("");

  $("#batchBody").innerHTML = `
    <div class="batch-list">${rows}</div>
    ${
      pass
        ? `<div class="warn-box">「通过」等于立即解封：这些人马上会恢复发言权限。如果只是暂时不想处理，直接关掉本窗口即可。</div>`
        : `<div class="hint-box">「驳回」只关闭申诉，不会延长封禁。若认为处罚偏轻，处理完可以到左侧重新封禁。</div>`
    }`;
  attachFade($("#batchBody .batch-list"));
  $("#batchModal").hidden = false;
}

async function runAppeal() {
  const pass = state.appealMode === "pass";
  const items = state.appealPending || [];
  if (!state.appealMode || !items.length) return;

  setBusy(true);
  $("#btnBatchConfirm").disabled = true;
  $("#btnBatchCancel").hidden = true;
  $("#batchBody").innerHTML = `<p class="muted small">正在提交 ${items.length} 条申诉，请稍候…</p>`;

  try {
    const data = await api("/api/appeals/handle", {
      method: "POST",
      body: JSON.stringify({
        refuse: !pass,
        items: items.map((a) => ({
          appeal_id: a.appeal_id,
          user_id: a.user_id,
          user_name: a.user_name || "",
          nick_name: a.nick_name || "",
          punish_day: a.punish_day || 0,
          appeal_reason: a.appeal_reason || "",
        })),
      }),
    });

    if (data.logs) renderLogs(data.logs);
    showAppealResult(data, pass);

    // 处理成功的从待办里摘掉，失败的留着重试
    const doneIds = new Set(
      (data.results || []).filter((r) => r.ok).map((r) => String(r.appeal_id))
    );
    state.appeals = state.appeals.filter((a) => !doneIds.has(String(a.appeal_id)));
    state.appealSel.clear();
    renderAppeals({ items: state.appeals });
    if (pass) refreshBlocks(true);
  } catch (err) {
    $("#batchModal").hidden = true;
    toast(err.message, "err");
  } finally {
    $("#btnBatchConfirm").disabled = false;
    setBusy(false);
  }
}

function showAppealResult(data, pass) {
  const failed = (data.results || []).filter((r) => !r.ok);
  state.appealDone = true;
  state.appealMode = null;

  $("#batchTitle").textContent = pass ? "申诉处理结果" : "申诉驳回结果";
  $("#batchBody").innerHTML = `
    <div class="result-sum">
      成功 <b class="ok-text">${data.success_count}</b> 条 ·
      失败 <b class="${failed.length ? "bad-text" : "ok-text"}">${data.fail_count}</b> 条
      <span class="muted small">· ${pass ? "已解封" : "已驳回"}</span>
    </div>
    ${
      failed.length
        ? `<div class="batch-list err">${failed
            .map(
              (r) => `<div class="batch-item">
                <span class="nm">${esc(r.name)}</span>
                <span class="sub">${esc(r.message)}</span>
              </div>`
            )
            .join("")}</div>`
        : `<div class="hint-box">全部处理成功。</div>`
    }
    ${data.hint ? `<div class="warn-box">${esc(data.hint)}</div>` : ""}`;
  attachFade($("#batchBody .batch-list"));
  $("#btnBatchConfirm").textContent = "知道了";
  $("#btnBatchConfirm").className = "btn primary";
  $("#btnBatchConfirm").disabled = false;
  $("#btnBatchCancel").hidden = true;
  $("#batchModal").hidden = false;

  toast(
    `${pass ? "通过申诉" : "驳回申诉"}：成功 ${data.success_count} 条，失败 ${data.fail_count} 条`,
    failed.length ? "err" : "ok"
  );
}

async function forgetLast() {
  if (!state.lastTarget) return;
  const key = state.lastTarget.key;
  try {
    await api(`/api/targets/${encodeURIComponent(key)}`, { method: "DELETE" });
    $("#query").value = "";
    localStorage.removeItem("tb_last_query");
    renderUser(null);
    const data = await api("/api/targets");
    renderTargets(data.targets);
    const last = data.targets && data.targets[0] ? data.targets[0] : null;
    renderMemory(last);
    toast("已清除记忆");
  } catch (err) {
    toast(err.message, "err");
  }
}

/* ---------------- 设置 ---------------- */

function openSettings() {
  const cfg = state.config || {};
  $("#setFname").value = cfg.fname || "";
  $("#setDay").value = cfg.default_day || 10;
  $("#setReason").value = cfg.default_reason || "";
  $("#setBduss").value = "";
  $("#setStoken").value = "";
  $("#setBduss").placeholder = cfg.configured
    ? `已配置（${cfg.bduss_tail || "****"}），留空表示不修改`
    : "粘贴 BDUSS";
  $("#setStoken").placeholder = cfg.has_stoken ? "已配置，留空表示不修改" : "可选，一般留空";
  $("#settingsModal").hidden = false;
}

async function saveSettings() {
  const payload = {
    fname: $("#setFname").value.trim(),
    default_day: Number($("#setDay").value) || 10,
    default_reason: $("#setReason").value.trim(),
  };
  const bduss = $("#setBduss").value.trim();
  const stoken = $("#setStoken").value.trim();
  if (bduss) payload.BDUSS = bduss;
  if (stoken) payload.STOKEN = stoken;

  if (!payload.fname) {
    toast("请先填写目标贴吧的名字", "err");
    $("#setFname").focus();
    return;
  }
  if (!bduss && !state.config?.configured) {
    toast("请先粘贴 BDUSS", "err");
    $("#setBduss").focus();
    return;
  }

  setBusy(true);
  try {
    const data = await api("/api/settings", {
      method: "POST",
      body: JSON.stringify(payload),
    });
    $("#settingsModal").hidden = true;
    applyConfig(data.config);
    setStatus(data.status);
    if (data.status.login) {
      refreshBlocks(true);
      refreshAppeals(true);
    }
    if (data.status.login) toast("设置已保存，登录成功");
    else toast(data.status.error || "设置已保存，但登录校验未通过", "err");
  } catch (err) {
    toast(err.message, "err");
  } finally {
    setBusy(false);
  }
}

/* ---------------- 诊断 ---------------- */

function diagRow(label, valueHtml) {
  return `<div class="confirm-line"><span>${esc(label)}</span><span>${valueHtml}</span></div>`;
}

function renderDiagnosis(d) {
  const self = d.self || {};
  const isBawu = /吧主/.test(d.role || "");
  const loggedIn = d.logged_in !== false && (self.user_id || 0) > 0;
  const rows = [
    diagRow("当前账号", `${esc(self.name || "-")} <code>UID ${self.user_id ?? "-"}</code>`),
    diagRow(
      "目标贴吧",
      `${esc(d.resolved_fname || d.fname || "")} <code>fid ${d.fid || "-"}</code>`
    ),
    diagRow(
      "吧务身份",
      `<b class="${isBawu ? "ok-text" : "bad-text"}">${esc(d.role || "未知")}</b>`
    ),
    diagRow(
      "APP 端凭证",
      `<span class="${d.client_tbs === "已获取" ? "ok-text" : "bad-text"}">${esc(d.client_tbs || "-")}</span>`
    ),
    diagRow(
      "网页端凭证",
      `<span class="${d.web_tbs === "可用" ? "ok-text" : "bad-text"}">${esc(d.web_tbs || "-")}</span>`
    ),
  ];
  if (d.bawu_error) {
    rows.push(diagRow("吧务信息", `<span class="bad-text">${esc(d.bawu_error)}</span>`));
  }

  let hint;
  if (!loggedIn) {
    hint = `<div class="warn-box">登录凭证无效：${esc(d.login_error || "BDUSS 已过期")}。请重新从浏览器复制 BDUSS 后，在「设置」里粘贴保存。</div>`;
  } else if (!isBawu) {
    hint = `<div class="warn-box">当前 BDUSS 对应的账号不在「${esc(d.resolved_fname || d.fname || "")}」的吧务团队里，封禁一定会被拒绝。请在浏览器里确认登录的是大吧主账号，重新复制一份 BDUSS。</div>`;
  } else {
    hint = `<div class="hint-box">吧务身份正常（${esc(d.role)}）。封禁会优先走 APP 端凭证；若报 3150003，先核对上方 fid 是否为你管理的那个吧。</div>`;
    if (d.web_tbs !== "可用") {
      const detail = String(d.web_tbs || "")
        .replace(/^不可用[（(]?/, "")
        .replace(/[）)]$/, "");
      hint += `<div class="warn-box">网页端凭证不可用${detail ? `：${esc(detail)}` : ""}。这只影响备用封禁通道，APP 端凭证正常时不受影响。</div>`;
    }
  }
  if (d.fid_corrected) {
    hint = `<div class="hint-box">吧名已自动修正：「${esc(d.fname)}」是另一个同名空吧，实际生效的是「${esc(d.resolved_fname)}」（fid ${d.fid}）。</div>` + hint;
  }
  if (state.lastAttempts && state.lastAttempts.length) {
    hint += `<div class="diag-attempts"><div class="diag-title">上次封禁尝试</div>${state.lastAttempts
      .map(
        (a) =>
          `<div class="diag-attempt"><span>${esc(a.route)}</span><em>${esc(a.error || "")}</em></div>`
      )
      .join("")}</div>`;
  }
  $("#diagBody").innerHTML = rows.join("") + hint;
}

async function openDiagnose() {
  $("#diagModal").hidden = false;
  $("#diagBody").innerHTML = '<p class="muted small">检查中…</p>';
  try {
    const data = await api("/api/diagnose");
    renderDiagnosis(data.diagnosis || {});
  } catch (err) {
    $("#diagBody").innerHTML = `<p class="bad-text small">${esc(err.message)}</p>`;
  }
}

/* ---------------- 初始化 ---------------- */

async function init() {
  try {
    const data = await api("/api/bootstrap");
    applyConfig(data.config);
    setStatus(data.status);
    renderTargets(data.targets);
    renderLogs(data.logs);
    renderMemory(data.last_target);

    const queryInput = $("#query");
    if (data.last_target) {
      queryInput.value = data.last_target.user_name || String(data.last_target.user_id);
    } else {
      const remembered = localStorage.getItem("tb_last_query");
      if (remembered) queryInput.value = remembered;
    }

    if (data.status.login) {
      refreshBlocks(true);
      refreshAppeals(true);
    }

    if (data.status.login && queryInput.value.trim()) {
      resolveTarget();
    } else if (!data.config.configured && !location.search.includes("plain")) {
      // 首次使用且未配置凭证时，直接弹出设置面板
      setTimeout(openSettings, 420);
    }
  } catch (err) {
    toast(err.message, "err");
  }
}

function bind() {
  $("#btnResolve").addEventListener("click", resolveTarget);
  $("#query").addEventListener("keydown", (e) => {
    if (e.key === "Enter") resolveTarget();
  });
  $("#reason").addEventListener("keydown", (e) => {
    if (e.key === "Enter" && state.user) openConfirm();
  });

  $$("#daySeg button").forEach((btn) => {
    btn.addEventListener("click", () => {
      const value = btn.dataset.day;
      if (value === "custom") {
        setDay(Number($("#customDay").value) || 10);
        state.custom = true;
        $$("#daySeg button").forEach((b) => b.classList.toggle("active", b === btn));
        $("#customDay").hidden = false;
        $("#customDay").focus();
        updateBanLabel();
      } else {
        setDay(Number(value));
      }
    });
  });
  $("#customDay").addEventListener("input", () => {
    state.custom = true;
    state.day = currentDay();
    updateBanLabel();
  });

  $("#btnBan").addEventListener("click", openConfirm);
  $("#btnUnban").addEventListener("click", doUnban);
  $("#btnConfirmBan").addEventListener("click", doBan);
  $("#btnRefreshBlocks").addEventListener("click", () => refreshBlocks(false));
  $("#btnScopeAll").addEventListener("click", () => selectAllUsers());
  $("#btnScopePage").addEventListener("click", () => selectCurrentPage());
  $("#btnPrevPage").addEventListener("click", () => gotoBlockPage(-1));
  $("#btnNextPage").addEventListener("click", () => gotoBlockPage(1));
  $("#btnBatchBan").addEventListener("click", () => openBatchConfirm("ban"));
  $("#btnBatchUnban").addEventListener("click", () => openBatchConfirm("unban"));
  $("#btnBatchConfirm").addEventListener("click", () => {
    if (state.appealDone) {
      $("#batchModal").hidden = true;
      state.appealDone = false;
      return;
    }
    if (state.batchDone) {
      $("#batchModal").hidden = true;
      state.batchDone = false;
      return;
    }
    if (state.appealMode) {
      runAppeal();
      return;
    }
    runBatch();
  });
  $("#btnRefreshAppeals").addEventListener("click", () => refreshAppeals(false));
  $("#btnAppealAll").addEventListener("click", selectAllAppeals);
  $("#btnAppealPass").addEventListener("click", () => openAppealConfirm(true));
  $("#btnAppealRefuse").addEventListener("click", () => openAppealConfirm(false));
  $("#btnForget").addEventListener("click", forgetLast);
  $("#btnSettings").addEventListener("click", openSettings);
  $("#btnSaveSettings").addEventListener("click", saveSettings);
  $("#btnDiagnose").addEventListener("click", openDiagnose);
  $("#btnRunDiagnose").addEventListener("click", openDiagnose);

  $$("[data-close]").forEach((el) =>
    el.addEventListener("click", () => {
      const modal = el.closest(".modal");
      if (modal) modal.hidden = true;
    })
  );
  $$(".modal").forEach((modal) =>
    modal.addEventListener("click", (e) => {
      if (e.target === modal) modal.hidden = true;
    })
  );
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") $$(".modal").forEach((m) => (m.hidden = true));
  });
}

bind();
init();
