"""
Rule-based intent extraction engine.
Complements the LLM's <<<EXTRACTED>>> block with deterministic fallback
for intent, urgency, and budget extraction from natural language.
"""
import re
from app.core.logger import get_logger

logger = get_logger(__name__)

class IntentExtractor:
    """
    Deterministic NLP extractor for Nigerian real estate conversations.
    """

    INTENT_KEYWORDS = {
        "buying": ["buy", "purchase", "own", "acquire", "get a place", "looking for", "want a house",
                    "want a flat", "want an apartment", "want a property", "i need a"],
        "renting": ["rent", "lease", "short let", "shortlet", "looking to rent", "monthly"],
        "investing": ["invest", "ROI", "return", "portfolio", "appreciation", "off-plan",
                      "passive income", "rental yield", "capital appreciation"],
        "browsing": ["just looking", "curious", "checking", "browsing", "window shopping",
                     "not sure yet", "exploring"],
    }

    URGENCY_KEYWORDS = {
        "immediate": ["today", "now", "asap", "immediately", "this week", "urgent",
                      "right away", "as soon as possible"],
        "short_term": ["this month", "next week", "soon", "within a month", "few weeks",
                       "end of month"],
        "medium_term": ["next month", "few months", "quarter", "Q1", "Q2", "Q3", "Q4",
                        "3 months", "6 months"],
        "long_term": ["next year", "planning ahead", "future", "not in a rush", "no hurry",
                      "saving up", "1 year", "2 years"],
    }

    BUDGET_PATTERNS = [
        # ₦150M, ₦150m, N150M
        (r'[₦N]\s?(\d+(?:\.\d+)?)\s*[mM]', lambda m: float(m.group(1)) * 1_000_000),
        # ₦150,000,000
        (r'[₦N]\s?([\d,]+)', lambda m: float(m.group(1).replace(",", ""))),
        # 150 million naira
        (r'(\d+(?:\.\d+)?)\s*million\s*(?:naira)?', lambda m: float(m.group(1)) * 1_000_000),
        # 150m naira
        (r'(\d+(?:\.\d+)?)\s*[mM]\s*(?:naira)?', lambda m: float(m.group(1)) * 1_000_000),
        # 50k
        (r'(\d+(?:\.\d+)?)\s*[kK]', lambda m: float(m.group(1)) * 1_000),
    ]

    LOCATION_ALIASES = {
        "ikoyi": ["ikoyi", "banana island", "old ikoyi"],
        "vi": ["victoria island", " vi ", " v.i ", "oniru"],
        "lekki": ["lekki", "lekki phase 1", "lekki phase 2", "chevron", "osapa", "agungi"],
        "ajah": ["ajah", "sangotedo", "abijo", "badore"],
        "ibeju": ["ibeju-lekki", "ibeju", "eleko", "awoyaya"],
        "epe": ["epe"],
        "ikeja": ["ikeja", "gra ikeja", "maryland"],
        "surulere": ["surulere"],
        "gbagada": ["gbagada"],
    }

    PROPERTY_TYPE_KEYWORDS = {
        "apartment": ["apartment", "flat", "condo", "penthouse"],
        "duplex": ["duplex", "semi-detached", "fully detached", "terrace"],
        "land": ["land", "plot", "acre"],
        "commercial": ["office", "warehouse", "shop", "mall"],
    }

    def extract_location(self, text: str) -> str | None:
        text_lower = text.lower()
        for canonical, aliases in self.LOCATION_ALIASES.items():
            if any(alias in text_lower for alias in aliases):
                return canonical.capitalize()
        return None

    def extract_property_type(self, text: str) -> str | None:
        text_lower = text.lower()
        for ptype, keywords in self.PROPERTY_TYPE_KEYWORDS.items():
            if any(kw in text_lower for kw in keywords):
                return ptype
        return None

    def extract_intent(self, text: str) -> str:
        text_lower = text.lower()
        scores = {}
        for intent, keywords in self.INTENT_KEYWORDS.items():
            score = sum(1 for kw in keywords if kw in text_lower)
            if score > 0:
                scores[intent] = score
        
        if scores:
            return max(scores, key=scores.get)
        return "unknown"

    def extract_urgency(self, text: str) -> str:
        text_lower = text.lower()
        for urgency, keywords in self.URGENCY_KEYWORDS.items():
            if any(kw in text_lower for kw in keywords):
                return urgency
        return "unknown"

    def extract_budget(self, text: str) -> float | None:
        for pattern, parser in self.BUDGET_PATTERNS:
            match = re.search(pattern, text)
            if match:
                try:
                    return parser(match)
                except (ValueError, TypeError):
                    continue
        return None

    def extract_all(self, text: str) -> dict:
        """
        Main entry point. Returns a dict with intent, urgency, budget, location, and type.
        """
        result = {
            "intent": self.extract_intent(text),
            "urgency": self.extract_urgency(text),
            "budget": self.extract_budget(text),
            "location": self.extract_location(text),
            "property_type": self.extract_property_type(text),
        }
        logger.info(f"Extractor result: {result}")
        return result

extractor = IntentExtractor()
