"""
Weekly Digest 발송 — active subscribers (api_keys.is_active=TRUE, unsubscribed_at IS NULL)

마스터플랜 "월요일 다이제스트 약속" 실 이행. 본 모듈 없으면 거짓광고 = 환불 + 표시광고법 §3.

실행:
    python services/send_weekly_digests.py              # 실 발송
    python services/send_weekly_digests.py --dry-run    # DB 발신 X, 콘솔만

호출 위치:
    .github/workflows/weekly_pipeline.yml — pipeline + health check 이후 step.

설계 (Gemini review_v1 D+29 반영):
    1. At-most-once 상태머신: pending INSERT (UNIQUE 충돌 = skip) → SMTP 발송 → sent UPDATE
    2. smtplib timeout=10s (무한 대기 차단, GH Actions hang 방지)
    3. Batch alert (개별 실패마다 Telegram X, loop 종료 후 1회 summary)
    4. ThreadedConnectionPool 싱글톤 + named cursor (fetchmany 100) — 메모리 O(1)
    5. Unsubscribe 링크 (정보통신망법 §50 / CAN-SPAM)
"""

import argparse
import base64
import hashlib
import hmac
import logging
import os
import smtplib
import sys
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from typing import Iterator, Optional

_HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_HERE))

_log = logging.getLogger("send_weekly_digests")
logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

# ── env ─────────────────────────────────────────────────────
SMTP_HOST       = os.environ.get("SMTP_HOST", "")
SMTP_PORT       = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USER       = os.environ.get("SMTP_USER", "")
SMTP_PASS       = os.environ.get("SMTP_PASS", "")
FROM_EMAIL      = os.environ.get("SMTP_FROM", "noreply@storescope.com")
BASE_URL        = os.environ.get("BASE_URL", "https://ddookim.github.io/storescope").rstrip("/")
SMTP_TIMEOUT    = 10  # seconds — 무한 대기 차단
FETCH_BATCH     = 100  # named cursor batch
IS_CI           = os.environ.get("GITHUB_ACTIONS") == "true" or bool(os.environ.get("RENDER_SERVICE_NAME"))
# HMAC unsub token — api/main.py 와 동일 로직.
# Production fail-fast: CI 환경에서 secret 누락 = backend 와 token mismatch = 모든 unsub 링크 invalid.
_UNSUB_SECRET   = os.environ.get("ADMIN_SECRET") or os.environ.get("PADDLE_WEBHOOK_SECRET", "")
if not _UNSUB_SECRET:
    if IS_CI:
        _log.critical("ADMIN_SECRET missing — unsubscribe links will be invalid (token mismatch with backend)")
        # raise X (CI step non-block 유지). 대신 main() 에서 alert.
        _UNSUB_SECRET_MISSING = True
    else:
        _UNSUB_SECRET = "dev-unsub-secret-change-me"
        _UNSUB_SECRET_MISSING = False
else:
    _UNSUB_SECRET_MISSING = False


def _iso_week() -> str:
    return datetime.now(timezone.utc).strftime("%Y-W%V")


def _iter_active_subscribers() -> Iterator[dict]:
    """ThreadedConnectionPool 싱글톤 + named cursor — 메모리 O(1).

    Yields {id, email, plan} for active + 미수신거부 사용자.
    """
    from api.auth import get_conn
    with get_conn() as conn:
        # named cursor = server-side, fetch 시점에만 메모리 사용
        with conn.cursor(name="active_subs_stream") as cur:
            cur.itersize = FETCH_BATCH
            cur.execute("""
                SELECT id, email, plan
                FROM api_keys
                WHERE is_active = TRUE
                  AND unsubscribed_at IS NULL
                  AND email IS NOT NULL
                  AND email <> ''
                ORDER BY id
            """)
            for row in cur:
                yield {"id": row[0], "email": row[1], "plan": row[2] or "starter"}


def _unlock_stale_pending() -> int:
    """Crash recovery — 1h+ pending → failed (재시도 가능 상태).

    Gemini D+29 review_v2 HIGH: 스크립트 crash 시 status='pending' 영구 잠금 →
    해당 사용자 그 주 영원히 발송 X (유료 가치 누락 = 결함).
    """
    from api.auth import get_conn
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE email_deliveries
                SET status = 'failed',
                    error_summary = 'stale pending (crash recovery)'
                WHERE status = 'pending'
                  AND attempted_at < NOW() - INTERVAL '1 hour'
            """)
            return cur.rowcount


def _claim_delivery(subscriber_id: int, week: str) -> bool:
    """At-most-once 1단계: pending INSERT (또는 failed 이전 시도 재claim).

    Returns True if 새로 claim. False if 이미 sent / 다른 실행이 처리 중.
    failed 상태는 재시도 가능 — 같은 UNIQUE 키에 status 만 갱신.
    """
    from api.auth import get_conn
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO email_deliveries (subscriber_id, week_label, delivery_type, status, attempted_at)
                VALUES (%s, %s, 'weekly_digest', 'pending', NOW())
                ON CONFLICT (subscriber_id, week_label, delivery_type) DO UPDATE
                  SET status = 'pending', attempted_at = NOW(), error_summary = NULL
                  WHERE email_deliveries.status = 'failed'
                RETURNING (xmax = 0) AS inserted, status
            """, (subscriber_id, week))
            row = cur.fetchone()
            if not row:
                # CONFLICT 발생 + status='sent' (UPDATE WHERE 조건 미충족) → claim 실패
                return False
            return True


def _mark_sent(subscriber_id: int, week: str, message_id: Optional[str]) -> None:
    """At-most-once 2단계: sent UPDATE."""
    from api.auth import get_conn
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE email_deliveries
                SET status = 'sent', message_id = %s, sent_at = NOW()
                WHERE subscriber_id = %s
                  AND week_label = %s
                  AND delivery_type = 'weekly_digest'
            """, (message_id, subscriber_id, week))


def _mark_failed(subscriber_id: int, week: str, err_summary: str) -> None:
    """At-most-once 실패 분기: failed UPDATE — 다음 실행 시 재시도 가능."""
    from api.auth import get_conn
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE email_deliveries
                SET status = 'failed', error_summary = %s
                WHERE subscriber_id = %s
                  AND week_label = %s
                  AND delivery_type = 'weekly_digest'
            """, (err_summary[:200], subscriber_id, week))


def _unsub_token(subscriber_id: int) -> str:
    """HMAC-SHA256(secret, sid)[:8] → URL-safe base64. api/main.py 와 동일."""
    mac = hmac.new(_UNSUB_SECRET.encode(), str(subscriber_id).encode(), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(mac[:8]).decode().rstrip("=")


def _unsubscribe_link(subscriber_id: int) -> str:
    """HMAC token 포함 — 봇 enumeration 차단. BASE_URL 은 GH Pages 가 아닌 backend (api host)."""
    api_host = os.environ.get("API_BASE_URL", "").rstrip("/") or BASE_URL
    return f"{api_host}/unsubscribe?sid={subscriber_id}&token={_unsub_token(subscriber_id)}"


def _inject_unsubscribe(html_body: str, subscriber_id: int) -> str:
    """HTML 본문 끝 직전에 unsubscribe 푸터 삽입. </body> 못 찾으면 끝에 append."""
    footer = (
        f'<div style="margin-top:32px;padding-top:16px;border-top:1px solid #E7E5E4;'
        f'font-size:12px;color:#78716C;text-align:center">'
        f'You are receiving this as a StoreScope subscriber. '
        f'<a href="{_unsubscribe_link(subscriber_id)}" style="color:#78716C;text-decoration:underline">Unsubscribe</a>'
        f'</div>'
    )
    lower = html_body.lower()
    idx = lower.rfind("</body>")
    if idx >= 0:
        return html_body[:idx] + footer + html_body[idx:]
    return html_body + footer


def _send_smtp(to_email: str, subject: str, html_body: str, text_body: str) -> Optional[str]:
    """SMTP 발송. 성공 시 Message-Id 반환 (없으면 ''). 실패 시 raise."""
    msg = MIMEMultipart("alternative")
    msg["From"] = FROM_EMAIL
    msg["To"] = to_email
    msg["Subject"] = subject
    msg.attach(MIMEText(text_body, "plain", "utf-8"))
    msg.attach(MIMEText(html_body, "html", "utf-8"))
    with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=SMTP_TIMEOUT) as srv:
        srv.ehlo()
        srv.starttls()
        srv.login(SMTP_USER, SMTP_PASS)
        srv.send_message(msg)
    return msg.get("Message-Id", "")


def _build_text_fallback(plan: str, week: str) -> str:
    return (
        f"StoreScope Weekly Digest — Week {week}\n\n"
        f"Your {plan.title()} plan digest is ready (HTML version is the full report).\n\n"
        f"Home: {BASE_URL}/\n"
        f"Questions? Reply to this email.\n"
    )


def main(dry_run: bool = False) -> int:
    week = _iso_week()
    _log.info("Weekly digest run started: week=%s dry_run=%s", week, dry_run)

    # Pre-flight checks — production 환경에서 결정적 misconfig 차단.
    if IS_CI and not dry_run:
        problems = []
        if not os.environ.get("API_BASE_URL"):
            problems.append("API_BASE_URL missing — unsubscribe links will point to BASE_URL (GH Pages, dead)")
        if _UNSUB_SECRET_MISSING:
            problems.append("ADMIN_SECRET missing — all unsubscribe tokens will mismatch backend")
        if problems:
            _log.error("Pre-flight checks failed: %s", "; ".join(problems))
            try:
                from pipeline.alerting import send_alert
                send_alert(
                    "Weekly digest pre-flight 실패 — 발송 차단\n\n"
                    + "\n".join(f"- {p}" for p in problems)
                    + f"\n\nweek: {week}\n\n"
                    "📋 복구 절차 (Runbook):\n"
                    "1. GitHub repo → Settings → Secrets and variables → Actions\n"
                    "2. 누락 secret 등록:\n"
                    "   - API_BASE_URL = https://storescope-api.onrender.com\n"
                    "   - ADMIN_SECRET = Render env vars 의 ADMIN_SECRET 값 복사\n"
                    "3. 검증: Actions 탭 → Weekly Pipeline → Run workflow (수동 트리거)\n"
                    "4. 다음 일요일 23:00 UTC 까지 자동 재시도 — 이번 주 발송 손실 0건",
                    level="CRITICAL",
                )
            except Exception:
                pass
            return 0  # workflow non-block 유지, alert 로 가시화

    # Crash recovery — stale pending 자동 unlock (Gemini review_v2 HIGH)
    if not dry_run:
        try:
            unlocked = _unlock_stale_pending()
            if unlocked > 0:
                _log.info("Unlocked %d stale pending deliveries (>1h)", unlocked)
        except Exception:
            _log.exception("stale pending unlock failed — continuing")

    if not SMTP_HOST and not dry_run:
        msg = "SMTP_HOST 미설정 — 발송 skip"
        _log.error(msg)
        if IS_CI:
            try:
                from pipeline.alerting import send_alert
                send_alert(
                    f"Weekly digest 발송 skip — SMTP 미설정\n"
                    f"week: {week}\n"
                    f"조치: SMTP_HOST/USER/PASS/FROM env 설정",
                    level="CRITICAL",
                )
            except Exception:
                pass
        return 0  # non-block: workflow 성공 유지

    # weekly_digest generate 는 plan별 1회만 (모든 starter 가 같은 HTML, pro 도 동일)
    from services.weekly_digest import generate
    cached_html: dict[str, str] = {}

    counts = {"total": 0, "claimed": 0, "sent": 0, "skipped": 0, "failed": 0}
    failures: list[str] = []   # email + err — batch alert 용

    try:
        for sub in _iter_active_subscribers():
            counts["total"] += 1
            sid, email, plan = sub["id"], sub["email"], sub["plan"]

            # 1. claim — pending INSERT (UNIQUE 충돌 시 skip)
            if dry_run:
                claimed = True
            else:
                try:
                    claimed = _claim_delivery(sid, week)
                except Exception as exc:
                    _log.exception("claim 실패: subscriber_id=%s", sid)
                    failures.append(f"{email} (claim: {exc!r})")
                    continue
            if not claimed:
                counts["skipped"] += 1
                continue
            counts["claimed"] += 1

            # 2. 발송 — HTML cache per plan
            if plan not in cached_html:
                try:
                    cached_html[plan] = generate(plan=plan)["html"]
                except Exception as exc:
                    _log.exception("digest generate 실패: plan=%s", plan)
                    failures.append(f"{email} (generate: {exc!r})")
                    if not dry_run:
                        _mark_failed(sid, week, f"generate: {exc!r}")
                    continue
            html_body = _inject_unsubscribe(cached_html[plan], sid)
            text_body = _build_text_fallback(plan, week)
            subject = f"StoreScope — This week's spreading products ({week})"

            if dry_run:
                _log.info("[DRY-RUN] would send to=%s plan=%s", email, plan)
                counts["sent"] += 1
                continue

            # 3. SMTP 발송
            try:
                message_id = _send_smtp(email, subject, html_body, text_body)
                _mark_sent(sid, week, message_id)
                counts["sent"] += 1
                _log.info("sent: to=%s plan=%s msg_id=%s", email, plan, message_id)
            except Exception as exc:
                _log.exception("SMTP 발송 실패: to=%s plan=%s", email, plan)
                _mark_failed(sid, week, repr(exc))
                failures.append(f"{email} ({plan}): {exc!r}")
                counts["failed"] += 1
                # continue — 다음 구독자 계속
    except Exception as exc:
        # iterator 자체 fail (DB 장애 등) — 즉시 CRITICAL alert
        _log.exception("Subscriber iteration failed catastrophically")
        if IS_CI and not dry_run:
            try:
                from pipeline.alerting import send_alert
                send_alert(
                    f"Weekly digest 발송 중단 (catastrophic)\n"
                    f"week: {week}\n"
                    f"error: {exc!r}\n"
                    f"counts: {counts}",
                    level="CRITICAL",
                )
            except Exception:
                pass
        return 1

    # ── Batch alert (Gemini HIGH) ────────────────────────────
    _log.info(
        "Weekly digest run done: week=%s total=%d claimed=%d sent=%d skipped=%d failed=%d",
        week, counts["total"], counts["claimed"], counts["sent"], counts["skipped"], counts["failed"],
    )

    if failures and IS_CI and not dry_run:
        # 100건 실패 → 100 alert X. 누적 후 1회 summary.
        try:
            from pipeline.alerting import send_alert
            preview = "\n".join(failures[:10])
            more = f"\n... +{len(failures) - 10} more" if len(failures) > 10 else ""
            send_alert(
                f"Weekly digest 발송 실패 (batch)\n"
                f"week: {week}\n"
                f"total: {counts['total']} / failed: {counts['failed']}\n"
                f"failures:\n{preview}{more}",
                level="WARNING",
            )
        except Exception:
            pass

    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true", help="DB 발신 X, 콘솔만 (smoke test)")
    args = p.parse_args()
    sys.exit(main(dry_run=args.dry_run))
