// ─── Reva — Onboarding wizard ───────────────────────────────────────────────
// Drives the post-signup setup. Reuses Supabase session for the bearer token;
// provisions the workspace on first load if the user has no org yet (covers the
// email-confirmation flow), then walks steps persisted via PATCH /api/onboarding.
const ORIGIN = window.location.origin;
const STEPS = ["company", "channel", "inventory", "plan", "live"];
const STEP_LABELS = { company: "Company", channel: "Channel", inventory: "Inventory", plan: "Plan", live: "Go live" };

let supa = null, token = null, me = null, plans = [];

function el(id) { return document.getElementById(id); }
function msg(t, err) { const e = el("wizMsg"); e.textContent = t || ""; e.classList.toggle("hidden", !t); e.style.color = err ? "var(--hot,#e1495f)" : "var(--ink-soft,#889)"; }

async function authFetch(path, opts = {}) {
  const headers = Object.assign({ "Content-Type": "application/json" }, opts.headers || {});
  if (token) headers["Authorization"] = `Bearer ${token}`;
  return fetch(ORIGIN + path, Object.assign({}, opts, { headers }));
}

async function init() {
  const cfg = await (await fetch(`${ORIGIN}/api/config`, { cache: "no-store" })).json();
  if (!cfg.auth_disabled) {
    if (!cfg.supabase_url) { return msg("Auth not configured on the server.", true); }
    supa = window.supabase.createClient(cfg.supabase_url, cfg.supabase_anon_key, {
      auth: { persistSession: true, autoRefreshToken: true, detectSessionInUrl: true },
    });
    const { data } = await supa.auth.getSession();
    if (!data.session) { window.location.href = "/signup"; return; }
    token = data.session.access_token;
  }
  await loadMe();
}

async function loadMe() {
  let res = await authFetch("/api/me");
  if (res.status === 403) {
    // No org yet (e.g. just confirmed email) — provision now.
    const company = sessionStorage.getItem("reva_company") || "My Workspace";
    await authFetch("/api/signup", { method: "POST", body: JSON.stringify({ company_name: company }) });
    res = await authFetch("/api/me");
  }
  if (!res.ok) return msg(`Could not load workspace (${res.status}).`, true);
  me = await res.json();
  el("wizLoading")?.remove();
  render();
}

function currentStep() {
  const s = (me && me.onboarding_step) || "company";
  return STEPS.includes(s) ? s : "company";
}

function renderSteps() {
  const cur = STEPS.indexOf(currentStep());
  el("wizSteps").innerHTML = STEPS.map((s, i) =>
    `<span class="wiz-dot ${i <= cur ? "done" : ""}">${STEP_LABELS[s]}</span>`
  ).join('<span class="wiz-sep">›</span>');
}

function render() {
  renderSteps();
  const step = currentStep();
  const body = el("wizBody");
  const next = el("wizNext"), skip = el("wizSkip");
  next.classList.add("hidden"); skip.classList.add("hidden");
  msg("");

  if (step === "company") {
    body.innerHTML = `<h2 class="wiz-h">Welcome${me.tenant?.company_name ? ", " + me.tenant.company_name : ""} 👋</h2>
      <p class="auth-sub">Your 14-day trial is live. Let's connect a channel and your listings.</p>`;
    setNext("Get started", () => advance("channel"));
  } else if (step === "channel") {
    body.innerHTML = `<h2 class="wiz-h">Connect a channel</h2>
      <p class="auth-sub">Reva talks to leads on WhatsApp. Add credentials in your environment
      (WhatsApp Evolution), then continue. You can do this later too.</p>`;
    setNext("I've connected a channel", () => advance("inventory"));
    setSkip(() => advance("inventory"));
  } else if (step === "inventory") {
    body.innerHTML = `<h2 class="wiz-h">Add your listings</h2>
      <p class="auth-sub">Seed inventory so Reva can match leads to real units. Use the inventory tools
      or import a sheet. Skip to explore with the demo catalog.</p>`;
    setNext("Inventory is ready", () => advance("plan"));
    setSkip(() => advance("plan"));
  } else if (step === "plan") {
    renderPlans();
  } else if (step === "live") {
    body.innerHTML = `<h2 class="wiz-h">You're all set 🎉</h2><p class="auth-sub">Heading to your dashboard…</p>`;
    setTimeout(() => (window.location.href = "/dashboard"), 800);
  }
}

async function renderPlans() {
  const body = el("wizBody");
  if (!plans.length) {
    try { plans = (await (await authFetch("/api/plans")).json()).plans || []; } catch (_e) {}
  }
  const sellable = plans.filter((p) => p.selectable);
  body.innerHTML = `<h2 class="wiz-h">Choose a plan</h2>
    <p class="auth-sub">You're on the free trial. Upgrade now or keep exploring — no card needed during the trial.</p>
    <div class="wiz-plans">${sellable.map((p) => `
      <div class="wiz-plan">
        <div class="wiz-plan-name">${p.label}</div>
        <div class="wiz-plan-price">₦${(p.price_naira || 0).toLocaleString()}<span>/mo</span></div>
        <div class="wiz-plan-meta">${p.limits.messages ? p.limits.messages.toLocaleString() + " msgs" : "Unlimited"} · ${p.limits.seats} seat${p.limits.seats === 1 ? "" : "s"}</div>
        <button type="button" class="btn-pill ghost" data-plan="${p.id}">Subscribe</button>
      </div>`).join("")}</div>`;
  body.querySelectorAll("button[data-plan]").forEach((b) =>
    b.addEventListener("click", () => subscribe(b.dataset.plan)));
  setNext("Continue on trial", () => advance("live"));
}

async function subscribe(plan) {
  msg("Starting secure checkout…");
  const res = await authFetch("/api/billing/subscribe", { method: "POST", body: JSON.stringify({ plan }) });
  const d = await res.json().catch(() => ({}));
  if (res.ok && d.authorization_url) { window.location.href = d.authorization_url; }
  else msg(d.detail || `Could not start checkout (${res.status}).`, true);
}

function setNext(label, fn) { const n = el("wizNext"); n.textContent = label; n.classList.remove("hidden"); n.onclick = fn; }
function setSkip(fn) { const s = el("wizSkip"); s.classList.remove("hidden"); s.onclick = fn; }

async function advance(step) {
  if (step === "live") { await patchStep("live"); window.location.href = "/dashboard"; return; }
  await patchStep(step);
}

async function patchStep(step) {
  const res = await authFetch("/api/onboarding", { method: "PATCH", body: JSON.stringify({ step }) });
  if (!res.ok) return msg(`Could not save progress (${res.status}).`, true);
  me.onboarding_step = step;
  render();
}

document.addEventListener("DOMContentLoaded", () => init().catch((e) => msg(String(e.message || e), true)));
