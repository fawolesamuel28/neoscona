"""ElevenLabs ConvAI client — the voice receptionist's brain.

Thin async wrapper over the ConvAI agents REST API:

  • create_agent   POST   /v1/convai/agents/create
  • update_agent   PATCH  /v1/convai/agents/{agent_id}
  • delete_agent   DELETE /v1/convai/agents/{agent_id}

Auth is the workspace `xi-api-key`. We do NOT import phone numbers here: the call is
bridged to the agent by KrosAI's native `elevenlabs` endpoint type (it only needs the
`elevenlabs_agent_id`), so number provisioning lives entirely in `krosai.py`.

This module holds NO tenant logic — each tenant gets its own agent; attribution
happens upstream via the channel registry (provider='elevenlabs', external_id=agent_id).
"""

from __future__ import annotations

import logging
import os
from typing import Any, Optional

import httpx

logger = logging.getLogger(__name__)

_BASE = "https://api.elevenlabs.io"
_TIMEOUT = httpx.Timeout(30.0, connect=10.0)

# Sensible defaults for a real-estate receptionist; overridable per call / via env.
DEFAULT_LLM = "gemini-2.5-flash"
DEFAULT_TTS_MODEL = "eleven_flash_v2"


class ElevenLabsError(RuntimeError):
    """Any non-2xx response or transport failure from the ConvAI API."""


def _api_key() -> str:
    key = (os.getenv("ELEVENLABS_API_KEY") or "").strip()
    if not key:
        raise ElevenLabsError("ELEVENLABS_API_KEY is not set")
    return key


def _default_voice_id() -> str:
    # cjVigY5qzO86Huf0OWal is ElevenLabs' documented ConvAI default voice.
    return (os.getenv("ELEVENLABS_DEFAULT_VOICE_ID") or "cjVigY5qzO86Huf0OWal").strip()


async def _request(method: str, path: str, *, json: Optional[dict] = None) -> dict[str, Any]:
    headers = {"xi-api-key": _api_key(), "Content-Type": "application/json"}
    url = f"{_BASE}{path}"
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.request(method, url, headers=headers, json=json)
    except httpx.HTTPError as exc:
        raise ElevenLabsError(f"{method} {path} transport error: {exc}") from exc

    if resp.status_code >= 400:
        # Surface the provider's error body — it names the offending field, which is
        # invaluable when tuning the agent config during provisioning.
        raise ElevenLabsError(f"{method} {path} -> {resp.status_code}: {resp.text[:500]}")
    if not resp.content:
        return {}
    try:
        return resp.json()
    except ValueError:
        return {}


async def create_agent(
    *,
    name: str,
    prompt: str,
    first_message: str = "",
    language: str = "en",
    voice_id: Optional[str] = None,
    llm: str = DEFAULT_LLM,
) -> str:
    """Create a ConvAI agent and return its `agent_id`.

    `prompt` is the system prompt (persona + qualifying instructions). `first_message`
    is what the receptionist says on pickup; empty means it waits for the caller.
    """
    body = {
        "name": (name or "Reva Voice Receptionist")[:120],
        "conversation_config": {
            "agent": {
                "prompt": {"prompt": prompt, "llm": llm},
                "first_message": first_message or "",
                "language": language or "en",
            },
            "tts": {
                "voice_id": voice_id or _default_voice_id(),
                "model_id": DEFAULT_TTS_MODEL,
            },
        },
    }
    data = await _request("POST", "/v1/convai/agents/create", json=body)
    agent_id = data.get("agent_id")
    if not agent_id:
        raise ElevenLabsError(f"create_agent returned no agent_id: {data}")
    logger.info("ElevenLabs agent created: %s (%s)", agent_id, name)
    return agent_id


async def update_agent(
    agent_id: str,
    *,
    prompt: Optional[str] = None,
    first_message: Optional[str] = None,
    language: Optional[str] = None,
    voice_id: Optional[str] = None,
) -> dict[str, Any]:
    """Patch an existing agent's persona/voice. Only provided fields are sent."""
    agent: dict[str, Any] = {}
    if prompt is not None:
        agent["prompt"] = {"prompt": prompt}
    if first_message is not None:
        agent["first_message"] = first_message
    if language is not None:
        agent["language"] = language

    conversation_config: dict[str, Any] = {}
    if agent:
        conversation_config["agent"] = agent
    if voice_id is not None:
        conversation_config["tts"] = {"voice_id": voice_id}
    if not conversation_config:
        return {}
    return await _request(
        "PATCH",
        f"/v1/convai/agents/{agent_id}",
        json={"conversation_config": conversation_config},
    )


async def delete_agent(agent_id: str) -> None:
    """Best-effort delete (used for rollback / deprovision); swallows errors."""
    try:
        await _request("DELETE", f"/v1/convai/agents/{agent_id}")
    except ElevenLabsError as exc:
        logger.warning("delete_agent %s failed (ignored): %s", agent_id, exc)


async def get_conversation_audio(conversation_id: str) -> bytes:
    """Fetch the recorded call audio (MP3) for a conversation.

    The post-call transcription webhook carries no recording URL, so the console
    streams audio on demand from here. Returns raw MP3 bytes; raises ElevenLabsError
    if the conversation has no audio or the request fails. The caller MUST verify
    tenant ownership of the conversation before invoking this.
    """
    headers = {"xi-api-key": _api_key()}
    url = f"{_BASE}/v1/convai/conversations/{conversation_id}/audio"
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.get(url, headers=headers)
    except httpx.HTTPError as exc:
        raise ElevenLabsError(f"GET conversation audio transport error: {exc}") from exc
    if resp.status_code >= 400:
        raise ElevenLabsError(
            f"GET conversation audio -> {resp.status_code}: {resp.text[:300]}"
        )
    return resp.content
