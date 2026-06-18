// ─── Reva Dashboard — Auth gate (Supabase) ──────────────────────────────────
// Loads /api/config, initializes Supabase Auth, and blocks the dashboard behind
// a login overlay until a session exists. Exposes a tiny global `RevaAuth` API
// that dashboard.js uses to attach the access token to fetch + WebSocket calls.
//
// Requires @supabase/supabase-js to be loaded first (see dashboard.html).

const RevaAuth = (() => {
  let client = null;
  let config = null;
  let session = null;
  let readyResolve;
  const ready = new Promise((r) => (readyResolve = r));

  async function loadConfig() {
    const res = await fetch(`${window.location.origin}/api/config`, { cache: "no-store" });
    if (!res.ok) throw new Error(`/api/config failed: ${res.status}`);
    return res.json();
  }

  async function init() {
    config = await loadConfig();

    // Dev bypass: server says auth is off → skip the whole gate.
    if (config.auth_disabled) {
      session = null;
      hideOverlay();
      readyResolve();
      return;
    }

    if (!config.supabase_url || !config.supabase_anon_key) {
      showOverlayError(
        "Auth is enabled but SUPABASE_URL / SUPABASE_ANON_KEY are not configured on the server."
      );
      return;
    }
    if (!window.supabase || !window.supabase.createClient) {
      showOverlayError("Supabase client library failed to load.");
      return;
    }

    client = window.supabase.createClient(config.supabase_url, config.supabase_anon_key, {
      auth: { persistSession: true, autoRefreshToken: true, detectSessionInUrl: true },
    });

    const { data } = await client.auth.getSession();
    session = data.session;

    client.auth.onAuthStateChange((_event, newSession) => {
      session = newSession;
      if (session) {
        hideOverlay();
      } else {
        showOverlay();
      }
    });

    if (session) hideOverlay();
    else showOverlay();

    readyResolve();
  }

  // ── Token access ──────────────────────────────────────────────────────────
  async function getAccessToken() {
    if (config && config.auth_disabled) return null;
    if (!client) return null;
    // getSession refreshes if near expiry.
    const { data } = await client.auth.getSession();
    session = data.session;
    return session ? session.access_token : null;
  }

  function isAuthDisabled() {
    return !!(config && config.auth_disabled);
  }

  async function signOut() {
    if (client) await client.auth.signOut();
    session = null;
    showOverlay();
  }

  // ── Login overlay UI ────────────────────────────────────────────────────────
  function el(id) {
    return document.getElementById(id);
  }

  function showOverlay() {
    const o = el("authOverlay");
    if (o) o.classList.remove("hidden");
  }
  function hideOverlay() {
    const o = el("authOverlay");
    if (o) o.classList.add("hidden");
  }
  function showOverlayError(msg) {
    showOverlay();
    const e = el("authError");
    if (e) {
      e.textContent = msg;
      e.classList.remove("hidden");
    }
  }
  function setAuthMsg(msg, isError) {
    const e = el("authError");
    if (!e) return;
    e.textContent = msg;
    e.classList.toggle("hidden", !msg);
    e.style.color = isError ? "var(--hot, #e1495f)" : "var(--ink-soft, #889)";
  }

  async function handlePasswordLogin(evt) {
    evt.preventDefault();
    if (!client) return;
    const email = el("authEmail").value.trim();
    const password = el("authPassword").value;
    if (!email || !password) return setAuthMsg("Enter email and password.", true);
    setAuthMsg("Signing in…", false);
    const { error } = await client.auth.signInWithPassword({ email, password });
    if (error) return setAuthMsg(error.message, true);
    setAuthMsg("", false);
    // onAuthStateChange hides the overlay and dashboard.js refreshes.
    if (window.RevaDashboard && window.RevaDashboard.onAuthenticated) {
      window.RevaDashboard.onAuthenticated();
    }
  }

  async function handleMagicLink() {
    if (!client) return;
    const email = el("authEmail").value.trim();
    if (!email) return setAuthMsg("Enter your email first.", true);
    setAuthMsg("Sending magic link…", false);
    const { error } = await client.auth.signInWithOtp({
      email,
      options: { emailRedirectTo: window.location.href },
    });
    if (error) return setAuthMsg(error.message, true);
    setAuthMsg("Check your email for the sign-in link.", false);
  }

  function wireOverlay() {
    el("authForm")?.addEventListener("submit", handlePasswordLogin);
    el("authMagicLink")?.addEventListener("click", handleMagicLink);
  }

  document.addEventListener("DOMContentLoaded", () => {
    wireOverlay();
    init().catch((e) => showOverlayError(String(e.message || e)));
  });

  return { ready, getAccessToken, signOut, isAuthDisabled };
})();

window.RevaAuth = RevaAuth;
