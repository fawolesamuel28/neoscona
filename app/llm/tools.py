"""
Tool definitions for Reva — allows the LLM to search inventory, book meetings, etc.
"""

DEALFLOW_TOOLS = [
    {
        "name": "search_inventory",
        "description": "Search available properties matching lead criteria. Call this the moment you have budget, location, and property type.",
        "input_schema": {
            "type": "object",
            "properties": {
                "location": {"type": "string"},
                "property_type": {"type": "string"},
                "max_budget_naira": {"type": "number"},
                "bedrooms": {"type": "integer"}
            },
            "required": ["location", "property_type", "max_budget_naira"]
        }
    },
    {
        "name": "book_meeting",
        "description": "Book a consultation meeting for this lead and provide a direct Calendly booking link. Call this when lead is qualified OR when no inventory matches — never leave them hanging.",
        "input_schema": {
            "type": "object",
            "properties": {
                "phone_number": {"type": "string"},
                "preferred_time": {"type": "string"},
                "notes": {"type": "string"}
            },
            "required": ["phone_number"]
        }
    },
    {
        "name": "send_properties",
        "description": "Send formatted property listings to the lead on WhatsApp.",
        "input_schema": {
            "type": "object",
            "properties": {
                "phone_number": {"type": "string"},
                "property_ids": {
                    "type": "array",
                    "items": {"type": "string"}
                }
            },
            "required": ["phone_number", "property_ids"]
        }
    },
    {
        "name": "search_knowledge_base",
        "description": (
            "Search the company's knowledge base for information about payment plans, "
            "FAQs, brochures, policies, or any topic not covered by live inventory data. "
            "Call this when the lead asks about documents, terms, processes, or anything "
            "beyond property listings."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Natural-language search query describing what the lead wants to know."
                }
            },
            "required": ["query"]
        }
    },
]
