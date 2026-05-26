# StoreScope 배포 체크리스트 (무료, 카드 없음)

## 현재 상태 (2026-05-26 기준)
- [x] 코드 완성 + 보안 감사 완료
- [x] DB: 1,671 clusters / 140,943 products / 1,419 stores
- [x] 로컬 FastAPI(:8000) + Streamlit(:8501) 정상 동작
- [x] Paddle Price ID 설정됨 (Starter / Pro)
- [x] git init 완료 (main 브랜치)

---

## 배포 전략 (단계별)

| 단계 | 방식 | 비용 | 조건 |
|------|------|------|------|
| 지금 | **Cloudflare Tunnel** | $0 | Mac 켜져 있을 때만 동작 |
| 단기 | **Render.com** | $0 | GitHub 계정만 필요, 카드 없음 |
| 중기 | **DigitalOcean** | $0 | GitHub Student Pack 승인 후 ($200 크레딧) |

---

## PHASE 1 — Cloudflare Tunnel (지금 바로, 30분)

로컬 서버를 인터넷에 임시 노출. Mac이 켜져 있는 동안 동작.

```bash
# 1. cloudflared 설치 (이미 설치돼 있으면 스킵)
brew install cloudflared

# 2. Cloudflare 로그인 (브라우저 열림)
cloudflared tunnel login

# 3. 터널 생성
cloudflared tunnel create storescope

# 4. 설정 파일 생성 — <tunnel-id>는 위 명령 출력값으로 교체
mkdir -p ~/.cloudflared
cat > ~/.cloudflared/config.yml << 'EOF'
tunnel: storescope
credentials-file: ~/.cloudflared/<tunnel-id>.json

ingress:
  - hostname: api.storescope.pages.dev
    service: http://localhost:8000
  - hostname: app.storescope.pages.dev
    service: http://localhost:8501
  - service: http_status:404
EOF

# 5. DNS 등록 (Cloudflare 대시보드에서 도메인 없어도 *.trycloudflare.com 무료 제공)
cloudflared tunnel route dns storescope api.storescope.pages.dev

# 6. 터널 실행
cloudflared tunnel run storescope
```

검증:
```bash
curl https://api.storescope.pages.dev/health
```

---

## PHASE 2 — Render.com 무료 배포 (영구 서버, GitHub 계정만 필요)

### 준비: GitHub 레포 생성

```bash
cd "/Users/dodokim/Documents/Claude/Projects/Auto-Biz Factory/StoreScope"
gh repo create storescope --private --source=. --push
```

### Render.com 서비스 생성 (브라우저)

1. render.com → GitHub으로 로그인
2. **New PostgreSQL** → Name: `storescope-db` → 생성 후 Internal Database URL 복사
3. **New Web Service** (FastAPI)
   - Repository: `storescope`
   - Build Command: `pip install -r requirements.txt`
   - Start Command: `uvicorn api.main:app --host 0.0.0.0 --port $PORT`
   - Instance: Free
4. **New Web Service** (Streamlit)
   - Repository: `storescope`
   - Start Command: `streamlit run app.py --server.port $PORT --server.address 0.0.0.0`
   - Instance: Free

### 환경변수 (각 서비스 → Environment 탭에 입력)

```
DATABASE_URL=<Render PostgreSQL Internal URL>
ADMIN_SECRET=<아래 명령으로 생성>
PADDLE_API_KEY=pdl_live_XXXXX
PADDLE_WEBHOOK_SECRET=XXXXX
PADDLE_STARTER_PRICE_ID=pri_XXXXX
PADDLE_PRO_PRICE_ID=pri_XXXXX
ALLOWED_ORIGINS=https://storescope-app.onrender.com
APP_URL=https://storescope-app.onrender.com
API_BASE_URL=https://storescope-api.onrender.com
```

```bash
# ADMIN_SECRET 생성
openssl rand -hex 32
```

### 기존 DB 데이터 → Render DB로 이전

```bash
# 로컬 storescope DB를 Render DB로 복사 (External URL 사용)
pg_dump storescope | psql "<Render PostgreSQL External URL>"
```

### 슬립 방지 (무료 플랜: 15분 비활성 시 슬립)

cron-job.org 무료 가입 → New Cron Job:
- URL: `https://storescope-api.onrender.com/health`
- Schedule: `*/14 * * * *` (14분마다 ping)

---

## PHASE 3 — DigitalOcean (Student Pack 승인 후)

GitHub Student Pack 승인 확인: https://education.github.com/pack

승인되면:
```bash
# DigitalOcean $200 크레딧 활성화 후
PROJECT="/Users/dodokim/Documents/Claude/Projects/Auto-Biz Factory/StoreScope"
SERVER_IP="[DO Droplet IP]"
cd "$PROJECT"
bash deploy/push.sh "$SERVER_IP" --init
scp .env root@$SERVER_IP:/opt/storescope/.env
```

---

## 환경변수 취득 방법

| 변수 | 취득 방법 | 예상 시간 |
|------|-----------|-----------|
| `ADMIN_SECRET` | `openssl rand -hex 32` | 1분 |
| `PADDLE_API_KEY` | paddle.com → Developer → API Keys | 2분 |
| `PADDLE_WEBHOOK_SECRET` | Paddle → Notifications → New destination | 3분 |
| `TELEGRAM_BOT_TOKEN` | @BotFather → /newbot (선택) | 3분 |
| `TELEGRAM_CHAT_ID` | bot에 메시지 후 getUpdates (선택) | 2분 |

---

## 검증 명령어

```bash
# 로컬
curl http://localhost:8000/health

# Render 배포 후
curl https://storescope-api.onrender.com/health

# Streamlit 접근
open https://storescope-app.onrender.com
```
