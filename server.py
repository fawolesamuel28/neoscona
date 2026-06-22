import os
import logging
from pathlib import Path
from dotenv import load_dotenv
from fastapi import FastAPI, Request, Response, Form, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, RedirectResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from contextlib import asynccontextmanager
import asyncio

# Load environment
load_dotenv()

# Logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
)
logger = logging.getLogger(__name__)

# Reva Imports (Assumes app/ directory was copied)
from app.cache.redis import close_redis_client
from app.core.dashboard_ws import dashboard_ws_manager
from app.core.sso import router as sso_router, page_session_ok, ACCESS_COOKIE
from app.core.auth import _decode_token, _auth_disabled
from app.webhooks.whatsapp import router as whatsapp_router
from app.webhooks.flutterwave import router as flutterwave_webhook_router
from app.webhooks.voice_elevenlabs import router as voice_webhook_router
from app.routers.leads import router as leads_router
from app.routers.billing import router as billing_router
from app.routers.agent_config import router as agent_config_router
from app.routers.onboarding import router as onboarding_router
from app.routers.config import router as config_router
from app.routers.health import router as health_router
from app.routers.voice import router as voice_router
from app.routers.elevenlabs_leads import router as elevenlabs_leads_router
from app.routers.voice_calls import router as voice_calls_router

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup tasks
    ping_task = None
    redis_url = os.getenv("REDIS_URL")
    dev_no_redis = os.getenv("DEV_NO_REDIS", "").lower() in ("1", "true", "yes")
    if not redis_url or dev_no_redis:
        logger.info("Skipping Redis listener (DEV_NO_REDIS set or REDIS_URL unset).")
    else:
        logger.info("Starting Redis listener...")
        try:
            await dashboard_ws_manager.start_redis_listener()
            ping_task = asyncio.create_task(dashboard_ws_manager.run_ping_loop())
        except Exception as e:
            logger.error(f"Failed to start Redis listener: {e}")
            ping_task = None
        # Scheduler: optional APScheduler job registration for recurring charges
        try:
            from app.billing import scheduler as billing_scheduler
            billing_scheduler.setup_scheduler()
            logger.info("Billing scheduler registered")
        except Exception:
            logger.info("Billing scheduler not registered (optional)")
        
    yield
    # Shutdown tasks
    if ping_task:
        ping_task.cancel()
        try:
            await dashboard_ws_manager.stop_redis_listener()
        except Exception:
            logger.exception("Error stopping Redis listener during shutdown")
        try:
            await close_redis_client()
        except Exception:
            logger.exception("Error closing Redis client during shutdown")

app = FastAPI(
    title="Neoscona Unified Platform",
    description="The Twilio of AI automation, built for Africa.",
    version="2.0.0",
    lifespan=lifespan
)

# CORS — credentialed cookie requests require explicit origins (browsers reject
# the "*" + allow_credentials combination). Override via ALLOWED_ORIGINS (CSV).
_default_origins = "https://neoscona.xyz,https://app.neoscona.xyz,http://localhost:8000,http://127.0.0.1:8000"
ALLOWED_ORIGINS = [o.strip() for o in os.getenv("ALLOWED_ORIGINS", _default_origins).split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Static & Templates
STATIC_DIR = Path(__file__).parent / "static"
TEMPLATES_DIR = Path(__file__).parent / "templates"
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

# ─── Auth / SSO ────────────────────────────────────────────────────────────
app.include_router(sso_router)  # /auth/session, /auth/refresh, /auth/logout

# ─── Include Reva Routers ──────────────────────────────────────────────────
app.include_router(whatsapp_router, prefix="/webhook", tags=["Webhooks"])
app.include_router(flutterwave_webhook_router, prefix="/webhook", tags=["Webhooks"])
app.include_router(voice_webhook_router, prefix="/webhook", tags=["Webhooks"])
app.include_router(leads_router, prefix="/api", tags=["Leads"])
app.include_router(billing_router, prefix="/api", tags=["Billing"])
app.include_router(agent_config_router, prefix="/api", tags=["Agent Config"])
app.include_router(onboarding_router, prefix="/api", tags=["Onboarding"])
app.include_router(config_router, prefix="/api", tags=["Configuration"])
app.include_router(health_router, prefix="/api", tags=["System"])
app.include_router(voice_router, prefix="/api", tags=["Voice Receptionist"])
app.include_router(elevenlabs_leads_router, prefix="/api", tags=["Voice Leads"])
app.include_router(voice_calls_router, prefix="/api", tags=["Voice Calls"])

# ─── Marketing & Platform Routes ───────────────────────────────────────────

def _template_session(request: Request) -> dict:
    """Flask-compat session dict for Jinja nav bars (derived from SSO cookie)."""
    if _auth_disabled():
        return {"user_id": "dev-user", "user_email": "dev@local"}
    token = request.cookies.get(ACCESS_COOKIE)
    if not token:
        return {}
    try:
        claims = _decode_token(token)
    except HTTPException:
        return {}
    user_id = claims.get("sub")
    if not user_id:
        return {}
    return {"user_id": user_id, "user_email": claims.get("email") or ""}


def get_base_context(request: Request):
    session = _template_session(request)
    email = session.get("user_email") or ""
    return {
        "supabase_url": os.getenv("SUPABASE_URL"),
        "supabase_anon_key": os.getenv("SUPABASE_ANON_KEY"),
        "session": session,
        "user": {"email": email or "Account"},
    }


def render_template(request: Request, name: str, **extra):
    return templates.TemplateResponse(request, name, {**get_base_context(request), **extra})

@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    return render_template(request, "neoscona.html")

@app.get("/blog", response_class=HTMLResponse)
async def blog(request: Request):
    return render_template(request, "blog.html", posts=[])

@app.get("/docs", response_class=HTMLResponse)
async def help_center(request: Request):
    return render_template(request, "docs.html")

@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    return render_template(request, "login.html")

@app.get("/signup", response_class=HTMLResponse)
async def signup_page(request: Request):
    return render_template(request, "signup.html")


@app.post("/signup")
async def signup_form_fallback():
    """Legacy HTML forms POST here if JS fails — send them back to the SPA flow."""
    return RedirectResponse(url="/signup", status_code=303)


@app.post("/login")
async def login_form_fallback():
    return RedirectResponse(url="/login", status_code=303)


@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request):
    if not page_session_ok(request):
        return RedirectResponse(url="/login")
    return render_template(request, "dashboard.html")

@app.get("/billing", response_class=HTMLResponse)
async def billing_page(request: Request):
    if not page_session_ok(request):
        return RedirectResponse(url="/login")
        
    session = _template_session(request)
    tenant_id = session.get("user_id")
    balance = 0.0
    
    if tenant_id:
        try:
            from app.services.billing import get_billing
            billing_data = await get_billing(tenant_id)
            tenant_info = billing_data.get("billing", {})
            if tenant_info and tenant_info.get("balance") is not None:
                balance = float(tenant_info.get("balance", 0.0))
        except Exception as e:
            logger.error(f"Failed to fetch billing balance for {tenant_id}: {e}")

    user_data = {"email": session.get("user_email") or "Account", "balance": balance}
    return render_template(request, "billing.html", user=user_data)

@app.get("/products/reva", response_class=HTMLResponse)
async def reva_landing(request: Request):
    return render_template(request, "reva_landing.html")

@app.get("/products/reva/console", response_class=HTMLResponse)
async def reva_console(request: Request):
    # Allow local dev access to the static console when running on localhost
    client_host = getattr(request.client, 'host', None)
    if client_host in ("127.0.0.1", "::1"):
        dashboard_file = Path(__file__).parent / "ai-leads-dashboard.html"
        if dashboard_file.exists():
            return FileResponse(str(dashboard_file))

    if not page_session_ok(request):
        return RedirectResponse(url="/login")

    dashboard_file = Path(__file__).parent / "ai-leads-dashboard.html"
    if dashboard_file.exists():
        return FileResponse(str(dashboard_file))
    return {"error": "Reva Console not found"}

@app.get("/products/reva/hot-leads", response_class=HTMLResponse)
async def reva_hot_leads(request: Request):
    # Mirror the console's localhost dev bypass so the sidebar links work locally.
    client_host = getattr(request.client, 'host', None)
    if client_host not in ("127.0.0.1", "::1") and not page_session_ok(request):
        return RedirectResponse(url="/login")
    return render_template(request, "reva_hot_leads.html")

@app.get("/products/reva/settings", response_class=HTMLResponse)
async def reva_settings(request: Request):
    # Mirror the console's localhost dev bypass so the sidebar links work locally.
    client_host = getattr(request.client, 'host', None)
    if client_host not in ("127.0.0.1", "::1") and not page_session_ok(request):
        return RedirectResponse(url="/login")
    return render_template(request, "reva_settings.html")

@app.get("/products/reva/voice", response_class=HTMLResponse)
async def reva_voice(request: Request):
    # Mirror the console's localhost dev bypass so the sidebar links work locally.
    client_host = getattr(request.client, 'host', None)
    if client_host not in ("127.0.0.1", "::1") and not page_session_ok(request):
        return RedirectResponse(url="/login")
    return render_template(request, "reva_voice.html")

# ─── Neoscona Voice product console ────────────────────────────────────────
# Three surfaces (overview / calls / settings), powered by the existing voice +
# elevenlabs-leads + voice-calls APIs. Same localhost-dev-bypass + page_session_ok
# guard as the Reva pages so the sidebar is navigable locally and gated remotely.

@app.get("/products/voice/console", response_class=HTMLResponse)
async def voice_console(request: Request):
    client_host = getattr(request.client, 'host', None)
    if client_host not in ("127.0.0.1", "::1") and not page_session_ok(request):
        return RedirectResponse(url="/login")
    return render_template(request, "voice_console.html")

@app.get("/products/voice/calls", response_class=HTMLResponse)
async def voice_calls_page(request: Request):
    client_host = getattr(request.client, 'host', None)
    if client_host not in ("127.0.0.1", "::1") and not page_session_ok(request):
        return RedirectResponse(url="/login")
    return render_template(request, "voice_calls.html")

@app.get("/products/voice/settings", response_class=HTMLResponse)
async def voice_settings_page(request: Request):
    client_host = getattr(request.client, 'host', None)
    if client_host not in ("127.0.0.1", "::1") and not page_session_ok(request):
        return RedirectResponse(url="/login")
    return render_template(request, "voice_settings.html")

@app.get("/healthz")
async def healthz():
    return {"status": "ok"}


@app.get("/logout")
async def logout():
    # Clear the SSO cookies, then send the user home. (The SPA also calls
    # POST /auth/logout for a GoTrue sign-out; this GET is the link fallback.)
    from app.core.sso import clear_session_cookies
    response = RedirectResponse(url="/")
    clear_session_cookies(response)
    return response

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
