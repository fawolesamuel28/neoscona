import os
import pytest

from app.services import flutterwave


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
