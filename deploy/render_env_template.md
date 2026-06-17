# Render Environment Variables — Copy-Paste 템플릿

Render Dashboard → `storescope-api` → Environment → Add Environment Variable 반복.

## 필수 (launch 차단)

### DATABASE_URL
```
postgresql://NEON_USER:NEON_PASS@ep-xxx-pooler.aws.neon.tech/storescope?sslmode=require
```
**출처**: Neon Dashboard → Connection details → Pooled connection 토글 ON → 복사.
**검증**: 형식 `postgresql://`로 시작, `-pooler` 포함, `?sslmode=require` 끝.

### PADDLE_API_KEY
```
pdl_live_apikey_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
```
**출처**: paddle.com → Developer → Authentication → API keys → Create new (또는 기존).
**주의**: `pdl_test_` 가 아니라 `pdl_live_` 시작 — 라이브 환경.

### PADDLE_CLIENT_TOKEN
```
live_d4e17395606608d2b4eaec9b46d
```
**출처**: paddle.com → Developer → Authentication → Client-side tokens.
**참고**: 이미 랜딩 HTML `Paddle.Initialize()` 에서 사용 중인 값과 동일해야 함.

### PADDLE_WEBHOOK_SECRET
```
pdl_ntfset_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
```
**출처**: paddle.com → Notifications → New destination 생성 후 표시되는 Signing secret.
**임시**: webhook 등록 전엔 빈 값 OK → 등록 후 즉시 입력.

## 자동 (render.yaml 디폴트 적용)

다음은 `render.neon.yaml`에 디폴트 정의됨. Render가 자동 입력. **수정 불필요**:

```
ADMIN_SECRET              (Render 자동 생성, 32 byte 랜덤)
APP_URL                   https://ddookim.github.io/storescope  (이메일 CTA 링크 — 라운드 16 추가)
PADDLE_STARTER_PRICE_ID   pri_01ksj3qpjyxsv2kprvxn7dpk10
PADDLE_PRO_PRICE_ID       pri_01ksh172kehejf3xc2ws26e6vy
PADDLE_WEBHOOK_SKIP_VERIFY false
ALLOWED_ORIGINS           https://ddookim.github.io,https://storescope-app.onrender.com
PYTHON_VERSION            3.11.9
SMTP_HOST                 smtp.gmail.com
SMTP_PORT                 587
SMTP_FROM                 noreply@storescope.com
SENTRY_TRACES_SAMPLE_RATE 0.01
RATE_LIMIT_TRENDING       60/minute
RATE_LIMIT_LEADS          5/minute
RATE_LIMIT_OPTOUT         10/minute
RATE_LIMIT_FRESHNESS      30/minute
```

## 선택 (당장 안 해도 OK)

### SENTRY_DSN (모니터링)
```
https://abc123xyz@o123456.ingest.us.sentry.io/789012
```
**출처**: sentry.io → 무료 가입 → New Project → Python (FastAPI) → DSN 표시.
**미설정 시**: SDK 자동 no-op. 에러 추적 안 됨이지만 launch 무관.

### TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID (Telegram 알림)
```
TELEGRAM_BOT_TOKEN  123456789:ABCdefGhi-JklMnoPqr-StuVwxYz
TELEGRAM_CHAT_ID    -100123456789
```
**출처**: @BotFather Telegram bot → `/newbot` → token 받음. Chat ID는 `/start` 후 `getUpdates`.
**미설정 시**: Telegram 알림 없음, Render Dashboard Logs에서 확인.

### SMTP_USER + SMTP_PASS (이메일 발송)
```
SMTP_USER  noreply@storescope.com
SMTP_PASS  ksdfj-kgjsd-lkfjd-glkjd
```
**출처**: Gmail 앱 비밀번호 (2FA 활성 후 https://myaccount.google.com/apppasswords).
**미설정 시**: 이메일 발송 안 됨 (X-Ray report + API key 발급 이메일). Console stub 출력.

## 별도 서비스: storescope-app

`storescope-app` 도 Environment 탭에서:
- `DATABASE_URL` = 동일 Neon URL (storescope-api 와 공유)
- `API_BASE_URL` = (자동, render.neon.yaml 디폴트)
- `GA_MEASUREMENT_ID` = (선택, 이미 랜딩에 `G-3YR22V5LW4` 하드코딩됨)

## 검증

env 입력 완료 후:
1. Render Dashboard → 두 서비스 모두 Status: Live (녹색)
2. `curl https://storescope-api-xxxx.onrender.com/health/db` → `{"status":"ok","db":"reachable"}`
3. Render Logs → "DB pool warmed up" 로그 확인
