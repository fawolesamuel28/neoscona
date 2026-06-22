"""Plan catalog — the single source of truth for limits, feature gates, and NGN pricing.

Plans live in code (not the DB) so quotas and feature flags are versioned and unit-testable.
Paystack plan codes are injected from env so the same catalog works across test/live keys.

Amounts are NGN kobo (Paystack's minor unit: ₦1 = 100 kobo). `unlimited` limits are None.
"""

from __future__ import annotations

import os
from typing import Optional

# A feature flag is "on" for a plan when present in its `features` set.
FEATURES = ("whatsapp", "voice", "followups", "calendar", "api", "priority_sla")


PLANS: dict[str, dict] = {
    # 14-day trial mirrors Growth features so prospects experience the full product.
    "trial": {
        "label": "Free trial",
        "price_kobo": 0,
        "price_ngn": 0,
        "limits": {"messages": 2_000, "seats": 3, "voice_minutes": 500},
        "features": {"whatsapp", "voice", "followups", "calendar"},
        "selectable": False,  # not directly purchasable
    },
    "starter": {
        "label": "Starter",
        "price_kobo": 35_000_00,   # ₦35,000 / mo
        "price_ngn": 35000,
        "limits": {"messages": 500, "seats": 1, "voice_minutes": 0},
        "features": {"whatsapp"},
        "selectable": True,
    },
    "growth": {
        "label": "Growth",
        "price_kobo": 180_000_00,  # ₦180,000 / mo
        "price_ngn": 180000,
        "limits": {"messages": 2_000, "seats": 3, "voice_minutes": 500},
        "features": {"whatsapp", "voice", "followups", "calendar"},
        "selectable": True,
    },
    "scale": {
        "label": "Scale",
        "price_kobo": None,        # custom / contact sales
        "price_ngn": None,
        "limits": {"messages": None, "seats": None, "voice_minutes": None},
        "features": set(FEATURES),
        "selectable": False,
    },
}

DEFAULT_PLAN = "trial"


def get_plan(plan: Optional[str]) -> dict:
    """Return the plan config, falling back to the trial plan for unknown/empty values."""
    return PLANS.get((plan or "").lower(), PLANS[DEFAULT_PLAN])


def plan_for(tenant: dict) -> dict:
    """Resolve the plan config for a tenant row (uses its `plan` column)."""
    return get_plan((tenant or {}).get("plan"))


def limit(plan: Optional[str], key: str) -> Optional[int]:
    """Quota for a metered key ('messages'|'seats'|'voice_minutes'). None = unlimited."""
    return get_plan(plan)["limits"].get(key)


def feature_enabled(plan: Optional[str], feature: str) -> bool:
    return feature in get_plan(plan)["features"]



def naira(price_kobo: Optional[int]) -> Optional[int]:
    """Convert kobo to whole naira for display (None stays None for custom plans)."""
    return None if price_kobo is None else price_kobo // 100


def plan_amount_ngn(plan: Optional[str]) -> Optional[int]:
    """Return the whole Naira amount for a plan (preferred for Flutterwave operations)."""
    p = get_plan(plan)
    # Prefer explicit `price_ngn` when present, otherwise convert `price_kobo`.
    if p.get("price_ngn") is not None:
        return p.get("price_ngn")
    return None if p.get("price_kobo") is None else p.get("price_kobo") // 100


def public_catalog() -> list[dict]:
    """Browser-safe plan list for the pricing/onboarding UI (no env secrets)."""
    out = []
    for key, p in PLANS.items():
        out.append({
            "id": key,
            "label": p["label"],
            "price_naira": naira(p["price_kobo"]),
            "limits": p["limits"],
            "features": sorted(p["features"]),
            "selectable": p["selectable"],
        })
    return out


def trial_days() -> int:
    try:
        return int(os.getenv("TRIAL_DAYS", "14"))
    except ValueError:
        return 14

