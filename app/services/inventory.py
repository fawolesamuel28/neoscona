"""
Inventory matching for Reva — filters developer units by lead profile.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
from typing import Any

from app.core.tenant import apply_tenant_defaults, require_tenant
from app.db.supabase import get_supabase
from app.models.inventory import UnitMatch

logger = logging.getLogger(__name__)

# Lagos area aliases for fuzzy location matching
AREA_ALIASES: dict[str, list[str]] = {
    "ikoyi": ["ikoyi", "old ikoyi", "banana island"],
    "victoria island": ["victoria island", "vi", "v.i", "oniru"],
    "lekki": ["lekki", "lekki phase 1", "lekki phase 2", "chevron", "osapa"],
    "ajah": ["ajah", "sangotedo", "abijo", "lakowe"],
    "ibeju-lekki": ["ibeju", "ibeju-lekki", "eleko", "awoyaya"],
    "epe": ["epe", "lagos epe"],
}

BEDROOM_PATTERNS = [
    (r"\b1\s*[-]?\s*bed", 1),
    (r"\bone\s*bed", 1),
    (r"\bstudio\b", 0),
    (r"\b2\s*[-]?\s*bed", 2),
    (r"\btwo\s*bed", 2),
    (r"\b3\s*[-]?\s*bed", 3),
    (r"\bthree\s*bed", 3),
    (r"\b4\s*[-]?\s*bed", 4),
    (r"\bduplex\b", 4),
    (r"\bland\b", -1),
    (r"\bcommercial\b", -2),
]


def parse_budget_naira(budget: str | None) -> tuple[int | None, int | None]:
    """
    Parse Nigerian budget strings into (min, max) naira.
    Examples: "15 million", "15M", "₦12-18m", "around 2.5 million naira"
    """
    if not budget:
        return None, None

    text = budget.lower().replace(",", "").replace("₦", "").replace("naira", "").strip()

    range_match = re.search(
        r"(\d+(?:\.\d+)?)\s*(?:m|million)?\s*[-–to]+\s*(\d+(?:\.\d+)?)\s*(?:m|million)?",
        text,
    )
    if range_match:
        lo = _to_naira(float(range_match.group(1)), text)
        hi = _to_naira(float(range_match.group(2)), text)
        return min(lo, hi), max(lo, hi)

    numbers = re.findall(r"(\d+(?:\.\d+)?)\s*(m|million|k|thousand)?", text)
    if not numbers:
        return None, None

    values = [_to_naira(float(n), u or text) for n, u in numbers]
    if "around" in text or "about" in text or len(values) == 1:
        center = values[0]
        return int(center * 0.85), int(center * 1.15)

    return min(values), max(values)


def _to_naira(amount: float, context: str) -> int:
    ctx = context.lower()
    if "k" in ctx and "m" not in ctx and amount < 1000:
        return int(amount * 1_000)
    if amount < 1000:
        return int(amount * 1_000_000)
    return int(amount)


def parse_bedrooms(property_type: str | None) -> int | None:
    if not property_type:
        return None
    text = property_type.lower()
    for pattern, beds in BEDROOM_PATTERNS:
        if re.search(pattern, text):
            return beds if beds >= 0 else None
    return None


def _normalize_areas(location: str | None) -> set[str]:
    if not location:
        return set()
    loc = location.lower()
    matched: set[str] = set()
    for canonical, aliases in AREA_ALIASES.items():
        if any(alias in loc for alias in aliases):
            matched.add(canonical)
    if not matched:
        matched.add(loc.strip())
    return matched


def _format_price(price: int) -> str:
    if price >= 1_000_000:
        value = price / 1_000_000
        if value == int(value):
            return f"₦{int(value)}M"
        return f"₦{value:.1f}M"
    return f"₦{price:,}"


def _score_unit(
    unit: dict[str, Any],
    budget_min: int | None,
    budget_max: int | None,
    lead_areas: set[str],
    lead_beds: int | None,
    lead_type: str | None,
) -> float:
    score = 0.0
    price = unit["price_naira"]
    dev = unit.get("developments") or {}
    unit_areas = set(dev.get("area_tags") or [])
    loc = (dev.get("location") or "").lower()

    # Location (40 pts)
    location_score = 0
    if lead_areas:
        if lead_areas & unit_areas:
            location_score = 40
        elif any(a in loc for a in lead_areas):
            location_score = 28
        else:
            location_score = 5
    else:
        location_score = 20
    score += location_score

    # Budget (35 pts) — HARD CUTOFF: if price > 1.3x budget_max, penalize heavily
    if budget_max is not None:
        if price > budget_max * 1.3:
            return 0.0 # Way over budget
            
        stretch = int(budget_max * 1.2)
        if budget_min is not None and budget_min <= price <= stretch:
            if budget_min <= price <= budget_max:
                score += 35
            else:
                score += 15 # Slightly over
        elif price <= stretch:
            score += 10
    else:
        score += 18

    # Property type / bedrooms (25 pts)
    unit_beds = unit.get("bedrooms")
    unit_type = (unit.get("property_type") or "").lower()
    lead_type_l = (lead_type or "").lower()

    if lead_beds is not None and unit_beds is not None:
        if unit_beds == lead_beds:
            score += 25
        elif abs(unit_beds - lead_beds) == 1:
            score += 15
    elif lead_type_l:
        if lead_type_l in unit_type or unit_type in lead_type_l:
            score += 22
        elif "apartment" in lead_type_l and unit_type == "flat":
            score += 20
        elif "bedroom" in lead_type_l and unit_beds:
            score += 12
    else:
        score += 10

    return round(score, 1)


async def fetch_available_units(tenant_id: str | None = None) -> list[dict[str, Any]]:
    tenant_id = require_tenant(tenant_id)
    try:
        db = get_supabase()

        def _fetch():
            query = (
                db.table("units")
                .select(
                    "id, unit_code, title, property_type, bedrooms, price_naira, "
                    "status, highlights, payment_plan_notes, "
                    "developments(name, phase, location, area_tags)"
                )
                .eq("status", "available")
                .eq("tenant_id", tenant_id)
            )
            return query.execute()

        result = await asyncio.to_thread(_fetch)
        if result.data:
            return result.data
    except Exception as exc:
        logger.warning("Supabase inventory unavailable, using catalog fallback: %s", exc)

    from app.data.catalog import catalog_units_for_matching

    return catalog_units_for_matching()


def calculate_payment_plan(price: float, upfront_pct: float, months: int) -> dict:
    upfront = price * (upfront_pct / 100)
    balance = price - upfront
    monthly = balance / months

    return {
        "upfront": f"₦{upfront:,.0f}",
        "monthly": f"₦{monthly:,.0f}/month",
        "duration": f"{months} months",
        "total": f"₦{price:,.0f}"
    }


async def search_properties(
    location: str,
    property_type: str,
    max_budget: float,
    bedrooms: int | None = None,
    *,
    tenant_id: str,
) -> list[dict]:
    """
    Unified search using PostgreSQL Full-Text Search and the v_available_inventory view.

    `tenant_id` is REQUIRED and passed to the FTS function as p_tenant_id: this runs
    under the service-role client (RLS bypassed), so the function-level tenant filter
    is the only thing keeping one workspace's catalog out of another's search results.
    """
    tenant_id = require_tenant(tenant_id)
    logger.info(f"FTS search started | tenant={tenant_id} | location={location} | type={property_type} | budget={max_budget} | beds={bedrooms}")
    results = []
    try:
        db = get_supabase()

        # Build search query string for FTS
        query_parts = []
        if location:
            query_parts.append(location)
        if property_type:
            query_parts.append(property_type)

        search_query = " ".join(query_parts)

        # Call the native Postgres function
        def _fetch():
            return db.rpc(
                'search_inventory_fts',
                {
                    'p_tenant_id': tenant_id,
                    'search_query': search_query,
                    'max_budget': max_budget,
                    'target_bedrooms': bedrooms
                }
            ).execute()
            
        result = await asyncio.to_thread(_fetch)
        
        if result.data:
            logger.info(f"FTS returned {len(result.data)} matches")
            for item in result.data:
                results.append({
                    "name": item.get("name"),
                    "location": item.get("location"),
                    "bedrooms": item.get("bedrooms"),
                    "price": item.get("price"),
                    "highlights": item.get("highlights", ""),
                    "source": item.get("source"),
                    "rank": item.get("rank")
                })
        
        return results

    except Exception as e:
        logger.error(f"FTS inventory search failed: {e}")
        return results


def format_property_message(properties: list[dict]) -> str:
    """
    Formats properties into a clean WhatsApp message.
    """
    if not properties:
        return None

    lines = ["Here are our available options that match what you're looking for 🏠\n"]

    for i, prop in enumerate(properties, 1):
        lines.append(
            f"*{i}. {prop.get('name', 'Property')}*\n"
            f"📍 {prop.get('location')}\n"
            f"🛏 {prop.get('bedrooms')} bedrooms\n"
            f"💰 ₦{prop.get('price', 0):,.0f}\n"
            f"✅ {prop.get('highlights', '')}\n"
        )

    lines.append(
        "\nReply with *1*, *2*, or *3* to get more details or book a viewing 👇"
    )

    return "\n".join(lines)


async def handle_inventory_result(
    properties: list[dict],
    phone_number: str,
    lead: dict
) -> str:
    """
    Smart fallback — if no properties found, immediately
    pivot to booking a consultation. Never leave lead hanging.
    """
    if properties:
        return format_property_message(properties)

    # Robust fallbacks for lead data to avoid "None" in response
    p_type = lead.get("property_type") or "property"
    budget = lead.get("budget")
    budget_phrase = f"budget of {budget}" if budget else "budget"
    location = lead.get("location") or "Lagos"

    # Attempt to get real available slots
    from app.services.calendly import get_available_slots
    from datetime import datetime
    
    slots = await get_available_slots(days_ahead=3)
    time_lines = []
    if slots:
        emojis = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣"]
        for i, slot in enumerate(slots[:3]):
            # Calendly returns ISO strings
            dt = datetime.fromisoformat(slot["start_time"].replace("Z", "+00:00"))
            time_str = dt.strftime("%A, %I:%M %p")
            time_lines.append(f"{emojis[i]} {time_str}")
    else:
        # Fallback to static if API fails
        time_lines = [
            "1️⃣ Tomorrow, 10:00 AM",
            "2️⃣ Tomorrow, 2:00 PM",
            "3️⃣ Friday, 11:00 AM"
        ]

    times_display = "\n".join(time_lines)

    # No inventory match — pivot immediately
    return (
        f"We don't have an exact match for your search in our current listings right now, "
        f"but new units come in regularly and some aren't listed publicly yet 🏠\n\n"
        f"Our consultant can show you options that fit your "
        f"{p_type} {budget_phrase} in "
        f"{location} — including off-market ones.\n\n"
        f"Here are the next available times:\n\n"
        f"{times_display}\n\n"
        f"Reply *1*, *2*, or *3* to pick a slot, or click here to book directly: {os.getenv('CALENDLY_EVENT_URL')}\n\n"
        f"Which works best for you? 👇"
    )


async def match_units(
    budget: str | None = None,
    location: str | None = None,
    property_type: str | None = None,
    limit: int = 3,
    tenant_id: str | None = None,
) -> list[UnitMatch]:
    """Return top matching available units for a lead profile."""
    units = await fetch_available_units(tenant_id=tenant_id)
    if not units:
        logger.warning("No available units in inventory")
        return []

    budget_min, budget_max = parse_budget_naira(budget)
    lead_areas = _normalize_areas(location)
    lead_beds = parse_bedrooms(property_type)

    scored: list[tuple[float, dict[str, Any]]] = []
    for unit in units:
        s = _score_unit(unit, budget_min, budget_max, lead_areas, lead_beds, property_type)
        if s >= 25:
            scored.append((s, unit))

    scored.sort(key=lambda x: x[0], reverse=True)

    matches: list[UnitMatch] = []
    for rank, (score, unit) in enumerate(scored[:limit], start=1):
        dev = unit.get("developments") or {}
        matches.append(
            UnitMatch(
                unit_id=unit["id"],
                unit_code=unit["unit_code"],
                title=unit["title"],
                development_name=dev.get("name", "Unknown"),
                phase=dev.get("phase"),
                location=dev.get("location", ""),
                property_type=unit["property_type"],
                bedrooms=unit.get("bedrooms"),
                price_naira=unit["price_naira"],
                price_display=_format_price(unit["price_naira"]),
                highlights=unit.get("highlights"),
                payment_plan_notes=unit.get("payment_plan_notes"),
                match_score=score,
                rank=rank,
            )
        )

    return matches


def format_matches_whatsapp(matches: list[UnitMatch], developer_name: str = "Atlantic Horizons") -> str:
    """Format matched units as Amara personally sharing options."""
    if not matches:
        return (
            "I just checked with our team — nothing that fits exactly right now, "
            "but I'll have someone call you today with fresh options 🙏"
        )

    lines = [
        "Okay so I checked what we have available — these fit what you described:\n"
    ]
    emojis = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣"]
    for i, m in enumerate(matches):
        phase = f" ({m.phase})" if m.phase else ""
        beds = f", {m.bedrooms} bed" if m.bedrooms else ""
        lines.append(
            f"{emojis[i]} *{m.title}*{beds}\n"
            f"📍 {m.development_name}{phase} — {m.location}\n"
            f"💰 {m.price_display}"
        )
        if m.payment_plan_notes:
            lines.append(f"   _{m.payment_plan_notes}_")
        lines.append("")

    lines.append(
        "Which one interests you most? I can set up a site visit for you to see it in person 😊"
    )
    return "\n".join(lines).strip()


async def save_lead_matches(
    phone_number: str,
    matches: list[UnitMatch],
    tenant_id: str | None = None,
) -> None:
    if not matches:
        return

    tenant_id = require_tenant(tenant_id)

    from app.cache.redis import cache_lead_matches

    snapshot = [m.model_dump() for m in matches]
    await cache_lead_matches(tenant_id, phone_number, snapshot)

    try:
        db = get_supabase()
        rows = [
            apply_tenant_defaults({
                "phone_number": phone_number,
                "unit_id": m.unit_id,
                "match_score": m.match_score,
                "rank": m.rank,
            }, tenant_id)
            for m in matches
        ]

        def _insert():
            return db.table("lead_unit_matches").insert(rows).execute()

        await asyncio.to_thread(_insert)
        logger.info("Saved %d unit matches for %s", len(matches), phone_number)
    except Exception as exc:
        logger.warning("Could not persist matches to Supabase (using Redis): %s", exc)


async def get_lead_matches(phone_number: str, tenant_id: str | None = None) -> list[dict[str, Any]]:
    tenant_id = require_tenant(tenant_id)
    try:
        db = get_supabase()

        def _fetch():
            query = (
                db.table("lead_unit_matches")
                .select(
                    "rank, match_score, offered_at, "
                    "units(unit_code, title, price_naira, property_type, bedrooms, "
                    "developments(name, location, phase))"
                )
                .eq("phone_number", phone_number)
                .eq("tenant_id", tenant_id)
            )
            return query.order("rank").execute()

        result = await asyncio.to_thread(_fetch)
        if result.data:
            return result.data
    except Exception as exc:
        logger.debug("lead_unit_matches fetch failed: %s", exc)

    from app.cache.redis import get_cached_lead_matches

    cached = await get_cached_lead_matches(tenant_id, phone_number)
    if not cached:
        return []

    return [
        {
            "rank": m.get("rank"),
            "match_score": m.get("match_score"),
            "units": {
                "unit_code": m.get("unit_code"),
                "title": m.get("title"),
                "price_naira": m.get("price_naira"),
                "property_type": m.get("property_type"),
                "bedrooms": m.get("bedrooms"),
                "developments": {
                    "name": m.get("development_name"),
                    "location": m.get("location"),
                    "phase": m.get("phase"),
                },
            },
        }
        for m in cached
    ]
