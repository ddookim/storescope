"""
StoreScope — 관리자 API
===========================
Stripe 없이 API 키 수동 발급 / 사용량 조회용.
X-Admin-Secret 헤더로 인증.

환경변수:
    ADMIN_SECRET=...  (미설정 시 /admin/* 전체 비활성화)
"""

import hmac
import os
from typing import Optional

import psycopg2.extras
from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, EmailStr

# get_conn: auth.py의 ThreadedConnectionPool 싱글톤 공유 — 별도 연결 생성 금지
from api.auth import create_api_key, deactivate_by_customer, get_conn

router = APIRouter(prefix="/admin", tags=["admin"])

ADMIN_SECRET = os.environ.get("ADMIN_SECRET", "")


def _require_admin(x_admin_secret: Optional[str] = Header(None)) -> None:
    if not ADMIN_SECRET:
        raise HTTPException(status_code=503, detail="ADMIN_SECRET 미설정 — 관리자 기능 비활성화")
    if not x_admin_secret:
        raise HTTPException(status_code=401, detail="유효하지 않은 관리자 시크릿")
    # CRITICAL-2 FIX: 상수 시간 비교로 타이밍 공격 방지
    if not hmac.compare_digest(
        x_admin_secret.encode("utf-8"),
        ADMIN_SECRET.encode("utf-8"),
    ):
        raise HTTPException(status_code=401, detail="유효하지 않은 관리자 시크릿")


class KeyCreateRequest(BaseModel):
    email: EmailStr  # HIGH-2 FIX: RFC 5322 이메일 형식 검증
    plan: str = "starter"  # "starter" | "pro"


class DeactivateRequest(BaseModel):
    customer_id: str


# ── POST /admin/key ─────────────────────────────────────────
@router.post("/key")
def issue_key(
    req: KeyCreateRequest,
    x_admin_secret: Optional[str] = Header(None),
):
    """API 키 수동 발급 (결제 없이 테스트/수동 온보딩용)."""
    _require_admin(x_admin_secret)

    if req.plan not in ("starter", "pro"):
        raise HTTPException(status_code=400, detail="plan은 'starter' 또는 'pro'만 가능")

    raw_key = create_api_key(email=req.email, plan=req.plan)
    return {
        "email": req.email,
        "plan": req.plan,
        "api_key": raw_key,
        "note": "이 키는 한 번만 표시됩니다. 안전하게 보관하세요.",
    }


# ── POST /admin/deactivate ──────────────────────────────────
@router.post("/deactivate")
def deactivate_key(
    req: DeactivateRequest,
    x_admin_secret: Optional[str] = Header(None),
):
    """고객 ID 기준 키 비활성화."""
    _require_admin(x_admin_secret)
    deactivate_by_customer(req.customer_id)
    return {"deactivated": req.customer_id}


# ── GET /admin/stats ────────────────────────────────────────
@router.get("/stats")
def get_stats(x_admin_secret: Optional[str] = Header(None)):
    """전체 키 발급 현황 + 오늘 사용량 조회."""
    _require_admin(x_admin_secret)

    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT
                    ak.id,
                    ak.key_prefix,
                    ak.email,
                    ak.plan,
                    ak.is_active,
                    ak.daily_limit,
                    COALESCE(au.request_count, 0) AS today_requests,
                    ak.created_at
                FROM api_keys ak
                LEFT JOIN api_usage au
                    ON ak.id = au.key_id AND au.used_date = CURRENT_DATE
                ORDER BY ak.created_at DESC
            """)
            keys = cur.fetchall()

            cur.execute("""
                SELECT plan, COUNT(*) AS count
                FROM api_keys
                WHERE is_active = TRUE
                GROUP BY plan
            """)
            plan_summary = cur.fetchall()

            cur.execute("""
                SELECT COALESCE(SUM(request_count), 0) AS total_today
                FROM api_usage
                WHERE used_date = CURRENT_DATE
            """)
            total_today = cur.fetchone()["total_today"]

    return {
        "plan_summary": [dict(r) for r in plan_summary],
        "total_requests_today": total_today,
        "keys": [dict(k) for k in keys],
    }
