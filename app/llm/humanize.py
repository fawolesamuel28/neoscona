"""
Keep lead-facing copy natural — strip AI/system leakage from model output.
"""

from __future__ import annotations

import re

# Phrases that break the illusion (lead-facing messages only)
_BANNED_PATTERNS = [
    r"\bI(?:'m| am) an? AI\b",
    r"\b(?:artificial intelligence|chatbot|bot|virtual assistant)\b",
    r"\b(?:as an AI|our (?:AI|system|platform|automation))\b",
    r"\bReva\b",
    r"\bDealFlow\b",
    r"\bautomated(?:ly)?\b",
    r"\b<<<EXTRACTED>>>.*",
    r"\bmy (?:programming|training|algorithm)\b",
]

_COMPILED = [re.compile(p, re.IGNORECASE | re.DOTALL) for p in _BANNED_PATTERNS]


def sanitize_lead_message(text: str) -> str:
    """Remove extraction blocks and robotic phrasing from outbound WhatsApp copy."""
    if not text:
        return text

    out = text
    if "<<<EXTRACTED>>>" in out:
        out = out.split("<<<EXTRACTED>>>", 1)[0].strip()

    for pattern in _COMPILED:
        out = pattern.sub("", out)

    return re.sub(r"\n{3,}", "\n\n", out).strip()
