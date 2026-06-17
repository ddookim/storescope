"""
Paddle 신규 구독 → API 키 생성 → 이메일 발송 풀 흐름 회귀 테스트

이전 통합 테스트 (test_paddle_webhook_integration.py) 는 webhook → 200 응답까지 검증.
본 테스트는 그 다음 단계 — 실제 부수효과 검증:
    1. _handle_new_subscription 실행
    2. Paddle API 통한 customer 이메일 조회 (mock)
    3. API 키 생성 + DB 적재 (실 DB)
    4. _send_api_key_email 호출

회귀 시나리오:
    a. customer_id 누락 시 silent fail (이전: print()만, 매출 손실)
    b. disposable email 통과
    c. 동일 customer_id 재호출 시 키 중복 발급 (idempotency 부재 시)
    d. plan 매핑 오류 (pro vs starter)
    e. SMTP 미설정 시 console fallback
"""

import os
import sys
import uuid
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

_here = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_here))
os.environ.setdefault("DATABASE_URL", "postgresql:///storescope")


@pytest.fixture
def cleanup_customer_ids():
    """테스트로 생성된 키 정리."""
    ids = []
    yield ids
    if ids:
        import psycopg2
        conn = psycopg2.connect(os.environ["DATABASE_URL"])
        cur = conn.cursor()
        for cid in ids:
            cur.execute("DELETE FROM api_keys WHERE customer_id = %s", (cid,))
        conn.commit()
        conn.close()


def test_new_subscription_creates_api_key(cleanup_customer_ids):
    """starter 구독 활성화 → DB에 1개 키 + plan=starter."""
    from api.paddle_routes import _handle_new_subscription

    customer_id = f"ctm_sub_test_{uuid.uuid4()}"
    cleanup_customer_ids.append(customer_id)

    with patch("api.paddle_routes._get_customer_email", return_value="test_starter@example.com"), \
         patch("api.paddle_routes._send_api_key_email") as mock_send:
        _handle_new_subscription(customer_id, "sub_test_001", "starter", None)

    # DB 검증
    import psycopg2
    conn = psycopg2.connect(os.environ["DATABASE_URL"])
    cur = conn.cursor()
    cur.execute(
        "SELECT plan, is_active, daily_limit FROM api_keys WHERE customer_id = %s",
        (customer_id,)
    )
    row = cur.fetchone()
    conn.close()

    assert row is not None, "API 키 DB 미생성"
    assert row[0] == "starter"
    assert row[1] is True, "is_active=False — 활성화 실패"
    assert row[2] == 500, f"starter daily_limit ≠ 500 (={row[2]})"
    # 이메일 발송 시도됨
    mock_send.assert_called_once()


def test_pro_subscription_creates_unlimited_key(cleanup_customer_ids):
    """pro 구독 활성화 → daily_limit=None (무제한)."""
    from api.paddle_routes import _handle_new_subscription

    customer_id = f"ctm_pro_test_{uuid.uuid4()}"
    cleanup_customer_ids.append(customer_id)

    with patch("api.paddle_routes._get_customer_email", return_value="test_pro@example.com"), \
         patch("api.paddle_routes._send_api_key_email"):
        _handle_new_subscription(customer_id, "sub_test_pro", "pro", None)

    import psycopg2
    conn = psycopg2.connect(os.environ["DATABASE_URL"])
    cur = conn.cursor()
    cur.execute(
        "SELECT plan, daily_limit FROM api_keys WHERE customer_id = %s",
        (customer_id,)
    )
    row = cur.fetchone()
    conn.close()

    assert row[0] == "pro"
    assert row[1] is None, f"pro 키에 daily_limit 설정됨: {row[1]}"


def test_missing_customer_email_alerts(cleanup_customer_ids):
    """customer 이메일 조회 실패 → CRITICAL 알림 + 키 미생성."""
    from api.paddle_routes import _handle_new_subscription

    customer_id = f"ctm_no_email_{uuid.uuid4()}"

    with patch("api.paddle_routes._get_customer_email", return_value=None), \
         patch("api.paddle_routes.send_alert") as mock_alert, \
         patch("api.paddle_routes._send_api_key_email") as mock_send:
        _handle_new_subscription(customer_id, "sub_no_email", "starter", None)

    # CRITICAL 알림 발송 검증
    mock_alert.assert_called_once()
    call_args = mock_alert.call_args
    assert "CRITICAL" in call_args.kwargs.get("level", "")
    assert customer_id in call_args.args[0]

    # 키는 생성 안 됨 (이메일 발송 시도도 안 됨)
    mock_send.assert_not_called()

    # DB에도 row 없음
    import psycopg2
    conn = psycopg2.connect(os.environ["DATABASE_URL"])
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM api_keys WHERE customer_id = %s", (customer_id,))
    assert cur.fetchone()[0] == 0
    conn.close()


def test_disposable_email_skipped(cleanup_customer_ids):
    """일회용 이메일 → 키 미생성 + WARNING 알림."""
    from api.paddle_routes import _handle_new_subscription

    customer_id = f"ctm_disposable_{uuid.uuid4()}"

    with patch("api.paddle_routes._get_customer_email", return_value="abuser@mailinator.com"), \
         patch("api.paddle_routes.send_alert") as mock_alert, \
         patch("api.paddle_routes._send_api_key_email") as mock_send:
        _handle_new_subscription(customer_id, "sub_disposable", "starter", None)

    mock_alert.assert_called_once()
    assert mock_alert.call_args.kwargs.get("level") == "WARNING"
    mock_send.assert_not_called()

    import psycopg2
    conn = psycopg2.connect(os.environ["DATABASE_URL"])
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM api_keys WHERE customer_id = %s", (customer_id,))
    assert cur.fetchone()[0] == 0
    conn.close()


def test_pro_trial_starts_with_500_limit(cleanup_customer_ids):
    """Pro 체험 (trial_ends_at 있음) → daily_limit=500 (남용 방지, TRIAL_DAILY_LIMIT)."""
    from api.paddle_routes import _handle_new_subscription

    customer_id = f"ctm_trial_{uuid.uuid4()}"
    cleanup_customer_ids.append(customer_id)
    trial_ends = "2099-12-31T23:59:59Z"

    with patch("api.paddle_routes._get_customer_email", return_value="trial@example.com"), \
         patch("api.paddle_routes._send_api_key_email"):
        _handle_new_subscription(customer_id, "sub_trial_001", "pro", trial_ends)

    import psycopg2
    conn = psycopg2.connect(os.environ["DATABASE_URL"])
    cur = conn.cursor()
    cur.execute(
        "SELECT plan, daily_limit, trial_ends_at FROM api_keys WHERE customer_id = %s",
        (customer_id,)
    )
    row = cur.fetchone()
    conn.close()

    assert row[0] == "pro"
    assert row[1] == 500, f"Pro trial daily_limit ≠ 500: {row[1]}"
    assert row[2] is not None, "trial_ends_at 미설정"
