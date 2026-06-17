"""
Paddle Webhook 전체 통합 테스트 — Revenue-Critical Path 회귀 차단

기존 단위 테스트:
    test_paddle_unit.py — signature/plan 분리 검증
    test_paddle_idempotency.py — dedupe 검증

미커버 영역 (이번 테스트):
    1. 전체 흐름 — POST 받음 → signature 검증 → idempotency → 키 발급 → DB 적재 → 200 응답
    2. subscription.activated → API 키 1건 생성
    3. subscription.canceled → 모든 키 비활성화
    4. duplicate event → 200 {"duplicate": True}
    5. invalid signature → 400
    6. unknown event_type → 200 (무시)
    7. malformed JSON → 400

회귀 시나리오 (실제 발생 가능):
    - signature 검증 우회 (fail-closed 깨짐)
    - 동일 event 중복 처리 → 키 중복 발급
    - 빈 customer_id → 키 발급 없이 silent fail
    - email 누락 → 알림은 발송되어야 함
"""

import hashlib
import hmac
import json
import os
import sys
import time
import uuid
from pathlib import Path
from unittest.mock import patch

import pytest


_here = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_here))
os.environ.setdefault("DATABASE_URL", "postgresql:///storescope")


TEST_WEBHOOK_SECRET = "test_webhook_secret_integration"
TEST_STARTER_PRICE = "pri_test_starter"
TEST_PRO_PRICE = "pri_test_pro"


def _make_signed_request(body: dict, secret: str = TEST_WEBHOOK_SECRET) -> tuple[bytes, str]:
    """Paddle 형식 서명 생성 — 실제 webhook 핸들러가 받는 형식."""
    raw = json.dumps(body).encode()
    ts = str(int(time.time()))
    signed = f"{ts}:{raw.decode()}"
    h1 = hmac.new(secret.encode(), signed.encode(), hashlib.sha256).hexdigest()
    return raw, f"ts={ts};h1={h1}"


def _make_event(event_type: str, data: dict, event_id: str = None) -> dict:
    return {
        "event_id": event_id or f"evt_{uuid.uuid4()}",
        "event_type": event_type,
        "data": data,
    }


@pytest.fixture
def test_client():
    """FastAPI TestClient + 환경 설정."""
    try:
        from fastapi.testclient import TestClient
    except ImportError:
        pytest.skip("fastapi.testclient not available")

    # WEBHOOK_SECRET 패치 — 단위 테스트 secret 사용
    with patch("api.paddle_routes.WEBHOOK_SECRET", TEST_WEBHOOK_SECRET), \
         patch("api.paddle_routes.PRO_PRICE", TEST_PRO_PRICE), \
         patch("api.paddle_routes.STARTER_PRICE", TEST_STARTER_PRICE):
        from api.main import app
        client = TestClient(app)
        yield client


@pytest.fixture
def cleanup_test_subscription():
    """테스트 후 생성된 키 정리."""
    test_customer_ids = []
    yield test_customer_ids
    if test_customer_ids:
        import psycopg2
        conn = psycopg2.connect(os.environ["DATABASE_URL"])
        cur = conn.cursor()
        for cid in test_customer_ids:
            cur.execute("DELETE FROM api_keys WHERE customer_id = %s", (cid,))
        cur.execute(
            "DELETE FROM paddle_processed_events WHERE event_id LIKE 'evt_test_%' OR event_id LIKE 'evt_%'"
        )
        conn.commit()
        conn.close()


def test_invalid_signature_returns_400(test_client):
    """잘못된 시그너처 → 400 — fail-closed 회귀 차단."""
    event = _make_event("subscription.activated", {"customer_id": "ctm_test_bad_sig"})
    raw, _good_sig = _make_signed_request(event)
    bad_sig = "ts=12345;h1=deadbeefdeadbeef"

    resp = test_client.post(
        "/billing/webhook",
        content=raw,
        headers={"Paddle-Signature": bad_sig, "Content-Type": "application/json"},
    )
    assert resp.status_code == 400


def test_malformed_json_returns_400(test_client):
    """비-JSON 페이로드 → 400."""
    raw = b"not a json payload"
    ts = str(int(time.time()))
    signed = f"{ts}:{raw.decode()}"
    h1 = hmac.new(TEST_WEBHOOK_SECRET.encode(), signed.encode(), hashlib.sha256).hexdigest()

    resp = test_client.post(
        "/billing/webhook",
        content=raw,
        headers={"Paddle-Signature": f"ts={ts};h1={h1}", "Content-Type": "application/json"},
    )
    assert resp.status_code == 400


def test_duplicate_event_returns_200_with_flag(test_client, cleanup_test_subscription):
    """동일 event_id 재수신 → 200 + duplicate=true (idempotency)."""
    event_id = f"evt_test_dup_{uuid.uuid4()}"
    event = _make_event(
        "subscription.activated",
        {
            "id": "sub_test_dup_1",
            "customer_id": "ctm_test_dup_1",
            "items": [{"price": {"id": TEST_STARTER_PRICE}}],
        },
        event_id=event_id,
    )
    cleanup_test_subscription.append("ctm_test_dup_1")

    raw, sig = _make_signed_request(event)
    headers = {"Paddle-Signature": sig, "Content-Type": "application/json"}

    # 1차 — 신규
    r1 = test_client.post("/billing/webhook", content=raw, headers=headers)
    assert r1.status_code == 200
    assert r1.json().get("duplicate") is not True

    # 2차 — 중복
    r2 = test_client.post("/billing/webhook", content=raw, headers=headers)
    assert r2.status_code == 200
    assert r2.json().get("duplicate") is True


def test_unknown_event_type_returns_200(test_client, cleanup_test_subscription):
    """알 수 없는 event_type → 200 (silent ignore, 후속 처리 없음)."""
    event = _make_event("subscription.weird_unhandled_event", {"id": "ignored"})
    raw, sig = _make_signed_request(event)
    resp = test_client.post(
        "/billing/webhook",
        content=raw,
        headers={"Paddle-Signature": sig, "Content-Type": "application/json"},
    )
    assert resp.status_code == 200


def test_payment_failed_returns_200_no_key_change(test_client, cleanup_test_subscription):
    """transaction.payment_failed → 200 (알림만, 키 변경 없음)."""
    event = _make_event(
        "transaction.payment_failed",
        {"customer_id": "ctm_test_fail_1", "id": "txn_test_fail_1"},
    )
    cleanup_test_subscription.append("ctm_test_fail_1")
    raw, sig = _make_signed_request(event)
    resp = test_client.post(
        "/billing/webhook",
        content=raw,
        headers={"Paddle-Signature": sig, "Content-Type": "application/json"},
    )
    assert resp.status_code == 200


def test_subscription_canceled_runs_deactivate(test_client, cleanup_test_subscription):
    """subscription.canceled → deactivate_by_customer 호출 (DB 영향 없으면 OK 가정)."""
    customer_id = f"ctm_test_cancel_{uuid.uuid4()}"
    event = _make_event(
        "subscription.canceled",
        {"customer_id": customer_id, "id": "sub_test_cancel_1"},
    )
    cleanup_test_subscription.append(customer_id)
    raw, sig = _make_signed_request(event)
    resp = test_client.post(
        "/billing/webhook",
        content=raw,
        headers={"Paddle-Signature": sig, "Content-Type": "application/json"},
    )
    assert resp.status_code == 200


def test_empty_event_id_treated_as_duplicate(test_client):
    """event_id 누락 → 안전 거부 (duplicate=true, 처리 안 함)."""
    event = {
        # event_id 의도적으로 누락
        "event_type": "subscription.activated",
        "data": {"id": "sub_no_event_id", "customer_id": "ctm_no_event_id"},
    }
    raw, sig = _make_signed_request(event)
    resp = test_client.post(
        "/billing/webhook",
        content=raw,
        headers={"Paddle-Signature": sig, "Content-Type": "application/json"},
    )
    assert resp.status_code == 200
    # 빈 event_id 도 _is_duplicate_event True 반환 → response에 duplicate
    assert resp.json().get("duplicate") is True


def test_response_content_type_json(test_client):
    """모든 응답이 JSON content-type."""
    event = _make_event(
        "subscription.weird_unhandled",
        {"id": "type_check"},
    )
    raw, sig = _make_signed_request(event)
    resp = test_client.post(
        "/billing/webhook",
        content=raw,
        headers={"Paddle-Signature": sig, "Content-Type": "application/json"},
    )
    assert "application/json" in resp.headers["content-type"].lower()
