import hashlib
import hmac
import time
from unittest.mock import patch

from api.paddle_routes import _resolve_plan, _verify_signature


def _make_sig(secret: str, body: bytes, ts: str) -> str:
    signed = f"{ts}:{body.decode()}"
    h1 = hmac.new(secret.encode(), signed.encode(), hashlib.sha256).hexdigest()
    return f"ts={ts};h1={h1}"


def test_verify_signature_valid():
    secret = "test_webhook_secret"
    body = b'{"event_type":"subscription.activated"}'
    ts = str(int(time.time()))
    sig = _make_sig(secret, body, ts)
    with patch("api.paddle_routes.WEBHOOK_SECRET", secret):
        assert _verify_signature(body, sig) is True


def test_verify_signature_tampered_body():
    secret = "test_webhook_secret"
    body = b'{"event_type":"subscription.activated"}'
    ts = str(int(time.time()))
    sig = _make_sig(secret, body, ts)
    with patch("api.paddle_routes.WEBHOOK_SECRET", secret):
        assert _verify_signature(b'{"event_type":"tampered"}', sig) is False


def test_verify_signature_bad_hash():
    with patch("api.paddle_routes.WEBHOOK_SECRET", "real_secret"):
        assert _verify_signature(b"body", "ts=123;h1=badhash") is False


def test_verify_signature_empty_secret_always_passes():
    with patch("api.paddle_routes.WEBHOOK_SECRET", ""):
        assert _verify_signature(b"any_body", "") is True


def test_resolve_plan_returns_pro_for_pro_price():
    with patch("api.paddle_routes.PRO_PRICE", "pri_pro_123"):
        assert _resolve_plan("pri_pro_123") == "pro"


def test_resolve_plan_returns_starter_for_unknown_price():
    with patch("api.paddle_routes.PRO_PRICE", "pri_pro_123"):
        assert _resolve_plan("pri_unknown_456") == "starter"


def test_resolve_plan_returns_starter_for_starter_price():
    with patch("api.paddle_routes.PRO_PRICE", "pri_pro_123"):
        with patch("api.paddle_routes.STARTER_PRICE", "pri_starter_789"):
            assert _resolve_plan("pri_starter_789") == "starter"
