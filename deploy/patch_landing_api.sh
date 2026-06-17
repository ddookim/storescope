#!/bin/bash
# landing line 15 (production API URL) 일괄 패치.
# 두 위치(작업본 + 배포본)를 동시에 갱신하고 diff=0 검증.
#
# 사용법:
#   bash deploy/patch_landing_api.sh "<NEW_PROD_API_URL>"
#
# 예:
#   bash deploy/patch_landing_api.sh "https://storescope-api.onrender.com"
#   bash deploy/patch_landing_api.sh "https://api.storescope.me"
#
# 동작:
#   1) 현재 line 15의 URL 추출 (정규식 매칭)
#   2) 두 파일 (working + deploy) 백업
#   3) sed 인플레이스 치환
#   4) 두 파일 diff=0 검증
#   5) 결과 보고 (배포 명령 안내)

set -euo pipefail

if [ $# -lt 1 ]; then
    echo "사용법: $0 <NEW_PROD_API_URL>"
    echo "예:    $0 'https://storescope-api.onrender.com'"
    echo "       $0 'https://api.storescope.me'"
    exit 1
fi

NEW_URL="$1"

# URL 형식 sanity check
if ! echo "$NEW_URL" | grep -qE "^https://[a-zA-Z0-9.-]+(:[0-9]+)?$"; then
    echo "[ABORT] URL 형식 이상 (https://host[:port]만 허용, trailing slash 금지)"
    exit 2
fi

WORKING="/Users/dodokim/auto biz_factory/storescope_landing_fixed.html"
DEPLOY="/Users/dodokim/Documents/Claude/Projects/Auto-Biz Factory/StoreScope/landing/index.html"

for f in "$WORKING" "$DEPLOY"; do
    if [ ! -f "$f" ]; then
        echo "[ABORT] 파일 없음: $f"
        exit 3
    fi
done

# 현재 URL 추출 — 'ddookim.github.io' 분기가 있는 라인에서만 https URL 추출
# (전체 grep는 fonts.googleapis.com/cdn.paddle.com 등을 잘못 잡을 위험)
OLD_URL=$(grep -F "ddookim.github.io" "$WORKING" | grep -oE "https://[a-z0-9.-]+" | head -1 || true)

if [ -z "$OLD_URL" ]; then
    echo "[ABORT] ddookim.github.io 분기에서 production API URL을 찾지 못함."
    echo "        line 15 부근 구조 변경 확인 필요."
    exit 4
fi

# 안전장치: 추출된 URL이 외부 CDN/폰트/GA 도메인이면 abort (잘못된 매칭 방지)
case "$OLD_URL" in
    *fonts.google*|*googletagmanager*|*cdn.paddle*|*github.io*)
        echo "[ABORT] 잘못된 URL 추출됨: $OLD_URL"
        echo "        landing HTML 구조 변경. 스크립트 패턴 갱신 필요."
        exit 4
        ;;
esac

# git uncommitted 변경 경고 (배포본 측)
if [ -d "/Users/dodokim/Documents/Claude/Projects/Auto-Biz Factory/StoreScope/.git" ]; then
    DEPLOY_DIRTY=$(cd "/Users/dodokim/Documents/Claude/Projects/Auto-Biz Factory/StoreScope" && git status --porcelain landing/ 2>/dev/null | head -1)
    if [ -n "$DEPLOY_DIRTY" ]; then
        echo "[WARN] StoreScope/landing/ 에 uncommitted 변경 존재:"
        echo "$DEPLOY_DIRTY"
        printf "그래도 진행? (y/N): "
        read -r ans
        [ "$ans" = "y" ] || { echo "중단."; exit 6; }
    fi
fi

if [ "$OLD_URL" = "$NEW_URL" ]; then
    echo "[SKIP] 이미 $NEW_URL 로 설정됨"
    exit 0
fi

TS=$(date +%Y%m%d_%H%M%S)
cp "$WORKING" "${WORKING}.bak-${TS}"
cp "$DEPLOY" "${DEPLOY}.bak-${TS}"
echo "[1/4] 백업: *.bak-${TS}"

# macOS sed는 -i '' 필요
sed -i '' "s|${OLD_URL}|${NEW_URL}|g" "$WORKING"
sed -i '' "s|${OLD_URL}|${NEW_URL}|g" "$DEPLOY"
echo "[2/4] 치환 완료: ${OLD_URL} → ${NEW_URL}"

# diff 검증
if ! diff -q "$WORKING" "$DEPLOY" >/dev/null; then
    echo "[FAIL] 두 파일 diff 발생. 수동 확인 필요."
    echo "  $WORKING"
    echo "  $DEPLOY"
    exit 5
fi
echo "[3/4] 작업본 ↔ 배포본 diff=0 검증 통과"

# 치환된 라인 확인
echo "[4/4] 치환된 라인 (deploy 기준):"
grep -nF "$NEW_URL" "$DEPLOY" | head -3

echo ""
echo "✓ 패치 완료. 라이브 반영하려면:"
echo "  cd '/Users/dodokim/Documents/Claude/Projects/Auto-Biz Factory/StoreScope'"
echo "  ./deploy/landing-deploy.sh"
echo ""
echo "롤백: cp '${WORKING}.bak-${TS}' '$WORKING' && cp '${DEPLOY}.bak-${TS}' '$DEPLOY'"
