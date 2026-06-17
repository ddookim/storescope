# Plan A 배포 가이드 (Neon + Render free + GH Student Pack)

**Why this doc**: 기존 [DEPLOY_CHECKLIST.md](DEPLOY_CHECKLIST.md)는 Render free PostgreSQL 가용을 전제. 2026-06-04 현재 Render 크레딧 고갈로 봉쇄 → Neon DB + Render web 분리 구조로 우회.

**상태 (2026-06-04)**:
- ✅ render.neon.yaml (singapore region, autoDeploy=false, Python 3.11.9 pin)
- ✅ deploy/migrate_to_external_pg.sh (--no-tablespaces + --single-transaction + 버전 호환 사전 체크)
- ✅ deploy/patch_landing_api.sh (anchored grep + safety guards + git uncommitted 경고)
- ✅ migrations/2026_06_04_paddle_idempotency.sql (이미 로컬 DB 적용)
- ✅ Paddle webhook idempotency 로직 (api/paddle_routes.py)
- ✅ app.py silent except 가시화
- ✅ /leads disposable email 차단

**남은 사용자 액션**: 4개.

---

## Step 1 — Student Pack + 도메인 + Neon (브라우저, 30분)

| # | 작업 | URL |
|---|---|---|
| 1.1 | GitHub Student Pack 신청 (.edu + 학생증) | https://education.github.com/discount_requests/application |
| 1.2 | 승인 알림 받으면 → Namecheap 가입 → `storescope.me` 1년 무료 발급 | https://nc.me |
| 1.3 | Cloudflare 가입 → 도메인 추가 → Namecheap 네임서버 변경 (DNS 관리권 확보) | https://dash.cloudflare.com |
| 1.4 | Neon 가입 (GitHub OAuth, 카드 X) → New project "storescope" → region: AWS ap-northeast-1 Tokyo | https://neon.tech |
| 1.5 | Neon Dashboard → Connect → **Pooled connection string 복사** (cold start 최소화용) | — |

---

## Step 2 — DB 이전 (터미널, 5분)

```bash
cd "/Users/dodokim/Documents/Claude/Projects/Auto-Biz Factory/StoreScope"

# 1.5에서 복사한 Neon Pooled URL
NEON_URL="postgresql://shopify:<pass>@ep-xxx-pooler.aws.neon.tech/storescope?sslmode=require"

bash deploy/migrate_to_external_pg.sh "$NEON_URL"

# 기대 출력:
#   [0/6] pg_dump major: 16, 외부 DB major: 16  OK
#   [1/6] 외부 DB 연결 검증...  OK
#   [2/6] pg_dump (10초)
#   [3/6] pg_restore --single-transaction (3분)
#   [4/6] clusters=1671 products=140943 stores=1419
#   [5/6] trend_score 컬럼 검증  OK
#   [RESULT] PASS

# Neon에 마이그레이션 3개 일괄 적용 (순서 무관, idempotent)
psql "$NEON_URL" -f migrations/2026_06_04_paddle_idempotency.sql
psql "$NEON_URL" -f migrations/2026_06_04_perf_indexes.sql           # 115x /trending 가속
psql "$NEON_URL" -f migrations/2026_06_04_drop_dead_tables.sql       # webhook_subscriptions 제거
psql "$NEON_URL" -f migrations/2026_06_07_rename_stripe_to_customer.sql  # Stripe 잔재 컬럼 정리 (라운드 10 발견)
# 또는 전체 자동:
# bash deploy/apply_migrations.sh "$NEON_URL"
```

---

## Step 3 — Render 배포 (브라우저 + 터미널, 15분)

```bash
# 신규 render.yaml로 교체 (Neon 호환 분기)
mv render.yaml render.original.yaml.bak
mv render.neon.yaml render.yaml
git add render.yaml render.original.yaml.bak
git commit -m "deploy: switch to Neon-as-DB variant (Render PG credit exhausted)"
git push origin main
```

브라우저:
1. https://dashboard.render.com → New + → Blueprint
2. `ddookim/storescope` 선택 → render.yaml 자동 감지 → Apply
3. `storescope-api` Environment 탭 → 다음 키 직접 입력:
   - `DATABASE_URL` = Neon Pooled URL (Step 1.5 값) — **필수**
   - `PADDLE_API_KEY` = Paddle Dashboard → Developer → Authentication → API keys — **필수**
   - `PADDLE_CLIENT_TOKEN` = Paddle → Authentication → Client-side tokens — **필수**
   - `PADDLE_WEBHOOK_SECRET` = (다음 Step 5에서 발급 — 임시 공란) — **필수**
   - `APP_URL`, `ALLOWED_ORIGINS`, `RATE_LIMIT_*` = render.neon.yaml 디폴트 — **자동 (수정 불필요)**
   - `TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID` = (선택, 알림)
   - `SMTP_USER` + `SMTP_PASS` = Gmail 앱 비밀번호 (선택, 이메일 발송)
4. `storescope-app` Environment 탭 → `DATABASE_URL` (동일 Neon URL) + `GA_MEASUREMENT_ID = G-3YR22V5LW4`

빌드 완료 (~5분) 후:
```bash
curl https://storescope-api.onrender.com/health
# 기대: {"status":"ok"}
curl https://storescope-api.onrender.com/health/freshness
# 기대: mode≠"stale" (DB 마이그레이션 직후라 신선)
```

---

## Step 4 — 랜딩 URL 전환 (터미널, 2분)

```bash
cd "/Users/dodokim/Documents/Claude/Projects/Auto-Biz Factory/StoreScope"
bash deploy/patch_landing_api.sh "https://storescope-api.onrender.com"
# diff=0 검증 통과 자동 확인

./deploy/landing-deploy.sh
# gh-pages 푸시 → ddookim.github.io/storescope/ 라이브 반영
```

---

## Step 5 — Paddle Webhook + 최종 검증 (브라우저, 10분)

1. Paddle Dashboard → Notifications → New destination:
   - URL: `https://storescope-api.onrender.com/billing/webhook`
   - Events: `subscription.activated`, `subscription.canceled`, `subscription.paused`, `transaction.payment_failed`, `subscription.past_due`
   - Save → **Signing secret 복사**
2. Render Dashboard → storescope-api → Environment → `PADDLE_WEBHOOK_SECRET` 값 입력 → Service 자동 재시작
3. Paddle Notifications → Send test event → Render logs에서 `Paddle webhook duplicate skipped` 또는 `received: true` 확인

---

## Step 6 — 슬립 방지 (브라우저, 2분)

https://cron-job.org → New Cron Job:
- URL: `https://storescope-api.onrender.com/health`
- Schedule: `*/14 * * * *` (14분마다, Render free 15분 sleep 직전 wake)

---

## Step 7 — 도메인 연결 (선택, 5분)

Render storescope-api → Settings → Custom Domain → `api.storescope.me` 입력 → Cloudflare DNS에 CNAME 자동 안내.

도메인 적용 후 랜딩 갱신:
```bash
bash deploy/patch_landing_api.sh "https://api.storescope.me"
./deploy/landing-deploy.sh
```

Render Environment 에서 `ALLOWED_ORIGINS`에 `https://storescope.me` 추가 (콤마 구분).

---

## 검증 체크리스트 (배포 완료 후)

- [ ] `curl https://api.storescope.me/health` → 200
- [ ] `curl https://api.storescope.me/health/freshness` → mode=ok, payments_blocked=false
- [ ] `https://ddookim.github.io/storescope/` 또는 `https://storescope.me` 랜딩에서 freshness 배너 없음
- [ ] Paddle test webhook → Render logs에 idempotency 메시지 출력
- [ ] Stripe Test Mode 결제 1건 → Email 발송 (또는 EMAIL STUB 로그) → API 키 생성
- [ ] DB row `SELECT COUNT(*) FROM api_keys WHERE is_active=true` 증가
- [ ] cron-job.org 실행 로그 → 14분마다 200 응답

---

## 롤백 (문제 발생 시)

| 문제 | 액션 |
|---|---|
| Render 빌드 실패 | Render Dashboard → Logs 확인, Python 버전 또는 requirements.txt 문제 가능 |
| Neon 연결 실패 | Pooler URL 재확인, `?sslmode=require` 누락 여부 점검 |
| Webhook signature 검증 실패 | `PADDLE_WEBHOOK_SECRET` 값 confirm 후 Render 재시작 |
| 랜딩에서 CORS 차단 | `ALLOWED_ORIGINS`에 실제 landing 호스트 추가 |
| 데이터 stale로 dead-man 발동 | 로컬 `python run_pipeline.py --from 1 --step 4` 재실행 후 Neon으로 재마이그레이션 |
