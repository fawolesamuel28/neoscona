"""
Atlantic Horizons Developments — mock inventory catalog (single source of truth).
Used for DB seeding and in-memory matching when Supabase tables are not ready.
"""

from __future__ import annotations

import uuid
from typing import Any

DEVELOPER = {
    "name": "Atlantic Horizons Developments",
    "slug": "atlantic-horizons",
    "tagline": "Premium Lagos living — Ikoyi to Lekki",
}

DEVELOPMENTS: list[dict[str, Any]] = [
    {
        "key": "horizon-ikoyi",
        "name": "Horizon Terraces",
        "phase": "Phase 3",
        "location": "Ikoyi, Lagos",
        "area_tags": ["ikoyi"],
        "description": "Waterfront-inspired apartments with 24hr power and concierge.",
    },
    {
        "key": "marina-vi",
        "name": "Marina View Residences",
        "phase": "Phase 1",
        "location": "Victoria Island, Lagos",
        "area_tags": ["victoria island"],
        "description": "Executive flats walking distance to business district.",
    },
    {
        "key": "greenpark-ajah",
        "name": "GreenPark Estate",
        "phase": "Phase 2",
        "location": "Sangotedo, Ajah",
        "area_tags": ["ajah"],
        "description": "Family-friendly estate with green spaces and flexible payment plans.",
    },
    {
        "key": "lekki-skies",
        "name": "Lekki Skies",
        "phase": "Phase 1",
        "location": "Ibeju-Lekki, Lagos",
        "area_tags": ["ibeju-lekki", "lekki"],
        "description": "Affordable entry homes along the Lekki corridor.",
    },
]

UNITS: list[dict[str, Any]] = [
    {"dev": "horizon-ikoyi", "unit_code": "HT-P3-201", "title": "2-Bed Executive Apartment", "property_type": "flat", "bedrooms": 2, "price_naira": 145_000_000, "size_sqm": 118, "highlights": "City + lagoon view, smart home package", "payment_plan_notes": "20% deposit · balance over 18 months"},
    {"dev": "horizon-ikoyi", "unit_code": "HT-P3-305", "title": "3-Bed Premium Apartment", "property_type": "flat", "bedrooms": 3, "price_naira": 198_000_000, "size_sqm": 165, "highlights": "Corner unit, private balcony", "payment_plan_notes": "20% deposit · balance over 24 months"},
    {"dev": "horizon-ikoyi", "unit_code": "HT-P3-PH1", "title": "4-Bed Penthouse Duplex", "property_type": "duplex", "bedrooms": 4, "price_naira": 320_000_000, "size_sqm": 280, "highlights": "Private terrace, 2 parking bays", "payment_plan_notes": "30% deposit · bespoke payment schedule"},
    {"dev": "horizon-ikoyi", "unit_code": "HT-P3-102", "title": "1-Bed Starter Apartment", "property_type": "flat", "bedrooms": 1, "price_naira": 78_000_000, "size_sqm": 62, "highlights": "Ideal for young professionals", "payment_plan_notes": "15% deposit · 12-month plan"},
    {"dev": "marina-vi", "unit_code": "MV-104", "title": "2-Bed Marina Apartment", "property_type": "flat", "bedrooms": 2, "price_naira": 125_000_000, "size_sqm": 105, "highlights": "Gym, pool, backup power", "payment_plan_notes": "20% deposit · 18 months"},
    {"dev": "marina-vi", "unit_code": "MV-208", "title": "3-Bed Corner Suite", "property_type": "flat", "bedrooms": 3, "price_naira": 175_000_000, "size_sqm": 148, "highlights": "Marina skyline view", "payment_plan_notes": "25% deposit · 24 months"},
    {"dev": "marina-vi", "unit_code": "MV-501", "title": "Studio Loft", "property_type": "flat", "bedrooms": 1, "price_naira": 52_000_000, "size_sqm": 45, "highlights": "Investment-friendly, high rental yield", "payment_plan_notes": "10% deposit · 12 months"},
    {"dev": "greenpark-ajah", "unit_code": "GP-2-12", "title": "2-Bed Terrace Home", "property_type": "flat", "bedrooms": 2, "price_naira": 38_500_000, "size_sqm": 92, "highlights": "Estate security, children's play area", "payment_plan_notes": "10% deposit · 24-month plan"},
    {"dev": "greenpark-ajah", "unit_code": "GP-2-28", "title": "3-Bed Family Apartment", "property_type": "flat", "bedrooms": 3, "price_naira": 52_000_000, "size_sqm": 120, "highlights": "Close to Shoprite Sangotedo", "payment_plan_notes": "15% deposit · 24 months"},
    {"dev": "greenpark-ajah", "unit_code": "GP-3-05", "title": "3-Bed Semi-Detached", "property_type": "duplex", "bedrooms": 3, "price_naira": 68_000_000, "size_sqm": 155, "highlights": "Private garden, BQ option", "payment_plan_notes": "20% deposit · 36 months"},
    {"dev": "greenpark-ajah", "unit_code": "GP-1-08", "title": "1-Bed Apartment", "property_type": "flat", "bedrooms": 1, "price_naira": 22_500_000, "size_sqm": 48, "highlights": "First-time buyer friendly", "payment_plan_notes": "5% deposit · 18 months"},
    {"dev": "greenpark-ajah", "unit_code": "GP-L-03", "title": "500sqm Residential Plot", "property_type": "land", "bedrooms": None, "price_naira": 18_000_000, "size_sqm": 500, "highlights": "Dry land, C of O processing", "payment_plan_notes": "Outright or 6-month plan"},
    {"dev": "lekki-skies", "unit_code": "LS-A-14", "title": "2-Bed Apartment", "property_type": "flat", "bedrooms": 2, "price_naira": 28_000_000, "size_sqm": 78, "highlights": "Near Dangote Refinery corridor", "payment_plan_notes": "10% deposit · 24 months"},
    {"dev": "lekki-skies", "unit_code": "LS-A-22", "title": "3-Bed Apartment", "property_type": "flat", "bedrooms": 3, "price_naira": 36_500_000, "size_sqm": 98, "highlights": "Estate road completed Q3", "payment_plan_notes": "10% deposit · 30 months"},
    {"dev": "lekki-skies", "unit_code": "LS-B-07", "title": "2-Bed Bungalow", "property_type": "bungalow", "bedrooms": 2, "price_naira": 32_000_000, "size_sqm": 85, "highlights": "Standalone, parking for 2 cars", "payment_plan_notes": "15% deposit · 24 months"},
    {"dev": "lekki-skies", "unit_code": "LS-C-01", "title": "Commercial Shop Unit", "property_type": "commercial", "bedrooms": None, "price_naira": 45_000_000, "size_sqm": 65, "highlights": "High foot traffic frontage", "payment_plan_notes": "25% deposit · 12 months"},
    {"dev": "horizon-ikoyi", "unit_code": "HT-P3-SOLD", "title": "2-Bed (Sold)", "property_type": "flat", "bedrooms": 2, "price_naira": 140_000_000, "status": "sold"},
    {"dev": "greenpark-ajah", "unit_code": "GP-2-99", "title": "2-Bed (Reserved)", "property_type": "flat", "bedrooms": 2, "price_naira": 37_000_000, "status": "reserved"},
]

# Stable UUIDs so fallback IDs match after DB seed
_DEV_IDS = {d["key"]: str(uuid.uuid5(uuid.NAMESPACE_DNS, f"reva.dev.{d['key']}")) for d in DEVELOPMENTS}
_UNIT_IDS = {
    f"{u['dev']}:{u['unit_code']}": str(uuid.uuid5(uuid.NAMESPACE_DNS, f"reva.unit.{u['dev']}.{u['unit_code']}"))
    for u in UNITS
}


def catalog_units_for_matching() -> list[dict[str, Any]]:
    """Flat unit list shaped like Supabase join response."""
    dev_by_key = {d["key"]: d for d in DEVELOPMENTS}
    out: list[dict[str, Any]] = []
    for u in UNITS:
        if u.get("status", "available") != "available":
            continue
        dev = dev_by_key[u["dev"]]
        out.append({
            "id": _UNIT_IDS[f"{u['dev']}:{u['unit_code']}"],
            "unit_code": u["unit_code"],
            "title": u["title"],
            "property_type": u["property_type"],
            "bedrooms": u.get("bedrooms"),
            "price_naira": u["price_naira"],
            "status": "available",
            "highlights": u.get("highlights"),
            "payment_plan_notes": u.get("payment_plan_notes"),
            "developments": {
                "name": dev["name"],
                "phase": dev.get("phase"),
                "location": dev["location"],
                "area_tags": dev["area_tags"],
            },
        })
    return out
