/* ════════════════════════════════════════════════════════════════════════
   Neoscona Voice — shared client plumbing.

   Self-contained auth (unlike the Reva pages, which lean on a cookie set
   elsewhere): bridge the Supabase session into the parent-domain SSO cookie,
   attach a bearer token to API calls, and surface 401 (sign-in) / 402 (no voice
   plan) cleanly. Also small DOM/format helpers used by all three Voice pages.

   Boot config is injected by each template as window.__VOICE_BOOT__ =
   { supabaseUrl, supabaseAnonKey }. Load @supabase/supabase-js BEFORE this file.
   ════════════════════════════════════════════════════════════════════════ */
(function () {
  "use strict";

  var BOOT = window.__VOICE_BOOT__ || {};
  var API = (window.location.origin + "/api").replace(/\/$/, "");
  var isLocal = ["127.0.0.1", "localhost", "::1"].indexOf(window.location.hostname) !== -1;

  var _token = null;
  var _client = null;

  function makeClient() {
    if (_client) return _client;
    if (!window.supabase || !BOOT.supabaseUrl || !BOOT.supabaseAnonKey) return null;
    _client = window.supabase.createClient(BOOT.supabaseUrl, BOOT.supabaseAnonKey);
    return _client;
  }

  // Keep the parent-domain SSO cookie in sync so server-rendered pages + API agree.
  function bridge(session) {
    return fetch("/auth/session", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "same-origin",
      body: JSON.stringify({
        access_token: session.access_token,
        refresh_token: session.refresh_token,
      }),
    }).catch(function () {});
  }

  var ready = (async function init() {
    var sb = makeClient();
    if (!sb) return { authed: false }; // No supabase lib/config — dev/localhost only.
    try {
      var res = await sb.auth.getSession();
      var session = res && res.data ? res.data.session : null;
      if (session) {
        _token = session.access_token;
        await bridge(session);
        sb.auth.onAuthStateChange(function (evt, s) {
          if (s) { _token = s.access_token; if (evt === "TOKEN_REFRESHED") bridge(s); }
        });
        return { authed: true, email: session.user && session.user.email };
      }
    } catch (e) { /* fall through */ }
    // No session: production pages require sign-in; localhost dev is allowed through.
    if (!isLocal) { window.location.href = "/login"; return { authed: false }; }
    return { authed: false };
  })();

  // path is API-relative ("/voice/calls"), absolute ("/api/..."), or a full URL.
  function resolveUrl(path) {
    if (/^https?:\/\//.test(path)) return path;
    if (path.indexOf("/api/") === 0) return window.location.origin + path;
    return API + (path.charAt(0) === "/" ? path : "/" + path);
  }

  async function authedFetch(path, opts) {
    opts = opts || {};
    var url = resolveUrl(path);
    var headers = Object.assign({}, opts.headers || {});
    if (_token) headers["Authorization"] = "Bearer " + _token;
    var resp = await fetch(url, Object.assign({ credentials: "same-origin" }, opts, { headers: headers }));
    if (resp.status === 401 && !isLocal) { window.location.href = "/login"; }
    if (resp.status === 402) { document.documentElement.classList.add("is-locked"); }
    return resp;
  }

  // GET helper returning parsed JSON or null (402/locked → null, page shows lock).
  async function getJSON(path) {
    try {
      var r = await authedFetch(path, { cache: "no-store" });
      if (!r || !r.ok) return null;
      return await r.json();
    } catch (e) { return null; }
  }

  // ─── DOM / format helpers ───────────────────────────────────────────────
  function esc(s) {
    return (s == null ? "" : String(s)).replace(/[&<>"]/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c];
    });
  }
  function initials(name) {
    return (name || "?").trim().split(/\s+/).map(function (w) { return w[0]; }).slice(0, 2).join("").toUpperCase() || "?";
  }
  function fmtDuration(secs) {
    secs = parseInt(secs || 0, 10);
    if (!secs) return "0:00";
    var m = Math.floor(secs / 60), s = secs % 60;
    return m + ":" + (s < 10 ? "0" : "") + s;
  }
  function fmtDate(iso) {
    if (!iso) return "";
    try {
      return new Date(iso).toLocaleString("en-NG", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
    } catch (e) { return ""; }
  }
  function toast(msg, kind) {
    var wrap = document.querySelector(".toast-wrap");
    if (!wrap) { wrap = document.createElement("div"); wrap.className = "toast-wrap"; document.body.appendChild(wrap); }
    var t = document.createElement("div");
    t.className = "toast" + (kind ? " " + kind : "");
    t.textContent = msg;
    wrap.appendChild(t);
    setTimeout(function () { t.style.opacity = "0"; setTimeout(function () { t.remove(); }, 200); }, 2800);
  }

  function initTheme() {
    var saved = localStorage.getItem("nd-theme");
    if (saved) document.documentElement.setAttribute("data-theme", saved);
  }
  function toggleTheme() {
    var cur = document.documentElement.getAttribute("data-theme");
    var next = cur === "dark" ? "light" : "dark";
    document.documentElement.setAttribute("data-theme", next);
    localStorage.setItem("nd-theme", next);
  }

  initTheme();

  window.Voice = {
    ready: ready,
    api: API,
    authedFetch: authedFetch,
    getJSON: getJSON,
    esc: esc,
    initials: initials,
    fmtDuration: fmtDuration,
    fmtDate: fmtDate,
    toast: toast,
    toggleTheme: toggleTheme,
  };
})();
