# StoreScope Deploy Guide — Render Web + Neon + Streamlit Cloud

**대상**: 사용자 (StoreScope 솔로 파운더)
**소요**: 약 70분 (분할 가능)
**카드 요구**: ❌ 없음 (모든 서비스 GitHub OAuth)
**비용**: $0

이전 plan 의 막힘 원인 = `render.yaml` 이 2 web service + 1 DB → Render free workspace 750hr/mo 한도 초과. 본 가이드는 **단일 FastAPI service + 외부 DB (Neon) + 외부 Streamlit (Cloud)** 로 분리.

---

## Step 0. 사전 준비 (2분)

```bash
cd "/Users/dodokim/Documents/Claude/Projects/Auto-Biz Factory/StoreScope"
git status                # M (수정) 파일 8개 확인
git log -1 --format="main HEAD: %h %ai %s"
```

기대: D+24~29 의 다음 파일들이 uncommitted —
- `landing/index.html`, `app.py`, `api/main.py`, `api/auth.py`, `api/paddle_routes.py`
- `services/xray_report.py`, `services/send_weekly_digests.py` (신규), `services/`
- `migrations/2026_06_29_email_deliveries.sql` (신규)
- `render.yaml`, `.github/workflows/weekly_pipeline.yml`, `deploy/launch_phase2.sh`
- `docs/DEPLOY.md` (이 파일)

---

## Step 1. Git commit + push (5분)

3 commit 분할 권장:

```bash
# 1) 랜딩 D+24~25 fix
git add landing/index.html
git commit -m "feat(landing): D+24~25 CSP fixes + i18n complete sweep + a11y hit area + footer h3"

# 2) Streamlit + backend D+28
git add app.py api/paddle_routes.py services/xray_report.py
git commit -m "fix(D+28): backend retry/idempotency + Streamlit env resolver + 거짓광고 카피 + lead timeout 60s"

# 3) Weekly digest + unsub + Render 재구성 D+29
git add api/main.py api/auth.py services/send_weekly_digests.py migrations/2026_06_29_email_deliveries.sql .github/workflows/weekly_pipeline.yml render.yaml deploy/launch_phase2.sh docs/DEPLOY.md
git commit -m "feat(D+29): weekly digest wiring + /unsubscribe HMAC + Neon ping + Render single-service config"

git push origin main
```

---

## Step 2. Neon DB 가입 + DATABASE_URL (5분)

1. https://neon.tech → **Sign up with GitHub** (카드 X)
2. **New Project**:
   - Name: `storescope`
   - Region: `aws-us-east-2` (Render 와 매칭)
   - PostgreSQL version: 17 (default)
3. Dashboard → **Connection Details** → **Pooled connection** 토글 ON
4. Connection string 복사:
   ```
   postgres://neondb_owner:xxx@ep-xxx-pooler.us-east-2.aws.neon.tech/neondb?sslmode=require
   ```
5. Database name 을 `storescope` 로 변경 (또는 default `neondb` 사용 시 그대로)

**환경변수 저장** (다음 단계용):
```bash
export NEON_URL='postgres://...pooler.us-east-2.aws.neon.tech/storescope?sslmode=require'
```

---

## Step 3. Migration 실행 (3분)

```bash
# Step 0 의 PROJECT_ROOT
cd "/Users/dodokim/Documents/Claude/Projects/Auto-Biz Factory/StoreScope"

# 기존 migrations 순서대로 (date 정렬)
for sql in migrations/*.sql; do
    echo "→ $sql"
    psql "$NEON_URL" -f "$sql" || { echo "[FAIL] $sql"; exit 1; }
done

# 검증
psql "$NEON_URL" -c "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='public';"
psql "$NEON_URL" -c "\d email_deliveries"   # D+29 신규
psql "$NEON_URL" -c "\d api_keys"           # unsubscribed_at column 확인
```

기대: tables 10+ 개, email_deliveries 존재, api_keys 에 unsubscribed_at column.

---

## Step 4. Render Web Service 생성 (15분)

1. https://render.com → **Sign in with GitHub** (카드 X)
2. **New** → **Blueprint** → Connect repo `ddookim/storescope` → main branch
3. `render.yaml` 자동 감지 → **Apply Blueprint**
4. 서비스 생성 후 **Environment** 탭에서 Secret env 입력:

| Key | 값 |
|-----|----|
| `DATABASE_URL` | Step 2 의 `$NEON_URL` |
| `PADDLE_API_KEY` | https://vendors.paddle.com → Developer tools → Authentication |
| `PADDLE_CLIENT_TOKEN` | 같은 위치 |
| `PADDLE_WEBHOOK_SECRET` | (Step 6 에서 받음 — 일단 dummy `change-me`, 추후 갱신) |
| `ADMIN_SECRET` | `generateValue: true` → Render 자동 생성 (값 복사 보관) |
| `TELEGRAM_BOT_TOKEN` | (선택) @BotFather 로 봇 생성 |
| `TELEGRAM_CHAT_ID` | (선택) https://api.telegram.org/bot<TOKEN>/getUpdates |
| `SMTP_HOST` | (Step 8 후) `smtp.resend.com` 또는 `smtp.gmail.com` |
| `SMTP_USER` | (Step 8 후) |
| `SMTP_PASS` | (Step 8 후) |
| `SMTP_FROM` | (Step 8 후) `noreply@yourdomain` 또는 Gmail 주소 |
| `SENTRY_DSN` | (선택) |

5. **Manual Deploy** → Build 시작 → 약 5분 대기
6. Build 완료 → URL 확인 (예: `https://storescope-api.onrender.com`)

```bash
export RENDER_API_URL='https://storescope-api.onrender.com'
```

---

## Step 5. 랜딩 + API 연결 (5분)

```bash
bash deploy/launch_phase2.sh "$RENDER_API_URL"
```

자동 실행 항목:
1. `/health` (DB-free) + `/health/db` + `/health/freshness` 검증
2. 랜딩 `index.html` line 22 trycloudflare URL → Render URL 치환
3. `gh-pages` 푸시 (라이브 갱신)
4. CORS preflight 점검

성공 메시지:
```
═══════════════════════════════════════════════════════════════
  Phase 2 완료. 랜딩 ↔ Render API 연결 라이브.
═══════════════════════════════════════════════════════════════
```

---

## Step 6. Paddle Webhook 등록 (5분)

1. https://vendors.paddle.com → **Developer tools** → **Notifications**
2. **+ New destination** → Webhook
3. **URL**: `https://storescope-api.onrender.com/billing/webhook`
4. **Events** 체크 (정확히):
   - subscription.activated
   - subscription.canceled
   - subscription.paused
   - subscription.past_due
   - transaction.payment_failed
   - transaction.completed
5. **Save** → **Signing secret** 복사
6. Render Dashboard → storescope-api → Environment → **`PADDLE_WEBHOOK_SECRET`** 에 붙여넣기 → Save (Render 자동 재배포 ~2분)
7. Paddle Notifications 페이지 → **Send test event** → 200 OK 응답 확인

---

## Step 7. Streamlit Cloud Dashboard (15분)

1. https://streamlit.io/cloud → **Sign in with GitHub** (카드 X)
2. **New app**:
   - Repo: `ddookim/storescope`
   - Branch: `main`
   - Main file path: `app.py`
3. **Advanced settings** → **Secrets** (TOML 형식 입력):
   ```toml
   DATABASE_URL = "postgres://...pooler.us-east-2.aws.neon.tech/storescope?sslmode=require"
   API_BASE_URL = "https://storescope-api.onrender.com"
   STREAMLIT_SHARING_MODE = "1"
   ADMIN_SECRET = "<Render 의 ADMIN_SECRET 값 복사>"
   GA_MEASUREMENT_ID = "G-3YR22V5LW4"
   ```
4. **Deploy** → URL 확정 (예: `https://storescope-app.streamlit.app`)
5. **나에게 URL 알려주기** → 내가 자율로:
   - 랜딩 `Paddle.Checkout.open` 에 `settings.successUrl: "<streamlit-url>?welcome=1"` 추가
   - landing-deploy 재실행
   - E2E 검증

---

## Step 8. SMTP Provider (15분, 선택)

이메일 발송 (API key 환영 + Weekly digest + X-Ray report). 미설정 시 코드가 CRITICAL alert 보내고 수동 처리 flow.

### 옵션 A. Resend (도메인 필요, 권장)

1. https://resend.com → Sign up (free 3,000/month)
2. **Domains** → Add your domain (GH Student Pack `.me` 등)
3. DNS records (SPF + DKIM + DMARC) 추가
4. **API Keys** → Create → 복사
5. Render env 갱신:
   ```
   SMTP_HOST=smtp.resend.com
   SMTP_PORT=587
   SMTP_USER=resend
   SMTP_PASS=re_xxxxxxxxxxxx
   SMTP_FROM=noreply@yourdomain.com
   ```

### 옵션 B. Gmail SMTP (도메인 X, 임시)

1. Google Account → Security → 2-Step Verification 활성
2. **App passwords** → Generate → 16자 복사
3. Render env:
   ```
   SMTP_HOST=smtp.gmail.com
   SMTP_PORT=587
   SMTP_USER=dodo32032@gmail.com
   SMTP_PASS=xxxxxxxxxxxxxxxx  (App password)
   SMTP_FROM=dodo32032@gmail.com
   ```
- 한도: 일 500건 (가입자 100명 < OK)
- 단점: SPF/DKIM 미설정 → spam 분류 risk

### 옵션 C. 보류 (sandbox-only launch)

SMTP 미설정 시:
- API key 환영 메일 → Telegram CRITICAL alert + 수동 발송
- Weekly digest → 발송 0
- X-Ray report (`/leads` 후 자동 발송) → 발송 0

D+30 launch baseline 측정만 우선, SMTP 는 baseline 본 후 결정.

---

## Step 9. GitHub Actions Secrets (10분)

GH repo → **Settings** → **Secrets and variables** → **Actions**:

### Repository secrets (Secret)

| Name | 값 |
|------|-----|
| `NEON_DB_URL` | Step 2 의 `$NEON_URL` |
| `SMTP_HOST` | Step 8 의 SMTP_HOST (없으면 빈 값) |
| `SMTP_PORT` | `587` |
| `SMTP_USER` | Step 8 의 SMTP_USER |
| `SMTP_PASS` | Step 8 의 SMTP_PASS |
| `SMTP_FROM` | Step 8 의 SMTP_FROM |
| `BASE_URL` | `https://ddookim.github.io/storescope` |
| `API_BASE_URL` | Step 4 의 `$RENDER_API_URL` |
| `ADMIN_SECRET` | Step 4 의 ADMIN_SECRET |
| `TELEGRAM_BOT_TOKEN` | (선택) |
| `TELEGRAM_CHAT_ID` | (선택) |

### Repository variables (public)

| Name | 값 |
|------|-----|
| `RENDER_API_URL` | Step 4 의 `$RENDER_API_URL` (keep_warm.yml 가 사용) |

### 검증

- Actions 탭 → **Weekly Pipeline** → **Run workflow** (수동 트리거) → 약 30분 소요
- 성공 시 Telegram 알림 + DB clusters 1,600+ 생성

---

## Step 10. 검증 (5분)

```bash
# 1. /health version SHA + uptime
curl "$RENDER_API_URL/health" | python3 -m json.tool
# 기대: {"status":"ok","version":"<7자 SHA>","uptime_sec":<int>}

# 2. /health/db
curl "$RENDER_API_URL/health/db" | python3 -m json.tool
# 기대: {"status":"ok","db":"reachable"}

# 3. /leads POST (이메일 캡처)
curl -X POST -H "Content-Type: application/json" \
     -d '{"email":"test@example.com","domain":"example.myshopify.com","source":"deploy-test"}' \
     "$RENDER_API_URL/leads"
# 기대: {"captured":true}

# 4. /unsubscribe — HMAC 검증 (잘못된 token = 400)
curl -i "$RENDER_API_URL/unsubscribe?sid=1&token=invalid"
# 기대: HTTP/2 400 + "링크가 유효하지 않습니다"

# 5. 라이브 KO 모드 영어 잔재 0건 (의도적 keep 9건 제외)
cd "/Users/dodokim/auto biz_factory" && node tools/i18n_sweep.js "https://ddookim.github.io/storescope/?lang=ko&_=$(date +%s)" 2>&1 | python3 -c "
import json,sys
d=json.load(sys.stdin)
no=[i for i in d['items'] if not i['hasI18n']]
print(f'KO 잔재 {len(no)}건 (목표 9건)')
"
```

---

## D+30 launch 후 측정 (자동)

- **GH Actions Weekly Pipeline**: 매주 일요일 23:00 UTC (= 월 08:00 KST) — crawl + digest 발송
- **keep_warm.yml**: 14분 cron `/health` ping → Render free sleep 회피
- **/health/freshness**: 30s cached, `mode=live/warning/stale` 분기

### Kill switch 측정 (메모리 `project_kill_switch_measurement.md`)

D+30 ~ D+60 측정 지표:
- GA page_view × 1.3 (AdBlock 보정)
- GH Pages traffic insights
- DB `email_leads` 카운트
- Paddle Checkout 시도
- DB `paddle_processed_events` 카운트

단일 GA 지표만 X — 다중 교차 검증.

---

## 트러블슈팅

### Render build 실패 — `psycopg2` install error
→ `requirements.txt` 가 `psycopg2-binary` 사용하는지 확인. `psycopg2` (소스 빌드) 는 Render slim image 에서 실패.

### `/health/db` 503 응답
→ Render env `DATABASE_URL` 정합 확인. Neon URL 의 `?sslmode=require` 누락 시 SSL 거부.

### Paddle webhook 400 응답
→ `PADDLE_WEBHOOK_SECRET` Render env 입력 정합. Paddle dashboard 의 Signing secret 과 정확 일치.

### Streamlit Cloud `RuntimeError: API_BASE_URL env required`
→ Streamlit Cloud Secrets 에 `API_BASE_URL` + `STREAMLIT_SHARING_MODE` 등록 확인.

### GH Actions Weekly digest pre-flight 실패
→ Repository secrets 의 `API_BASE_URL` + `ADMIN_SECRET` 등록 확인. Telegram 알림 메시지의 Runbook 참조.

### Neon scale-to-zero cold start 시 일부 요청 500
→ 본 라운드 적용: `api/auth.py` 의 `_is_conn_alive` ping + 1회 retry. 그래도 fail 시 Neon dashboard 의 Compute 활성 상태 확인.

### Render free 15분 sleep + Paddle webhook timeout
→ `--timeout-keep-alive 75` 적용됨. + keep_warm.yml 14분 ping. + Paddle 자동 retry 3회.

---

## 잔여 사용자 결정 사항

| 항목 | 옵션 | 권장 |
|------|------|------|
| 도메인 | A) GH Student Pack `.me` 1년 free / B) 보류 (sandbox-only) / C) `noreply@resend.dev` 공유 | **A** (장기), 또는 **B** (baseline 측정 우선) |
| Streamlit Cloud login | 메인 X-Ray 는 공개 / Admin Dashboard 만 ADMIN_SECRET (이미 적용) | 현재 정합 |
| Paddle 실 결제 vs sandbox | 도메인 검증 후 실 결제 / 도메인 전까지 sandbox | 도메인 확정 후 실 결제 |
