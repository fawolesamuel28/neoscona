import logging
import os
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

# Load environment variables from .env
load_dotenv()

from app.webhooks.whatsapp import router as whatsapp_router
from app.webhooks.calendly import router as calendly_router
from app.webhooks.paystack import router as paystack_webhook_router
from app.routers.leads import router as leads_router
from app.routers.elevenlabs_leads import router as elevenlabs_leads_router
from app.routers.inventory import router as inventory_router
from app.routers.health import router as health_router
from app.routers.config import router as config_router
from app.routers.onboarding import router as onboarding_router
from app.routers.billing import router as billing_router
from app.routers.agent_config import router as agent_config_router
from app.routers.inbox import router as inbox_router
from app.routers.dashboard_ws import router as dashboard_ws_router

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------
from contextlib import asynccontextmanager
import asyncio

from app.cache.redis import close_redis_client
from app.core.dashboard_ws import dashboard_ws_manager

@asynccontextmanager
async def lifespan(app: FastAPI):
    await dashboard_ws_manager.start_redis_listener()
    ping_task = asyncio.create_task(dashboard_ws_manager.run_ping_loop())
    yield
    ping_task.cancel()
    try:
        await ping_task
    except asyncio.CancelledError:
        pass
    await dashboard_ws_manager.stop_redis_listener()
    await close_redis_client()

# Hide interactive API docs in production unless DOCS_ENABLED is set.
_is_production = (os.getenv("ENVIRONMENT") or "development").lower() == "production"
_docs_enabled = (os.getenv("DOCS_ENABLED") or "").lower() in ("1", "true", "yes")
_expose_docs = _docs_enabled or not _is_production

app = FastAPI(
    title="Reva API",
    description="Reva — AI sales engine for Nigerian real estate developers.",
    version="1.0.0",
    docs_url="/docs" if _expose_docs else None,
    redoc_url="/redoc" if _expose_docs else None,
    openapi_url="/openapi.json" if _expose_docs else None,
    lifespan=lifespan,
)

# ---------------------------------------------------------------------------
# CORS
# Set ALLOWED_ORIGINS to a comma-separated list of dashboard origins in prod,
# e.g. ALLOWED_ORIGINS="https://app.reva.ng,https://reva.ng".
# A wildcard ("*") cannot be combined with credentialed requests (the browser
# rejects it), so when origins are not explicitly configured we serve a
# non-credentialed wildcard and log a warning instead of silently shipping an
# invalid, world-open CORS policy.
# ---------------------------------------------------------------------------
_origins_env = os.getenv("ALLOWED_ORIGINS", "").strip()
if _origins_env and _origins_env != "*":
    allowed_origins = [o.strip() for o in _origins_env.split(",") if o.strip()]
    allow_credentials = True
else:
    logger.warning(
        "ALLOWED_ORIGINS not set — using non-credentialed wildcard CORS. "
        "Set ALLOWED_ORIGINS to your dashboard origin(s) before production."
    )
    allowed_origins = ["*"]
    allow_credentials = False

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=allow_credentials,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# Routers
# ---------------------------------------------------------------------------
app.include_router(whatsapp_router, prefix="/webhook")
app.include_router(calendly_router, prefix="/webhook")
app.include_router(paystack_webhook_router, prefix="/webhook")
app.include_router(leads_router, prefix="/api")
app.include_router(elevenlabs_leads_router, prefix="/api")
app.include_router(inventory_router, prefix="/api")
app.include_router(health_router, prefix="/api")
app.include_router(config_router, prefix="/api")
app.include_router(onboarding_router, prefix="/api")
app.include_router(billing_router, prefix="/api")
app.include_router(agent_config_router, prefix="/api")
app.include_router(inbox_router, prefix="/api")
app.include_router(dashboard_ws_router, prefix="/api")

# ---------------------------------------------------------------------------
# Static files + Dashboard
# ---------------------------------------------------------------------------
STATIC_DIR = Path(__file__).parent / "static"
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

# ---------------------------------------------------------------------------
# Core routes
# ---------------------------------------------------------------------------
@app.get("/", tags=["System"], include_in_schema=False)
async def root():
    landing_file = Path(__file__).parent.parent / "landing.html"
    if landing_file.exists():
        return FileResponse(
            str(landing_file),
            headers={"Cache-Control": "no-cache, must-revalidate"},
        )
    return {"message": "Reva API is running. Visit /docs for the API reference."}


@app.get("/dashboard", tags=["Dashboard"], summary="Live lead pipeline dashboard")
async def dashboard():
    dashboard_file = STATIC_DIR / "dashboard.html"
    if dashboard_file.exists():
        return FileResponse(
            str(dashboard_file),
            headers={"Cache-Control": "no-cache, must-revalidate"},
        )
    return {"message": "Dashboard file not found."}


def _serve_static_page(filename: str):
    page = STATIC_DIR / filename
    if page.exists():
        return FileResponse(str(page), headers={"Cache-Control": "no-cache, must-revalidate"})
    return {"message": f"{filename} not found."}


@app.get("/signup", tags=["Onboarding"], include_in_schema=False)
async def signup_page():
    return _serve_static_page("signup.html")


@app.get("/onboarding", tags=["Onboarding"], include_in_schema=False)
async def onboarding_page():
    return _serve_static_page("onboarding.html")


@app.get("/settings", tags=["Dashboard"], include_in_schema=False)
async def settings_page():
    return _serve_static_page("settings.html")
