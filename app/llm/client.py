"""
LLM abstraction layer — supports Ollama (local) and Anthropic (cloud).
Provider is selected via the LLM_PROVIDER environment variable.
"""

from __future__ import annotations

import json
import os
import random
import time
from typing import Any

import httpx

from app.llm.humanize import sanitize_lead_message
from app.llm.tools import DEALFLOW_TOOLS
from app.core.circuit_breaker import llm_breaker
from app.core.metrics import LLM_LATENCY, TOOL_CALLS
from app.core.logger import get_logger

FALLBACK_RESPONSES = [
    "Sorry, network was acting up on my end. Can you send that again? 🙏",
    "Give me 2 mins — WhatsApp is misbehaving. I'll reply shortly 😅",
    "Sorry! I didn't catch that. Mind sending it one more time?",
]

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Config — all driven by environment variables
# ---------------------------------------------------------------------------
LLM_PROVIDER: str = os.getenv("LLM_PROVIDER", "gemini")
GEMINI_MODEL: str = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")


# ---------------------------------------------------------------------------
# Provider implementations
# ---------------------------------------------------------------------------

async def call_ollama(messages: list[dict[str, str]], system: str) -> str:
    """Call a local Ollama instance."""
    base_url = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434").rstrip("/")
    model = os.getenv("OLLAMA_MODEL", "llama3.1")
    payload = {
        "model": model,
        "messages": [{"role": "system", "content": system}, *messages],
        "stream": False,
    }
    async with httpx.AsyncClient(timeout=120.0) as client:
        response = await client.post(f"{base_url}/api/chat", json=payload)
        response.raise_for_status()
        return response.json()["message"]["content"]


async def call_gemini(messages: list[dict[str, str]], system: str) -> str:
    """Call Google Gemini's REST API."""
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY environment variable is not set.")

    gemini_messages = []
    for msg in messages:
        role = "model" if msg["role"] == "assistant" else "user"
        gemini_messages.append({"role": role, "parts": [{"text": msg["content"]}]})

    payload = {
        "system_instruction": {"parts": [{"text": system}]},
        "contents": gemini_messages,
        "generationConfig": {"temperature": 0.7},
    }

    model = GEMINI_MODEL
    url = (
        f"https://generativelanguage.googleapis.com/v1beta/models/"
        f"{model}:generateContent?key={api_key}"
    )

    async with httpx.AsyncClient(timeout=120.0) as client:
        response = await client.post(url, json=payload)
        response.raise_for_status()
        data = response.json()
        return data["candidates"][0]["content"]["parts"][0]["text"]


async def call_claude(messages: list[dict[str, str]], system: str) -> str:
    """Call Anthropic's Claude API with tool support."""
    try:
        from anthropic import AsyncAnthropic
    except ImportError:
        raise RuntimeError(
            "anthropic package is not installed. "
            "Run: pip install anthropic"
        )

    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY environment variable is not set.")

    client = AsyncAnthropic(api_key=api_key)
    
    # Format tools for Anthropic
    anthropic_tools = []
    for t in DEALFLOW_TOOLS:
        anthropic_tools.append({
            "name": t["name"],
            "description": t["description"],
            "input_schema": t["input_schema"]
        })

    response = await client.messages.create(
        model=os.getenv("ANTHROPIC_MODEL", "claude-3-5-sonnet-20241022"),
        max_tokens=1024,
        system=system,
        messages=messages,
        tools=anthropic_tools
    )
    
    # If there's a tool call, we need to handle it. 
    # For now, we'll return the raw response object or a serialized version 
    # that parse_ai_response can handle, or update the flow.
    
    # Simplified: if it's a tool call, we'll return a special string
    if response.stop_reason == "tool_use":
        tool_use = next(block for block in response.content if block.type == "tool_use")
        return f"<<<TOOL_CALL>>>\n{json.dumps({'name': tool_use.name, 'input': tool_use.input})}\n<<<END_TOOL>>>"
    
    return response.content[0].text


# Provider registry — add new providers here
_PROVIDERS = {
    "gemini": call_gemini,
    "anthropic": call_claude,
    "ollama": call_ollama,
}


# ---------------------------------------------------------------------------
# Response parser
# ---------------------------------------------------------------------------

def parse_ai_response(raw_response: str) -> tuple[str, dict[str, Any]]:
    """
    Split the AI response into message and extraction, OR handle tool calls.
    """
    if "<<<TOOL_CALL>>>" in raw_response:
        try:
            parts = raw_response.split("<<<TOOL_CALL>>>", 1)
            json_part = parts[1].split("<<<END_TOOL>>>", 1)[0].strip()
            tool_call = json.loads(json_part)
            return "TOOL_CALL", tool_call
        except Exception as e:
            logger.error(f"Failed to parse tool call: {e}")
            return raw_response.strip(), {}

    try:
        if "<<<EXTRACTED>>>" in raw_response and "<<<END>>>" in raw_response:
            parts = raw_response.split("<<<EXTRACTED>>>", 1)
            message_part = parts[0].strip()
            json_part = parts[1].split("<<<END>>>", 1)[0].strip()
            extracted: dict[str, Any] = json.loads(json_part)

            # Normalise null-string values into actual None
            for key in ("budget", "location", "property_type", "timeline", "name", "language"):
                if extracted.get(key) in ("null", "None", ""):
                    extracted[key] = None

            score = extracted.get("seriousness_score")
            if score is not None:
                try:
                    extracted["seriousness_score"] = max(1, min(10, int(score)))
                except (TypeError, ValueError):
                    extracted["seriousness_score"] = None

            return message_part, extracted

    except (json.JSONDecodeError, IndexError, KeyError) as exc:
        logger.warning("Failed to parse extraction block: %s", exc)

    return raw_response.strip(), {}


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

async def execute_tool(tool_name: str, tool_input: dict, phone_number: str, lead_data: dict, *, tenant_id: str | None = None) -> str:
    """Execute the tool and return a response for the AI.

    `tenant_id` (channel-resolved, preferred over lead_data) scopes the catalog and
    knowledge-base reads so a tool call can't surface another workspace's data.
    """
    from app.services.inventory import search_properties, handle_inventory_result
    from app.services.messaging import send_property_images
    from app.services.calendly import create_booking_link

    tenant_id = tenant_id or (lead_data or {}).get("tenant_id")
    
    if tool_name == "search_inventory":
        location = tool_input.get("location")
        prop_type = tool_input.get("property_type")
        # Handle variations in naming
        budget = tool_input.get("max_budget_naira") or tool_input.get("max_budget") or tool_input.get("price")
        bedrooms = tool_input.get("bedrooms") or tool_input.get("beds")
        
        # Ensure budget is numeric
        if isinstance(budget, str):
            try:
                # Remove commas and currency symbols
                budget = float(budget.replace(",", "").replace("₦", "").replace("N", "").strip())
            except:
                budget = 0
        
        properties = await search_properties(location, prop_type, budget, bedrooms, tenant_id=tenant_id)
        return await handle_inventory_result(properties, phone_number, lead_data)
        
    elif tool_name == "send_properties":
        # Implementation for sending images/details for specific IDs
        return "Details and images for the selected properties have been sent to your chat."
        
    elif tool_name == "book_meeting":
        lead_name = lead_data.get("name") or "Valued Client"
        link = await create_booking_link(phone_number, lead_name)
        
        preferred_time = tool_input.get("preferred_time", "your preferred time")
        
        return (
            f"I've set things up for our meeting on {preferred_time}! 🗓️\n\n"
            f"Please use this link to pick the exact time that works best for you: {link}\n\n"
            "I'll see you then! 👍"
        )
        
    elif tool_name == "search_knowledge_base":
        query = tool_input.get("query", "")
        if not tenant_id:
            return "No knowledge base available for this account."

        from app.services.knowledge import search_knowledge
        chunks = await search_knowledge(tenant_id, query, top_k=5)

        if not chunks:
            return (
                "I couldn't find anything in our documents about that. "
                "Let me help you with what I know, or I can connect you with a colleague."
            )

        # Format chunks as grounded context for the AI
        context_parts = []
        for i, chunk in enumerate(chunks, 1):
            source = chunk.get("filename", "document")
            score = chunk.get("similarity", 0)
            content = chunk.get("content", "")
            context_parts.append(
                f"[Source: {source} | Relevance: {score:.0%}]\n{content}"
            )

        return (
            "KNOWLEDGE BASE RESULTS — use this information to answer the lead's question. "
            "Quote facts accurately; do not add information not present here.\n\n"
            + "\n\n---\n\n".join(context_parts)
        )

    return "Tool execution failed."


async def get_ai_response(
    messages: list[dict[str, str]],
    stage: str,
    phone_number: str,
    lead_data: dict[str, Any] | None = None,
    tenant_id: str | None = None,
) -> tuple[str, dict[str, Any]]:
    """
    Single function the webhook calls for every inbound message.

    `tenant_id` should be the channel-resolved tenant (passed by the worker). We
    prefer it over any tenant_id sitting in `lead_data`, which isn't reliably present
    on early turns — and which scopes the per-tenant persona, knowledge base, and
    inventory catalog tools.
    """
    # Build context-enriched system prompt. The base persona is per-tenant (falls back
    # to the default "Amara" persona when the tenant has no config / lookup fails).
    from app.llm.prompts import build_dealflow_prompt
    from app.services.agent_config import get_agent_config

    tenant_id = tenant_id or (lead_data or {}).get("tenant_id")
    cfg = await get_agent_config(tenant_id)
    context_sections = [
        build_dealflow_prompt(cfg),
        f"\n## LEAD INFO\nPhone: {phone_number}",
        f"\n## CURRENT STAGE\n{stage.upper()}"
    ]
    if lead_data:
        known = {k: v for k, v in lead_data.items() if v is not None}
        if known:
            context_sections.append(
                f"\n## DATA ALREADY COLLECTED\n{json.dumps(known, indent=2)}"
            )
    system = "\n".join(context_sections)

    provider_fn = _PROVIDERS.get(LLM_PROVIDER)
    if provider_fn is None:
        logger.error("Unknown LLM_PROVIDER: %s", LLM_PROVIDER)
        return _fallback_response()

    try:
        # Circuit breaker protected LLM call with latency tracking
        start_time = time.time()
        raw = await llm_breaker.call(provider_fn, messages, system)
        duration = time.time() - start_time
        LLM_LATENCY.labels(provider=LLM_PROVIDER).observe(duration)
        logger.info(f"LLM call completed in {duration:.2f}s (provider={LLM_PROVIDER})")

        message, extracted = parse_ai_response(raw)

        # Anti-hallucination check
        from app.llm.anti_hallucination import check_response
        is_valid, checked_message = await check_response(message)
        if not is_valid:
            logger.warning("Anti-hallucination blocked AI response. Using fallback.")
            message = checked_message
        
        # Proactive check: if AI says it's searching/booking but didn't call the tool
        search_keywords = ["searching", "checking", "inventory", "available"]
        booking_keywords = ["book", "meeting", "viewing", "visit", "schedule"]
        
        needs_search = any(k in raw.lower() for k in search_keywords)
        needs_booking = any(k in raw.lower() for k in booking_keywords)
        
        if message != "TOOL_CALL" and (needs_search or needs_booking):
            logger.info("AI mentioned searching/booking but didn't call tool. Forcing tool call prompt.")
            messages.append({"role": "assistant", "content": raw})
            
            prompt = "You mentioned searching or booking. "
            if needs_search:
                prompt += "Please call the `search_inventory` tool now. "
            if needs_booking:
                prompt += "Please call the `book_meeting` tool now with the lead's phone number. "
            prompt += "Use the <<<TOOL_CALL>>> format to get actual results/links. Do not make up confirmation details."
            
            messages.append({"role": "user", "content": prompt})
            raw = await provider_fn(messages, system)
            message, extracted = parse_ai_response(raw)

        # Handle tool call loop
        max_turns = 2
        current_turn = 0
        
        while message == "TOOL_CALL" and current_turn < max_turns:
            current_turn += 1
            tool_name = extracted["name"]
            tool_input = extracted["input"]
            logger.info(f"AI requested tool: {tool_name} with {tool_input}")
            
            try:
                tool_result = await execute_tool(tool_name, tool_input, phone_number, lead_data or {}, tenant_id=tenant_id)
                TOOL_CALLS.labels(tool=tool_name, status="success").inc()
            except Exception as tool_err:
                TOOL_CALLS.labels(tool=tool_name, status="failure").inc()
                logger.error(f"Tool {tool_name} failed: {tool_err}")
                tool_result = "I had trouble looking that up. Let me help you another way."
            
            # If the tool result is a final user-facing message, return it immediately
            if tool_name in ["search_inventory", "book_meeting"]:
                return tool_result, {}

            # For knowledge-base results, feed back to AI for natural response
            # (the result contains grounded context the AI should use)
            
            # Feed tool result back to AI
            messages.append({"role": "assistant", "content": raw})
            messages.append({"role": "user", "content": f"TOOL_RESULT: {tool_result}"})
            
            raw = await provider_fn(messages, system)
            message, extracted = parse_ai_response(raw)

        message = sanitize_lead_message(message)
        logger.info(
            "AI responded | provider=%s | stage=%s | extracted=%s",
            LLM_PROVIDER, stage, extracted,
        )
        return message, extracted

    except httpx.TimeoutException:
        logger.error(
            "LLM request timed out (provider=%s)",
            LLM_PROVIDER,
        )
        return _fallback_response()

    except Exception as exc:
        logger.error("LLM call failed (provider=%s): %s", LLM_PROVIDER, exc)
        if LLM_PROVIDER == "gemini":
            logger.error("Ensure GEMINI_API_KEY is correctly set.")
        return _fallback_response()


def _fallback_response() -> tuple[str, dict]:
    """Graceful degradation message when the AI is unreachable."""
    return (
        random.choice(FALLBACK_RESPONSES),
        {},
    )
