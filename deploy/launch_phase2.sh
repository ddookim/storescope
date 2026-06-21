#!/bin/bash
# StoreScope Launch — Phase 2 (Render 헬스 검증 + 랜딩 패치 + gh-pages 푸시)
#
# 사용법:
#   bash deploy/launch_phase2.sh "<RENDER_API_URL>"
#
# 동작:
#   1. /health, /health/db, /health/freshness 검증
#   2. landing line 15 API URL → Render URL 치환 (patch_landing_api.sh)
#   3. landing-deploy.sh 호출 (gh-pages 푸시)
#   4. live landing CORS 사전 점검
#   5. Paddle webhook 등록 안내 출력
#
# 예:
#   bash deploy/launch_phase2.sh "https://storescope-api-xxxx.onrender.com"

set -euo pipefail

if [ $# -lt 1 ]; then
    echo "사용법: $0 <RENDER_API_URL>"
    echo "예:    $0 'https://storescope-api-xxxx.onrender.com'"
    exit 1
fi

API_URL="${1%/}"  # trailing slash 제거
PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

# URL 검증 — 헤더 출력 전 (abort 시 헤더만 보이는 혼란 방지)
if ! echo "$API_URL" | grep -qE "^https://[a-zA-Z0-9.-]+(:[0-9]+)?$"; then
    echo "[ABORT] URL 형식 이상 (https://host 형식, trailing slash 금지)"
    echo "        받은 값: $1"
    exit 2
fi

echo "===================================="
echo "  StoreScope Launch — Phase 2 (Render 검증 + 랜딩 패치)"
echo "===================================="
echo "  Target API: $API_URL"
echo

# ── Step 1: Health 3-tier 검증 ───────────────────────────────────────────
echo "[1/4] Render 헬스 검증..."

check_health() {
    local endpoint="$1"
    local expected="$2"
    local result
    result=$(curl -fsS --max-time 10 "$API_URL$endpoint" 2>/dev/null || echo "FAIL")
    if [ "$result" = "FAIL" ]; then
        echo "  [FAIL] $endpoint 응답 없음. Render Logs 확인 필요."
        return 1
    fi
    if ! echo "$result" | grep -q "$expected"; then
        echo "  [FAIL] $endpoint 비정상 응답: $result"
        return 1
    fi
    echo "  ✓ $endpoint"
    return 0
}

check_health "/health" "ok" || exit 3
check_health "/health/db" "reachable" || exit 4

# freshness 는 mode=live OR mode=warning 모두 OK (stale 만 fail)
FRESH=$(curl -fsS --max-time 10 "$API_URL/health/freshness" 2>/dev/null || echo "FAIL")
if echo "$FRESH" | grep -qE "stale"; then
    echo "  [WARN] /health/freshness mode=stale — 파이프라인 14일 이상 미실행. launch 후 weekly_pipeline 즉시 트리거 권장."
elif echo "$FRESH" | grep -qE "live|warning|ok"; then
    echo "  ✓ /health/freshness ($(echo "$FRESH" | grep -oE '"mode":"[^"]*"' || echo 'mode=ok'))"
else
    echo "  [FAIL] /health/freshness 비정상: $FRESH"
    exit 5
fi

echo

# ── Step 2: landing API URL 패치 ─────────────────────────────────────────
echo "[2/4] landing line 15 API URL 치환..."
bash "$PROJECT_ROOT/deploy/patch_landing_api.sh" "$API_URL"
echo

# ── Step 3: landing 배포 ─────────────────────────────────────────────────
echo "[3/4] landing gh-pages 푸시..."
if [ -x "$PROJECT_ROOT/deploy/landing-deploy.sh" ]; then
    bash "$PROJECT_ROOT/deploy/landing-deploy.sh"
else
    echo "  [SKIP] landing-deploy.sh 미존재 — 수동 푸시 필요"
fi
echo

# ── Step 4: CORS 사전 점검 ───────────────────────────────────────────────
echo "[4/4] CORS 점검 (github.io origin 허용 확인)..."
CORS_HDR=$(curl -fsS -o /dev/null -w "%{http_code}" \
    -H "Origin: https://ddookim.github.io" \
    -H "Access-Control-Request-Method: GET" \
    -X OPTIONS "$API_URL/leads" 2>/dev/null || echo "FAIL")
if [ "$CORS_HDR" = "200" ] || [ "$CORS_HDR" = "204" ]; then
    echo "  ✓ CORS preflight OK ($CORS_HDR)"
else
    echo "  [WARN] CORS preflight=$CORS_HDR — Render env ALLOWED_ORIGINS 점검 필요"
    echo "         기대값: ALLOWED_ORIGINS=https://ddookim.github.io (또는 regex 기본값)"
fi

echo
echo "═══════════════════════════════════════════════════════════════"
echo "  Phase 2 완료. 랜딩 ↔ Render API 연결 라이브."
echo "═══════════════════════════════════════════════════════════════"
echo
echo "남은 단계 (5분):"
echo
echo "  1) Paddle Webhook 등록"
echo "     paddle.com → Developer → Notifications → New destination"
echo "     URL: $API_URL/billing/webhook"
echo "     Events: subscription.activated / canceled / paused / past_due"
echo "             transaction.completed / payment_failed"
echo "     → Save → Signing secret 복사"
echo
echo "  2) Render Env Vars 갱신"
echo "     storescope-api → Environment → PADDLE_WEBHOOK_SECRET = <Signing secret>"
echo "     → 자동 재배포 (~2분)"
echo
echo "  3) Paddle test event 발사 (Notifications 페이지 → Send test)"
echo "     기대: 200 OK"
echo
echo "  4) UptimeRobot or cron-job.org 등록"
echo "     URL: $API_URL/health"
echo "     interval: 5분 (Render free cold start 방지)"
echo
echo "  5) 검증 명령 (launch 후 즉시):"
echo "     curl -X POST -H 'Content-Type: application/json' \\"
echo "          -d '{\"url\":\"https://example.myshopify.com\"}' \\"
echo "          $API_URL/leads"
echo "     기대: 200 + lead_id"
echo
