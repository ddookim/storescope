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
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Optional

import requests as req_lib
from fastapi import APIRouter, HTTPException, Request, Header

from api.auth import create_api_key, deactivate_by_customer
from pipeline.alerting import send_alert

_log = logging.getLogger(__name__)

router = APIRouter(prefix="/billing", tags=["billing"])

PADDLE_API_KEY     = os.environ.get("PADDLE_API_KEY", "")
WEBHOOK_SECRET     = os.environ.get("PADDLE_WEBHOOK_SECRET", "")
STARTER_PRICE      = os.environ.get("PADDLE_STARTER_PRICE_ID", "")
PRO_PRICE          = os.environ.get("PADDLE_PRO_PRICE_ID", "")

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
    data       = event.get("data", {})
    loop       = asyncio.get_running_loop()

    if event_type == "subscription.activated":
        customer_id     = data.get("customer_id", "")
        subscription_id = data.get("id", "")
        items           = data.get("items", [])
        price_id        = items[0].get("price", {}).get("id", "") if items else ""
        plan            = _resolve_plan(price_id)
        await loop.run_in_executor(
            None, _handle_new_subscription,
            customer_id, subscription_id, plan
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
    customer_id: str, subscription_id: str, plan: str
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
    raw_key = create_api_key(
        email=customer_email,
        plan=plan,
        customer_id=customer_id,
        subscription_id=subscription_id,
    )
    _send_api_key_email(to_email=customer_email, api_key=raw_key, plan=plan)
