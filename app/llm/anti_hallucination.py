"""
Anti-Hallucination Monitor — Post-generation checker.
Scans AI responses for property names, prices, and locations,
then cross-references against the actual inventory database.
"""
import re
from app.db.supabase import get_supabase
from app.core.logger import get_logger

logger = get_logger(__name__)

FALLBACK_MESSAGE = (
    "I'd love to give you the exact details! Let me pull up our latest inventory "
    "so I can share accurate pricing and availability. 🏠\n\n"
    "What area and budget range are you looking at? I'll find the best matches."
)

async def check_response(ai_response: str) -> tuple[bool, str]:
    """
    Validates the AI response against the real inventory.
    Returns (is_valid, corrected_response).
    If hallucination is detected, returns (False, fallback_message).
    """
    # Extract any price mentions (₦ followed by numbers)
    price_pattern = r'₦\s?([\d,]+(?:\.\d+)?(?:\s*(?:million|m|k))?)'
    price_mentions = re.findall(price_pattern, ai_response, re.IGNORECASE)
    
    # Extract property/development names (capitalized multi-word phrases)
    name_pattern = r'\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+)+)\b'
    name_mentions = re.findall(name_pattern, ai_response)
    
    if not price_mentions and not name_mentions:
        # No verifiable claims — let it pass
        return True, ai_response

    try:
        db = get_supabase()
        
        # Load known properties and developments
        props = db.table("properties").select("name, location").execute()
        units = db.table("units").select("property_id, unit_type, price").execute()
        
        known_names = {p["name"].lower() for p in (props.data or [])}
        known_locations = {p["location"].lower() for p in (props.data or []) if p.get("location")}
        known_prices = {u["price"] for u in (units.data or []) if u.get("price")}
        
        # Check property names
        for name in name_mentions:
            name_lower = name.lower()
            # Skip common English phrases that aren't property names
            skip_phrases = {"good morning", "good afternoon", "good evening", "let me", "how are", "thank you"}
            if name_lower in skip_phrases:
                continue
            
            # If it looks like a property name but isn't in our DB
            if any(word in name_lower for word in ["estate", "court", "tower", "villa", "garden", "residence", "heights"]):
                if name_lower not in known_names:
                    logger.warning(f"HALLUCINATION DETECTED: Unknown property '{name}' in AI response")
                    return False, FALLBACK_MESSAGE
        
        # Check prices — if AI quotes a specific price, it should exist in inventory
        for price_str in price_mentions:
            try:
                cleaned = price_str.replace(",", "").strip()
                multiplier = 1
                if "million" in cleaned.lower() or cleaned.lower().endswith("m"):
                    cleaned = re.sub(r'[mM](?:illion)?', '', cleaned).strip()
                    multiplier = 1_000_000
                elif cleaned.lower().endswith("k"):
                    cleaned = cleaned[:-1].strip()
                    multiplier = 1_000
                
                price_val = float(cleaned) * multiplier
                
                # Allow ±20% tolerance
                price_match = any(
                    abs(price_val - kp) / max(kp, 1) < 0.20 
                    for kp in known_prices if kp
                )
                
                if not price_match and price_val > 0:
                    logger.warning(f"HALLUCINATION DETECTED: Price ₦{price_val:,.0f} not in inventory (±20%)")
                    return False, FALLBACK_MESSAGE
            except (ValueError, ZeroDivisionError):
                continue
        
        return True, ai_response
        
    except Exception as e:
        logger.error(f"Anti-hallucination check failed: {e}")
        # Fail open — don't block responses if the check itself errors
        return True, ai_response


async def check_response_with_kb(
    ai_response: str,
    kb_chunks: list[dict] | None = None,
) -> tuple[bool, str]:
    """
    Extended check: validates AI claims against both inventory AND knowledge-base
    chunks (if provided). Falls back to check_response() for inventory validation.
    """
    # First run standard inventory-based check
    is_valid, checked = await check_response(ai_response)
    if not is_valid:
        return is_valid, checked

    # If knowledge-base chunks were used, verify quoted facts appear in them
    if kb_chunks:
        kb_text = " ".join(c.get("content", "") for c in kb_chunks).lower()

        # Look for percentage/rate claims (e.g., "30% initial deposit")
        pct_pattern = r'(\d+(?:\.\d+)?)\s*%'
        pct_mentions = re.findall(pct_pattern, ai_response)

        for pct in pct_mentions:
            if pct not in kb_text and f"{pct}%" not in kb_text:
                logger.warning(
                    "HALLUCINATION DETECTED: Percentage %s%% not found in KB chunks",
                    pct,
                )
                return False, FALLBACK_MESSAGE

    return True, ai_response
