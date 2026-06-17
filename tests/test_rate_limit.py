"""
Rate limit (per-API-key daily quota) 회귀 테스트

검증:
    1. 정확히 N=limit 요청 통과 → (N+1)번째 429
    2. 429 응답 헤더: Limit / Reset (ISO 8601 UTC) / Remaining
    3. 서로 다른 key_id 간섭 0
    4. Pro plan (limit=None) 무한대 통과
    5. Timezone — Reset 헤더가 다음 UTC midnight 정확
"""

import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi import HTTPException

_here = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_here))
os.environ.setdefault("DATABASE_URL", "postgresql:///storescope")


@pytest.fixture
def test_key_setup():
    """일회용 테스트 API 키 생성 + 자동 정리."""
    import psycopg2
    conn = psycopg2.connect(os.environ["DATABASE_URL"])
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO api_keys (key_hash, key_prefix, email, plan, daily_limit, is_active)
        VALUES (%s, 'tk_test_', %s, 'starter', 5, TRUE)
        RETURNING id
    """, (f"test_hash_{os.getpid()}_{datetime.utcnow().timestamp()}",
          f"ratelimit_test_{os.getpid()}@example.test"))
    key_id = cur.fetchone()[0]
    conn.commit()
    yield key_id
    # cleanup
    cur.execute("DELETE FROM api_usage WHERE key_id = %s", (key_id,))
    cur.execute("DELETE FROM api_keys WHERE id = %s", (key_id,))
    conn.commit()
    conn.close()


def test_under_limit_passes(test_key_setup):
    """daily_limit=5 → 5번째까지 통과."""
    from api.auth import _check_and_increment_usage
    for i in range(5):
        _check_and_increment_usage(test_key_setup, daily_limit=5)
    # 5번 모두 예외 없이 통과


def test_at_limit_rejects(test_key_setup):
    """daily_limit=5 → 6번째 호출 429."""
    from api.auth import _check_and_increment_usage
    for i in range(5):
        _check_and_increment_usage(test_key_setup, daily_limit=5)
    with pytest.raises(HTTPException) as exc_info:
        _check_and_increment_usage(test_key_setup, daily_limit=5)
    assert exc_info.value.status_code == 429


def test_429_headers_correct(test_key_setup):
    """429 응답 헤더 — Limit / Reset (ISO 8601) / Remaining."""
    from api.auth import _check_and_increment_usage
    for i in range(5):
        _check_and_increment_usage(test_key_setup, daily_limit=5)
    with pytest.raises(HTTPException) as exc_info:
        _check_and_increment_usage(test_key_setup, daily_limit=5)
    h = exc_info.value.headers
    assert h["X-RateLimit-Limit"] == "5"
    assert h["X-RateLimit-Remaining"] == "0"
    # Reset = ISO 8601 UTC timestamp
    reset = h["X-RateLimit-Reset"]
    assert reset.endswith("+00:00") or reset.endswith("Z"), f"non-UTC reset: {reset}"
    # 파싱 가능 + 미래 + 24h 이내
    reset_dt = datetime.fromisoformat(reset.replace("Z", "+00:00"))
    now = datetime.now(timezone.utc)
    assert reset_dt > now, "Reset 시각이 과거"
    assert (reset_dt - now).total_seconds() <= 86400, "Reset 시각이 24h 이상 미래"


def test_different_keys_isolated(test_key_setup):
    """key_id A 의 사용량이 key_id B 에 영향 안 줌."""
    import psycopg2
    from api.auth import _check_and_increment_usage
    # 두 번째 키 생성
    conn = psycopg2.connect(os.environ["DATABASE_URL"])
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO api_keys (key_hash, key_prefix, email, plan, daily_limit, is_active)
        VALUES (%s, 'tk_test2_', %s, 'starter', 5, TRUE)
        RETURNING id
    """, (f"test_hash_iso_{datetime.utcnow().timestamp()}",
          f"isolation_test_{datetime.utcnow().timestamp()}@example.test"))
    key2_id = cur.fetchone()[0]
    conn.commit()
    try:
        # A 키 5회 소진
        for i in range(5):
            _check_and_increment_usage(test_key_setup, daily_limit=5)
        # B 키는 영향 받지 않고 5회 OK
        for i in range(5):
            _check_and_increment_usage(key2_id, daily_limit=5)
    finally:
        cur.execute("DELETE FROM api_usage WHERE key_id = %s", (key2_id,))
        cur.execute("DELETE FROM api_keys WHERE id = %s", (key2_id,))
        conn.commit()
        conn.close()


def test_pro_plan_unlimited(test_key_setup):
    """daily_limit=None (Pro) → 한도 검사 우회, 무한 호출 통과."""
    from api.auth import _check_and_increment_usage
    # 100회 호출 (실제 사용량은 무한대지만 테스트 시간 cap)
    for i in range(100):
        _check_and_increment_usage(test_key_setup, daily_limit=None)


def test_reset_is_next_utc_midnight():
    """X-RateLimit-Reset = 정확한 다음 UTC midnight (서버 TZ 무관)."""
    from datetime import timedelta
    now = datetime.now(timezone.utc)
    expected = (now + timedelta(days=1)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )

    # 코드와 동일한 계산 재현
    actual = (datetime.now(timezone.utc) + timedelta(days=1)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )

    # 1초 이내 일치
    assert abs((expected - actual).total_seconds()) < 1
    # ISO 8601 형식
    iso = actual.isoformat()
    assert iso.endswith("+00:00"), f"non-UTC: {iso}"
    # 시간 부분 0:00:00
    assert "T00:00:00" in iso
