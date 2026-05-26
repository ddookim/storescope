"""
StoreScope — Telegram 알림 모듈
=================================
원칙: No news is good news.
  - 정상 상태 → 완전 무음 (알림 없음)
  - 이상 감지 → level에 따라 Telegram 메시지 발송

환경변수:
    TELEGRAM_BOT_TOKEN  — BotFather에서 발급
    TELEGRAM_CHAT_ID    — 봇을 추가한 채팅/채널 ID

미설정 시: 콘솔 출력으로 fallback (로컬 개발용)
"""

import os
import urllib.request
import urllib.parse
import json
import logging
from typing import Optional

_log = logging.getLogger(__name__)

_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
_CHAT_ID   = os.environ.get("TELEGRAM_CHAT_ID", "")

_LEVEL_PREFIX = {
    "INFO":     "ℹ️",
    "WARNING":  "⚠️",
    "ERROR":    "🔴",
    "CRITICAL": "🚨",
}


def send_alert(message: str, level: str = "ERROR") -> None:
    """
    Telegram으로 알림 발송.
    TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID 미설정 시 콘솔 출력으로 fallback.
    네트워크 오류는 조용히 무시 — 알림 실패로 파이프라인이 중단되어선 안 됨.
    """
    prefix = _LEVEL_PREFIX.get(level.upper(), "📢")
    text   = f"{prefix} [StoreScope {level.upper()}]\n{message}"

    if not _BOT_TOKEN or not _CHAT_ID:
        print(f"[ALERT STUB] {text}")
        return

    payload = json.dumps({
        "chat_id":    _CHAT_ID,
        "text":       text,
        "parse_mode": "HTML",
    }).encode()

    url = f"https://api.telegram.org/bot{_BOT_TOKEN}/sendMessage"
    req = urllib.request.Request(url, data=payload,
                                  headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            if resp.status != 200:
                _log.warning("Telegram 알림 실패: HTTP %s", resp.status)
    except Exception as exc:
        # 알림 발송 실패는 non-fatal — 파이프라인 계속 진행
        _log.warning("Telegram 알림 발송 오류 (무시): %s", exc)


def send_pipeline_start(step_name: str) -> None:
    """파이프라인 단계 시작 알림 — 필요 시 사용 (기본은 무음)."""
    send_alert(f"파이프라인 시작: {step_name}", level="INFO")


def send_pipeline_success(step_name: str, elapsed: float, stats: Optional[dict] = None) -> None:
    """파이프라인 단계 성공 — 완전 무음 (No news is good news)."""
    # 정상 완료는 알림하지 않음 — 마스터플랜 원칙 1번
    pass


def send_pipeline_failure(step_name: str, error: str, elapsed: float) -> None:
    """파이프라인 단계 실패 → CRITICAL 발송."""
    send_alert(
        f"단계: {step_name}\n"
        f"오류: {error[:300]}\n"
        f"경과: {elapsed:.0f}초",
        level="CRITICAL",
    )
