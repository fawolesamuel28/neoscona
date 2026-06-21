import asyncio
import os
import logging
from app.core.logger import get_logger

logger = get_logger(__name__)

from app.workers.celery_app import celery_app
from app.models.lead import IncomingMessage
from app.services.leads import log_message, upsert_lead, get_lead
from app.services.agent_notify import assign_agent, notify_agent
from app.services.messaging import send_outbound_message, send_typing_indicator
from app.cache.redis import is_already_done, mark_as_done, reset_redis_pool
from app.services.conversation import build_conversation_context, save_ai_response, advance_stage_if_ready
from app.llm.client import get_ai_response
from app.services.agent_commands import handle_agent_command
from app.core.dashboard_events import notify_dashboard_update
from app.core.tenant import require_tenant

async def _process_message_async(phone_number: str, message: str, message_id: str, message_type: str, source: str, media_url: str = None, tenant_id: str = None):
    """
    Detailed message processing pipeline.

    `tenant_id` is resolved by the gateway from the channel registry and is REQUIRED
    — it scopes every read/write so a message never lands in the wrong workspace.
    """

    # 0. Tenant chokepoint — the gateway guarantees this; fail loudly if it didn't.
    tenant_id = require_tenant(tenant_id)

    # 1. Worker-level Idempotency Check (prevent re-processing 'done' messages)
    if await is_already_done(message_id):
        logger.info("Message %s already processed. Skipping.", message_id)
        return

    # 1.1 Agent Command Check
    is_command = await handle_agent_command(phone_number, message, tenant_id=tenant_id)
    if is_command:
        await mark_as_done(message_id)
        return

    incoming = IncomingMessage(
        phone_number=phone_number,
        message=message,
        message_id=message_id,
        message_type=message_type,
        source=source,
        media_url=media_url
    )

    try:
        # 2. Process Voice Notes if any
        if message_type == "audio" and media_url:
            from app.services.transcription import transcribe_voice_note 
            transcript = await transcribe_voice_note(media_url)
            incoming.message = f"[VOICE_NOTE]: {transcript}"

        # 3. Log Inbound
        await log_message(incoming.phone_number, "user", incoming.message, tenant_id=tenant_id)

        # 4. Build Context
        context = await build_conversation_context(incoming, tenant_id=tenant_id)

        # Ensure every inbound message creates/updates a dashboard lead row
        await upsert_lead(
            incoming.phone_number,
            {"source": incoming.source},
            stage=context.get("stage") or "new",
            tenant_id=tenant_id,
        )
        
        # 5. Check for PAUSE state
        lead = context.get("lead_data", {})
        if lead.get("is_paused"):
            logger.info(f"Lead {phone_number} is PAUSED. Skipping AI response.")
            return

        # 5.1 Plan entitlement gate — skip the AI reply when the tenant's plan or
        # subscription disallows it (soft by default; see app/core/entitlements.py).
        # Use the channel-resolved tenant, not the lead row's (authoritative source).
        from app.core.entitlements import reply_allowed
        decision = await reply_allowed(tenant_id)
        if not decision.allowed:
            logger.info(
                "Reply gated for %s (%s). Skipping AI response.",
                phone_number, decision.reason,
            )
            await mark_as_done(message_id)
            return

        # 6. Handle Media Sentinals
        MEDIA_RESPONSES = {
            "image": "I can see you sent an image! Our agent will take a look. For now, what area are you looking at? 😊",
            "audio": "I noticed you sent a voice note — I've transcribed it for our records. How can I help further?",
            "document": "Got your document! Our agent will review it. What type of property are you looking for?",
            "video": "Thanks for the video! What location are you considering?"
        }
        
        if message_type in MEDIA_RESPONSES and not (message_type == "audio" and media_url):
            media_msg = MEDIA_RESPONSES[message_type]
            await log_message(incoming.phone_number, "assistant", media_msg, tenant_id=tenant_id)
            await upsert_lead(
                incoming.phone_number,
                {"source": incoming.source},
                stage=context.get("stage") or "qualifying",
                tenant_id=tenant_id,
            )
            await send_outbound_message(incoming.phone_number, media_msg, incoming.source)
            await mark_as_done(message_id)
            return

        # 7. Typing Indicator
        await send_typing_indicator(incoming.phone_number, incoming.source)

        # 7.1 Behavioural Intel (Scoring + Physical Extraction)
        from app.services.scoring import scoring_engine
        from app.services.extractor import extractor
        
        beh_intel = extractor.extract_all(incoming.message)
        logger.info(f"Extracted intent for {phone_number}: {beh_intel}")

        # 8. AI Response
        ai_message, extracted_data = await get_ai_response(
            messages=context["history"],
            stage=context["stage"],
            phone_number=incoming.phone_number,
            lead_data=context["lead_data"],
            tenant_id=tenant_id,
        )

        # 10. Update Lead Data
        # Merge LLM data with Rule-based data
        merged_lead_data = {
            **context["lead_data"],
            **{k: v for k, v in extracted_data.items() if v is not None},
            "intent": beh_intel["intent"] if beh_intel["intent"] != "unknown" else context["lead_data"].get("intent"),
            "urgency": beh_intel["urgency"],
            "source": incoming.source,
            "tenant_id": tenant_id,
        }
        
        # 10.1 Combined Scoring
        llm_score = extracted_data.get("seriousness_score") or 5
        final_score = await scoring_engine.get_combined_score(
            incoming.phone_number,
            incoming.message,
            llm_score,
            tenant_id=tenant_id,
        )
        merged_lead_data["seriousness_score"] = final_score

        # 11. State Machine Transition
        new_stage = await advance_stage_if_ready(
            incoming.phone_number,
            context["stage"],
            merged_lead_data,
            tenant_id=tenant_id,
        )

        # 12. Persist
        await save_ai_response(
            incoming.phone_number,
            context["history"],
            ai_message,
            extracted_data,
            tenant_id=tenant_id,
        )
        
        await upsert_lead(incoming.phone_number, merged_lead_data, new_stage, tenant_id=tenant_id)
        await log_message(incoming.phone_number, "assistant", ai_message, tenant_id=tenant_id)

        # 12.1 Hot Lead Routing
        score = merged_lead_data.get("seriousness_score", 0)
        if score >= 8:
            logger.info(f"HOT LEAD detected: {phone_number} (Score: {score})")
            senior_agent = await assign_agent(merged_lead_data.get("development_id"), tenant_id=tenant_id)
            if senior_agent:
                await notify_agent(merged_lead_data, senior_agent)

        # 13. Send Reply
        await send_outbound_message(incoming.phone_number, ai_message, incoming.source)

        # 13.1 Meter the outbound AI reply (all channels converge here). Fire-and-forget.
        from app.services.usage import record_usage
        await record_usage(tenant_id, "message")

        # 14. Mark as done
        await mark_as_done(message_id)

    except Exception as e:
        logger.error(f"Error in async message processing: {e}")
        raise e

@celery_app.task(name="process_message_task", bind=True, max_retries=3)
def process_message_task(self, phone_number: str, message: str, message_id: str, message_type: str, source: str, media_url: str = None, tenant_id: str = None):
    """Celery task wrapper for message processing."""
    try:
        asyncio.run(_process_message_async(
            phone_number, message, message_id, message_type, source, media_url, tenant_id
        ))
        notify_dashboard_update("pipeline_updated", phone_number=phone_number)
    except Exception as exc:
        logger.warning(f"Retrying task due to error: {exc}")
        raise self.retry(exc=exc, countdown=5)
    finally:
        reset_redis_pool()

@celery_app.task(name="run_nurture_job_task")
def run_nurture_job_task():
    from app.jobs.nurture import run_nurture_job
    asyncio.run(run_nurture_job())

@celery_app.task(name="run_resurrection_job_task")
def run_resurrection_job_task():
    from app.jobs.resurrection import run_resurrection_job
    asyncio.run(run_resurrection_job())

@celery_app.task(name="sync_portal_leads_task")
def sync_portal_leads_task():
    from app.jobs.portal_sync import sync_portal_leads
    asyncio.run(sync_portal_leads())

@celery_app.task(name="send_weekly_roi_report_task")
def send_weekly_roi_report_task():
    from app.jobs.roi_report import send_weekly_roi_report
    asyncio.run(send_weekly_roi_report())

@celery_app.task(name="rollup_usage_task")
def rollup_usage_task():
    from app.jobs.billing import rollup_usage
    asyncio.run(rollup_usage())

@celery_app.task(name="expire_trials_task")
def expire_trials_task():
    from app.jobs.billing import expire_trials
    asyncio.run(expire_trials())

@celery_app.task(name="check_inbox_sla_task")
def check_inbox_sla_task():
    from app.jobs.inbox_sla import check_inbox_sla
    asyncio.run(check_inbox_sla())
