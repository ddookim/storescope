"""
StoreScope — Paddle 결제 라우터
===================================
플랜:
  Starter  $19/월  — API 500 req/일
  Pro      $49/월  — API 무제한 + 웹훅 알림

환경변수 (.env):
  PADDLE_API_KEY=pdl_live_...
  PADDLE_CLIENT_TOKEN=live_...
  PADDLE_STARTER_PRICE_ID=pri_...
  PADDLE_PRO_PRICE_ID=pri_...
  PADDLE_WEBHOOK_SECRET=...  (Paddle Dashboard → Notifications에서 발급)
"""

import asyncio
import hashlib
import hmac
import json
import logging
import os
import smtplib
import time
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Optional

import requests as req_lib
from fastapi import APIRouter, HTTPException, Request, Header

from api.auth import create_api_key, deactivate_by_customer, get_conn, is_disposable_email
from pipeline.alerting import send_alert


# ── Webhook idempotency dedupe ──────────────────────────────────
# Paddle 재시도 정책: webhook 200 응답 받기까지 1h~3day 재발송. 동일 event_id를
# N회 처리하면 subscription.activated → N개 API 키 발급되는 매출/보안 결함.
# DB UNIQUE PRIMARY KEY(event_id) 위반 시 ON CONFLICT DO NOTHING으로 멱등 보장.
# migrations/2026_06_04_paddle_idempotency.sql 선행 적용 필수.
import random as _random
_DEDUPE_CALL_COUNT = 0  # 자가 청소 카운터


def _is_duplicate_event(event_id: str, event_type: str) -> bool:
    """True if duplicate (already processed). False if new (and marks as processed)."""
    global _DEDUPE_CALL_COUNT
    if not event_id:
        # event_id 누락 = Paddle 페이로드 비정상. 안전 위해 처리하지 않고 중복 취급.
        _log.warning("Paddle webhook missing event_id (type=%s) — skipping for safety", event_type)
        return True
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO paddle_processed_events (event_id, event_type) "
                    "VALUES (%s, %s) ON CONFLICT (event_id) DO NOTHING",
                    (event_id, event_type),
                )
                # cur.rowcount: 1이면 신규 삽입, 0이면 이미 존재(=중복)
                inserted = cur.rowcount == 1

                # AUTOMATE: 자가 청소 — 100번째 호출마다 90일 이전 row 삭제.
                # 별도 cron 불필요. webhook 호출 빈도가 청소 빈도와 자연 비례 → solo-friendly.
                _DEDUPE_CALL_COUNT += 1
                if _DEDUPE_CALL_COUNT >= 100:
                    _DEDUPE_CALL_COUNT = 0
                    cur.execute(
                        "DELETE FROM paddle_processed_events "
                        "WHERE processed_at < NOW() - INTERVAL '90 days'"
                    )
                    if cur.rowcount > 0:
                        _log.info("Self-cleaned %d old paddle events (>90d)", cur.rowcount)
        if not inserted:
            _log.info("Paddle webhook duplicate skipped: event_id=%s type=%s", event_id, event_type)
        return not inserted
    except Exception as exc:
        # DB 장애 시 fail-open (처리 진행) — DB 다운에 결제 처리 차단 방지.
        # 대신 알림으로 수동 dedupe 가능하도록 통지.
        _log.error("Dedupe check failed for event_id=%s: %s — proceeding without idempotency", event_id, exc)
        send_alert(
            f"Paddle webhook dedupe check failed (DB error)\n"
            f"event_id: {event_id}\nevent_type: {event_type}\n"
            f"조치: 수동으로 중복 처리 검증 필요",
            level="WARNING",
        )
        return False

_log = logging.getLogger(__name__)

router = APIRouter(prefix="/billing", tags=["billing"])

PADDLE_API_KEY     = os.environ.get("PADDLE_API_KEY", "")
WEBHOOK_SECRET     = os.environ.get("PADDLE_WEBHOOK_SECRET", "")
STARTER_PRICE      = os.environ.get("PADDLE_STARTER_PRICE_ID", "")
PRO_PRICE          = os.environ.get("PADDLE_PRO_PRICE_ID", "")
# Replay 윈도우 — Paddle webhook timestamp (ts) age 허용 한계.
# 5분: 정상 retry/네트워크 지연 흡수 + 캡처된 시그니처의 무한 replay 차단.
WEBHOOK_MAX_AGE_SEC = 5 * 60

SMTP_HOST  = os.environ.get("SMTP_HOST", "")
SMTP_PORT  = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USER  = os.environ.get("SMTP_USER", "")
SMTP_PASS  = os.environ.get("SMTP_PASS", "")
FROM_EMAIL = os.environ.get("SMTP_FROM", "noreply@storescope.com")


# ── Webhook 서명 검증 (HMAC-SHA256) ─────────────────────────────
def _verify_signature(raw_body: bytes, sig_header: str) -> bool:
    # SEC-ALERT: fail-closed — WEBHOOK_SECRET 미설정 시 모든 웹훅 거부.
    # 이전 코드(return True)는 secret 누락 시 공격자가 subscription.activated를 위조해
    # 무제한 무료 API 키를 생성할 수 있는 인증 우회 취약점이었음.
    # 개발 환경에서도 .env에 임의 값을 설정하거나 PADDLE_WEBHOOK_SKIP_VERIFY=true를 사용할 것.
    if os.environ.get("PADDLE_WEBHOOK_SKIP_VERIFY", "").lower() == "true":
        return True  # FIX: 개발 전용 명시적 우회 — 환경 변수로만 허용
    if not WEBHOOK_SECRET:
        return False  # FIX: secret 미설정 = fail-closed
    try:
        parts = dict(p.split("=", 1) for p in sig_header.split(";"))
        ts = parts.get("ts", "")
        h1 = parts.get("h1", "")
        # SEC: Replay 방어 — ts age WEBHOOK_MAX_AGE_SEC 초과 시 거부.
        # ts 비숫자/빈값 = 즉시 거부 (정상 Paddle 은 항상 unix epoch second 송신).
        if not ts or not ts.isdigit():
            return False
        if abs(int(time.time()) - int(ts)) > WEBHOOK_MAX_AGE_SEC:
            return False
        signed = f"{ts}:{raw_body.decode('utf-8')}"
        expected = hmac.new(
            WEBHOOK_SECRET.encode(), signed.encode(), hashlib.sha256
        ).hexdigest()
        return hmac.compare_digest(expected, h1)
    except Exception:
        return False


# ── 플랜 판별 ────────────────────────────────────────────────────
def _resolve_plan(price_id: str) -> str:
    return "pro" if price_id == PRO_PRICE else "starter"


# ── Paddle API로 고객 이메일 조회 ────────────────────────────────
def _get_customer_email(customer_id: str) -> Optional[str]:
    if not PADDLE_API_KEY or not customer_id:
        return None
    try:
        resp = req_lib.get(
            f"https://api.paddle.com/customers/{customer_id}",
            headers={"Authorization": f"Bearer {PADDLE_API_KEY}"},
            timeout=10,
        )
        if resp.status_code == 200:
            return resp.json().get("data", {}).get("email")
    except Exception as exc:
        print(f"[Paddle] 고객 이메일 조회 실패: {exc}")
    return None


# ── 이메일 발송 ──────────────────────────────────────────────────
def _send_api_key_email(to_email: str, api_key: str, plan: str) -> None:
    limit_label = "500 req/day" if plan == "starter" else "Unlimited"
    body = (
        f"Welcome to StoreScope {plan.title()} plan!\n\n"
        f"Your API key:\n  {api_key}\n\n"
        f"Add to requests: X-API-Key: {api_key[:8]}...\n"
        f"Daily limit: {limit_label}\n\n"
        f"Keep this key safe — it won't be shown again.\n"
        f"API docs: https://storescope.netlify.app\n"
        f"Questions? Reply to this email."
    )
    if not SMTP_HOST:
        print(f"[EMAIL STUB] To={to_email} Plan={plan} Key={api_key[:20]}...")
        return
    msg = MIMEMultipart()
    msg["From"]    = FROM_EMAIL
    msg["To"]      = to_email
    msg["Subject"] = f"StoreScope — Your {plan.title()} API Key"
    msg.attach(MIMEText(body, "plain", "utf-8"))
    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=10) as srv:
            srv.ehlo(); srv.starttls()
            srv.login(SMTP_USER, SMTP_PASS)
            srv.send_message(msg)
        print(f"[Email] 발송: {to_email} ({plan})")
    except Exception as exc:
        print(f"[Email error] {to_email}: {exc}")


# ── GET /billing/plans ───────────────────────────────────────────
@router.get("/plans")
def get_plans():
    return {
        "plans": [
            {
                "id": "starter",
                "name": "Starter",
                "price_usd": 19,
                "interval": "month",
                "price_id": STARTER_PRICE,
                # FIX: /billing/plans features를 랜딩 페이지와 일치 — API 소비자 혼란 제거
                "features": [
                    "500 API requests/day",
                    "Access to all trending clusters",
                    "Store-level product data",
                    "7-day trend history",
                    "Email support",
                ],
                "limits": {"daily_requests": 500, "trend_history_days": 7},
            },
            {
                "id": "pro",
                "name": "Pro",
                "price_usd": 49,
                "interval": "month",
                "price_id": PRO_PRICE,
                "features": [
                    "Unlimited API requests",
                    "Access to all trending clusters",
                    "Store-level product data",
                    "30-day trend history",
                    "Webhook alerts on new clusters",
                    "CSV bulk export",
                    "Priority support",
                ],
                "limits": {"daily_requests": None, "trend_history_days": 30},
            },
        ]
    }


# ── POST /billing/webhook ────────────────────────────────────────
@router.post("/webhook")
async def paddle_webhook(
    request: Request,
    paddle_signature: Optional[str] = Header(None, alias="Paddle-Signature"),
):
    raw_body = await request.body()

    if not _verify_signature(raw_body, paddle_signature or ""):
        raise HTTPException(status_code=400, detail="Invalid webhook signature")

    try:
        event = json.loads(raw_body)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    event_type = event.get("event_type", "")
    event_id   = event.get("event_id", "") or event.get("notification_id", "")
    data       = event.get("data", {})
    loop       = asyncio.get_running_loop()

    # Idempotency: 동일 event_id 중복 수신 시 200 반환하고 처리 skip.
    # Paddle retry 정책 안전하게 흡수 + 200 응답으로 Paddle의 추가 재시도 차단.
    if _is_duplicate_event(event_id, event_type):
        return {"received": True, "duplicate": True}

    if event_type == "subscription.activated":
        customer_id     = data.get("customer_id", "")
        subscription_id = data.get("id", "")
        items           = data.get("items", [])
        price_id        = items[0].get("price", {}).get("id", "") if items else ""
        plan            = _resolve_plan(price_id)
        trial_dates     = data.get("trial_dates") or {}
        trial_ends_at   = trial_dates.get("ends_at")  # ISO8601 string or None
        await loop.run_in_executor(
            None, _handle_new_subscription,
            customer_id, subscription_id, plan, trial_ends_at
        )

    elif event_type in ("subscription.canceled", "subscription.paused"):
        customer_id = data.get("customer_id", "")
        await loop.run_in_executor(None, deactivate_by_customer, customer_id)
        _log.info("Paddle subscription ended: customer_id=%s event=%s", customer_id, event_type)

    elif event_type in ("transaction.payment_failed", "subscription.past_due"):
        # FIX: 결제 실패 이벤트 처리 누락 → 미납 고객이 키를 무기한 사용하는 취약점.
        # 결제 실패 즉시 CRITICAL 알림 발송 (키는 Paddle이 canceled를 보낼 때까지 유지).
        # 즉각 비활성화는 Paddle의 재시도 정책과 충돌할 수 있으므로 알림만 발송.
        customer_id = data.get("customer_id") or data.get("id", "unknown")
        send_alert(
            f"결제 실패 이벤트 수신\n"
            f"event_type: {event_type}\n"
            f"customer_id: {customer_id}\n"
            f"조치: Paddle 대시보드 확인 및 수동 처리 필요",
            level="WARNING",
        )
        _log.warning("Paddle payment failed: event=%s customer_id=%s", event_type, customer_id)

    return {"received": True}


def _handle_new_subscription(
    customer_id: str, subscription_id: str, plan: str, trial_ends_at=None
) -> None:
    customer_email = _get_customer_email(customer_id)
    if not customer_email:
        # FIX: 고객이 결제했으나 이메일 조회 실패 → 즉시 CRITICAL 알림 발송.
        # 이전: print()만 남기고 리턴 → 결제 성공 + 키 미발급 = 수익 손실 + 고객 불만.
        send_alert(
            f"결제 완료 후 API 키 발급 실패\n"
            f"customer_id: {customer_id}\n"
            f"subscription_id: {subscription_id}\n"
            f"plan: {plan}\n"
            f"조치: /admin/key 엔드포인트로 수동 발급 필요",
            level="CRITICAL",
        )
        _log.error(
            "Paddle email lookup failed for customer_id=%s sub=%s plan=%s — manual key issuance required",
            customer_id, subscription_id, plan,
        )
        return
    if is_disposable_email(customer_email):
        send_alert(
            f"일회용 이메일로 결제 시도\ncustomer_id: {customer_id}\nemail: {customer_email}",
            level="WARNING",
        )
        _log.warning("Disposable email detected: %s customer_id=%s", customer_email, customer_id)
        return

    raw_key = create_api_key(
        email=customer_email,
        plan=plan,
        customer_id=customer_id,
        subscription_id=subscription_id,
        trial_ends_at=trial_ends_at,
    )
    _send_api_key_email(to_email=customer_email, api_key=raw_key, plan=plan)
