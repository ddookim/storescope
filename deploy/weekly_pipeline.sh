#!/bin/bash
# 매주 토요일 새벽 02:55 실행: docker compose run --rm pipeline (일회성)
# 설치: sudo cp weekly_pipeline.sh /usr/local/bin/ && sudo chmod +x /usr/local/bin/weekly_pipeline.sh
#        sudo cp storescope-pipeline.service storescope-pipeline.timer /etc/systemd/system/
#        sudo systemctl daemon-reload && sudo systemctl enable --now storescope-pipeline.timer

set -euo pipefail

APP_DIR="/opt/storescope"
LOG_DIR="/var/log/storescope"
LOG_FILE="$LOG_DIR/pipeline_$(date +%Y%m%d_%H%M%S).log"

mkdir -p "$LOG_DIR"

# .env에서 Telegram 자격증명 로드 (systemd 환경에 환경변수 없음)
if [ -f "$APP_DIR/.env" ]; then
    # shellcheck disable=SC1090
    set -a && source "$APP_DIR/.env" && set +a
fi

# FIX: 파이프라인 실패 시 즉시 Telegram CRITICAL 알림 — 30시간 무감지 gap 제거.
# alerting.py와 동일 채널 사용, curl 의존성만으로 동작 (Python 불필요)
_tg_alert() {
    local token="${TELEGRAM_BOT_TOKEN:-}"
    local chat="${TELEGRAM_CHAT_ID:-}"
    if [ -n "$token" ] && [ -n "$chat" ]; then
        curl -sS -X POST "https://api.telegram.org/bot${token}/sendMessage" \
            -d chat_id="$chat" \
            -d text="$1" \
            --max-time 10 > /dev/null 2>&1 || true
    else
        echo "[ALERT] $1"
    fi
}

_exit_code=0
trap '_exit_code=$?
    if [ "$_exit_code" -eq 0 ]; then
        echo "[$(date)] 파이프라인 완료 (exit 0)" | tee -a "$LOG_FILE"
    else
        echo "[$(date)] 파이프라인 실패 (exit $_exit_code)" | tee -a "$LOG_FILE"
        _tg_alert "🚨 [StoreScope CRITICAL] 주간 파이프라인 실패 (exit $_exit_code)
로그: '"$LOG_FILE"'
서버에서 확인: journalctl -u storescope-pipeline -n 50"
    fi
    find "$LOG_DIR" -name "pipeline_*.log" -mtime +30 -delete
    exit "$_exit_code"
' EXIT

echo "[$(date)] 파이프라인 시작" | tee "$LOG_FILE"

# FIX(2): -f 옵션 제거 후 APP_DIR로 이동 — docker compose는 현재 디렉터리 기준으로
# 프로젝트 이름(네트워크 이름)을 결정하므로, 반드시 프로젝트 루트에서 실행해야
# api/db/streamlit 서비스와 동일한 Docker 네트워크(storescope_default)에 합류할 수 있음
cd "$APP_DIR"

# FIX(1): -T 플래그 추가 — systemd/cron 환경은 TTY가 없어 docker compose run이
# "the input device is not a TTY" 에러로 즉시 종료됨; -T로 가상 터미널 할당 비활성화
docker compose --env-file .env --profile pipeline \
    run --rm -T pipeline \
    2>&1 | tee -a "$LOG_FILE"
