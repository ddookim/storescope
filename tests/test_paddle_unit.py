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


def test_verify_signature_empty_secret_fail_closed():
    """SEC: 빈 WEBHOOK_SECRET = fail-closed (이전 fail-open 버그 회귀 방지).
    이전 테스트는 fail-open 동작을 검증했으나 paddle_routes.py 가 fail-closed로 수정됨.
    빈 secret + signature 검증 통과 = 인증 우회 취약점."""
    with patch("api.paddle_routes.WEBHOOK_SECRET", ""):
        assert _verify_signature(b"any_body", "") is False


def test_verify_signature_skip_via_env_flag():
    """개발 환경 전용 우회 — PADDLE_WEBHOOK_SKIP_VERIFY=true 명시 시만 통과."""
    import os
    os.environ["PADDLE_WEBHOOK_SKIP_VERIFY"] = "true"
    try:
        with patch("api.paddle_routes.WEBHOOK_SECRET", ""):
            assert _verify_signature(b"any_body", "") is True
    finally:
        del os.environ["PADDLE_WEBHOOK_SKIP_VERIFY"]


def test_verify_signature_stale_ts_rejected():
    """SEC: Replay 공격 방어 — ts 가 WEBHOOK_MAX_AGE_SEC 초과 시 거부.
    공격 시나리오: 공격자가 valid webhook 캡처 후 며칠 뒤 재전송 → 무료 키 추가 생성."""
    secret = "test_webhook_secret"
    body = b'{"event_type":"subscription.activated"}'
    # 1시간 전 timestamp = WEBHOOK_MAX_AGE_SEC(5분) 초과
    stale_ts = str(int(time.time()) - 3600)
    sig = _make_sig(secret, body, stale_ts)
    with patch("api.paddle_routes.WEBHOOK_SECRET", secret):
        assert _verify_signature(body, sig) is False


def test_verify_signature_future_ts_rejected():
    """SEC: 미래 timestamp 도 차단 (clock skew 이상 또는 위조)."""
    secret = "test_webhook_secret"
    body = b'{"event_type":"subscription.activated"}'
    # 1시간 후 timestamp = clock skew 이상
    future_ts = str(int(time.time()) + 3600)
    sig = _make_sig(secret, body, future_ts)
    with patch("api.paddle_routes.WEBHOOK_SECRET", secret):
        assert _verify_signature(body, sig) is False


def test_verify_signature_nonnumeric_ts_rejected():
    """SEC: ts 가 숫자가 아니면 즉시 거부 (정상 Paddle은 epoch second 송신)."""
    secret = "test_webhook_secret"
    body = b'{"event_type":"subscription.activated"}'
    # ts=abc 같은 비정상 값
    h1 = hmac.new(secret.encode(), b"abc:" + body, hashlib.sha256).hexdigest()
    sig = f"ts=abc;h1={h1}"
    with patch("api.paddle_routes.WEBHOOK_SECRET", secret):
        assert _verify_signature(body, sig) is False


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
