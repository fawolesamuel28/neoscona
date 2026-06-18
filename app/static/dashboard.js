// ─── Reva Dashboard — Live Pipeline (template layout) ─────────────
const API = window.location.origin + "/api";
const WS_URL = `${window.location.protocol === "https:" ? "wss:" : "ws:"}//${window.location.host}/api/ws/dashboard`;

// ── Auth-aware fetch + WS token (provided by auth.js / RevaAuth) ─────────────
async function authToken() {
  try {
    return window.RevaAuth ? await window.RevaAuth.getAccessToken() : null;
  } catch (_e) {
    return null;
  }
}

// Drop-in replacement for fetch() that attaches the Supabase bearer token and
// bounces to the login overlay on 401.
async function authedFetch(url, opts = {}) {
  const token = await authToken();
  const headers = Object.assign({}, opts.headers || {});
  if (token) headers["Authorization"] = `Bearer ${token}`;
  const res = await fetch(url, Object.assign({}, opts, { headers }));
  if (res.status === 401 && window.RevaAuth && !window.RevaAuth.isAuthDisabled()) {
    await window.RevaAuth.signOut();
  }
  return res;
}

const POLL_INTERVAL_WS = 60_000;
const POLL_INTERVAL_FALLBACK = 4_000;
const POLL_HIDDEN_INTERVAL = 30_000;
const WS_RECONNECT_BASE_MS = 1_000;
const WS_RECONNECT_MAX_MS = 30_000;

// ── Lucide icons (guarded) + toast notifications ────────────────────────────
function renderIcons() {
  try { if (window.lucide) lucide.createIcons(); } catch (_e) {}
}

function toast(message, kind) {
  const stack = document.getElementById("toastStack");
  if (!stack) return;
  const t = document.createElement("div");
  t.className = `toast ${kind || ""}`;
  const icon = kind === "error" ? "alert-circle" : "check-circle";
  t.innerHTML = `<i data-lucide="${icon}"></i><span></span>`;
  t.querySelector("span").textContent = message;
  stack.appendChild(t);
  renderIcons();
  requestAnimationFrame(() => t.classList.add("in"));
  setTimeout(() => {
    t.classList.remove("in");
    setTimeout(() => t.remove(), 300);
  }, 2600);
}

// Centered empty-state block with a Lucide icon.
function emptyState(icon, title, hint, colspan) {
  const inner = `<div class="empty-state">
    <div class="es-icon"><i data-lucide="${icon}"></i></div>
    <div class="es-title">${title}</div>
    ${hint ? `<div class="es-hint">${hint}</div>` : ""}
  </div>`;
  return colspan ? `<tr><td colspan="${colspan}" style="padding:0">${inner}</td></tr>` : inner;
}

const AVATAR_GRADIENTS = [
  "linear-gradient(135deg,var(--grad-mint),var(--grad-sky))",
  "linear-gradient(135deg,var(--grad-lav),var(--grad-rose))",
  "linear-gradient(135deg,var(--grad-peach),var(--grad-mint))",
  "linear-gradient(135deg,var(--grad-sky),var(--grad-lav))",
  "linear-gradient(135deg,var(--grad-rose),var(--grad-peach))",
];

const SOURCE_COLORS = {
  telegram: "var(--sky)",
  whatsapp_organic: "var(--ok)",
  whatsapp_evolution: "var(--ok)",
  elevenlabs_receptionist: "#7c3aed",
  unknown: "var(--ink-soft)",
};

let allLeads = [];
let voiceLeads = [];
let lastStats = {};
let selectedPhone = null;
let selectedVoiceId = null;
let detailMode = "pipeline";
let leadsFingerprint = "";
let voiceFingerprint = "";
let statsFingerprint = "";
let pollTimer = null;
let isRefreshing = false;
let ws = null;
let wsConnected = false;
let wsReconnectAttempt = 0;
let wsReconnectTimer = null;
let refreshDebounceTimer = null;

let navFilter = "all";
let stageFilter = "all";
let searchQuery = "";

document.addEventListener("DOMContentLoaded", () => {
  el("btnImportPipeline")?.addEventListener("click", importSelectedVoiceLead);
  el("drawerClose")?.addEventListener("click", closeDrawer);
  el("drawerBackdrop")?.addEventListener("click", closeDrawer);
  el("searchInput")?.addEventListener("input", (e) => {
    searchQuery = e.target.value.trim().toLowerCase();
    renderLeadsTable();
  });

  document.querySelectorAll(".filter-chip").forEach((chip) => {
    chip.addEventListener("click", () => {
      document.querySelectorAll(".filter-chip").forEach((c) => c.classList.remove("active"));
      chip.classList.add("active");
      stageFilter = chip.dataset.stage || "all";
      renderLeadsTable();
    });
  });

  document.querySelectorAll(".sidebar .nav-item[data-filter]").forEach((item) => {
    item.addEventListener("click", () => {
      document.querySelectorAll(".sidebar .nav-item").forEach((n) => n.classList.remove("active"));
      item.classList.add("active");
      navFilter = item.dataset.filter || "all";
      if (navFilter === "telegram") stageFilter = "telegram";
      else if (navFilter === "new") stageFilter = "new";
      else if (navFilter === "hot") stageFilter = "hot";
      else if (navFilter === "takeover") stageFilter = "takeover";
      else if (navFilter === "voice") {
        const first = voiceLeads[0];
        if (first) openVoiceDrawer(first.id);
        return;
      }
      syncFilterChips();
      renderLeadsTable();
    });
  });

  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") closeDrawer();
  });
  document.addEventListener("visibilitychange", onVisibilityChange);

  el("btnSignOut")?.addEventListener("click", () => window.RevaAuth?.signOut());
  el("btnUpgrade")?.addEventListener("click", upgradeToGrowth);

  // Inbox / takeover drawer controls
  el("btnTakeover")?.addEventListener("click", () => inboxAction("takeover"));
  el("btnHandback")?.addEventListener("click", () => inboxAction("handback"));
  el("btnSendReply")?.addEventListener("click", sendHumanReply);
  el("replyText")?.addEventListener("keydown", (e) => {
    if ((e.metaKey || e.ctrlKey) && e.key === "Enter") sendHumanReply();
  });
  el("btnAddNote")?.addEventListener("click", addLeadNote);
  el("tagInput")?.addEventListener("keydown", (e) => {
    if (e.key === "Enter") { e.preventDefault(); addTagFromInput(); }
  });

  // Wait for the auth gate before hitting protected endpoints. auth.js resolves
  // RevaAuth.ready once it knows whether a session exists (or auth is disabled).
  startBooted = false;
  (window.RevaAuth ? window.RevaAuth.ready : Promise.resolve()).then(() => {
    bootDashboard();
  });
});

let startBooted = false;

// Starts the live data flow. Safe to call again after a fresh login.
function bootDashboard() {
  if (startBooted) return;
  startBooted = true;
  connectWebSocket();
  startPolling();
  refreshAll();
  loadPlanUsage();
}

// ── Plan & usage panel + trial banner (from /api/me) ─────────────────────────
async function loadPlanUsage() {
  try {
    const res = await authedFetch(`${API}/me`, { cache: "no-store" });
    if (res.status === 403) { window.location.href = "/onboarding"; return; }  // no org yet
    if (!res.ok) return;
    renderPlanUsage(await res.json());
  } catch (_e) { /* non-fatal */ }
}

function renderPlanUsage(me) {
  const plan = me.plan || {}, usage = me.usage || {}, tenant = me.tenant || {};
  const m = usage.messages || {};
  el("planName").textContent = plan.label || tenant.plan || "—";

  const used = m.used || 0;
  el("usageMsgLabel").textContent =
    m.limit == null ? `${used.toLocaleString()} · unlimited` : `${used.toLocaleString()} / ${(m.limit).toLocaleString()}`;
  const bar = el("usageMsgBar");
  const pct = m.pct == null ? 0 : Math.min(100, Math.round(m.pct * 100));
  if (bar?.firstElementChild) bar.firstElementChild.style.width = pct + "%";
  bar?.classList.toggle("warn", !!(m.warn || m.over));

  el("btnUpgrade")?.classList.remove("hidden");
  renderTrialBanner(tenant, m);
}

function renderTrialBanner(tenant, m) {
  const b = el("trialBanner");
  if (!b) return;
  let text = "";
  if (tenant.subscription_status === "trialing" && tenant.trial_ends_at) {
    const days = Math.max(0, Math.ceil((new Date(tenant.trial_ends_at) - new Date()) / 86400000));
    text = `Free trial — ${days} day${days === 1 ? "" : "s"} left.`;
  } else if (tenant.subscription_status === "past_due") {
    text = "Your trial has ended — upgrade to keep Reva replying to leads.";
  }
  if (m && m.over) text += " You've reached your monthly message limit (overage is being tracked).";
  else if (m && m.warn) text += " You're nearing your monthly message limit.";
  b.textContent = text.trim();
  b.classList.toggle("hidden", !text);
}

async function upgradeToGrowth() {
  try {
    const res = await authedFetch(`${API}/billing/subscribe`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ plan: "growth" }),
    });
    const d = await res.json().catch(() => ({}));
    if (res.ok && d.authorization_url) window.location.href = d.authorization_url;
    else alert(d.detail || "Could not start checkout. Please try again.");
  } catch (_e) { /* ignore */ }
}

// Called by auth.js after a successful login (covers the case where the user
// logged in after the initial boot resolved with no session).
window.RevaDashboard = {
  onAuthenticated() {
    if (!startBooted) bootDashboard();
    else {
      connectWebSocket();
      refreshAll();
      loadPlanUsage();
    }
  },
};

function syncFilterChips() {
  document.querySelectorAll(".filter-chip").forEach((c) => {
    c.classList.toggle("active", (c.dataset.stage || "all") === stageFilter);
  });
}

function wsUrl() {
  return WS_URL;
}

async function connectWebSocket() {
  if (ws && (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING)) return;

  clearTimeout(wsReconnectTimer);

  // Append the access token so the server can authenticate the socket
  // (app/routers/dashboard_ws.py rejects tokenless sockets with code 4401).
  const token = await authToken();
  const url = token ? `${wsUrl()}?token=${encodeURIComponent(token)}` : wsUrl();
  ws = new WebSocket(url);

  ws.addEventListener("open", () => {
    wsConnected = true;
    wsReconnectAttempt = 0;
    startPolling();
    setSyncState("realtime");
  });

  ws.addEventListener("message", (event) => {
    try {
      handleDashboardEvent(JSON.parse(event.data));
    } catch (e) {
      console.warn("Invalid WS message", e);
    }
  });

  ws.addEventListener("close", () => {
    wsConnected = false;
    startPolling();
    setSyncState("polling");
    scheduleWsReconnect();
  });

  ws.addEventListener("error", () => {
    wsConnected = false;
  });
}

function scheduleWsReconnect() {
  clearTimeout(wsReconnectTimer);
  const delay = Math.min(WS_RECONNECT_BASE_MS * Math.pow(2, wsReconnectAttempt), WS_RECONNECT_MAX_MS);
  wsReconnectAttempt += 1;
  wsReconnectTimer = setTimeout(() => {
    if (!document.hidden) connectWebSocket();
  }, delay);
}

function handleDashboardEvent(msg) {
  if (!msg || msg.type === "ping") return;

  if (msg.type === "voice_updated") {
    voiceFingerprint = "";
    statsFingerprint = "";
    queueRefresh(async () => {
      await Promise.all([loadVoiceLeads(), loadStats()]);
      if (msg.voice_lead_id && selectedVoiceId === msg.voice_lead_id) {
        await openVoiceDrawer(msg.voice_lead_id, true);
      }
    });
    return;
  }

  if (msg.type === "pipeline_updated") {
    leadsFingerprint = "";
    statsFingerprint = "";
    queueRefresh(async () => {
      await Promise.all([loadStats(), loadLeads()]);
      if (msg.phone_number && selectedPhone === msg.phone_number) {
        await openLeadDrawer(msg.phone_number, true);
      }
    });
    return;
  }

  leadsFingerprint = "";
  voiceFingerprint = "";
  statsFingerprint = "";
  queueRefresh(() => refreshAll());
}

function queueRefresh(fn) {
  clearTimeout(refreshDebounceTimer);
  refreshDebounceTimer = setTimeout(() => {
    fn().catch((e) => console.error("Refresh failed:", e));
  }, 80);
}

function startPolling() {
  clearInterval(pollTimer);
  const useFallback = !wsConnected;
  const ms = document.hidden
    ? POLL_HIDDEN_INTERVAL
    : useFallback
      ? POLL_INTERVAL_FALLBACK
      : POLL_INTERVAL_WS;
  pollTimer = setInterval(refreshAll, ms);
}

function onVisibilityChange() {
  if (!document.hidden) {
    if (!wsConnected) connectWebSocket();
    refreshAll();
  }
  startPolling();
}

async function refreshAll() {
  if (isRefreshing) return;
  isRefreshing = true;
  setSyncState("syncing");
  try {
    await Promise.all([loadStats(), loadLeads(), loadVoiceLeads()]);
    if (wsConnected) setSyncState("realtime");
    else setSyncState("polling");
  } catch {
    if (!wsConnected) setSyncState("error");
  } finally {
    isRefreshing = false;
  }
}

function setSyncState(state) {
  const status = el("syncStatus");
  const label = el("syncLabel");
  if (!status || !label) return;

  status.classList.remove("sync-live--on", "sync-live--sync");
  const now = new Date();
  const time = now.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });

  if (state === "syncing") {
    status.classList.add("sync-live--sync");
    label.textContent = "Updating…";
  } else if (state === "realtime") {
    status.classList.add("sync-live--on");
    label.textContent = `Live · ${time}`;
  } else if (state === "polling") {
    status.classList.add("sync-live--on");
    label.textContent = `Polling · ${time}`;
  } else {
    label.textContent = "Offline";
  }
}

async function loadStats() {
  try {
    const res = await authedFetch(`${API}/stats`, { cache: "no-store" });
    const d = await res.json();
    const fp = JSON.stringify(d);
    if (fp === statsFingerprint) return;
    statsFingerprint = fp;
    lastStats = d;

    el("kpiTotal").textContent = d.total;
    el("kpiToday").textContent = d.today;
    el("kpiHot").textContent = d.hot_leads ?? 0;
    el("kpiQualified").textContent = d.qualified;
    el("kpiBooked").textContent = d.booked;
    el("kpiConversion").textContent = d.conversion_rate + "%";
    if (d.total > 0) {
      el("kpiQualifiedPct").textContent = `${((d.qualified / d.total) * 100).toFixed(1)}% qualified`;
    } else {
      el("kpiQualifiedPct").textContent = "";
    }

    if (el("kpiVoice")) {
      el("kpiVoice").textContent = d.voice_leads ?? "—";
      const sub = [];
      if ((d.voice_leads_new ?? 0) > 0) sub.push(`${d.voice_leads_new} unread`);
      if ((d.voice_leads_today ?? 0) > 0) sub.push(`${d.voice_leads_today} today`);
      el("kpiVoiceNew").textContent = sub.join(" · ");
    }

    if (el("navAll")) el("navAll").textContent = d.total;
    if (el("navHot")) el("navHot").textContent = d.hot_leads ?? 0;
    if (el("navVoice")) el("navVoice").textContent = d.voice_leads ?? 0;

    renderFunnel(d);
    renderStageChart(d);
    renderSourceDonut(d.by_source || {});
    updatePageSubtitle();
  } catch (e) {
    console.error("Stats fetch failed:", e);
    throw e;
  }
}

async function loadLeads() {
  try {
    const res = await authedFetch(`${API}/leads`, { cache: "no-store" });
    const d = await res.json();
    const leads = d.leads || [];
    const fp = leads.map((l) =>
      `${l.phone_number}:${l.stage}:${l.seriousness_score}:${l.last_message_at || l.updated_at || l.created_at}:${l.message_count || 0}`
    ).join("|");
    const isFullRefresh = fp !== leadsFingerprint;
    leadsFingerprint = fp;
    allLeads = leads;

    if (isFullRefresh) renderLeadsTable();
    else highlightSelectedRow();
    renderActivity();
    updatePageSubtitle();
  } catch (e) {
    console.error("Leads fetch failed:", e);
    throw e;
  }
}

function updatePageSubtitle() {
  const sub = el("pageSubtitle");
  if (!sub) return;
  const tg = allLeads.filter((l) => isTelegram(l)).length;
  sub.textContent = `${allLeads.length} leads · ${tg} on Telegram · sorted by last activity`;
}

function getFilteredLeads() {
  let list = [...allLeads];

  if (navFilter === "hot" || stageFilter === "hot") {
    list = list.filter((l) => (l.seriousness_score ?? 0) >= 8);
  } else if (stageFilter === "takeover") {
    list = list.filter((l) => l.is_paused);
  } else if (stageFilter === "telegram") {
    list = list.filter(isTelegram);
  } else if (stageFilter !== "all") {
    list = list.filter((l) => (l.stage || "new") === stageFilter);
  }

  if (searchQuery) {
    list = list.filter((l) => {
      const hay = [
        l.name,
        l.phone_number,
        l.location,
        l.budget,
        l.property_type,
        formatSource(l.source),
      ].filter(Boolean).join(" ").toLowerCase();
      return hay.includes(searchQuery);
    });
  }

  return list;
}

function isTelegram(lead) {
  const src = (lead.source || "").toLowerCase();
  if (src === "telegram") return true;
  const phone = lead.phone_number || "";
  const digits = phone.replace(/\D/g, "");
  return digits.length > 0 && digits.length <= 12 && !phone.startsWith("+234");
}

function renderLeadsTable() {
  const tbody = el("leadsTableBody");
  if (!tbody) return;

  const leads = getFilteredLeads();
  if (el("navTakeover")) el("navTakeover").textContent = allLeads.filter((l) => l.is_paused).length;
  if (!leads.length) {
    tbody.innerHTML = emptyState("inbox", "No leads here yet", "Leads appear as soon as they message you.", 6);
    renderIcons();
    return;
  }

  tbody.innerHTML = leads.map((lead) => rowHtml(lead)).join("");

  tbody.querySelectorAll("tr[data-phone]").forEach((row) => {
    row.addEventListener("click", () => openLeadDrawer(row.dataset.phone));
    row.addEventListener("keydown", (e) => {
      if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        openLeadDrawer(row.dataset.phone);
      }
    });
  });

  highlightSelectedRow();
  renderIcons();
}

function rowHtml(lead) {
  const phone = lead.phone_number || "";
  const name = lead.name || displayId(phone);
  const initials = initialsFor(name, phone);
  const grad = avatarGradient(phone);
  const score = lead.seriousness_score ?? 0;
  const scorePct = Math.min(score * 10, 100);
  const scoreColor = score >= 8 ? "var(--ok)" : score >= 5 ? "var(--warn)" : "var(--ink-soft)";
  const stage = lead.stage || "new";
  const stageClass = stagePillClass(stage);
  const stageLabel = stageLabelText(stage);
  const src = lead.source || inferSourceFromPhone(phone);
  const srcPill = sourcePillHtml(src);
  const msgCount = lead.message_count ?? "—";
  const touched = lead.last_message_at || lead.updated_at || lead.created_at;
  const timeLabel = touched ? formatTimeAgo(touched) : "—";
  const meta = [lead.location, lead.budget].filter(Boolean).join(" · ");
  const selected = phone === selectedPhone ? " is-selected" : "";

  return `
    <tr data-phone="${escAttr(phone)}" tabindex="0" class="${selected.trim()}">
      <td>
        <div class="lead-cell">
          <div class="lead-avatar" style="background:${grad}">${esc(initials)}</div>
          <div>
            <div class="lead-name">${esc(name)}</div>
            <div class="lead-co">${esc(meta || displayId(phone))}</div>
          </div>
        </div>
      </td>
      <td>${srcPill}</td>
      <td>
        <div class="score-wrap">
          <div class="score-track"><div class="score-fill" style="width:${scorePct}%;background:${scoreColor}"></div></div>
          <span class="score-num">${score * 10}</span>
        </div>
      </td>
      <td><span class="stage-pill ${stageClass}">${esc(stageLabel)}</span></td>
      <td><span class="time-label">${esc(String(msgCount))}</span></td>
      <td><span class="time-label">${esc(timeLabel)}</span></td>
    </tr>
  `;
}

function highlightSelectedRow() {
  document.querySelectorAll("#leadsTableBody tr[data-phone]").forEach((row) => {
    row.classList.toggle("is-selected", row.dataset.phone === selectedPhone);
  });
}

function openDrawer() {
  el("drawerBackdrop")?.classList.add("open");
  el("leadDrawer")?.classList.add("open");
  el("leadDrawer")?.setAttribute("aria-hidden", "false");
}

function closeDrawer() {
  el("drawerBackdrop")?.classList.remove("open");
  el("leadDrawer")?.classList.remove("open");
  el("leadDrawer")?.setAttribute("aria-hidden", "true");
  selectedPhone = null;
  selectedVoiceId = null;
  highlightSelectedRow();
  document.querySelectorAll(".voice-card").forEach((c) => c.classList.remove("is-active"));
}

async function openLeadDrawer(phone, silent = false) {
  detailMode = "pipeline";
  selectedPhone = phone;
  selectedVoiceId = null;
  resetDetailPanelsForPipeline();
  highlightSelectedRow();
  openDrawer();

  const lead = allLeads.find((l) => l.phone_number === phone);
  el("drawerTitle").textContent = lead?.name || displayId(phone);
  el("drawerSub").textContent = formatSource(lead?.source) || "Pipeline lead";

  el("detailPlaceholder").classList.add("hidden");
  el("detailContent").classList.remove("hidden");

  try {
    const res = await authedFetch(`${API}/leads/${encodeURIComponent(phone)}`, { cache: "no-store" });
    const d = await res.json();
    if (d.error) return;

    const ld = d.lead;
    el("detName").textContent = ld.name || "—";
    el("detPhone").textContent = ld.phone_number || "—";
    el("detSource").textContent = formatSource(ld.source) || "—";
    el("detBudget").textContent = ld.budget || "—";
    el("detLocation").textContent = ld.location || "—";
    el("detType").textContent = ld.property_type || "—";
    el("detTimeline").textContent = ld.timeline || "—";
    el("detScore").textContent = (ld.seriousness_score ?? "—") + "/10";

    renderMatchedUnits(d.matched_units || []);

    const thread = el("conversationThread");
    const prevScroll = thread.scrollHeight - thread.scrollTop;
    const wasAtBottom = prevScroll < 48;

    thread.innerHTML = "";
    (d.conversation || []).forEach((msg) => {
      const bubble = document.createElement("div");
      bubble.className = `msg msg--${msg.role}`;
      bubble.textContent = msg.message;
      thread.appendChild(bubble);
    });

    if (wasAtBottom || silent) thread.scrollTop = thread.scrollHeight;

    // Inbox / takeover state for this lead
    renderTakeoverState(ld);
    renderTags(ld.tags || []);
    if (!silent) loadNotes(phone);
    renderIcons();
  } catch (e) {
    console.error("Lead detail fetch failed:", e);
  }
}

// ── Inbox / takeover ─────────────────────────────────────────────────────────
let currentTags = [];

function renderTakeoverState(ld) {
  const paused = !!ld.is_paused;
  const status = el("takeoverStatus");
  if (status) status.textContent = paused ? "🟠 You've taken over — AI is paused" : "🟢 AI is handling this lead";
  el("btnTakeover")?.classList.toggle("hidden", paused);
  el("btnHandback")?.classList.toggle("hidden", !paused);
}

async function inboxAction(action) {
  if (!selectedPhone) return;
  try {
    const res = await authedFetch(`${API}/inbox/${encodeURIComponent(selectedPhone)}/${action}`, { method: "POST" });
    if (!res.ok) { toast(`Couldn't ${action === "handback" ? "hand back" : "take over"} (${res.status})`, "error"); return; }
    const d = await res.json();
    renderTakeoverState(d.lead || {});
    renderIcons();
    toast(action === "takeover" ? "You've taken over — AI paused" : "Handed back to AI", "ok");
    await loadLeads();
  } catch (e) { toast("Network error", "error"); }
}

async function sendHumanReply() {
  if (!selectedPhone) return;
  const box = el("replyText");
  const text = (box.value || "").trim();
  if (!text) return;
  el("btnSendReply").disabled = true;
  try {
    const res = await authedFetch(`${API}/inbox/${encodeURIComponent(selectedPhone)}/reply`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text }),
    });
    if (res.ok) {
      box.value = "";
      await openLeadDrawer(selectedPhone, true);  // refresh thread
      toast("Reply sent", "ok");
    } else {
      toast(`Reply failed (${res.status})`, "error");
    }
  } catch (e) { toast("Network error", "error"); }
  finally { el("btnSendReply").disabled = false; }
}

function renderTags(tags) {
  currentTags = Array.isArray(tags) ? [...tags] : [];
  const box = el("tagChips");
  if (!box) return;
  box.innerHTML = currentTags.map((t, i) =>
    `<span class="tag-chip">${escapeHtml(t)}<button type="button" data-i="${i}" aria-label="remove">×</button></span>`
  ).join("");
  box.querySelectorAll("button[data-i]").forEach((b) =>
    b.addEventListener("click", () => { currentTags.splice(Number(b.dataset.i), 1); saveTags(); }));
}

function addTagFromInput() {
  const input = el("tagInput");
  const val = (input.value || "").trim();
  if (!val) return;
  if (!currentTags.includes(val)) currentTags.push(val);
  input.value = "";
  saveTags();
}

async function saveTags() {
  if (!selectedPhone) return;
  renderTags(currentTags);
  try {
    await authedFetch(`${API}/inbox/${encodeURIComponent(selectedPhone)}/tags`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ tags: currentTags }),
    });
  } catch (e) { console.error(e); }
}

async function loadNotes(phone) {
  const list = el("notesList");
  if (!list) return;
  try {
    const res = await authedFetch(`${API}/inbox/${encodeURIComponent(phone)}/notes`, { cache: "no-store" });
    if (!res.ok) return;
    const d = await res.json();
    const notes = d.notes || [];
    list.innerHTML = notes.length
      ? notes.map((n) => `<div class="note"><div class="note-meta">${escapeHtml(n.author_email || "Agent")} · ${formatTimeAgo(n.created_at)}</div>${escapeHtml(n.body)}</div>`).join("")
      : emptyState("sticky-note", "No notes yet", "Add a private note for your team.");
    renderIcons();
  } catch (e) { console.error(e); }
}

async function addLeadNote() {
  if (!selectedPhone) return;
  const box = el("noteText");
  const body = (box.value || "").trim();
  if (!body) return;
  el("btnAddNote").disabled = true;
  try {
    const res = await authedFetch(`${API}/inbox/${encodeURIComponent(selectedPhone)}/notes`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ body }),
    });
    if (res.ok) { box.value = ""; await loadNotes(selectedPhone); toast("Note added", "ok"); }
    else { toast(`Couldn't add note (${res.status})`, "error"); }
  } catch (e) { toast("Network error", "error"); }
  finally { el("btnAddNote").disabled = false; }
}

async function loadVoiceLeads() {
  try {
    const res = await authedFetch(`${API}/elevenlabs-leads`, { cache: "no-store" });
    const d = await res.json();
    const leads = d.leads || [];
    const fp = leads.map((l) => `${l.id}:${l.is_new}:${l.created_at}`).join("|");
    if (fp === voiceFingerprint) return;
    voiceFingerprint = fp;
    voiceLeads = leads;
    renderVoiceLeads();
    renderActivity();
    if (el("navVoice")) el("navVoice").textContent = leads.length;
  } catch (e) {
    console.error("Voice leads fetch failed:", e);
    const list = el("voiceLeadsList");
    if (list) list.innerHTML = '<p class="empty">Could not load voice leads</p>';
  }
}

function renderVoiceLeads() {
  const list = el("voiceLeadsList");
  const pill = el("voiceNewPill");
  if (!list) return;

  const unread = voiceLeads.filter((l) => l.is_new).length;
  if (pill) {
    if (unread > 0) {
      pill.textContent = `${unread} new`;
      pill.classList.remove("hidden");
    } else {
      pill.classList.add("hidden");
    }
  }

  if (!voiceLeads.length) {
    list.innerHTML = '<p class="empty">No voice calls yet</p>';
    return;
  }

  list.innerHTML = voiceLeads.map((lead) => {
    const active = lead.id === selectedVoiceId ? " is-active" : "";
    const badge = lead.is_new ? '<span class="badge badge--new">New</span>' : "";
    const contact = lead.contact_phone || lead.phone_number || lead.whatsapp_number || "No number";
    const meta = [lead.location, lead.budget, lead.property_type].filter(Boolean).join(" · ");
    const time = lead.created_at ? formatTimeAgo(lead.created_at) : "";
    const name = lead.name || "Unknown caller";

    return `
      <article class="voice-card${active}" data-voice-id="${lead.id}" role="button" tabindex="0">
        <div class="voice-card__top">
          <span class="voice-card__name">${esc(name)}</span>
          ${badge}
        </div>
        <div class="voice-card__meta">${esc(contact)}${meta ? "<br>" + esc(meta) : ""}</div>
        ${time ? `<div class="voice-card__time">${time}</div>` : ""}
      </article>
    `;
  }).join("");

  list.querySelectorAll(".voice-card").forEach((card) => {
    const id = Number(card.dataset.voiceId);
    card.addEventListener("click", () => openVoiceDrawer(id));
    card.addEventListener("keydown", (e) => {
      if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        openVoiceDrawer(id);
      }
    });
  });
}

async function openVoiceDrawer(leadId, silent = false) {
  detailMode = "voice";
  selectedVoiceId = leadId;
  selectedPhone = null;
  highlightSelectedRow();

  document.querySelectorAll(".voice-card").forEach((c) => c.classList.remove("is-active"));
  document.querySelector(`.voice-card[data-voice-id="${leadId}"]`)?.classList.add("is-active");

  openDrawer();
  el("detailPlaceholder").classList.add("hidden");
  el("detailContent").classList.remove("hidden");
  el("detailActions")?.classList.remove("hidden");
  el("detailSummary")?.classList.remove("hidden");
  togglePipelineDetailSections(false);

  try {
    const res = await authedFetch(`${API}/elevenlabs-leads/${leadId}`, { cache: "no-store" });
    const d = await res.json();
    if (!res.ok) return;

    const lead = d.lead;
    if (!lead) return;

    el("drawerTitle").textContent = lead.name || "Voice caller";
    el("drawerSub").textContent = "Voice receptionist";
    el("detName").textContent = lead.name || "—";
    el("detPhone").textContent = lead.contact_phone || lead.phone_number || lead.whatsapp_number || "—";
    el("detSource").textContent = "Voice receptionist";
    el("detBudget").textContent = lead.budget || "—";
    el("detLocation").textContent = lead.location || "—";
    el("detType").textContent = lead.property_type || "—";
    el("detTimeline").textContent = lead.timeline || "—";
    el("detScore").textContent = "—";
    el("detSummary").textContent = lead.ai_summary || "No summary captured for this call.";

    const wa = whatsappLink(lead.contact_phone || lead.whatsapp_number || lead.phone_number);
    const btnWa = el("btnWhatsApp");
    if (wa) {
      btnWa.href = wa;
      btnWa.classList.remove("hidden");
    } else {
      btnWa.href = "#";
      btnWa.classList.add("hidden");
    }

    const btnImport = el("btnImportPipeline");
    if (lead.contact_phone) {
      btnImport.disabled = false;
      btnImport.textContent = "Add to pipeline";
    } else {
      btnImport.disabled = true;
      btnImport.textContent = "No phone to import";
    }

    if (lead.is_new && !silent) {
      const viewedRes = await authedFetch(`${API}/elevenlabs-leads/${leadId}/viewed`, { method: "PATCH" });
      if (viewedRes.ok) {
        const local = voiceLeads.find((l) => l.id === leadId);
        if (local) local.is_new = false;
        voiceFingerprint = "";
        renderVoiceLeads();
        statsFingerprint = "";
        loadStats();
      }
    }
  } catch (e) {
    console.error("Voice lead detail failed:", e);
  }
}

async function importSelectedVoiceLead() {
  if (!selectedVoiceId) return;
  const btn = el("btnImportPipeline");
  btn.disabled = true;
  btn.textContent = "Importing…";
  try {
    const res = await authedFetch(`${API}/elevenlabs-leads/${selectedVoiceId}/import`, { method: "POST" });
    const d = await res.json();
    if (!res.ok) {
      const detail = d.detail;
      const msg = Array.isArray(detail)
        ? detail.map((e) => e.msg || String(e)).join("; ")
        : detail || "Import failed";
      throw new Error(msg);
    }
    btn.textContent = "Added";
    leadsFingerprint = "";
    voiceFingerprint = "";
    statsFingerprint = "";
    await refreshAll();
    if (d.lead?.phone_number) openLeadDrawer(d.lead.phone_number);
  } catch (e) {
    console.error(e);
    btn.textContent = "Import failed";
    btn.disabled = false;
  }
}

function whatsappLink(phone) {
  if (!phone) return null;
  const digits = String(phone).replace(/\D/g, "");
  if (digits.length < 10) return null;
  let national = digits;
  if (national.startsWith("234")) national = national.slice(3);
  else if (national.startsWith("0")) national = national.slice(1);
  if (national.length > 10) national = national.slice(-10);
  if (national.length !== 10 || !"789".includes(national[0])) return null;
  return `https://wa.me/234${national}`;
}

function togglePipelineDetailSections(show) {
  ["matchedUnits", "conversationThread"].forEach((id) => {
    const node = el(id);
    if (!node) return;
    const title = node.previousElementSibling;
    if (title?.classList.contains("section-label")) title.classList.toggle("hidden", !show);
    node.classList.toggle("hidden", !show);
  });
}

function resetDetailPanelsForPipeline() {
  el("detailActions")?.classList.add("hidden");
  el("detailSummary")?.classList.add("hidden");
  togglePipelineDetailSections(true);
}

function renderFunnel(stats) {
  const container = el("funnelBars");
  if (!container) return;
  const total = stats.total || 1;
  const stages = [
    { label: "New", val: stats.new, color: "var(--sky)" },
    { label: "Qualifying", val: stats.qualifying, color: "var(--ink-muted)" },
    { label: "Qualified", val: stats.qualified, color: "var(--ok)" },
    { label: "Booked", val: stats.booked, color: "var(--warn)" },
  ];

  container.innerHTML = stages.map((s) => {
    const pct = Math.max((s.val / total) * 100, 2);
    return `
      <div class="funnel-row">
        <span class="funnel-label" style="color:${s.color}">${s.label}</span>
        <div class="funnel-track"><div class="funnel-fill" style="width:${pct}%;background:${s.color};opacity:.75"></div></div>
        <span class="funnel-n">${s.val}</span>
      </div>
    `;
  }).join("");
}

function renderStageChart(stats) {
  const container = el("stageChart");
  if (!container) return;
  const stages = [
    { key: "new", label: "New" },
    { key: "qualifying", label: "Qual" },
    { key: "qualified", label: "Qual'd" },
    { key: "booked", label: "Booked" },
  ];
  const max = Math.max(stats.new, stats.qualifying, stats.qualified, stats.booked, 1);

  container.innerHTML = `
    <div class="mini-chart">
      ${stages.map((s, i) => {
        const val = stats[s.key] ?? 0;
        const h = Math.max((val / max) * 100, 8);
        const cls = i === stages.length - 1 ? "mc-bar current" : "mc-bar";
        return `<div class="${cls}" style="height:${h}%" title="${s.label}: ${val}"></div>`;
      }).join("")}
    </div>
    <div style="display:flex;justify-content:space-between;font-size:10px;color:var(--ink-soft);font-family:var(--font-mono)">
      ${stages.map((s) => `<span>${s.label}</span>`).join("")}
    </div>
  `;
}

function renderSourceDonut(bySource) {
  const wrap = el("sourceDonut");
  if (!wrap) return;

  const entries = Object.entries(bySource).sort((a, b) => b[1] - a[1]);
  const total = entries.reduce((s, [, n]) => s + n, 0) || 1;

  if (!entries.length) {
    wrap.innerHTML = '<p class="empty" style="padding:16px 20px">No source data yet</p>';
    return;
  }

  let angle = 0;
  const segments = entries.map(([src, count]) => {
    const pct = (count / total) * 360;
    const color = SOURCE_COLORS[src] || SOURCE_COLORS.unknown;
    const start = angle;
    angle += pct;
    return `${color} ${start}deg ${angle}deg`;
  });

  wrap.innerHTML = `
    <div class="donut" style="background:conic-gradient(${segments.join(", ")})"></div>
    <div class="donut-sources">
      ${entries.map(([src, count]) => {
        const pct = Math.round((count / total) * 100);
        const color = SOURCE_COLORS[src] || SOURCE_COLORS.unknown;
        return `
          <div class="ds-row">
            <div class="ds-swatch" style="background:${color}"></div>
            <span class="ds-label">${esc(formatSource(src))}</span>
            <span class="ds-pct">${pct}%</span>
          </div>
        `;
      }).join("")}
    </div>
  `;
}

function renderActivity() {
  const feed = el("activityFeed");
  if (!feed) return;

  const items = [];

  voiceLeads.slice(0, 3).forEach((lead) => {
    const name = lead.name || "Caller";
    const time = lead.created_at ? formatTimeAgo(lead.created_at) : "";
    const grad = avatarGradient(String(lead.id));
    const ini = initialsFor(name);
    items.push(`
      <div class="feed-item" data-voice-id="${lead.id}">
        <div class="feed-avatar" style="background:${grad}">${esc(ini)}</div>
        <div>
          <div class="feed-text"><strong>${esc(name)}</strong> called receptionist${lead.is_new ? " · <strong>New</strong>" : ""}</div>
          ${time ? `<div class="feed-time">${time}</div>` : ""}
        </div>
      </div>
      <div class="feed-divider"></div>
    `);
  });

  allLeads.slice(0, 6).forEach((lead, i) => {
    const phone = lead.phone_number || "";
    const name = lead.name || displayId(phone);
    const action = lead.stage === "done" ? "booked a meeting"
      : lead.stage === "qualified" ? "fully qualified"
      : lead.stage === "qualifying" ? "in conversation"
      : isTelegram(lead) ? "messaged on Telegram"
      : "just messaged";
    const time = (lead.last_message_at || lead.created_at) ? formatTimeAgo(lead.last_message_at || lead.created_at) : "";
    const grad = avatarGradient(phone);
    const ini = initialsFor(name, phone);

    items.push(`
      <div class="feed-item" data-phone="${escAttr(phone)}">
        <div class="feed-avatar" style="background:${grad}">${esc(ini)}</div>
        <div>
          <div class="feed-text"><strong>${esc(name)}</strong> ${action}</div>
          ${time ? `<div class="feed-time">${time}</div>` : ""}
        </div>
      </div>
      ${i < 5 ? '<div class="feed-divider"></div>' : ""}
    `);
  });

  if (!items.length) {
    feed.innerHTML = '<p class="empty">No activity yet</p>';
    return;
  }

  feed.innerHTML = items.join("");

  feed.querySelectorAll("[data-voice-id]").forEach((item) => {
    item.addEventListener("click", () => openVoiceDrawer(Number(item.dataset.voiceId)));
  });
  feed.querySelectorAll("[data-phone]").forEach((item) => {
    item.addEventListener("click", () => openLeadDrawer(item.dataset.phone));
  });
}

function renderMatchedUnits(matches) {
  const container = el("matchedUnits");
  if (!matches.length) {
    container.innerHTML = '<p class="empty">No units offered yet</p>';
    return;
  }

  container.innerHTML = matches.map((m) => {
    const u = m.units || m;
    const dev = u.developments || {
      name: u.development_name || m.development_name || "",
      location: u.location || m.location || "",
    };
    const price = formatPriceNaira(u.price_naira ?? m.price_naira);
    const rank = m.rank ? `#${m.rank} ` : "";
    return `
      <article class="unit-card">
        <div class="unit-card__title">${rank}${esc(u.title || u.unit_code || "Unit")}</div>
        <div class="unit-card__meta">${esc(dev.name || "")} · ${esc(dev.location || "")}</div>
        <div class="unit-card__price">${esc(price)}${m.match_score ? ` · match ${Math.round(m.match_score)}%` : ""}</div>
      </article>
    `;
  }).join("");
}

function sourcePillHtml(source) {
  const label = formatSource(source);
  let cls = "source-pill";
  if (source === "telegram") cls += " source-pill--telegram";
  else if (source?.includes("whatsapp")) cls += " source-pill--whatsapp";
  else if (source === "elevenlabs_receptionist") cls += " source-pill--voice";
  return `<span class="${cls}">${esc(label)}</span>`;
}

function stagePillClass(stage) {
  const map = { new: "new", qualifying: "engaged", qualified: "qual", booking: "negotiat", done: "closed" };
  return map[stage] || "new";
}

function stageLabelText(stage) {
  const map = { new: "New", qualifying: "Qualifying", qualified: "Qualified", booking: "Booking", done: "Booked" };
  return map[stage] || capitalize(stage);
}

function inferSourceFromPhone(phone) {
  const digits = (phone || "").replace(/\D/g, "");
  if (digits.length > 0 && digits.length <= 12 && !String(phone).startsWith("+234")) return "telegram";
  return "whatsapp_organic";
}

function avatarGradient(seed) {
  let h = 0;
  for (let i = 0; i < (seed || "").length; i++) h = (h + seed.charCodeAt(i)) % AVATAR_GRADIENTS.length;
  return AVATAR_GRADIENTS[h];
}

function initialsFor(name, fallback) {
  const parts = (name || "").trim().split(/\s+/).filter(Boolean);
  if (parts.length >= 2) return (parts[0][0] + parts[1][0]).toUpperCase();
  if (parts.length === 1 && parts[0].length >= 2) return parts[0].slice(0, 2).toUpperCase();
  const id = fallback || name || "?";
  return id.slice(0, 2).toUpperCase();
}

function displayId(phone) {
  if (!phone) return "Unknown";
  if (isTelegram({ phone_number: phone, source: "telegram" })) return `Telegram ${phone}`;
  return phone.length > 12 ? phone.slice(0, 6) + "…" + phone.slice(-4) : phone;
}

function el(id) { return document.getElementById(id); }

function esc(str) {
  const d = document.createElement("div");
  d.textContent = str ?? "";
  return d.innerHTML;
}

function escAttr(str) {
  return esc(str).replace(/"/g, "&quot;");
}

function capitalize(s) { return s.charAt(0).toUpperCase() + s.slice(1); }

function formatSource(source) {
  if (!source) return "Unknown";
  if (source === "elevenlabs_receptionist") return "Voice";
  if (source === "telegram") return "Telegram";
  if (source.includes("whatsapp")) return "WhatsApp";
  return source.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

function formatPriceNaira(n) {
  if (!n) return "—";
  if (n >= 1_000_000) {
    const m = n / 1_000_000;
    return m % 1 === 0 ? `₦${m}M` : `₦${m.toFixed(1)}M`;
  }
  return `₦${n.toLocaleString()}`;
}

function formatTimeAgo(isoStr) {
  try {
    const d = new Date(isoStr);
    const now = new Date();
    const diff = Math.floor((now - d) / 1000);
    if (diff < 60) return "just now";
    if (diff < 3600) return Math.floor(diff / 60) + "m ago";
    if (diff < 86400) return Math.floor(diff / 3600) + "h ago";
    if (diff < 604800) return Math.floor(diff / 86400) + "d ago";
    return d.toLocaleDateString();
  } catch {
    return "";
  }
}

function escapeHtml(str) {
  return String(str ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
