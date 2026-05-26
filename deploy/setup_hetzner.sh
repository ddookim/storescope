#!/bin/bash
# StoreScope — Hetzner Ubuntu 24.04 초기 서버 세팅
# 실행: bash setup_hetzner.sh
#
# 전제 조건:
#   - Ubuntu 24.04 LTS 신규 서버
#   - root 또는 sudo 권한 사용자
#   - 도메인 DNS A 레코드가 이 서버 IP를 가리키고 있어야 SSL 발급 가능
set -euo pipefail

# FIX(1): noninteractive 모드 선언 — apt 실행 중 설정 파일 덮어쓰기 프롬프트가 떠서 무인 스크립트가 hang 되는 것을 원천 차단
export DEBIAN_FRONTEND=noninteractive

# FIX(2): 스크립트 파일이 위치한 디렉터리로 작업 경로 강제 전환 — 어느 경로에서 실행해도 cp 등 상대 경로 명령이 실패하지 않음
cd "$(dirname "$0")"

APP_DIR="/opt/storescope"
APP_USER="storescope"

# FIX(1): --force-confdef/--force-confold 옵션 추가 — 기존 설정 파일 충돌 시 자동으로 현재 파일 유지, 프롬프트 없이 진행
APT_OPTS='-o Dpkg::Options::="--force-confdef" -o Dpkg::Options::="--force-confold"'

echo "=== 1. 시스템 업데이트 ==="
apt-get update
eval apt-get upgrade -y "$APT_OPTS"
eval apt-get install -y "$APT_OPTS" git curl ufw

echo "=== 2. Docker 설치 ==="
curl -fsSL https://get.docker.com | sh
apt-get install -y docker-compose-plugin
systemctl enable --now docker

echo "=== 2-1. docker-rollout 설치 (zero-downtime deploy) ==="
# FIX: push.sh --restart 에서 docker rollout 명령 사용 — 미설치 시 배포 실패
curl -fsSL https://raw.githubusercontent.com/wowu/docker-rollout/master/docker-rollout \
    -o /usr/local/lib/docker/cli-plugins/docker-rollout
chmod +x /usr/local/lib/docker/cli-plugins/docker-rollout

echo "=== 3. Nginx + Certbot 설치 ==="
eval apt-get install -y "$APT_OPTS" nginx certbot python3-certbot-nginx
systemctl enable --now nginx

echo "=== 4. 방화벽 설정 ==="
# FIX(3): Docker·Nginx 설치 완료 후 UFW 적용 — 설치 중 네트워크 차단 방지
# OpenSSH·HTTP·HTTPS만 허용, 나머지 인바운드 전부 차단
ufw --force reset
ufw default deny incoming
ufw default allow outgoing
ufw allow OpenSSH
ufw allow 80/tcp
ufw allow 443/tcp
ufw --force enable
echo "  방화벽 활성화 완료 (22, 80, 443 허용)"

echo "=== 5. 앱 유저 생성 ==="
id -u "$APP_USER" &>/dev/null || useradd -r -s /bin/bash -d "$APP_DIR" "$APP_USER"
usermod -aG docker "$APP_USER"

echo "=== 6. 앱 디렉터리 준비 ==="
mkdir -p "$APP_DIR"
chown "$APP_USER:$APP_USER" "$APP_DIR"

echo "=== 7. systemd 유닛 등록 ==="
cp weekly_pipeline.sh /usr/local/bin/weekly_pipeline.sh
cp backup_db.sh       /usr/local/bin/backup_db.sh
chmod +x /usr/local/bin/weekly_pipeline.sh /usr/local/bin/backup_db.sh
cp storescope-pipeline.service storescope-pipeline.timer \
   storescope-health-check.service storescope-health-check.timer \
   storescope-backup.service storescope-backup.timer \
   /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now storescope-pipeline.timer
systemctl enable --now storescope-health-check.timer
# FIX: pg_dump 자동 백업 타이머 등록 — 단일 pgdata 볼륨 장애점 제거
systemctl enable --now storescope-backup.timer
echo "  파이프라인 타이머 등록 완료 (매주 토요일 02:55 KST)"
echo "  헬스체크 타이머 등록 완료 (매일 09:00 KST)"
echo "  DB 백업 타이머 등록 완료 (매일 03:00 KST → /opt/storescope/backups/)"

echo ""
echo "================================================================"
echo " 설치 완료. 이제 아래 순서로 배포를 진행하세요."
echo "================================================================"
echo ""
echo "[ 1단계 ] 코드 배포"
echo "  bash push.sh SERVER_IP --init"
echo ""
echo "[ 2단계 ] 환경변수 설정"
echo "  cp $APP_DIR/.env.example $APP_DIR/.env"
echo "  nano $APP_DIR/.env   # 실제 값 입력"
echo "  chown $APP_USER:$APP_USER $APP_DIR/.env && chmod 600 $APP_DIR/.env"
echo ""
echo "[ 3단계 ] DB + 앱 기동"
echo "  cd $APP_DIR"
echo "  docker compose up -d db"
echo "  sleep 5"
echo "  docker compose up -d"
echo ""
echo "[ 4단계 ] Nginx 설정"
echo "  sed -i 's/YOUR_DOMAIN.com/실제도메인.com/g' $APP_DIR/deploy/nginx.conf"
echo "  cp $APP_DIR/deploy/nginx.conf /etc/nginx/sites-available/storescope"
echo "  ln -sf /etc/nginx/sites-available/storescope /etc/nginx/sites-enabled/"
echo "  rm -f /etc/nginx/sites-enabled/default"
echo "  mkdir -p /var/www/certbot"
echo "  nginx -t && systemctl reload nginx"
echo ""
echo "[ 5단계 ] SSL 인증서 발급"
echo "  certbot certonly --webroot -w /var/www/certbot -d api.실제도메인.com -d app.실제도메인.com"
echo "  systemctl reload nginx"
echo ""
echo "[ 6단계 ] 정상 확인"
echo "  curl https://api.실제도메인.com/health"
echo "  docker compose ps"
echo "  systemctl list-timers storescope*"
