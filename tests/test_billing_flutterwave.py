import os
import pytest

from app.services import flutterwave
from unittest.mock import patch, AsyncMock
from app.services import billing


def test_verify_webhook_hash_missing_env():
    # No env => verification fails
    if "FLUTTERWAVE_WEBHOOK_HASH" in os.environ:
        del os.environ["FLUTTERWAVE_WEBHOOK_HASH"]
    assert not flutterwave.verify_webhook_hash("something")


def test_verify_webhook_hash_ok():
    os.environ["FLUTTERWAVE_WEBHOOK_HASH"] = "secret-val"
    assert flutterwave.verify_webhook_hash("secret-val")


def test_secret_key_missing_raises():
    if "FLUTTERWAVE_SECRET_KEY" in os.environ:
        del os.environ["FLUTTERWAVE_SECRET_KEY"]
    with pytest.raises(RuntimeError):
        flutterwave._secret_key()

@pytest.mark.anyio
async def test_apply_flw_event_subscription():
    with patch("app.services.billing._update_tenant", new_callable=AsyncMock) as mock_upd:
        await billing.apply_flw_event("charge.completed", {
            "status": "successful",
            "meta": {"tenant_id": "123", "plan": "growth"},
            "tx_ref": "ref-1",
            "customer": {"email": "test@local.com"}
        })
        mock_upd.assert_called_once()
        args = mock_upd.call_args[0]
        assert args[0] == "id"
        assert args[1] == "123"
        assert args[2]["plan"] == "growth"
        assert args[2]["subscription_status"] == "active"

@pytest.mark.anyio
async def test_apply_flw_event_topup():
    with patch("app.services.billing._credit_balance", new_callable=AsyncMock) as mock_cred, \
         patch("app.services.billing._update_tenant", new_callable=AsyncMock) as mock_upd:
         
        await billing.apply_flw_event("charge.completed", {
            "status": "successful",
            "meta": {"tenant_id": "999", "topup": True},
            "amount": 50000,
            "tx_ref": "ref-topup"
        })
        mock_cred.assert_called_once_with("999", 50000.0)
        mock_upd.assert_not_called()
