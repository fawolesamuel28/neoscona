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
from app.webhooks.paystack import router as paystack_webhook_router
from app.routers.leads import router as leads_router
from app.routers.billing import router as billing_router
from app.routers.agent_config import router as agent_config_router
from app.routers.onboarding import router as onboarding_router
from app.routers.config import router as config_router
from app.routers.health import router as health_router

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup tasks
    logger.info("Starting Redis listener...")
    try:
        await dashboard_ws_manager.start_redis_listener()
        ping_task = asyncio.create_task(dashboard_ws_manager.run_ping_loop())
    except Exception as e:
        logger.error(f"Failed to start Redis listener: {e}")
        ping_task = None
        
    yield
    # Shutdown tasks
    if ping_task:
        ping_task.cancel()
    await dashboard_ws_manager.stop_redis_listener()
    await close_redis_client()

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
app.include_router(paystack_webhook_router, prefix="/webhook", tags=["Webhooks"])
app.include_router(leads_router, prefix="/api", tags=["Leads"])
app.include_router(billing_router, prefix="/api", tags=["Billing"])
app.include_router(agent_config_router, prefix="/api", tags=["Agent Config"])
app.include_router(onboarding_router, prefix="/api", tags=["Onboarding"])
app.include_router(config_router, prefix="/api", tags=["Configuration"])
app.include_router(health_router, prefix="/api", tags=["System"])

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

@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request):
    if not page_session_ok(request):
        return RedirectResponse(url="/login")
    return render_template(request, "dashboard.html")

@app.get("/products/reva", response_class=HTMLResponse)
async def reva_landing(request: Request):
    return render_template(request, "reva_landing.html")

@app.get("/products/reva/console", response_class=HTMLResponse)
async def reva_console(request: Request):
    if not page_session_ok(request):
        return RedirectResponse(url="/login")
    dashboard_file = Path(__file__).parent / "ai-leads-dashboard.html"
    if dashboard_file.exists():
        return FileResponse(str(dashboard_file))
    return {"error": "Reva Console not found"}

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
