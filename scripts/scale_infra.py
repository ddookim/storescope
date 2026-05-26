"""
StoreScope — DigitalOcean Droplet 자동 ON/OFF
==============================================
파이프라인 실행 전 Droplet 기동, 완료 후 셧다운.
월 ~$24 Droplet을 주 1회 4시간만 켜면 월 $0.57 수준으로 절감.

환경변수:
    DO_API_TOKEN   — DigitalOcean Personal Access Token (Read+Write)
    DO_DROPLET_ID  — 대상 Droplet ID

사용 예:
    python -m scripts.scale_infra power_on
    python -m scripts.scale_infra power_off
    python -m scripts.scale_infra status
"""

import argparse
import json
import os
import sys
import time
import urllib.request
from typing import Optional

_API_BASE = "https://api.digitalocean.com/v2"
_TIMEOUT  = 15   # 초

_TOKEN      = os.environ.get("DO_API_TOKEN", "")
_DROPLET_ID = os.environ.get("DO_DROPLET_ID", "")

_POLL_INTERVAL = 10   # 상태 폴링 간격 (초)
_POLL_TIMEOUT  = 300  # 최대 대기 (초)


def _headers() -> dict:
    if not _TOKEN:
        print("[scale_infra] DO_API_TOKEN 미설정 — 스텁 모드", flush=True)
    return {
        "Authorization": f"Bearer {_TOKEN}",
        "Content-Type":  "application/json",
    }


def _request(method: str, path: str, body: Optional[dict] = None) -> dict:
    url  = f"{_API_BASE}{path}"
    data = json.dumps(body).encode() if body else None
    req  = urllib.request.Request(url, data=data, headers=_headers(), method=method)
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
            raw = resp.read()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        body_text = exc.read().decode(errors="replace")
        raise RuntimeError(f"DO API {method} {path} → HTTP {exc.code}: {body_text}") from exc


def _get_status() -> str:
    """Droplet 현재 상태 반환 ('active', 'off', 'new', ...)."""
    data = _request("GET", f"/droplets/{_DROPLET_ID}")
    return data.get("droplet", {}).get("status", "unknown")


def _do_action(action_type: str) -> None:
    _request("POST", f"/droplets/{_DROPLET_ID}/actions", {"type": action_type})


def _wait_for_status(target: str) -> None:
    """target 상태가 될 때까지 폴링."""
    deadline = time.monotonic() + _POLL_TIMEOUT
    while time.monotonic() < deadline:
        status = _get_status()
        print(f"  상태: {status}", flush=True)
        if status == target:
            return
        time.sleep(_POLL_INTERVAL)
    raise TimeoutError(f"Droplet이 {_POLL_TIMEOUT}초 내에 '{target}' 상태가 되지 않음")


def power_on() -> None:
    if not _TOKEN or not _DROPLET_ID:
        print("[scale_infra] DO_API_TOKEN 또는 DO_DROPLET_ID 미설정 — 건너뜀", flush=True)
        return

    current = _get_status()
    if current == "active":
        print(f"[scale_infra] Droplet {_DROPLET_ID} 이미 실행 중", flush=True)
        return

    print(f"[scale_infra] Droplet {_DROPLET_ID} 기동 중...", flush=True)
    _do_action("power_on")
    _wait_for_status("active")
    print(f"[scale_infra] Droplet {_DROPLET_ID} 기동 완료", flush=True)


def power_off() -> None:
    if not _TOKEN or not _DROPLET_ID:
        print("[scale_infra] DO_API_TOKEN 또는 DO_DROPLET_ID 미설정 — 건너뜀", flush=True)
        return

    current = _get_status()
    if current == "off":
        print(f"[scale_infra] Droplet {_DROPLET_ID} 이미 정지됨", flush=True)
        return

    print(f"[scale_infra] Droplet {_DROPLET_ID} 셧다운 중...", flush=True)
    _do_action("shutdown")
    _wait_for_status("off")
    print(f"[scale_infra] Droplet {_DROPLET_ID} 셧다운 완료", flush=True)


def status() -> None:
    if not _TOKEN or not _DROPLET_ID:
        print("[scale_infra] DO_API_TOKEN 또는 DO_DROPLET_ID 미설정", flush=True)
        return
    s = _get_status()
    print(f"[scale_infra] Droplet {_DROPLET_ID} 상태: {s}", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="DigitalOcean Droplet ON/OFF")
    parser.add_argument(
        "action",
        choices=["power_on", "power_off", "status"],
        help="실행할 액션",
    )
    args = parser.parse_args()

    try:
        {"power_on": power_on, "power_off": power_off, "status": status}[args.action]()
    except Exception as exc:
        print(f"[scale_infra] 오류: {exc}", flush=True)
        sys.exit(1)
