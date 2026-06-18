"""
Portal Email Intercept — Gmail API integration.
Polls inbox for PropertyPro / Nigeria Property Centre lead notification emails,
extracts phone numbers and property interest, then initiates WhatsApp outreach.
"""
import os
import re
import base64
from app.core.logger import get_logger
from app.db.supabase import get_supabase
from app.services.messaging import send_outbound_message

logger = get_logger(__name__)

# Email sender patterns for property portal notifications
PORTAL_SENDERS = [
    "noreply@propertypro.ng",
    "leads@propertypro.ng",
    "notifications@nigeriapropertycentre.com",
    "noreply@nigeriapropertycentre.com",
]

# Phone number patterns in Nigerian format
PHONE_PATTERNS = [
    r'(?:\+?234|0)([789]\d{9})',          # +2348012345678 or 08012345678
    r'(?:tel|phone|mobile|call)[\s:]*(?:\+?234|0)([789]\d{9})',
]


class PortalInterceptService:
    def __init__(self):
        self.gmail_service = None

    def _get_gmail_service(self):
        """Lazy-initialize the Gmail API client."""
        if self.gmail_service:
            return self.gmail_service
        
        try:
            from google.oauth2.credentials import Credentials
            from googleapiclient.discovery import build

            creds_json = os.getenv("GOOGLE_CREDENTIALS_JSON")
            token_json = os.getenv("GOOGLE_TOKEN_JSON")
            
            if not creds_json or not token_json:
                logger.warning("Gmail API credentials not configured. Portal sync disabled.")
                return None

            import json
            token_data = json.loads(token_json)
            creds = Credentials.from_authorized_user_info(token_data)
            self.gmail_service = build("gmail", "v1", credentials=creds)
            return self.gmail_service
        except Exception as e:
            logger.error(f"Failed to initialize Gmail service: {e}")
            return None

    def _extract_phone_numbers(self, text: str) -> list[str]:
        """Extract Nigerian phone numbers from email body."""
        phones = []
        for pattern in PHONE_PATTERNS:
            matches = re.findall(pattern, text)
            for match in matches:
                # Normalize to 234XXXXXXXXXX format
                normalized = f"234{match}"
                if normalized not in phones:
                    phones.append(normalized)
        return phones

    def _extract_property_interest(self, subject: str, body: str) -> dict:
        """Extract property interest signals from the email."""
        combined = f"{subject} {body}".lower()
        
        interest = {
            "source": "portal_email",
            "location": None,
            "property_type": None,
            "budget": None,
        }

        # Common Lagos locations
        locations = ["lekki", "ikoyi", "vi", "victoria island", "ajah", "ikeja",
                     "yaba", "surulere", "sangotedo", "epe", "ibeju", "banana island"]
        for loc in locations:
            if loc in combined:
                interest["location"] = loc.title()
                break

        # Property types
        types = {"apartment": "apartment", "flat": "apartment", "duplex": "duplex",
                 "terrace": "terrace", "detached": "detached", "semi-detached": "semi-detached",
                 "penthouse": "penthouse", "studio": "studio", "bungalow": "bungalow"}
        for key, val in types.items():
            if key in combined:
                interest["property_type"] = val
                break

        return interest

    async def poll_portal_leads(self):
        """
        Main polling function. Called by the portal_sync cron job every 15 minutes.
        """
        service = self._get_gmail_service()
        if not service:
            logger.info("Gmail service not available. Skipping portal sync.")
            return

        try:
            # Build query for unread emails from portal senders
            sender_query = " OR ".join(f"from:{s}" for s in PORTAL_SENDERS)
            query = f"is:unread ({sender_query})"

            results = service.users().messages().list(
                userId="me", q=query, maxResults=20
            ).execute()
            
            messages = results.get("messages", [])
            logger.info(f"Portal sync: found {len(messages)} unread portal emails")

            db = get_supabase()
            
            for msg_meta in messages:
                try:
                    msg = service.users().messages().get(
                        userId="me", id=msg_meta["id"], format="full"
                    ).execute()

                    # Extract subject
                    headers = {h["name"]: h["value"] for h in msg.get("payload", {}).get("headers", [])}
                    subject = headers.get("Subject", "")

                    # Extract body
                    body = self._decode_email_body(msg.get("payload", {}))

                    # Extract phone numbers
                    phones = self._extract_phone_numbers(body)
                    if not phones:
                        logger.debug(f"No phone numbers found in portal email: {subject}")
                        # Still mark as read to avoid re-processing
                        self._mark_as_read(service, msg_meta["id"])
                        continue

                    # Extract property interest
                    interest = self._extract_property_interest(subject, body)

                    for phone in phones:
                        # Check if lead already exists
                        existing = db.table("leads").select("id").eq("phone_number", phone).execute()
                        if existing.data:
                            logger.info(f"Portal lead {phone} already exists. Skipping outreach.")
                            continue

                        # Initiate outreach
                        location = interest.get("location", "Lagos")
                        outreach_msg = (
                            f"Hi! 👋 This is Amara from Reva Properties.\n\n"
                            f"I noticed you were looking at properties in {location} on one of the listing sites. "
                            f"We have some great options that might interest you!\n\n"
                            f"What's your budget range? I can send you our best matches right away. 🏠"
                        )
                        await send_outbound_message(phone, outreach_msg, "whatsapp_organic")
                        logger.info(f"Portal outreach sent to {phone} (source: {subject[:50]})")

                    # Mark email as read
                    self._mark_as_read(service, msg_meta["id"])

                except Exception as e:
                    logger.error(f"Failed to process portal email {msg_meta['id']}: {e}")

        except Exception as e:
            logger.error(f"Portal sync polling failed: {e}")

    def _decode_email_body(self, payload: dict) -> str:
        """Recursively decode email body from Gmail API payload."""
        body = ""
        if "body" in payload and payload["body"].get("data"):
            body = base64.urlsafe_b64decode(payload["body"]["data"]).decode("utf-8", errors="ignore")
        
        for part in payload.get("parts", []):
            if part.get("mimeType", "").startswith("text/"):
                if part.get("body", {}).get("data"):
                    body += base64.urlsafe_b64decode(part["body"]["data"]).decode("utf-8", errors="ignore")
            elif "parts" in part:
                body += self._decode_email_body(part)
        
        return body

    def _mark_as_read(self, service, msg_id: str):
        """Mark email as read to avoid re-processing."""
        try:
            service.users().messages().modify(
                userId="me", id=msg_id,
                body={"removeLabelIds": ["UNREAD"]}
            ).execute()
        except Exception as e:
            logger.error(f"Failed to mark email {msg_id} as read: {e}")


portal_service = PortalInterceptService()
