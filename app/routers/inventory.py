from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Query

from app.core.auth import Principal, get_current_principal
from app.models.inventory import LeadMatchRequest
from app.services.inventory import fetch_available_units, get_lead_matches, match_units

logger = logging.getLogger(__name__)
router = APIRouter(tags=["Inventory"])

# Tenant scoping relies on the tenant_id columns added in migration 010
# (units / lead_unit_matches, defaulting to the seeded tenant). The Supabase
# catalog fallback in fetch_available_units is not tenant-aware (single dataset).


@router.get("/units")
async def list_units(
    status: str = Query(default="available"),
    principal: Principal = Depends(get_current_principal),
):
    """List inventory units (for dashboard / admin)."""
    units = await fetch_available_units(tenant_id=principal.tenant_id)
    if status != "available":
        return {"units": units, "total": len(units)}
    return {"units": units, "total": len(units)}


@router.post("/inventory/match")
async def match_inventory(
    body: LeadMatchRequest,
    principal: Principal = Depends(get_current_principal),
):
    """Preview unit matches for a lead profile (testing / dashboard)."""
    matches = await match_units(
        budget=body.budget,
        location=body.location,
        property_type=body.property_type,
        limit=body.limit,
        tenant_id=principal.tenant_id,
    )
    return {"matches": [m.model_dump() for m in matches], "total": len(matches)}


@router.get("/leads/{phone_number}/matches")
async def lead_matches(
    phone_number: str,
    principal: Principal = Depends(get_current_principal),
):
    """Units previously offered to a lead."""
    matches = await get_lead_matches(phone_number, tenant_id=principal.tenant_id)
    return {"phone_number": phone_number, "matches": matches}
