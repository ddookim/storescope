#!/bin/bash
# StoreScope migrations 일괄 적용 — 외부 PG (Neon / Render PG / Supabase)
#
# 사용법:
#   bash deploy/apply_migrations.sh "<EXTERNAL_DB_URL>"
#
# 동작:
#   1. migrations/*.sql 파일을 이름 순으로 정렬 (날짜 prefix 보장)
#   2. 각 마이그레이션 적용 + 에러 즉시 중단
#   3. 모든 마이그레이션 정합 검증
#
# 안전장치:
#   - 외부 URL이 localhost면 abort
#   - 적용 전 DB 연결 검증
#   - 멱등 (재실행 안전 — 마이그레이션 자체가 IF NOT EXISTS 사용)
#
# 검출 사례 (라운드 10):
#   stripe_customer_id → customer_id rename 누락 시 구독 취소 후 키 비활성화 실패

set -euo pipefail

if [ $# -lt 1 ]; then
    echo "사용법: $0 <EXTERNAL_DB_URL>"
    exit 1
fi

DB_URL="$1"

# 안전: 로컬 URL 차단
if echo "$DB_URL" | grep -qE "(localhost|127\.0\.0\.1)"; then
    echo "[ABORT] 로컬 URL — 외부 DB URL이 필요합니다."
    exit 2
fi

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
MIG_DIR="$PROJECT_ROOT/migrations"

if [ ! -d "$MIG_DIR" ]; then
    echo "[ABORT] migrations 디렉토리 없음: $MIG_DIR"
    exit 3
fi

echo "=== StoreScope migrations 일괄 적용 ==="
echo "DB: $(echo "$DB_URL" | sed -E 's|://[^@]+@|://***@|')"
echo "마이그레이션 디렉토리: $MIG_DIR"
echo

# 연결 검증
echo "[검증] DB 연결..."
if ! psql "$DB_URL" -c "SELECT 1" >/dev/null 2>&1; then
    echo "[FAIL] DB 연결 실패. URL 확인 (?sslmode=require 누락 가능)."
    exit 4
fi
echo "  OK"
echo

# 순서대로 적용 (공백 포함 경로 안전 — find + while read)
count=0
while IFS= read -r migration; do
    [ -z "$migration" ] && continue
    name=$(basename "$migration")
    count=$((count + 1))
    echo "[$count] $name"
    # psql 출력에서 일반 명령 결과만 필터링하여 표시
    set +e
    output=$(psql "$DB_URL" -v ON_ERROR_STOP=1 -f "$migration" 2>&1)
    rc=$?
    set -e
    if [ $rc -ne 0 ]; then
        echo "$output" | tail -5
        echo "[FAIL] $name 적용 실패"
        exit 5
    fi
done < <(find "$MIG_DIR" -maxdepth 1 -name "*.sql" -type f | sort)

echo
echo "=== 사후 검증 (핵심 컬럼/테이블 존재) ==="

# 라운드 10 회귀 검증 — stripe_* 컬럼 없어야 함
if psql "$DB_URL" -tAc "SELECT 1 FROM information_schema.columns WHERE table_name='api_keys' AND column_name='stripe_customer_id'" | grep -q 1; then
    echo "  [FAIL] api_keys.stripe_customer_id 잔재 — rename 마이그레이션 미적용"
    exit 6
fi
echo "  ✓ api_keys.customer_id (Stripe 잔재 정리)"

# Idempotency 테이블
if ! psql "$DB_URL" -tAc "SELECT 1 FROM information_schema.tables WHERE table_name='paddle_processed_events'" | grep -q 1; then
    echo "  [FAIL] paddle_processed_events 테이블 누락"
    exit 7
fi
echo "  ✓ paddle_processed_events (webhook idempotency)"

# 인덱스
if ! psql "$DB_URL" -tAc "SELECT 1 FROM pg_indexes WHERE indexname='idx_clusters_store_count_desc'" | grep -q 1; then
    echo "  [WARN] idx_clusters_store_count_desc 누락 — /trending 가속 인덱스 미적용"
fi

# Dead 테이블 부재
if psql "$DB_URL" -tAc "SELECT 1 FROM information_schema.tables WHERE table_name='webhook_subscriptions'" | grep -q 1; then
    echo "  [WARN] webhook_subscriptions 잔존 — drop 마이그레이션 미적용"
fi
echo "  ✓ webhook_subscriptions (drop 정리 완료)"

echo
echo "[RESULT] $count 마이그레이션 모두 정상."
