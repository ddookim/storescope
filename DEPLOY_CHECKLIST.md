# StoreScope 배포 체크리스트

## 현재 상태 (2026-05-26 기준)
- [x] 코드 완성 + 보안 감사 완료
- [x] DB: 1,671 clusters / 140,943 products / 1,419 stores
- [x] 로컬 FastAPI (:8000) + Streamlit (:8501) 정상 동작
- [x] Paddle Price ID 설정됨 (Starter / Pro)

---

## STEP 1 — .env 완성 (30분, 유저 필수)

```bash
# 각 값을 실제로 채울 것
nano "/Users/dodokim/Documents/Claude/Projects/Auto-Biz Factory/ShopifyIntel/.env"
```

| 변수 | 취득 방법 | 예상 시간 |
|------|-----------|-----------|
| `ADMIN_SECRET` | `openssl rand -hex 32` | 1분 |
| `PADDLE_API_KEY` | paddle.com → Developer → API Keys | 2분 |
| `PADDLE_WEBHOOK_SECRET` | paddle.com → Notifications → New destination | 3분 |
| `TELEGRAM_BOT_TOKEN` | Telegram → @BotFather → /newbot | 3분 |
| `TELEGRAM_CHAT_ID` | 봇에게 메시지 후 getUpdates API 조회 | 2분 |
| `SMTP_PASS` | Google 계정 → 보안 → 앱 비밀번호 | 3분 |

---

## STEP 2 — Hetzner 서버 생성 (10분, 유저 필수)

1. hetzner.com → Cloud → 프로젝트 생성 → Add Server
2. 설정: **Ubuntu 24.04** / **CX21 (€4.15/월)** / Frankfurt
3. SSH 키 등록 (로컬 `~/.ssh/id_rsa.pub` 붙여넣기)
4. 서버 생성 → IP 주소 메모

```bash
# SSH 키 없으면 생성
ssh-keygen -t ed25519 -f ~/.ssh/storescope_hetzner
cat ~/.ssh/storescope_hetzner.pub   # Hetzner에 붙여넣기
```

---

## STEP 3 — 도메인 DNS 설정 (15분, 유저 필수)

도메인 구매 후 (Namecheap ~$10/년):
```
A  api.yourdomain.com  →  [Hetzner IP]
A  app.yourdomain.com  →  [Hetzner IP]
```
DNS 전파 대기: 5~30분

---

## STEP 4 — 서버 초기화 + 코드 배포 (20분, 명령어만 실행)

```bash
PROJECT="/Users/dodokim/Documents/Claude/Projects/Auto-Biz Factory/ShopifyIntel"
SERVER_IP="[Hetzner IP]"

# 1. 코드 + 초기 설정 (1회)
cd "$PROJECT"
bash deploy/push.sh "$SERVER_IP" --init

# 2. .env 서버에 복사
scp .env root@$SERVER_IP:/opt/storescope/.env
ssh root@$SERVER_IP "chmod 600 /opt/storescope/.env"

# 3. nginx 도메인 교체
DOMAIN="yourdomain.com"
ssh root@$SERVER_IP "
  sed -i 's/YOUR_DOMAIN.com/$DOMAIN/g' /opt/storescope/deploy/nginx.conf
  cp /opt/storescope/deploy/nginx.conf /etc/nginx/sites-available/storescope
  ln -sf /etc/nginx/sites-available/storescope /etc/nginx/sites-enabled/
  rm -f /etc/nginx/sites-enabled/default
  mkdir -p /var/www/certbot
  nginx -t && systemctl reload nginx
"

# 4. DB + 앱 기동
ssh root@$SERVER_IP "
  cd /opt/storescope
  docker compose up -d db
  sleep 8
  docker compose up -d api streamlit
"

# 5. SSL 발급
ssh root@$SERVER_IP "
  certbot certonly --webroot -w /var/www/certbot \
    -d api.$DOMAIN -d app.$DOMAIN \
    --non-interactive --agree-tos -m dodo32032@gmail.com
  systemctl reload nginx
"

# 6. 검증
curl https://api.$DOMAIN/health
```

---

## STEP 5 — 데이터 서버로 전송 (10분)

```bash
PROJECT="/Users/dodokim/Documents/Claude/Projects/Auto-Biz Factory/ShopifyIntel"
SERVER_IP="[Hetzner IP]"

# clusters.json + trending.json + products/ 디렉터리 전송
rsync -avz --progress \
  "$PROJECT/data/clusters.json" \
  "$PROJECT/data/trending.json" \
  root@$SERVER_IP:/opt/storescope/data/

# 서버에서 load_to_db 실행
ssh root@$SERVER_IP "
  cd /opt/storescope
  docker compose exec api python3 -m pipeline.load_to_db
"
```

---

## STEP 6 — Paddle 웹훅 URL 등록 (5분, 유저 필수)

paddle.com → Notifications → New destination:
- **URL:** `https://api.yourdomain.com/billing/webhook`
- **Events:** `subscription.activated`, `subscription.canceled`, `subscription.paused`, `transaction.payment_failed`

---

## STEP 7 — 런치 (5분)

1. [LAUNCH_ASSETS.md](LAUNCH_ASSETS.md) 에서 Reddit 포스트 복사
2. r/dropship → New Post → 붙여넣기
3. r/shopify → 동일 포스트

---

## 검증 명령어 모음

```bash
DOMAIN="yourdomain.com"

# 헬스체크
curl https://api.$DOMAIN/health

# 트렌딩 (API 키 없으면 401 — 정상)
curl https://api.$DOMAIN/trending

# Streamlit 접근
open https://app.$DOMAIN

# 서버 상태
ssh root@SERVER_IP "docker compose ps && systemctl list-timers storescope*"

# 로그 확인
ssh root@SERVER_IP "docker compose logs api --tail=50"
```
