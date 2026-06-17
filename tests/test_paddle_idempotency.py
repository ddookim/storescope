"""
Paddle Webhook Idempotency 단위 테스트 — Revenue-Critical 회귀 차단.

paddle_routes.py 의 _is_duplicate_event() 가 정확히 다음 3가지를 보장해야 함:
    1. 첫 호출 = 신규 → False 반환 + DB row 생성
    2. 재호출 (same event_id) = 중복 → True 반환 + DB row 미증가
    3. 빈 event_id = 안전 거부 → True 반환 (처리 안 함)

이게 깨지면: Paddle 재시도 시 N개 API 키 중복 발급 = 매출 손실 + 보안 결함.

Migration 의존: migrations/2026_06_04_paddle_idempotency.sql 적용된 DB 필요.
실행:
    cd StoreScope
    DATABASE_URL=postgresql:///storescope PYTHONPATH=. pytest tests/test_paddle_idempotency.py -v
"""

import os
import uuid

import pytest

# 사전조건: DATABASE_URL 설정
if not os.environ.get("DATABASE_URL"):
    os.environ["DATABASE_URL"] = "postgresql:///storescope"


@pytest.fixture
def fresh_event_id():
    """매 테스트마다 유니크 event_id 생성 + 자동 정리."""
    eid = f"evt_test_{uuid.uuid4()}"
    yield eid
    # cleanup
    from api.auth import get_conn
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM paddle_processed_events WHERE event_id = %s", (eid,))
    except Exception:
        pass  # DB 미접근 시 cleanup skip — 테스트 자체는 통과 가능


def test_new_event_returns_false(fresh_event_id):
    """첫 호출 = 신규 = False (not duplicate)."""
    from api.paddle_routes import _is_duplicate_event
    result = _is_duplicate_event(fresh_event_id, "subscription.activated")
    assert result is False, "신규 event_id가 duplicate로 잘못 판정됨"


def test_duplicate_event_returns_true(fresh_event_id):
    """동일 event_id 재호출 = True (duplicate)."""
    from api.paddle_routes import _is_duplicate_event
    _is_duplicate_event(fresh_event_id, "subscription.activated")  # 첫 호출
    result = _is_duplicate_event(fresh_event_id, "subscription.activated")  # 재호출
    assert result is True, "중복 event_id가 신규로 잘못 판정됨 → API 키 중복 발급 위험"


def test_empty_event_id_returns_true():
    """빈 event_id = True (안전 거부, 처리 안 함)."""
    from api.paddle_routes import _is_duplicate_event
    result = _is_duplicate_event("", "subscription.activated")
    assert result is True, "빈 event_id가 처리됨 → Paddle 비정상 페이로드 통과"


def test_different_event_ids_both_new(fresh_event_id):
    """서로 다른 event_id는 둘 다 신규."""
    from api.paddle_routes import _is_duplicate_event
    eid2 = f"evt_test_{uuid.uuid4()}"
    try:
        r1 = _is_duplicate_event(fresh_event_id, "subscription.activated")
        r2 = _is_duplicate_event(eid2, "subscription.activated")
        assert r1 is False
        assert r2 is False, "다른 event_id가 중복으로 잘못 판정됨"
    finally:
        from api.auth import get_conn
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM paddle_processed_events WHERE event_id = %s", (eid2,))


def test_db_persistence(fresh_event_id):
    """첫 호출 후 DB에 row 1개 생성 검증."""
    from api.paddle_routes import _is_duplicate_event
    from api.auth import get_conn

    _is_duplicate_event(fresh_event_id, "subscription.activated")

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*), event_type FROM paddle_processed_events WHERE event_id = %s GROUP BY event_type",
                (fresh_event_id,)
            )
            row = cur.fetchone()
    assert row is not None, "DB에 row 미생성"
    assert row[0] == 1, f"row 수 {row[0]} (1 기대)"
    assert row[1] == "subscription.activated", f"event_type {row[1]} 기록 오류"


def test_idempotent_under_concurrent_simulation(fresh_event_id):
    """동시 N회 호출 시뮬레이션 → DB에 정확히 1 row."""
    from api.paddle_routes import _is_duplicate_event
    from api.auth import get_conn

    results = []
    for _ in range(5):
        results.append(_is_duplicate_event(fresh_event_id, "subscription.activated"))

    # 첫 호출만 False, 나머지 모두 True
    assert results == [False, True, True, True, True], (
        f"동시 호출 idempotency 깨짐: {results}"
    )

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) FROM paddle_processed_events WHERE event_id = %s",
                (fresh_event_id,)
            )
            count = cur.fetchone()[0]
    assert count == 1, f"DB row {count}개 (1 기대) — 중복 발급 발생 가능"
