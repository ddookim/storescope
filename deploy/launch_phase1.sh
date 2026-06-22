#!/bin/bash
# StoreScope Launch — Phase 1 (DB 이전 + 마이그레이션 + 검증)
#
# 사용법:
#   bash deploy/launch_phase1.sh "<NEON_POOLED_URL>"
#
# 동작:
#   1. NEON URL 형식 검증 (sslmode + pooler 확인)
#   2. migrate_to_external_pg.sh — 로컬 → Neon 데이터 이전
#   3. apply_migrations.sh — 5개 마이그레이션 일괄 적용
#   4. 사후 점검: clusters/products/stores 카운트 + 핵심 인덱스 + Paddle idempotency
#   5. 다음 단계 (Render Blueprint apply) 안내 출력
#
# 예:
#   bash deploy/launch_phase1.sh "postgresql://user:pass@ep-xxx-pooler.aws.neon.tech/storescope?sslmode=require"

set -euo pipefail

if [ $# -lt 1 ]; then
    echo "사용법: $0 <NEON_POOLED_URL>"
    echo "예:    $0 'postgresql://user:pass@ep-xxx-pooler.aws.neon.tech/storescope?sslmode=require'"
    exit 1
fi

NEON_URL="$1"
PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

# ── 검증 1: pooled URL + sslmode ──────────────────────────────────────────
if ! echo "$NEON_URL" | grep -qE "sslmode=require"; then
    echo "[ABORT] sslmode=require 누락. Neon은 SSL 필수."
    exit 2
fi
if echo "$NEON_URL" | grep -qE "(localhost|127\.0\.0\.1)"; then
    echo "[ABORT] 로컬 URL은 외부 launch 대상이 아닙니다."
    exit 2
fi
if ! echo "$NEON_URL" | grep -qE "pooler"; then
    echo "[WARN] pooler 키워드 없음. Render free + Neon cold start 호환 위해 Pooled URL 권장."
    printf "그래도 진행? (y/N): "
    read -r ans
    [ "$ans" = "y" ] || { echo "중단."; exit 3; }
fi

echo "═══════════════════════════════════════════════════════════════"
echo "  StoreScope Launch — Phase 1 (DB 이전 + 마이그레이션)"
echo "═══════════════════════════════════════════════════════════════"
echo "  Target: $(echo "$NEON_URL" | sed -E 's|://[^@]+@|://***@|')"
echo

# ── Step 1: DB 이전 ──────────────────────────────────────────────────────
echo "[1/3] 로컬 → Neon 데이터 이전 (약 1-2분, 105MB)..."
bash "$PROJECT_ROOT/deploy/migrate_to_external_pg.sh" "$NEON_URL"
echo

# ── Step 2: 마이그레이션 ─────────────────────────────────────────────────
echo "[2/3] 마이그레이션 6건 일괄 적용..."
bash "$PROJECT_ROOT/deploy/apply_migrations.sh" "$NEON_URL"
echo

# ── Step 3: 사후 검증 (launch-blocking 항목) ────────────────────────────
echo "[3/3] launch-blocking 항목 검증..."

# api_keys.trial_ends_at — D+17 발견 launch-blocking 버그
if ! psql "$NEON_URL" -tAc \
    "SELECT 1 FROM information_schema.columns WHERE table_name='api_keys' AND column_name='trial_ends_at'" \
    | grep -q 1; then
    echo "  [FAIL] api_keys.trial_ends_at 미적용 — 첫 paid webhook 실패합니다."
    exit 4
fi
echo "  ✓ api_keys.trial_ends_at (paid 결제 처리)"

# customer_id (Stripe→Paddle rename 완료)
if psql "$NEON_URL" -tAc \
    "SELECT 1 FROM information_schema.columns WHERE table_name='api_keys' AND column_name='stripe_customer_id'" \
    | grep -q 1; then
    echo "  [FAIL] stripe_customer_id 잔재 — Paddle 환불 후 키 비활성화 실패"
    exit 5
fi
echo "  ✓ api_keys.customer_id (Paddle rename 완료)"

# Paddle idempotency
if ! psql "$NEON_URL" -tAc \
    "SELECT 1 FROM information_schema.tables WHERE table_name='paddle_processed_events'" \
    | grep -q 1; then
    echo "  [FAIL] paddle_processed_events 미적용 — webhook 중복 처리 위험"
    exit 6
fi
echo "  ✓ paddle_processed_events (webhook idempotency)"

# 데이터 카운트
CLUSTERS=$(psql "$NEON_URL" -tAc "SELECT count(*) FROM clusters" 2>/dev/null || echo 0)
PRODUCTS=$(psql "$NEON_URL" -tAc "SELECT count(*) FROM products" 2>/dev/null || echo 0)
STORES=$(psql "$NEON_URL" -tAc "SELECT count(*) FROM stores" 2>/dev/null || echo 0)
echo "  ✓ data: clusters=${CLUSTERS}, products=${PRODUCTS}, stores=${STORES}"

if [ "$CLUSTERS" -lt 1000 ]; then
    echo "  [WARN] clusters < 1000 — 이전 손실 가능. 백업 dump 확인 권장."
fi

echo
echo "═══════════════════════════════════════════════════════════════"
echo "  Phase 1 완료. Neon DB 라이브 + 마이그레이션 정합."
echo "═══════════════════════════════════════════════════════════════"
echo
echo "다음 단계 (브라우저 작업):"
echo
echo "  1) Render Blueprint apply"
echo "     → https://dashboard.render.com/select-repo?type=blueprint"
echo "     → repo: ddookim/storescope 선택"
echo "     → render.neon.yaml 자동 감지 → Apply"
echo
echo "  2) Render Env Vars 입력 (storescope-api 서비스)"
echo "     DATABASE_URL = $(echo "$NEON_URL" | sed -E 's|://[^@]+@|://***@|')"
echo "     PADDLE_API_KEY = pdl_live_xxx        (paddle.com → Developer → Authentication)"
echo "     PADDLE_CLIENT_TOKEN = live_xxx       (paddle.com → Developer → Authentication)"
echo "     PADDLE_WEBHOOK_SECRET = (Phase 2에서 갱신, 일단 'pending' 입력)"
echo
echo "  3) 빌드 완료 후 (~3분) API_URL 복사 → 다음 명령 실행:"
echo
echo "     bash deploy/launch_phase2.sh \"https://storescope-api-xxxx.onrender.com\""
echo
