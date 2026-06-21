import contextvars
import logging
import os

from supabase import Client, create_client

logger = logging.getLogger(__name__)

_client: Client | None = None

# Holds the current request's Supabase *user* access token, set by the auth
# dependencies (app/core/auth.py). Service/webhook/job code leaves this unset and
# falls back to the service-role client. asyncio.to_thread copies the context, so
# a token set before a threaded DB call is visible inside it.
_user_token_var: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "supabase_user_token", default=None
)


def get_supabase() -> Client:
    """
    Returns a configured singleton Supabase client (SERVICE ROLE).

    This client bypasses RLS and must only be used for trusted server-side work:
    membership/tenant lookups, provisioning, webhooks, and background jobs — all of
    which MUST filter by tenant_id explicitly in application code. For user-facing
    request paths use get_request_client()/get_user_client() so RLS applies.
    Requires SUPABASE_URL and SUPABASE_KEY (or SUPABASE_SERVICE_KEY).
    """
    global _client
    if _client is None:
        url = os.getenv("SUPABASE_URL")
        key = os.getenv("SUPABASE_SERVICE_KEY") or os.getenv("SUPABASE_KEY")

        if not url or not key:
            raise ValueError(
                "SUPABASE_URL and SUPABASE_KEY (or SUPABASE_SERVICE_KEY) must be set.",
            )

        _client = create_client(url, key)
        if os.getenv("SUPABASE_SERVICE_KEY"):
            logger.info("Supabase client initialized (service role).")
        else:
            logger.info("Supabase client initialized.")

    return _client


def set_request_token(token: str | None) -> None:
    """Record the current request's Supabase access token so downstream service
    code can build a user-scoped (RLS-enforced) client. Called by the auth deps."""
    _user_token_var.set(token)


def get_user_client(access_token: str) -> Client:
    """
    A per-request Supabase client authenticated as the END USER: anon key as the
    apikey, the user's access token as the bearer. Queries run as the Postgres
    `authenticated` role, so Row-Level Security applies (auth.uid() = the user).

    A fresh client is created per call — we never mutate the shared service-role
    client's auth, which would race across concurrent requests.
    """
    url = os.getenv("SUPABASE_URL")
    anon = os.getenv("SUPABASE_ANON_KEY") or os.getenv("SUPABASE_KEY")
    if not url or not anon:
        raise ValueError(
            "SUPABASE_URL and SUPABASE_ANON_KEY (or SUPABASE_KEY) must be set for user clients.",
        )
    client = create_client(url, anon)
    # Override the default (anon) Authorization with the user's token so PostgREST
    # evaluates RLS as that user. The apikey header stays the anon key.
    client.postgrest.auth(access_token)
    return client


def get_request_client() -> Client:
    """
    The Supabase client for the CURRENT user-facing request: RLS-enforced when a
    user token is in context, falling back to the service-role client otherwise
    (dev bypass / no authenticated user). Tenant-scoped service functions should
    use this for reads/writes on behalf of a signed-in user.
    """
    token = _user_token_var.get()
    if token:
        return get_user_client(token)
    return get_supabase()
