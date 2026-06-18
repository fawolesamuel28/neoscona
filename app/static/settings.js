// ─── Reva — AI Agent Settings (in-shell) ────────────────────────────────────
// Supabase session → bearer token → authFetch. Loads the tenant's merged agent
// config (GET /api/agent-config), tracks dirty state, saves via PUT, and shows a
// toast. Same API contract as before; this file only upgrades the rendering.
const ORIGIN = window.location.origin;
const LANG_CHOICES = ["english", "nigerian english", "pidgin", "yoruba", "igbo", "hausa", "french"];

let supa = null, token = null, choices = [], loaded = null;

function el(id) { return document.getElementById(id); }
function icons() { try { if (window.lucide) lucide.createIcons(); } catch (_e) {} }

// ── Toast ───────────────────────────────────────────────────────────────────
function toast(message, kind) {
  const stack = el("toastStack");
  if (!stack) return;
  const t = document.createElement("div");
  t.className = `toast ${kind || ""}`;
  const icon = kind === "error" ? "alert-circle" : "check-circle";
  t.innerHTML = `<i data-lucide="${icon}"></i><span></span>`;
  t.querySelector("span").textContent = message;
  stack.appendChild(t);
  icons();
  requestAnimationFrame(() => t.classList.add("in"));
  setTimeout(() => {
    t.classList.remove("in");
    setTimeout(() => t.remove(), 300);
  }, 2600);
}

async function authFetch(path, opts = {}) {
  const headers = Object.assign({ "Content-Type": "application/json" }, opts.headers || {});
  if (token) headers["Authorization"] = `Bearer ${token}`;
  return fetch(ORIGIN + path, Object.assign({}, opts, { headers }));
}

async function init() {
  icons();
  const cfg = await (await fetch(`${ORIGIN}/api/config`, { cache: "no-store" })).json();
  if (!cfg.auth_disabled) {
    if (!cfg.supabase_url) { return fail("Auth is not configured on the server."); }
    supa = window.supabase.createClient(cfg.supabase_url, cfg.supabase_anon_key, {
      auth: { persistSession: true, autoRefreshToken: true, detectSessionInUrl: true },
    });
    const { data } = await supa.auth.getSession();
    if (!data.session) { window.location.href = "/signup"; return; }
    token = data.session.access_token;
  }
  await load();
}

function fail(msg) {
  el("cfgLoading").textContent = msg;
  toast(msg, "error");
}

// ── Chip toggles ──────────────────────────────────────────────────────────────
function chipGroup(containerId, options, selected) {
  const box = el(containerId);
  box.innerHTML = options.map((o) => {
    const on = selected.includes(o);
    return `<label class="chip-toggle ${on ? "on" : ""}">
      <input type="checkbox" value="${o}" ${on ? "checked" : ""}/>${o}</label>`;
  }).join("");
  box.querySelectorAll("input").forEach((input) =>
    input.addEventListener("change", () => {
      input.closest(".chip-toggle").classList.toggle("on", input.checked);
      markDirty();
    }));
}

function checkedValues(containerId) {
  return Array.from(el(containerId).querySelectorAll("input:checked")).map((i) => i.value);
}

// ── Load / render ─────────────────────────────────────────────────────────────
async function load() {
  const res = await authFetch("/api/agent-config");
  if (res.status === 403) { window.location.href = "/onboarding"; return; }
  if (!res.ok) return fail(`Could not load settings (${res.status}).`);
  const data = await res.json();
  const c = data.config || {};
  choices = data.qualifying_choices || ["budget", "location", "property_type", "timeline"];

  el("agent_name").value = c.agent_name || "";
  el("company_name").value = c.company_name || "";
  el("tone").value = c.tone || "";
  el("guardrails").value = c.guardrails || "";
  el("custom_instructions").value = c.custom_instructions || "";
  chipGroup("languages", LANG_CHOICES, (c.languages || []).map((x) => String(x).toLowerCase()));
  chipGroup("qualifying_fields", choices, c.qualifying_fields || []);
  if (c.company_name) el("wsName").textContent = c.company_name;

  loaded = snapshot();
  el("cfgLoading")?.remove();
  el("cfgForm").classList.remove("hidden");

  ["agent_name", "company_name", "tone", "guardrails", "custom_instructions"].forEach((id) =>
    el(id).addEventListener("input", markDirty));

  // staggered reveal
  requestAnimationFrame(() => document.querySelectorAll(".reveal").forEach((r) => r.classList.add("in")));
  updatePreview();
  setClean();
  icons();
}

function snapshot() {
  return JSON.stringify({
    agent_name: el("agent_name").value.trim(),
    company_name: el("company_name").value.trim(),
    tone: el("tone").value.trim(),
    languages: checkedValues("languages"),
    qualifying_fields: checkedValues("qualifying_fields"),
    guardrails: el("guardrails").value.trim(),
    custom_instructions: el("custom_instructions").value.trim(),
  });
}

function markDirty() {
  updatePreview();
  const dirty = snapshot() !== loaded;
  el("saveStatus").textContent = dirty ? "Unsaved changes" : "All changes saved";
  el("cfgSave").disabled = !dirty;
  el("cfgDiscard").disabled = !dirty;
}

function setClean() {
  loaded = snapshot();
  el("saveStatus").textContent = "All changes saved";
  el("cfgSave").disabled = true;
  el("cfgDiscard").disabled = true;
}

function updatePreview() {
  const name = (el("agent_name").value.trim() || "Amara").split(/\s+/)[0];
  const company = el("company_name").value.trim() || "Atlantic Horizons";
  el("previewBubble").textContent =
    `Hi! I'm ${name} with ${company}. I'd love to help — what's your budget range?`;
}

async function save(ev) {
  ev.preventDefault();
  el("cfgSave").disabled = true;
  el("saveStatus").textContent = "Saving…";
  const body = JSON.parse(snapshot());
  try {
    const res = await authFetch("/api/agent-config", { method: "PUT", body: JSON.stringify(body) });
    if (res.status === 403) { toast("Only admins can change agent settings.", "error"); return markDirty(); }
    if (!res.ok) {
      const d = await res.json().catch(() => ({}));
      toast(d.detail ? JSON.stringify(d.detail) : `Save failed (${res.status}).`, "error");
      return markDirty();
    }
    setClean();
    toast("Settings saved", "ok");
  } catch (e) {
    toast(String(e.message || e), "error");
    markDirty();
  }
}

function discard() {
  load();  // re-fetch resets every field to the server state
}

async function signOut() {
  try { if (supa) await supa.auth.signOut(); } catch (_e) {}
  window.location.href = "/dashboard";
}

document.addEventListener("DOMContentLoaded", () => {
  el("cfgForm").addEventListener("submit", save);
  el("cfgDiscard").addEventListener("click", discard);
  el("btnSignOut").addEventListener("click", signOut);
  init().catch((e) => fail(String(e.message || e)));
});
