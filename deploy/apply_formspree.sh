#!/bin/bash
# Formspree swap 자동 적용 스크립트 (D+58 launch validation path).
#
# 사용법:
#   bash deploy/apply_formspree.sh <FORMSPREE_ID>
# 예:
#   bash deploy/apply_formspree.sh xkgbojlv
#
# 실행 내용:
#   1) landing/index.html CSP connect-src 에 formspree.io 추가
#   2) hero form fetch → Formspree URL
#   3) X-Ray form fetch → Formspree URL
#   4) mock mode 강제 return (Formspree 사용으로 mock 불필요)
#   5) 로컬 verify_landing.sh 실행
#   6) commit + push origin main (gh-pages 자동 sync workflow 발동)
#
# 안전장치:
#   - 이미 적용된 상태면 no-op
#   - verify_landing.sh 실패 시 abort (커밋 안 함)
#   - Formspree ID 형식 검증 (영숫자 8자 이상)

set -euo pipefail

FORMSPREE_ID="${1:-}"

if [ -z "$FORMSPREE_ID" ]; then
  echo "ERROR: FORMSPREE_ID 인자 필요"
  echo "사용법: bash deploy/apply_formspree.sh <FORMSPREE_ID>"
  echo "Formspree ID = formspree.io dashboard 에서 form 생성 후 URL 마지막 slug (예: xkgbojlv)"
  exit 1
fi

if ! echo "$FORMSPREE_ID" | grep -qE '^[a-zA-Z0-9]{8,}$'; then
  echo "ERROR: FORMSPREE_ID 형식 이상 (영숫자 8자 이상 예상): $FORMSPREE_ID"
  exit 1
fi

# 작업 디렉토리로 이동 (스크립트 위치 기준)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR/.."

LANDING="landing/index.html"
FORMSPREE_URL="https://formspree.io/f/$FORMSPREE_ID"

if [ ! -f "$LANDING" ]; then
  echo "ERROR: $LANDING 없음. StoreScope repo root 에서 실행 확인."
  exit 1
fi

# 이미 적용된 상태 체크
if grep -q "formspree.io/f/" "$LANDING"; then
  echo "이미 Formspree URL 적용됨. 재적용은 수동 확인 후 진행."
  grep -n "formspree.io/f/" "$LANDING" | head -3
  exit 0
fi

# 백업
cp "$LANDING" "$LANDING.bak-$(date +%Y%m%d%H%M%S)"
echo "백업: $LANDING.bak-*"

# ── 수정 1: CSP connect-src ────
sed -i.tmp "s|https://api.storescope.com;|https://api.storescope.com https://formspree.io;|" "$LANDING"
rm "$LANDING.tmp"
echo "✓ CSP: formspree.io 추가"

# ── 수정 2: hero form fetch (source: hero) ────
python3 <<PYEOF
import re
p = "$LANDING"
with open(p) as f: s = f.read()

# hero fetch 교체 (JSON.stringify({email:email, source:'hero'}) 근처)
hero_pat = r"fetch\(API_BASE \+ '/leads',\s*\{[^}]*headers:\s*\{\s*'Content-Type':\s*'application/json'\s*\},\s*body:\s*JSON\.stringify\(\{\s*email:\s*email,\s*source:\s*'hero'\s*\}\)"
hero_repl = "fetch('$FORMSPREE_URL', {\n      method: 'POST',\n      headers: { 'Content-Type': 'application/json', 'Accept': 'application/json' },\n      body: JSON.stringify({ email: email, source: 'hero' })"
new_s, n1 = re.subn(hero_pat, hero_repl, s, flags=re.DOTALL)
if n1 != 1:
    print(f"ERROR: hero fetch 매칭 실패 (n={n1}). 수동 검토 필요.")
    exit(2)

# xray fetch 교체
xray_pat = r"fetch\(API_BASE \+ '/leads',\s*\{[^}]*headers:\s*\{\s*'Content-Type':\s*'application/json'\s*\},\s*body:\s*JSON\.stringify\(\{\s*email:\s*email,\s*domain:\s*store,\s*source:\s*'xray'\s*\}\)"
xray_repl = "fetch('$FORMSPREE_URL', {\n      method: 'POST',\n      headers: { 'Content-Type': 'application/json', 'Accept': 'application/json' },\n      body: JSON.stringify({\n        email: email,\n        domain: store,\n        source: 'xray'\n      })"
new_s, n2 = re.subn(xray_pat, xray_repl, new_s, flags=re.DOTALL)
if n2 != 1:
    print(f"ERROR: xray fetch 매칭 실패 (n={n2}). 수동 검토 필요.")
    exit(2)

# mock mode 강제 return
mock_pat = r"\(function setupMockAPI\(\) \{\n(\s+)var apiBase"
mock_repl = r"(function setupMockAPI() {\n\1return; // Formspree 사용으로 mock 불필요 (2026-07-29)\n\1var apiBase"
new_s, n3 = re.subn(mock_pat, mock_repl, new_s)
if n3 != 1:
    print(f"WARN: mock mode 이미 비활성 or 패턴 변경 (n={n3}). 계속.")

with open(p, 'w') as f: f.write(new_s)
print(f"✓ hero fetch replaced ({n1}), xray fetch replaced ({n2}), mock disabled ({n3})")
PYEOF

# ── 검증 ────
echo "── verify_landing.sh 재실행 ──"
bash deploy/verify_landing.sh "$LANDING" 2>&1 | tail -5
if bash deploy/verify_landing.sh "$LANDING" 2>&1 | grep -q "FAIL=0"; then
  echo "✓ 검증 PASS"
else
  echo "ERROR: verify_landing.sh FAIL. 백업 복원 권장:"
  echo "  cp $LANDING.bak-* $LANDING"
  exit 3
fi

# ── commit + push ────
echo "── git commit + push ──"
git add "$LANDING"
git commit -m "feat(landing): Formspree swap for real lead capture (D+58 launch)

- CSP: connect-src + formspree.io
- Hero form fetch → $FORMSPREE_URL
- X-Ray form fetch → $FORMSPREE_URL
- Mock mode disabled (Formspree 실 저장)

Formspree free tier: 50 submissions/mo (form 당)
sync_gh_pages workflow 자동 발동 → 라이브 반영 60초 내"

git push origin main
echo ""
echo "── 완료 ──"
echo "1. sync_gh_pages workflow 자동 발동 대기 (30-60s)"
echo "2. 확인: curl -sS https://ddookim.github.io/storescope/ | grep formspree.io"
echo "3. Formspree 대시보드에서 test@example.com submit 후 수신 확인"
