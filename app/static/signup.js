// ─── Reva — Signup (Supabase signUp → provision workspace) ──────────────────
const ORIGIN = window.location.origin;
let supa = null;

function el(id) { return document.getElementById(id); }
function msg(text, isError) {
  const e = el("signupMsg");
  e.textContent = text;
  e.classList.toggle("hidden", !text);
  e.style.color = isError ? "var(--hot,#e1495f)" : "var(--ink-soft,#889)";
}

async function init() {
  const cfg = await (await fetch(`${ORIGIN}/api/config`, { cache: "no-store" })).json();
  if (cfg.auth_disabled) { window.location.href = "/onboarding"; return; }  // dev bypass
  if (!cfg.supabase_url || !cfg.supabase_anon_key) {
    return msg("Signups are not configured on the server yet.", true);
  }
  supa = window.supabase.createClient(cfg.supabase_url, cfg.supabase_anon_key, {
    auth: { persistSession: true, autoRefreshToken: true },
  });
  el("signupForm").addEventListener("submit", onSubmit);
}

async function onSubmit(ev) {
  ev.preventDefault();
  const company = el("company").value.trim();
  const email = el("email").value.trim();
  const password = el("password").value;
  if (!company || !email || !password) return msg("Please fill in all fields.", true);

  msg("Creating your account…");
  const { data, error } = await supa.auth.signUp({
    email,
    password,
    options: { emailRedirectTo: `${ORIGIN}/onboarding` },
  });
  if (error) return msg(error.message, true);

  // Stash company name so onboarding can provision after email confirmation.
  sessionStorage.setItem("reva_company", company);

  if (!data.session) {
    return msg("Almost there — check your email to confirm, then you'll land in onboarding.", false);
  }
  await provision(data.session.access_token, company);
}

async function provision(token, company) {
  msg("Setting up your workspace…");
  const res = await fetch(`${ORIGIN}/api/signup`, {
    method: "POST",
    headers: { "Authorization": `Bearer ${token}`, "Content-Type": "application/json" },
    body: JSON.stringify({ company_name: company }),
  });
  if (!res.ok) return msg(`Could not create workspace (${res.status}). Please try again.`, true);
  window.location.href = "/onboarding";
}

document.addEventListener("DOMContentLoaded", () => init().catch((e) => msg(String(e.message || e), true)));
