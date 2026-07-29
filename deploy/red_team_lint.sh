#!/bin/bash
# StoreScope Red Team Linter — feedback 룰 자동 강제
# =====================================================
#
# ⚠ SOURCE: self-synthesized (2026-07-29 D+58, [[feedback-github-verified-code-priority]] 준수 표기)
#   GitHub verified source 아님 = StoreScope 도메인 특화 공격 벡터 감지 (경쟁사 이름, i18n dupe key, CJK overflow-wrap 등)
#   semgrep/.semgrep.yml 이 커버 못 하는 것 담당.
#   보안 영향: 0 (read-only grep + 카운트, 코드 수정 X).
#   보완 tool (GitHub verified):
#     - semgrep (custom .semgrep.yml YAML 룰)
#     - gitleaks (secret 스캔)
#     - github/codeql-action (semantic security)
#
# Source: 2026-07-29 D+58 사용자 red team 라운드에서 반복 발견된 문제들의 정규식 강제.
#
# 사용법:
#   bash deploy/red_team_lint.sh           # StoreScope repo root 에서 실행
#   bash deploy/red_team_lint.sh --strict  # WARN 도 FAIL 처리 (CI 용)
#
# 커버:
#   1. no-competitor-naming: TikTok/Amazon/Facebook/Instagram/YouTube 등 브랜드 직접 언급
#   2. no-personal-identity: dodo@ 이메일, "학생", "자율운항" 등 개인 정체
#   3. no-false-advertising: 코드 미구현 기능 광고 (수동 검토 heuristic)
#   4. i18n-integrity: dict 내 중복 key, HTML 사용 key 와 dict 미매칭
#   5. cjk-overflow-risk: CSS 에 overflow-wrap 없이 CJK 텍스트 담는 grid cell
#   6. csp-safety: script-src unsafe-* + 외부 origin 조합 위험도
#   7. dead-code: 사용 안 하는 i18n dict key, orphan price ID
#
# Exit code:
#   0 = PASS (또는 --strict 아닌데 WARN 만)
#   1 = FAIL (규칙 위배 또는 --strict 모드에서 WARN)

set -euo pipefail

STRICT="${1:-}"
LANDING="landing/index.html"

if [ ! -f "$LANDING" ]; then
    echo "ERROR: $LANDING 없음. StoreScope repo root 에서 실행 확인." >&2
    exit 1
fi

PASS=0
FAIL=0
WARN=0

_pass() { echo "  ✓ PASS: $1"; PASS=$((PASS+1)); }
_fail() { echo "  ✗ FAIL: $1" >&2; FAIL=$((FAIL+1)); }
_warn() {
    echo "  ⚠ WARN: $1"
    WARN=$((WARN+1))
    if [ "$STRICT" = "--strict" ]; then FAIL=$((FAIL+1)); fi
}

echo "── Rule 1: no-competitor-naming ──────────────────────"
# 브랜드 직접 언급 (i18n dict + HTML 본문 전체)
BRANDS='TikTok|Amazon\b|Facebook|Instagram|YouTube|ByteDance|Meta\b'
BRAND_HITS=$(grep -cE "$BRANDS" "$LANDING" 2>/dev/null || true)
BRAND_HITS=${BRAND_HITS:-0}
if [ "$BRAND_HITS" -eq 0 ]; then
    _pass "브랜드 직접 언급 0건 (TikTok/Amazon/Facebook/Instagram/YouTube/ByteDance/Meta)"
else
    _fail "브랜드 직접 언급 $BRAND_HITS 건 발견 — 표시광고법 §3 + 상표권 리스크. grep 으로 위치 확인:"
    grep -nE "$BRANDS" "$LANDING" | head -5 | sed 's/^/       /' >&2
fi

# 경쟁 카테고리 (자기 참조)
CATEGORIES='ad spy\b|광고 스파이($|[^크])|広告スパイ($|[^ク])|ad-spy'
CAT_HITS=$(grep -cE "$CATEGORIES" "$LANDING" 2>/dev/null || true)
CAT_HITS=${CAT_HITS:-0}
if [ "$CAT_HITS" -eq 0 ]; then
    _pass "경쟁 카테고리 언급 0건 (ad spy 등)"
else
    _fail "경쟁 카테고리 $CAT_HITS 건. 부정경쟁 우려. 자기 강점 중심 카피로 재작성 필요."
    grep -nE "$CATEGORIES" "$LANDING" | head -3 | sed 's/^/       /' >&2
fi

echo ""
echo "── Rule 2: no-personal-identity ──────────────────────"
DODO_HITS=$(grep -rlE 'dodo@storescope\.com' . --include="*.py" --include="*.html" --include="*.md" --exclude-dir=.git 2>/dev/null | wc -l | tr -d ' ' || true)
DODO_HITS=${DODO_HITS:-0}
if [ "$DODO_HITS" -eq 0 ]; then
    _pass "dodo@ 개인 이메일 leak 0건"
else
    _fail "dodo@storescope.com $DODO_HITS 파일에 leak. support@ 로 치환 필요."
    grep -rlE 'dodo@storescope\.com' . --include="*.py" --include="*.html" --include="*.md" --exclude-dir=.git 2>/dev/null | head -5 | sed 's/^/       /' >&2
fi

# "Hi Dodo" body pre-fill
DODO_BODY=$(grep -cE 'Hi.?Dodo|Hi%20Dodo|안녕.*도도' "$LANDING" 2>/dev/null || true)
if [ "$DODO_BODY" -eq 0 ]; then
    _pass "email body pre-fill 개인 이름 0건"
else
    _fail "email body 'Hi Dodo' 등 개인 이름 $DODO_BODY 건"
fi

# 학생/충남대/CNU 언급 (HTML comment 제외 — 사용자 미노출 텍스트)
STUDENT=$(grep -vE '^\s*/\*|^\s*<!--|^\s*\*' "$LANDING" | grep -cE '학생[^은는이가]|자율운항|충남대|CNU|대학생' 2>/dev/null || true)
if [ "$STUDENT" -eq 0 ]; then
    _pass "학생/대학 정체 노출 0건"
else
    _warn "학생/대학 정체 언급 $STUDENT 건 — 프로페셔널 톤 검토"
fi

echo ""
echo "── Rule 3: no-false-advertising heuristic ────────────"
# 특정 지표 hardcode (구현 안 되어 있을 가능성)
UNLIMITED=$(grep -cE 'Unlimited|무제한|無制限' "$LANDING" 2>/dev/null || true)
if [ "$UNLIMITED" -le 5 ]; then
    _pass "unlimited 언급 $UNLIMITED 건 (Pro 플랜 정합 범위)"
else
    _warn "unlimited 언급 $UNLIMITED 건 — 각 언급이 실제 무제한 기능인지 코드 검증 필요"
fi

# 없는 SLA 약속 (返金保証 = 환불 보증 = 다른 개념, 제외)
SLA=$(grep -E '99\.[0-9]+%|SLA|guaranteed uptime' "$LANDING" 2>/dev/null | grep -vE '返金保証|品質保証|money-back' | wc -l | tr -d ' ' || true)
if [ "$SLA" -eq 0 ]; then
    _pass "SLA/uptime 약속 0건 (계약 리스크 없음)"
else
    _warn "SLA/uptime 약속 $SLA 건 — Render free tier 는 SLA 없음, 표시광고법 §3 위반 가능"
fi

echo ""
echo "── Rule 4: i18n-integrity ────────────────────────────"
DUPE_CHECK=$(python3 <<'PYEOF'
import re
with open('landing/index.html') as f: s = f.read()
# EN/KO/JA 블록 각각 분리
en_m = re.search(r'"en"\s*:\s*\{(.*?)^\s*\}', s, re.DOTALL | re.MULTILINE) or re.search(r'\{\s*hero_h1_line1:.*?\}(?=\s*,\s*"ko"|\s*,\s*ko\s*:)', s, re.DOTALL)
def dupes(block_str, name):
    keys = re.findall(r'^\s{2,}(\w+):', block_str, re.MULTILINE)
    from collections import Counter
    c = Counter(keys)
    dupes = [k for k, v in c.items() if v > 1]
    return name, dupes
# 3 lang 블록 접근
blocks = re.findall(r'\{[^{}]*?hero_h1_line1[^{}]*?\}', s, re.DOTALL)
for i, b in enumerate(blocks[:3]):
    name = ['EN','KO','JA'][i]
    keys = re.findall(r'^\s{6,}(\w+):', b, re.MULTILINE)
    from collections import Counter
    c = Counter(keys)
    dupes_list = [k for k, v in c.items() if v > 1]
    if dupes_list:
        print(f'DUPE:{name}:{len(dupes_list)}:{",".join(dupes_list[:5])}')
    else:
        print(f'OK:{name}')
PYEOF
)
DUPE_ISSUES=$(echo "$DUPE_CHECK" | grep -c "DUPE:" 2>/dev/null || true)
DUPE_ISSUES=${DUPE_ISSUES:-0}
if [ "$DUPE_ISSUES" -eq 0 ]; then
    _pass "i18n dict 중복 key 0건 (3 lang)"
else
    _fail "i18n dict 중복 key 발견:"
    echo "$DUPE_CHECK" | grep "DUPE:" | sed 's/^/       /' >&2
fi

echo ""
echo "── Rule 5: cjk-overflow-risk ─────────────────────────"
# .faq-item details[open] p 에 overflow-wrap 있어야 함 (D+58 R2 학습)
if grep -qE '\.faq-item details\[open\] p.*overflow-wrap' "$LANDING"; then
    _pass "FAQ 답변 CSS overflow-wrap 존재 (CJK 오버플로 방지)"
else
    _fail "FAQ 답변에 overflow-wrap 없음 — JA/KO CJK 긴 문장 셀 밖 오버플로 위험"
fi

# grid cell width 제한 없는 상태
GRID_CELLS=$(grep -cE 'grid-template-columns.*1fr 1fr' "$LANDING" 2>/dev/null || true)
if [ "$GRID_CELLS" -gt 5 ]; then
    _warn "grid 2-col 사용 $GRID_CELLS 건 — 각 cell 의 CJK wrap 안전성 별도 검증 권장"
fi

echo ""
echo "── Rule 6: csp-safety ────────────────────────────────"
if grep -qE "'unsafe-eval'" "$LANDING"; then
    _warn "CSP 에 unsafe-eval 존재 — eval/Function() 사용 확인, 가능하면 제거"
else
    _pass "CSP unsafe-eval 없음"
fi

# CORS-related open
if grep -qE "connect-src[^;]*\*\.onrender\.com" "$LANDING"; then
    _warn "CSP connect-src *.onrender.com wildcard — production hostname 확정 후 축소 권장"
else
    _pass "CSP connect-src wildcard 없음"
fi

echo ""
echo "── Rule 7: dead-code ─────────────────────────────────"
# orphan price ID — openCheckout('pri_...') OR data-price-mo="pri_..." 사용 감지
ALL_PRICES=$(grep -oE 'pri_[a-z0-9]+' "$LANDING" | sort -u)
USED_PRICES=$(grep -oE "openCheckout\('pri_[a-z0-9]+'|data-price-mo=\"pri_[a-z0-9]+" "$LANDING" | grep -oE 'pri_[a-z0-9]+' | sort -u)
ORPHAN_PRICES=$(comm -23 <(echo "$ALL_PRICES") <(echo "$USED_PRICES") | wc -l | tr -d ' ')
if [ "$ORPHAN_PRICES" -eq 0 ]; then
    _pass "orphan price ID 0건"
else
    _warn "orphan price ID $ORPHAN_PRICES 건 (unused Paddle price):"
    comm -23 <(echo "$ALL_PRICES") <(echo "$USED_PRICES") | sed 's/^/       /'
fi

# dead i18n keys (EN dict 정의 but HTML 사용 X)
DEAD_KEYS=$(python3 <<'PYEOF' 2>/dev/null
import re
with open('landing/index.html') as f: s = f.read()
used_i18n = set(re.findall(r'\bdata-i18n="([^"]+)"', s))
used_pl = set(re.findall(r'\bdata-i18n-placeholder="([^"]+)"', s))
aria_raw = re.findall(r'\bdata-aria-i18n="([^"]+)"', s)
aria = set(r.split(':', 1)[1] if ':' in r else r for r in aria_raw)
all_used = used_i18n | used_pl | aria
en_m = re.search(r'\{[^{}]*hero_h1_line1[^{}]*\}', s, re.DOTALL)
if en_m:
    en_keys = set(re.findall(r'^\s{6,}(\w+):', en_m.group(0), re.MULTILINE))
    dead = en_keys - all_used
    print(len(dead))
else:
    print(0)
PYEOF
)
if [ "$DEAD_KEYS" -le 30 ]; then
    _pass "dead i18n keys $DEAD_KEYS (허용 범위 <30)"
else
    _warn "dead i18n keys $DEAD_KEYS — cleanup 라운드 권장"
fi

echo ""
echo "═════════════════════════════════════════════════════════"
echo "  RESULT: PASS=$PASS  FAIL=$FAIL  WARN=$WARN"
echo "═════════════════════════════════════════════════════════"

if [ "$FAIL" -gt 0 ]; then
    exit 1
fi
exit 0
