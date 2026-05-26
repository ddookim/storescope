#!/bin/bash
# 로컬 → Hetzner 서버 코드 동기화
# 사용: bash deploy/push.sh SERVER_IP [--restart]
#
# 최초 1회: bash deploy/push.sh SERVER_IP --init
# 이후 업데이트: bash deploy/push.sh SERVER_IP --restart
set -euo pipefail

SERVER_IP="${1:?사용법: $0 SERVER_IP [--init|--restart]}"
MODE="${2:-}"
APP_DIR="/opt/storescope"
REMOTE="root@$SERVER_IP"

echo ">>> 코드 동기화: $SERVER_IP"
rsync -avz --progress \
    --exclude '.env' \
    --exclude '.git' \
    --exclude '__pycache__' \
    --exclude '*.pyc' \
    --exclude 'data/' \
    --exclude '.dockerignore' \
    ./ "$REMOTE:$APP_DIR/"

if [ "$MODE" = "--init" ]; then
    echo ">>> 초기 설정 실행"
    ssh "$REMOTE" "bash $APP_DIR/deploy/setup_hetzner.sh"

elif [ "$MODE" = "--restart" ]; then
    echo ">>> 무중단 배포 시작"
    # FIX: docker compose up -d 는 zero-downtime 아님 — docker rollout 사용
    # setup_hetzner.sh 에서 docker-rollout 설치됨
    ssh "$REMOTE" "
        cd $APP_DIR && \
        docker compose --env-file .env build api streamlit && \
        docker rollout api --timeout 120 && \
        docker rollout streamlit --timeout 120
    "
    echo ">>> 배포 완료"
    ssh "$REMOTE" "cd $APP_DIR && docker compose ps"
fi

echo ">>> 완료"
