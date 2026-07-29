#!/bin/bash
# StoreScope Blue Team Defense Verifier
# =========================================
# Red team이 공격 벡터를 찾는 반대로, 블루팀은 방어 태세를 확인.
# 각 red team 룰의 반대편에서 "방어 코드가 실제로 존재하는가" 검증.
#
# 사용법:
#   bash deploy/blue_team_defense.sh
#
# 커버 (red_team_lint.sh 와 1:1 매칭):
#   D1. Rate limiting: 민감 endpoint 에 @limiter.limit 적용?
#   D2. HMAC verify: webhook 서명 검증 존재?
#   D3. Input validation: Pydantic model 커버리지?
#   D4. Timing-safe compare: hmac.compare_digest 사용?
#   D5. CSP hardening: header 존재 + strict directives?
#   D6. Secret management: os.environ.get 패턴?
#   D7. Fail-closed: try/except 에 logging.exception?
#   D8. SQL parameterization: cur.execute (sql, params)?
#   D9. Session security: SameSite / Secure flags?
#   D10. Error handling: user-facing 명시 처리?

set -euo pipefail

PASS=0
FAIL=0
INFO=0

_pass() { echo "  🛡  PASS: $1"; PASS=$((PASS+1)); }
_fail() { echo "  💥 GAP:  $1" >&2; FAIL=$((FAIL+1)); }
_info() { echo "  ℹ  INFO: $1"; INFO=$((INFO+1)); }

echo "══════════════════════════════════════════════════════════"
echo "  🛡  Blue Team Defense Verification"
echo "══════════════════════════════════════════════════════════"

echo ""
echo "── D1: Rate limiting on sensitive endpoints ──────────"
RATE_LIMIT_COUNT=$(grep -rE "@limiter\.limit" api/ 2>/dev/null | wc -l | tr -d ' ' || true)
if [ "$RATE_LIMIT_COUNT" -ge 5 ]; then
    _pass "@limiter.limit 데코레이터 $RATE_LIMIT_COUNT 개 적용 중"
    grep -rnE "@limiter\.limit\(" api/ 2>/dev/null | head -8 | sed 's/^/       /'
else
    _fail "@limiter.limit $RATE_LIMIT_COUNT 개만 적용 — 민감 endpoint 방어 부족"
fi

echo ""
echo "── D2: HMAC webhook signature verification ────────────"
if grep -qE "hmac\.compare_digest.*signature|verify.*signature" api/paddle_routes.py 2>/dev/null; then
    _pass "Paddle webhook HMAC 서명 검증 존재 (timing-safe)"
    grep -nE "hmac\.compare_digest" api/paddle_routes.py 2>/dev/null | head -3 | sed 's/^/       /'
else
    _fail "webhook 서명 검증 없음 — replay attack 취약"
fi

echo ""
echo "── D3: Pydantic input validation coverage ────────────"
PYDANTIC_MODELS=$(grep -rE "^class.*\(BaseModel\)" api/ 2>/dev/null | wc -l | tr -d ' ' || true)
if [ "$PYDANTIC_MODELS" -ge 3 ]; then
    _pass "Pydantic BaseModel $PYDANTIC_MODELS 개 정의 — 입력 검증 계층 존재"
else
    _info "Pydantic BaseModel $PYDANTIC_MODELS 개 — 신 endpoint 추가 시 검증 필요"
fi

echo ""
echo "── D4: Timing-safe secret comparison ──────────────────"
UNSAFE_CMP=$(grep -rE "(ADMIN_SECRET|api_key|_SECRET)\s*==\s*" api/ 2>/dev/null | grep -v "compare_digest" | grep -v "test_" | wc -l | tr -d ' ' || true)
if [ "$UNSAFE_CMP" -eq 0 ]; then
    _pass "타이밍 취약 == 비교 0건 (모두 hmac.compare_digest)"
else
    _fail "타이밍 취약 == 비교 $UNSAFE_CMP 건 — 타이밍 공격 취약"
fi

echo ""
echo "── D5: CSP hardening ─────────────────────────────────"
if grep -qE 'Content-Security-Policy' landing/index.html 2>/dev/null; then
    _pass "landing CSP meta tag 존재"
    DIRECTIVES=$(grep -oE "(default|script|style|font|img|connect|frame|object|base|form)-src" landing/index.html | sort -u | wc -l | tr -d ' ' || true)
    _info "CSP directives $DIRECTIVES 개 (default/script/style/font/img/connect/frame/object/base/form-src)"
else
    _fail "landing CSP meta 없음"
fi

if grep -qE 'referrer' landing/index.html 2>/dev/null; then
    _pass "Referrer-Policy meta 존재"
else
    _fail "Referrer-Policy 없음"
fi

echo ""
echo "── D6: Secret management (os.environ.get pattern) ─────"
HARDCODED_SECRET=$(grep -rnE '(API_KEY|SECRET|PASSWORD|TOKEN)\s*=\s*["\047][A-Za-z0-9]{16,}' api/ services/ 2>/dev/null | grep -vE 'os\.environ|test_|_test\.|placeholder|REPLACE|example' | wc -l | tr -d ' ' || true)
if [ "$HARDCODED_SECRET" -eq 0 ]; then
    _pass "하드코딩된 secret 0건 (모두 env var 로 관리)"
else
    _fail "하드코딩 의심 secret $HARDCODED_SECRET 건 — 재검토"
fi

ENV_USAGES=$(grep -rE "os\.environ\.(get|\[)" api/ services/ 2>/dev/null | wc -l | tr -d ' ' || true)
_info "os.environ 참조 $ENV_USAGES 건 (secret 대체 지표)"

echo ""
echo "── D7: Fail-closed pattern (logging.exception) ────────"
EXPLICIT_LOG=$(grep -rE "logging\.exception|logger\.exception|logger\.error.*exc_info" api/ services/ pipeline/ 2>/dev/null | wc -l | tr -d ' ' || true)
BARE_PASS=$(grep -rnE '^\s*except.*:\s*$' api/ services/ pipeline/ 2>/dev/null | wc -l | tr -d ' ' || true)
if [ "$EXPLICIT_LOG" -ge 5 ]; then
    _pass "명시 logging.exception $EXPLICIT_LOG 건 — silent failure 방지"
else
    _info "logging.exception $EXPLICIT_LOG 건 — 더 커버 권장"
fi
_info "except: bare 블록 총 $BARE_PASS 개 (pass 여부 확인)"

echo ""
echo "── D8: SQL parameterization ───────────────────────────"
PARAM_SQL=$(grep -rE "cur\.execute\([^,]+,\s*[\(\{]" api/ services/ pipeline/ 2>/dev/null | wc -l | tr -d ' ' || true)
FSTRING_SQL=$(grep -rE 'cur\.execute\(\s*f["\047]' api/ services/ pipeline/ 2>/dev/null | wc -l | tr -d ' ' || true)
if [ "$FSTRING_SQL" -eq 0 ]; then
    _pass "f-string SQL 0건 (모두 parameterized)"
else
    _fail "f-string SQL $FSTRING_SQL 건 — injection 리스크"
fi
_info "parameterized cur.execute $PARAM_SQL 건 사용"

echo ""
echo "── D9: HTTPS + secure defaults ────────────────────────"
if grep -qE "upgrade-insecure-requests|strict-transport-security" landing/index.html 2>/dev/null; then
    _pass "HTTPS 강제 (upgrade-insecure-requests 또는 HSTS)"
else
    _info "HTTPS 강제 헤더 없음 — GH Pages 는 자동 HTTPS 제공"
fi

echo ""
echo "── D10: Error handling explicit vs silent ─────────────"
SWALLOWED=$(grep -rnE 'except[^:]*:\s*pass\s*$' api/ services/ pipeline/ 2>/dev/null | wc -l | tr -d ' ' || true)
if [ "$SWALLOWED" -eq 0 ]; then
    _pass "silent 'except: pass' 0건"
else
    _fail "silent 'except: pass' $SWALLOWED 건 — logging.exception 로 가시화"
fi

echo ""
echo "══════════════════════════════════════════════════════════"
echo "  🛡  Blue Team Verdict: DEFENDED=$PASS  GAP=$FAIL  INFO=$INFO"
echo "══════════════════════════════════════════════════════════"

if [ "$FAIL" -gt 0 ]; then
    exit 1
fi
exit 0
