#!/bin/bash
# FIX: pg_dump 자동화 백업 — pgdata 볼륨 단일 장애점 제거.
# 로컬 7일 보관 + Backblaze B2 무료 티어 오프사이트 격리로 랜섬웨어 대응.
# 설치: systemd timer (storescope-backup.timer) 또는 cron:
#   0 3 * * * /usr/local/bin/backup_db.sh >> /var/log/storescope/backup.log 2>&1
set -euo pipefail

APP_DIR="/opt/storescope"
BACKUP_DIR="$APP_DIR/backups"
DB_CONTAINER="storescope-db-1"   # docker compose 기본 네이밍: <project>-<service>-<n>
DB_NAME="storescope"
DB_USER="shopify"
KEEP_DAYS=7
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
BACKUP_FILE="$BACKUP_DIR/storescope_${TIMESTAMP}.sql.gz"

# Telegram 알림 헬퍼 (alerting.py와 동일 채널)
_tg_alert() {
    local msg="$1"
    local token="${TELEGRAM_BOT_TOKEN:-}"
    local chat="${TELEGRAM_CHAT_ID:-}"
    if [ -n "$token" ] && [ -n "$chat" ]; then
        curl -sS -X POST "https://api.telegram.org/bot${token}/sendMessage" \
            -d chat_id="$chat" \
            -d text="$msg" \
            --max-time 10 > /dev/null 2>&1 || true
    else
        echo "[ALERT] $msg"
    fi
}

# .env에서 환경변수 로드 (systemd OnCalendar 실행 시 환경 없음)
if [ -f "$APP_DIR/.env" ]; then
    # shellcheck disable=SC1090
    set -a && source "$APP_DIR/.env" && set +a
fi

mkdir -p "$BACKUP_DIR"

echo "[$(date)] 백업 시작: $BACKUP_FILE"

# FIX: docker exec로 컨테이너 내부 pg_dump 실행 → 네트워크 노출 없이 dump
if ! docker exec "$DB_CONTAINER" \
        pg_dump -U "$DB_USER" --no-password "$DB_NAME" \
        | gzip -9 > "$BACKUP_FILE"; then
    _tg_alert "🔴 [StoreScope CRITICAL] DB 백업 실패 — 즉시 확인 필요 ($TIMESTAMP)"
    exit 1
fi

SIZE=$(du -h "$BACKUP_FILE" | cut -f1)
echo "[$(date)] 백업 완료: $SIZE"

# FIX: 백업 무결성 2중 검증 — 0바이트 또는 손상된 gzip을 7일 보관 후 실제 복구 시점에 발견하는 상황 방지.
# (1) 최소 크기 검사: storescope DB가 정상 운영 중이라면 10KB 미만은 이상 신호
MIN_BACKUP_KB=10
ACTUAL_KB=$(du -k "$BACKUP_FILE" | cut -f1)
if [ "$ACTUAL_KB" -lt "$MIN_BACKUP_KB" ]; then
    _tg_alert "🔴 [StoreScope CRITICAL] 백업 파일 크기 이상: ${ACTUAL_KB}KB (최소 ${MIN_BACKUP_KB}KB) — dump 실패 가능성"
    rm -f "$BACKUP_FILE"
    exit 1
fi

# (2) gzip 헤더/CRC 무결성 검사 — 압축 손상 여부 확인
if ! gzip -t "$BACKUP_FILE" 2>/dev/null; then
    _tg_alert "🔴 [StoreScope CRITICAL] 백업 gzip 손상 ($BACKUP_FILE) — 즉시 수동 백업 필요"
    rm -f "$BACKUP_FILE"
    exit 1
fi

echo "[$(date)] 무결성 검증 통과 (${ACTUAL_KB}KB)"

# 로컬 보관 로테이션 (KEEP_DAYS일 초과분 삭제)
find "$BACKUP_DIR" -name "storescope_*.sql.gz" -mtime +"$KEEP_DAYS" -delete
REMAINING=$(find "$BACKUP_DIR" -name "storescope_*.sql.gz" | wc -l)
echo "[$(date)] 보관 중: ${REMAINING}개 파일"

# FIX: rclone이 설정된 경우 Backblaze B2 오프사이트 동기화
# 설정: rclone config → b2 provider → 버킷명 storescope-backups
# 무료 티어: 10GB 스토리지 / 월, 1GB 다운로드 / 일 무료
if command -v rclone &>/dev/null; then
    if rclone listremotes 2>/dev/null | grep -q "^b2:"; then
        if rclone copy "$BACKUP_FILE" b2:storescope-backups/ \
                --log-level ERROR --retries 3; then
            echo "[$(date)] B2 업로드 완료"
        else
            _tg_alert "⚠️ [StoreScope WARNING] B2 오프사이트 백업 실패 — 로컬 백업은 정상"
        fi
    fi
fi

echo "[$(date)] 백업 파이프라인 완료"
