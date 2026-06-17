# LAUNCH TODAY — 35분 정확 경로

**전제**: $0, 카드 X, GH Student Pack 없이도 OK. 마스터플랜 STEP 0 완수.

## 진짜 필요한 것 vs 선택사항

| 항목 | 진짜 필요? | 이유 |
|---|---|---|
| Render 무료 web service | ✅ | API + Streamlit 호스팅 |
| Neon 무료 PG | ✅ | DB (Render PG 크레딧 고갈) |
| Paddle 라이브 키 | ✅ | 결제 |
| GitHub repo (이미 있음) | ✅ | Render 연결 |
| **GH Student Pack** | ❌ | onrender.com 서브도메인 OK |
| **커스텀 도메인** | ❌ | Y2부터 검토 |
| **Sentry 가입** | ❌ | 환경변수 미설정 시 SDK 자동 no-op (코드 검증 완료) |
| **sudo Claude Code 업그레이드** | ❌ | 개발 도구, 운영 무관 |
| **GitHub 토큰 revoke** | ❌ | 별도 보안 작업 (지금 안 해도 launch OK) |

→ 위 5개 미해결 항목 모두 launch 차단 아님.

---

## 35분 정확 경로

### Step 1 — Neon 가입 + DB 생성 (5분)

1. https://neon.tech → **Sign up with GitHub**
2. New project: name=`storescope`, region=**Tokyo (ap-northeast-1)** (한국 latency 최소)
3. Dashboard → **Connection details** → **Pooled connection** 토글 ON → connection string 복사
   - 형식: `postgresql://user:pass@ep-xxx-pooler.aws.neon.tech/storescope?sslmode=require`
   - Pooler URL 필수 (Render free 와 cold start 호환)
4. 별도 탭에 string 임시 저장

### Step 2 — DB 이전 (5분)

```bash
cd "/Users/dodokim/Documents/Claude/Projects/Auto-Biz Factory/StoreScope"
NEON_URL="postgresql://user:pass@ep-xxx-pooler.aws.neon.tech/storescope?sslmode=require"
bash deploy/migrate_to_external_pg.sh "$NEON_URL"
# 기대: PASS — clusters=1671 products=140943 stores=1419

# 마이그레이션 5건 일괄 적용 (스크립트 자동 정렬 + 검증)
bash deploy/apply_migrations.sh "$NEON_URL"
# 또는 명시 순서 (idempotency / 인덱스 / dead 테이블 / Stripe → Paddle 컬럼 rename / api_keys trial_ends_at)
# psql "$NEON_URL" -f migrations/2026_06_04_paddle_idempotency.sql
# psql "$NEON_URL" -f migrations/2026_06_04_perf_indexes.sql
# psql "$NEON_URL" -f migrations/2026_06_04_drop_dead_tables.sql
# psql "$NEON_URL" -f migrations/2026_06_07_rename_stripe_to_customer.sql  # 환불 후 키 비활성화 가능하게
# psql "$NEON_URL" -f migrations/2026_06_18_api_keys_trial_ends_at.sql      # 첫 paid webhook 처리 차단 막기 (D+17 발견)
```

### Step 3 — Render Blueprint Apply (5분)

```bash
# render.yaml → render.neon.yaml 로 교체 (Neon 호환 분기)
mv render.yaml render.original.yaml.bak
mv render.neon.yaml render.yaml
git add -A
git commit -m "deploy: Plan A Neon+Render free for D+6 launch"
git push origin main
```

브라우저:
1. https://dashboard.render.com → **New +** → **Blueprint**
2. **Connect GitHub repo** → `ddookim/storescope` 선택 (Render에 GitHub 권한 부여 필요)
3. **render.yaml** 자동 감지 → 2개 서비스 생성: `storescope-api` + `storescope-app`
4. **Apply** 클릭 → 빌드 시작

### Step 4 — Env Vars 입력 (10분)

Render Dashboard → `storescope-api` → **Environment** → 다음 6개 직접 입력:

| Key | 값 | 출처 |
|---|---|---|
| `DATABASE_URL` | Step 1.5 Neon Pooled URL | Neon Dashboard |
| `PADDLE_API_KEY` | `pdl_live_xxx` | paddle.com → Developer → Authentication |
| `PADDLE_CLIENT_TOKEN` | `live_xxx` | paddle.com → Developer → Authentication → Client tokens |
| `PADDLE_WEBHOOK_SECRET` | (Step 6 발급 후 입력 — 임시 공란) | paddle.com → Notifications |
| `ALLOWED_ORIGINS` | (기본값 그대로 — regex로 storescope-* 자동 허용) | `render.neon.yaml` 자동 |
| `APP_URL` | (기본값 그대로 `https://ddookim.github.io/storescope`) | render.neon.yaml 자동 — 라운드 16 추가 |
| (기타) | (선택사항 모두 비워두기) | — |

`storescope-app` 도 Environment 탭에서:
- `DATABASE_URL` = 동일 Neon URL
- `API_BASE_URL` = 자동 (render.neon.yaml 디폴트)

저장 → 자동 재배포 (~3분).

### Step 5 — Health 검증 (3분)

배포 완료 대기 → Render Dashboard `storescope-api` URL 복사 (예: `https://storescope-api-xxxx.onrender.com`).

```bash
API_URL="https://storescope-api-xxxx.onrender.com"
curl "$API_URL/health"             # {"status":"ok"}
curl "$API_URL/health/db"          # {"status":"ok","db":"reachable"}
curl "$API_URL/health/freshness"   # mode=live (or warning/stale)
```

세 응답 모두 정상이면 백엔드 라이브.

### Step 6 — Paddle Webhook 등록 + 시크릿 갱신 (5분)

1. paddle.com → **Developer** → **Notifications** → **New destination**
2. URL: `{API_URL}/billing/webhook`
3. Events 6개 선택:
   - `subscription.activated`
   - `subscription.canceled`
   - `subscription.paused`
   - `subscription.past_due`
   - `transaction.payment_failed`
   - `transaction.completed`
4. **Save** → **Signing secret** 복사
5. Render Dashboard → `storescope-api` → Environment → `PADDLE_WEBHOOK_SECRET` 값 입력 → 자동 재배포

### Step 7 — 랜딩 URL 전환 + 푸시 (2분)

```bash
cd "/Users/dodokim/Documents/Claude/Projects/Auto-Biz Factory/StoreScope"
bash deploy/patch_landing_api.sh "$API_URL"
# diff=0 자동 검증
./deploy/landing-deploy.sh
# gh-pages 푸시 → ddookim.github.io/storescope/ 라이브 반영
```

---

## 완료 확인 — 마스터플랜 STEP 0 8/8

| # | 작업 | 게이트 |
|---|---|---|
| 1 | GitHub repo + push | git log → 최신 commit |
| 2 | Render Web + DB | `/health/db` 200 |
| 3 | 환경변수 6개 | Render Dashboard 6 set |
| 4 | DB 이전 (Neon) | clusters=1671 검증 |
| 5 | Paddle Webhook URL | Paddle test event 200 |
| 6 | UptimeRobot 5분 ping | 추가: cron-job.org URL=`/health` schedule=`*/14 * * * *` (2분) |
| 7 | 랜딩 결제 라이브 키 | landing fetch /health/freshness 200 |
| 8 | LemonSqueezy 백업 등록 | https://lemonsqueezy.com 가입만 (20분 별도, 백업용) |

7/8 = launch 완료. UptimeRobot ping 추가까지 38분.

---

## Launch 후 첫 24시간 KPI 측정

`pages/01_Admin_Dashboard.py` 자동 표시:
- X-Ray DAU (7일 평균)
- 이메일 캡처 누적
- 활성 paid 구독
- 신규 paid (7d)
- D+30 자동 분기 예측 (현재 데이터 기준)

```bash
streamlit run pages/01_Admin_Dashboard.py
```

---

## 선택사항 (당장 안 해도 launch 됨)

| 항목 | 시점 | 시간 |
|---|---|---|
| GitHub OAuth 토큰 revoke + 새 PAT | 보안 강화, 1주 내 | 5분 |
| Claude Code sudo 업그레이드 | 개발 환경, 언제든 | 1분 |
| Sentry DSN 입력 | 모니터링, launch 후 | 5분 |
| GH Student Pack 신청 | 도메인 + DO 크레딧, Y2 검토 | 15분 |
| LemonSqueezy 백업 가입 | Path D 보험, 1주 내 | 20분 |

---

## 트러블슈팅

| 증상 | 원인 | 처리 |
|---|---|---|
| Render 빌드 실패 | `requirements.txt` 의 패키지 충돌 | Render Logs 확인, `requirements.txt` 1줄씩 격리 |
| Neon 연결 실패 | `?sslmode=require` 누락 | URL 끝에 추가 |
| 결제 webhook 4xx | signing secret 불일치 | Paddle Dashboard → Notification → secret 재복사 → Render env 재입력 |
| 랜딩 CORS 차단 | ALLOWED_ORIGINS 불일치 | Render env → `https://ddookim.github.io,https://storescope-app-xxxx.onrender.com` 콤마 추가 |
| 첫 결제 시 KEY 발급 안 됨 | Sentry DSN 없어 에러 invisible | `_log.error` 로그가 Render console 출력 → Dashboard Logs 검색 |
