"""KrosAI client — inbound phone numbers + call-routing endpoints.

KrosAI supplies the phone number and bridges inbound calls to our ElevenLabs ConvAI
agent natively: an *endpoint* of type ``elevenlabs`` (carrying only the
``elevenlabs_agent_id``) is the routing target a number's ``endpointId`` points to.
So the full wiring is:

    create ElevenLabs agent → create KrosAI endpoint(type=elevenlabs, agent_id)
      → purchase a KrosAI number attached to that endpoint → inbound flows to the agent.

No raw SIP plumbing is required on our side; KrosAI handles the bridge.

Wrapped operations (https://api.krosai.com/v1, Bearer key):

  • list_available_numbers   GET    /phone-numbers?action=available
  • purchase_number          POST   /phone-numbers
  • get_number               GET    /phone-numbers/{id}
  • assign_number            PATCH  /phone-numbers/{id}
  • release_number           DELETE /phone-numbers/{id}?action=release
  • create_endpoint          POST   /endpoints
  • delete_endpoint          DELETE /endpoints/{id}

This module holds NO tenant logic; the caller scopes everything to a tenant.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Optional

import httpx

logger = logging.getLogger(__name__)

_TIMEOUT = httpx.Timeout(30.0, connect=10.0)


class KrosAIError(RuntimeError):
    """Any non-2xx response or transport failure from the KrosAI API."""


def _base_url() -> str:
    return (os.getenv("KROSAI_API_BASE") or "https://api.krosai.com/v1").rstrip("/")


def _api_key() -> str:
    key = (os.getenv("KROSAI_API_KEY") or "").strip()
    if not key:
        raise KrosAIError("KROSAI_API_KEY is not set")
    return key


async def _request(
    method: str,
    path: str,
    *,
    json: Optional[dict] = None,
    params: Optional[dict] = None,
) -> Any:
    headers = {"Authorization": f"Bearer {_api_key()}", "Content-Type": "application/json"}
    url = f"{_base_url()}{path}"
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.request(method, url, headers=headers, json=json, params=params)
    except httpx.HTTPError as exc:
        raise KrosAIError(f"{method} {path} transport error: {exc}") from exc

    if resp.status_code >= 400:
        raise KrosAIError(f"{method} {path} -> {resp.status_code}: {resp.text[:500]}")
    if resp.status_code == 204 or not resp.content:
        return {}
    try:
        return resp.json()
    except ValueError:
        return {}


# ─── Phone numbers ─────────────────────────────────────────────────────────────

async def list_available_numbers(
    country: str, *, number_type: str = "local", limit: int = 20, offset: int = 0
) -> list[dict[str, Any]]:
    """Available inventory the tenant can buy (action=available). `country` is required."""
    data = await _request(
        "GET",
        "/phone-numbers",
        params={
            "action": "available",
            "country": country,
            "type": number_type,
            "limit": max(1, min(limit, 100)),
            "offset": max(0, offset),
        },
    )
    # The API returns a list (possibly wrapped); normalize to a list of dicts.
    if isinstance(data, dict):
        return data.get("data") or data.get("phone_numbers") or []
    return data or []


async def purchase_number(
    *, inventory_id: str, endpoint_id: Optional[str] = None
) -> dict[str, Any]:
    """Purchase an available number by inventory id, optionally attaching an endpoint.

    Returns the phone number object (`id`, `e164`, `status`, …). Surfaces KrosAI's
    402 INSUFFICIENT_BALANCE / 403 KYC errors verbatim via KrosAIError.
    """
    body: dict[str, Any] = {"inventory_id": inventory_id}
    if endpoint_id:
        body["endpointId"] = endpoint_id
    return await _request("POST", "/phone-numbers", json=body)


async def get_number(phone_id: str) -> dict[str, Any]:
    """Phone number detail, including sip_credentials (owner/admin only)."""
    return await _request("GET", f"/phone-numbers/{phone_id}")


async def assign_number(
    phone_id: str, *, endpoint_id: Optional[str] = None, allow_inbound: bool = True
) -> dict[str, Any]:
    """Route a number's calls to `endpoint_id` and toggle inbound."""
    body: dict[str, Any] = {"allowInbound": allow_inbound}
    if endpoint_id is not None:
        body["endpointId"] = endpoint_id
    return await _request("PATCH", f"/phone-numbers/{phone_id}", json=body)


async def release_number(phone_id: str) -> None:
    """Soft-release a number (keeps the record). Best-effort for rollback/deprovision."""
    try:
        await _request("DELETE", f"/phone-numbers/{phone_id}", params={"action": "release"})
    except KrosAIError as exc:
        logger.warning("release_number %s failed (ignored): %s", phone_id, exc)


# ─── Endpoints (the routing target a number points to) ─────────────────────────

async def create_elevenlabs_endpoint(*, name: str, elevenlabs_agent_id: str) -> dict[str, Any]:
    """Create an endpoint that bridges inbound calls to an ElevenLabs ConvAI agent.

    Uses KrosAI's native `elevenlabs` endpoint type — provider_config carries only the
    agent id; KrosAI handles the connection to ElevenLabs. Returns the endpoint object.
    """
    return await _request(
        "POST",
        "/endpoints",
        json={
            "name": (name or "reva-voice")[:100],
            "type": "elevenlabs",
            "provider_config": {"elevenlabs_agent_id": elevenlabs_agent_id},
        },
    )


async def delete_endpoint(endpoint_id: str) -> None:
    """Best-effort delete (rollback / deprovision)."""
    try:
        await _request("DELETE", f"/endpoints/{endpoint_id}")
    except KrosAIError as exc:
        logger.warning("delete_endpoint %s failed (ignored): %s", endpoint_id, exc)
