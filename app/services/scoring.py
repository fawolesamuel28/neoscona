import datetime
import logging
from typing import Dict, Any, List
from app.cache.redis import get_redis_client
from app.core.logger import get_logger

logger = get_logger(__name__)

class ScoringEngine:
    """
    Calculates a multi-dimensional lead score.
    Higher score = higher priority / more likely to convert.
    """

    # Signals that indicate high intent
    HOT_KEYWORDS = [
        "payment plan", "installment", "visit", "viewing", "C of O", 
        "Governor's Consent", "title", "when can I", "interested in",
        "price list", "discount", "inspection"
    ]

    @staticmethod
    async def calculate_behavioural_score(phone_number: str, current_message: str) -> float:
        """
        Signals:
        1. Keyword density (max 3 points)
        2. Response speed (max 4 points)
        3. Message volume (max 3 points)
        """
        score = 0.0

        # 1. Keywords
        keyword_hits = [k for k in ScoringEngine.HOT_KEYWORDS if k.lower() in current_message.lower()]
        score += min(3.0, len(keyword_hits) * 0.75)

        # 2. Timing/Speed (requires history)
        try:
            redis = await get_redis_client()
            last_msg_time_str = await redis.get(f"lead:{phone_number}:last_msg_time")
            now = datetime.datetime.now()
            
            if last_msg_time_str:
                last_time = datetime.datetime.fromisoformat(last_msg_time_str)
                delta = (now - last_time).total_seconds()
                
                # If they respond within 2 mins, they are engaged
                if delta < 120:
                    score += 4.0
                elif delta < 600: # Within 10 mins
                    score += 2.0
                elif delta < 3600: # Within 1 hour
                    score += 1.0
            
            # Update last message time
            await redis.set(f"lead:{phone_number}:last_msg_time", now.isoformat())

            # 3. Message volume — track total messages sent by this lead
            vol_key = f"lead:{phone_number}:msg_count"
            msg_count = await redis.incr(vol_key)
            if msg_count == 1:
                await redis.expire(vol_key, 86400 * 7)  # 7-day window
            
            if msg_count >= 15:
                score += 3.0
            elif msg_count >= 8:
                score += 2.0
            elif msg_count >= 3:
                score += 1.0

        except Exception as e:
            logger.error(f"Failed to calculate speed/volume score: {e}")

        return score

    @classmethod
    async def get_combined_score(cls, phone_number: str, message: str, seriousness_score: int) -> int:
        """
        seriousness_score: 1-10 from LLM
        behavioural_score: 0-10 from rules
        Combined: weighted average (60% AI, 40% Behavioural)
        """
        beh_score = await cls.calculate_behavioural_score(phone_number, message)
        
        # Scale beh_score if it exceeds 10 (unlikely with current weights)
        beh_score = min(10.0, beh_score)
        
        combined = (seriousness_score * 0.6) + (beh_score * 0.4)
        final_score = round(combined)
        
        logger.info(f"Scoring lead {phone_number}: AI={seriousness_score}, BEH={beh_score:.1f}, FINAL={final_score}")
        
        return max(1, min(10, final_score))

scoring_engine = ScoringEngine()
