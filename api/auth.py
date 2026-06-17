"""
StoreScope — API 키 인증 + 레이트 리밋
==========================================
- X-API-Key 헤더로 인증
- Starter: 500 req/일, Pro: 무제한
- SHA-256 해시로 키 저장 (raw 키는 발급 시 1회만 반환)
- ThreadedConnectionPool 싱글톤으로 커넥션 재사용
"""

import hashlib
import logging
import os
import secrets
import sys
import threading
from contextlib import contextmanager
from typing import Optional

import psycopg2
import psycopg2.extras
import psycopg2.pool
from fastapi import Header, HTTPException

logger = logging.getLogger(__name__)

# HIGH-1: 시작 시 필수 환경변수 검증 — 폴백 없음
_DB_URL_RAW = os.environ.get("DATABASE_URL")
if not _DB_URL_RAW:
    print("FATAL: DATABASE_URL 환경변수가 설정되지 않았습니다.", file=sys.stderr)
    sys.exit(1)
DB_URL: str = _DB_URL_RAW

PLAN_DAILY_LIMITS = {
    "starter": 500,
    "pro": None,
}
TRIAL_DAILY_LIMIT = 500  # Pro 체험 중 동일 제한 — 남용 방지

DISPOSABLE_EMAIL_DOMAINS = {
    "mailinator.com", "guerrillamail.com", "temp-mail.org", "throwam.com",
    "yopmail.com", "sharklasers.com", "guerrillamailblock.com", "grr.la",
    "guerrillamail.info", "spam4.me", "trashmail.com", "dispostable.com",
    "tempmail.com", "fakeinbox.com", "maildrop.cc", "getnada.com",
    "discard.email", "mailnull.com", "spamgourmet.com", "trashmail.me",
}

# ── 커넥션 풀 싱글톤 (double-checked locking) ─────────────────
_pool: Optional[psycopg2.pool.ThreadedConnectionPool] = None
_pool_lock = threading.Lock()


def _get_pool() -> psycopg2.pool.ThreadedConnectionPool:
    global _pool
    if _pool is None or _pool.closed:
        with _pool_lock:
            if _pool is None or _pool.closed:
                # Render free 512MB / uvicorn 1 worker / Neon free 0.25 vCPU 환경.
                # max=60일 때 60×10MB=600MB → OOM. max=10이면 100MB로 안전.
                # 사용자 트래픽 < 100rps 가정 (D+30 검증 단계). 부족 시 max=15로 상향.
                _pool = psycopg2.pool.ThreadedConnectionPool(2, 10, dsn=DB_URL)
    return _pool


@contextmanager
def get_conn():
    """풀에서 커넥션을 빌려 yield; 정상 종료 시 commit, 예외 시 rollback 후 반납."""
    p = _get_pool()
    conn = p.getconn()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        p.putconn(conn)


# ── 키 해싱 ──────────────────────────────────────────────────

def _hash_key(raw_key: str) -> str:
    return hashlib.sha256(raw_key.encode()).hexdigest()


def generate_api_key() -> tuple[str, str]:
    raw = "si_" + secrets.token_urlsafe(32)
    return raw, _hash_key(raw)


# ── 공개 API ─────────────────────────────────────────────────

def is_disposable_email(email: str) -> bool:
    domain = email.split("@")[-1].lower() if "@" in email else ""
    return domain in DISPOSABLE_EMAIL_DOMAINS


def create_api_key(
    email: str,
    plan: str,
    customer_id: Optional[str] = None,
    subscription_id: Optional[str] = None,
    trial_ends_at=None,
) -> str:
    """DB에 API 키 저장 후 raw 키 반환 (1회만 가능)."""
    raw, key_hash = generate_api_key()
    # 체험 중 Pro도 500건/일 제한 적용
    if trial_ends_at and plan == "pro":
        daily_limit = TRIAL_DAILY_LIMIT
    else:
        daily_limit = PLAN_DAILY_LIMITS.get(plan)

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO api_keys
                    (key_hash, key_prefix, email, plan,
                     customer_id, subscription_id, daily_limit, trial_ends_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (key_hash, raw[:12], email, plan,
                 customer_id, subscription_id, daily_limit, trial_ends_at),
            )
    return raw


def deactivate_by_customer(customer_id: str) -> None:
    """구독 종료 시 해당 고객의 모든 키 비활성화."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE api_keys SET is_active = FALSE WHERE customer_id = %s",
                (customer_id,),
            )


def _check_and_increment_usage(key_id: int, daily_limit: Optional[int]) -> None:
    """
    CRITICAL-1 FIX: 한도 검사 + 카운트 증가를 단일 트랜잭션 + FOR UPDATE로 원자적 처리.
    한도 초과 시 카운트 증가 없이 즉시 예외 발생.

    FIX 2026-06-07: timezone 일관성 — used_date는 UTC 기준으로 통일.
    이전: CURRENT_DATE (서버 TZ=Singapore) vs 헤더 "midnight UTC" 불일치 → 사용자 혼란.
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT request_count FROM api_usage
                WHERE key_id = %s AND used_date = (NOW() AT TIME ZONE 'UTC')::date
                FOR UPDATE
                """,
                (key_id,),
            )
            row = cur.fetchone()
            current_count = row[0] if row else 0

            if daily_limit is not None and current_count >= daily_limit:
                # 정확한 reset 시각 계산 — 다음 UTC midnight (ISO 8601).
                import datetime as _dt
                from datetime import timezone as _tz
                now_utc = _dt.datetime.now(_tz.utc)
                next_midnight = (now_utc + _dt.timedelta(days=1)).replace(
                    hour=0, minute=0, second=0, microsecond=0
                )
                raise HTTPException(
                    status_code=429,
                    detail=f"일일 한도 초과 ({daily_limit} req/일). Pro 플랜으로 업그레이드하세요.",
                    headers={
                        "X-RateLimit-Limit": str(daily_limit),
                        "X-RateLimit-Reset": next_midnight.isoformat(),
                        "X-RateLimit-Remaining": "0",
                    },
                )

            cur.execute(
                """
                INSERT INTO api_usage (key_id, used_date, request_count)
                VALUES (%s, (NOW() AT TIME ZONE 'UTC')::date, 1)
                ON CONFLICT (key_id, used_date)
                DO UPDATE SET request_count = api_usage.request_count + 1
                """,
                (key_id,),
            )


def require_api_key(x_api_key: Optional[str] = Header(None)) -> dict:
    """FastAPI Depends 의존성 — 보호된 엔드포인트에 사용."""
    if not x_api_key:
        raise HTTPException(
            status_code=401,
            detail="X-API-Key 헤더가 필요합니다. /billing/plans 에서 플랜을 확인하세요.",
        )
    # si_ prefix(3) + token_urlsafe(32) = 최대 약 46자. 100자 초과는 유효한 키가 아님.
    # 길이 무제한 입력으로 SHA-256 + DB 쿼리를 유발하는 DoS 벡터 차단.
    if len(x_api_key) > 100:
        raise HTTPException(status_code=401, detail="유효하지 않은 API 키입니다.")

    key_hash = _hash_key(x_api_key)

    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT id, plan, daily_limit, is_active, trial_ends_at FROM api_keys WHERE key_hash = %s",
                (key_hash,),
            )
            row = cur.fetchone()
            if row and row["plan"] == "pro" and row["trial_ends_at"]:
                from datetime import timezone
                import datetime as _dt
                now = _dt.datetime.now(timezone.utc)
                trial_end = row["trial_ends_at"]
                if hasattr(trial_end, "tzinfo") and trial_end.tzinfo is None:
                    trial_end = trial_end.replace(tzinfo=timezone.utc)
                if now > trial_end and row["daily_limit"] == TRIAL_DAILY_LIMIT:
                    # 체험 종료 → Pro 무제한으로 자동 전환
                    cur.execute(
                        "UPDATE api_keys SET daily_limit = NULL, trial_ends_at = NULL WHERE id = %s",
                        (row["id"],),
                    )
                    row = dict(row)
                    row["daily_limit"] = None

    if not row:
        raise HTTPException(status_code=401, detail="유효하지 않은 API 키입니다.")
    if not row["is_active"]:
        raise HTTPException(status_code=403, detail="비활성화된 API 키입니다.")

    _check_and_increment_usage(row["id"], row["daily_limit"])
    return dict(row)
