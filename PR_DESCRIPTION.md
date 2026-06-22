Title: feat(billing): migrate Paystack → Flutterwave (webhooks, scheduler, migration)

## Summary

This branch replaces Paystack with Flutterwave across the billing stack and adds recurring, tokenized monthly renewals.

Key changes

- Add `app/services/flutterwave.py` (initialize, verify, tokenized charge, webhook hash verification)
- Add `app/webhooks/flutterwave.py` (POST /webhook/flutterwave with hash verification, replay guard, dedup)
- Replace billing flows in `app/services/billing.py` to use Flutterwave (returns `payment_link` + `tx_ref`)
- Add recurring scheduler `app/billing/scheduler.py` for tokenized charges and re-checkout links
- Add migration `migrations/005_flutterwave.sql` (tenant `flw_*` columns + `flutterwave_events` audit table)
- Preserve historical Paystack code as `paystack_legacy.py` and `paystack_legacy` webhook; remove active Paystack handlers
- Update templates, docs, and `.env.example` to reference Flutterwave
- Add scripts to apply migration (`scripts/apply_migration_supabase.sh` / `.ps1`) and tests (`tests/test_billing_flutterwave.py`)

Security & safety

- Webhook verification uses constant-time `secrets.compare_digest` against `FLUTTERWAVE_WEBHOOK_HASH`.
- All provider keys are read at call-time; none are stored in code.
- Idempotency via unique `flutterwave_events.flw_id` constraint.

Testing

- Local test suite: `python -m pytest` → 41 passed (includes new Flutterwave tests).

Manual verification checklist (staging)

1. Set `FLUTTERWAVE_SECRET_KEY`, `FLUTTERWAVE_WEBHOOK_HASH`, `FLUTTERWAVE_CALLBACK_URL` in staging env.
2. Apply `migrations/005_flutterwave.sql` to the staging DB.
3. Configure Flutterwave dashboard webhook to `https://<staging>/webhook/flutterwave`.
4. Subscribe via onboarding UI and complete a test card charge; verify tenant activation and `flw_card_token`.
5. Trigger `charge_due_tenants()` (via scheduler or management endpoint) and confirm tokenized charge advances `next_billing_date`.

Notes

- Token expiry emails, dunning retries (cancel after 3 failures), and automated re-auth email flows are TODOs and should be implemented in follow-up PRs.
- Do not remove historical DB columns (paystack\_\*) yet — keep for audit and rollback.
Mon, Jun 22, 2026  8:55:50 AM - PR prep
