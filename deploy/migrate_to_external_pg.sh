#!/bin/bash
# StoreScope DB 이전: 로컬 PostgreSQL → 외부 무료 PG (Neon / Render / Supabase)
#
# 사용법:
#   bash deploy/migrate_to_external_pg.sh "<EXTERNAL_DB_URL>"
#
# 예 (Neon):
#   bash deploy/migrate_to_external_pg.sh "postgresql://shopify:xxx@ep-xxx.aws.neon.tech/storescope?sslmode=require"
#
# 동작:
#   1) 로컬 storescope DB 백업 (custom format, gzip 자동)
#   2) 외부 DB 연결 검증 (psql ping)
#   3) pg_restore --clean 으로 외부 DB 복원
#   4) 핵심 카운트 검증 (clusters=1671, products>=140000, stores>=1400)
#   5) trend_score 컬럼 존재 검증
#   6) 백업 파일 보존 (재시도용)
#
# 안전장치:
#   - 외부 URL이 "localhost"/"127."이면 abort (실수로 로컬 덮어쓰기 방지)
#   - pg_restore 실패 시 즉시 종료 + 백업 경로 출력
#   - 검증 카운트 미달 시 비제로 exit

set -euo pipefail

if [ $# -lt 1 ]; then
    echo "사용법: $0 <EXTERNAL_DB_URL>"
    echo "예:    $0 'postgresql://user:pass@ep-xxx.neon.tech/storescope?sslmode=require'"
    exit 1
fi

EXTERNAL_URL="$1"
LOCAL_DB="${LOCAL_DB:-storescope}"
TS="$(date +%Y%m%d_%H%M%S)"
DUMP_FILE="/tmp/storescope_${TS}.dump"

# ── 안전장치: 로컬 URL 방지 ─────────────────────────────────────
if echo "$EXTERNAL_URL" | grep -qE "(localhost|127\.0\.0\.1|@localhost|@127\.)"; then
    echo "[ABORT] EXTERNAL_URL이 로컬을 가리킵니다. 외부 PG URL이 필요합니다."
    exit 2
fi

# ── 0. PG 버전 호환성 사전 체크 ──────────────────────────────────
echo "[0/6] PG 버전 호환성 체크..."
DUMP_MAJOR=$(pg_dump --version 2>/dev/null | grep -oE '[0-9]+' | head -1)
echo "  pg_dump major: $DUMP_MAJOR"

# ── 1. 외부 DB 연결 사전 검증 + 버전 확인 ────────────────────────
echo "[1/6] 외부 DB 연결 검증..."
if ! psql "$EXTERNAL_URL" -c "SELECT 1;" >/dev/null 2>&1; then
    echo "[FAIL] 외부 DB 연결 실패. URL 확인 (SSL 옵션 ?sslmode=require 필요할 수 있음)."
    exit 3
fi
TARGET_MAJOR=$(psql "$EXTERNAL_URL" -tAc "SHOW server_version_num;" 2>/dev/null | head -1 | cut -c1-2 | sed 's/^0//')
echo "  외부 DB major: $TARGET_MAJOR"
if [ -n "$DUMP_MAJOR" ] && [ -n "$TARGET_MAJOR" ] && [ "$DUMP_MAJOR" -gt "$TARGET_MAJOR" ]; then
    echo "[FAIL] pg_dump($DUMP_MAJOR) > 외부 DB($TARGET_MAJOR). 외부가 더 낮은 버전 → 복원 실패 위험."
    echo "       PostgreSQL $TARGET_MAJOR 이하 pg_dump 사용 또는 외부 DB 업그레이드 필요."
    exit 3
fi
echo "  OK"

# ── 2. 로컬 DB 덤프 (Neon/외부 호환 옵션) ────────────────────────
# --no-tablespaces: Neon은 custom tablespace 거부
# -Z 9: 최대 압축 (Postgres custom format 내장 gzip)
echo "[2/6] 로컬 $LOCAL_DB → $DUMP_FILE ..."
pg_dump -Fc -Z 9 --no-tablespaces -d "$LOCAL_DB" -f "$DUMP_FILE"
DUMP_SIZE=$(du -h "$DUMP_FILE" | cut -f1)
echo "  OK ($DUMP_SIZE, 최대 압축)"

# ── 3. 외부 DB 복원 ──────────────────────────────────────────────
# --single-transaction: 부분 실패 시 자동 롤백 → DB 일관성 유지
# --clean --if-exists: 기존 객체 안전 삭제 후 재생성
# user/role 경고는 정상 (--no-owner --no-acl 사용 시), 진짜 에러만 비제로 exit
echo "[3/6] 외부 DB 복원 시작 (single-transaction, 수분 소요)..."
RESTORE_LOG=/tmp/migrate_log_${TS}.txt
set +e
pg_restore --no-owner --no-acl --clean --if-exists --single-transaction \
    -d "$EXTERNAL_URL" "$DUMP_FILE" >"$RESTORE_LOG" 2>&1
RESTORE_EXIT=$?
set -e
if [ "$RESTORE_EXIT" -ne 0 ]; then
    echo "[FAIL] pg_restore exit=$RESTORE_EXIT — single-transaction 자동 롤백됨"
    echo "       로그: $RESTORE_LOG"
    echo "       마지막 10줄:"
    tail -10 "$RESTORE_LOG" | sed 's/^/         /'
    exit 4
fi
echo "  복원 완료 (롤백 발생 없음)"

# ── 4. 핵심 카운트 검증 ──────────────────────────────────────────
echo "[4/5] 카운트 검증..."
CLUSTERS=$(psql "$EXTERNAL_URL" -tAc "SELECT COUNT(*) FROM clusters;" 2>/dev/null || echo 0)
PRODUCTS=$(psql "$EXTERNAL_URL" -tAc "SELECT COUNT(*) FROM products;" 2>/dev/null || echo 0)
STORES=$(psql "$EXTERNAL_URL" -tAc "SELECT COUNT(*) FROM stores;" 2>/dev/null || echo 0)
echo "  clusters=$CLUSTERS  products=$PRODUCTS  stores=$STORES"

FAIL=0
[ "$CLUSTERS" -lt 1500 ] && { echo "  [FAIL] clusters < 1500 (기대 1671)"; FAIL=1; }
[ "$PRODUCTS" -lt 140000 ] && { echo "  [FAIL] products < 140000"; FAIL=1; }
[ "$STORES" -lt 1400 ] && { echo "  [FAIL] stores < 1400"; FAIL=1; }

# ── 5. trend_score 컬럼 검증 (2026-05-26 migration) ──────────────
echo "[5/5] trend_score 컬럼 검증..."
HAS_TREND=$(psql "$EXTERNAL_URL" -tAc "SELECT COUNT(*) FROM information_schema.columns WHERE table_name='clusters' AND column_name='trend_score';" 2>/dev/null || echo 0)
if [ "$HAS_TREND" != "1" ]; then
    echo "  [FAIL] trend_score 컬럼 없음 (migrate_2026_05_26.sql 누락 가능)"
    FAIL=1
else
    echo "  OK"
fi

# ── 결과 ─────────────────────────────────────────────────────────
echo ""
if [ "$FAIL" -ne 0 ]; then
    echo "[RESULT] FAIL — 백업 파일 보존: $DUMP_FILE"
    echo "        로그: /tmp/migrate_log_${TS}.txt"
    exit 4
fi

echo "[RESULT] PASS — 외부 DB 정상 동기화"
echo "  백업 파일 (안전 위해 보존): $DUMP_FILE"
echo ""
echo "다음 단계:"
echo "  1. Render storescope-api ENV에 DATABASE_URL 입력 (같은 URL)"
echo "  2. Render storescope-app ENV에 DATABASE_URL 입력 (같은 URL)"
echo "  3. curl https://<api-url>/health/freshness  → mode 검증"
