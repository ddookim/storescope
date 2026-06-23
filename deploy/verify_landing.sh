#!/bin/bash
# StoreScope landing 회귀 검증 (배포 전 마지막 게이트)
# 사용법:
#   bash deploy/verify_landing.sh                              # 기본 작업본 검증
#   bash deploy/verify_landing.sh /path/to/index.html          # 특정 파일 검증
#
# 검출:
#   1. HTML parser 에러 (악성 + 깨진 구조)
#   2. 죽은 외부 URL (trycloudflare in href, NOT in API_BASE resolver — 자동 패치 대상)
#   3. OG image 라이브 200 응답
#   4. 마스터플랜 sweep 후 dead 카테고리 부활 (annual toggle, save-pill 등)
#   5. Schema.org JSON 파싱
#   6. 파일 사이즈 임계 (180KB-220KB 범위 — 마스터플랜 sweep 트렌드)
#   7. formsubmit.co 외부 의존 부활 차단
#   8. Hero CTA 마스터플랜 정합 (X-Ray primary)
#   9. 가짜 promo (limited time, X seats left) 차단

set -euo pipefail

FILE="${1:-/Users/dodokim/auto biz_factory/storescope_landing_fixed.html}"

if [ ! -f "$FILE" ]; then
    echo "[ABORT] 파일 없음: $FILE"; exit 1
fi

TS=$(date +%Y%m%d_%H%M%S)
PASS=0; FAIL=0; WARN=0
mark_pass() { echo "  PASS: $1"; PASS=$((PASS+1)); }
mark_fail() { echo "  FAIL: $1"; FAIL=$((FAIL+1)); }
mark_warn() { echo "  WARN: $1"; WARN=$((WARN+1)); }

echo "=== StoreScope landing verify ($(basename "$FILE")) at $TS ==="
echo "----------"

# 1. HTML parser
if python3 -c "from html.parser import HTMLParser; HTMLParser().feed(open('$FILE').read())" 2>/dev/null; then
    mark_pass "HTML parser 통과"
else
    mark_fail "HTML parser 에러"
fi

# 2. 죽은 외부 URL — href= 안에 trycloudflare가 있으면 (script API_BASE 제외)
DEAD_HREFS=$(grep -cE 'href="https://[a-z0-9-]+\.trycloudflare\.com' "$FILE" || true)
if [ "$DEAD_HREFS" -eq 0 ]; then
    mark_pass "href 죽은 trycloudflare URL 0건"
else
    mark_fail "href 죽은 trycloudflare URL $DEAD_HREFS건"
fi

# 3. OG image
OG_URL=$(grep -oE 'property="og:image" content="[^"]+"' "$FILE" | sed -E 's/.*content="([^"]+)".*/\1/')
if [ -n "$OG_URL" ]; then
    OG_CODE=$(curl -s -o /dev/null -w "%{http_code}" "$OG_URL" --max-time 8 || echo "000")
    if [ "$OG_CODE" = "200" ]; then
        mark_pass "OG image 라이브 200: $OG_URL"
    else
        mark_warn "OG image 응답 $OG_CODE: $OG_URL (소셜 공유 시 깨질 수 있음)"
    fi
else
    mark_fail "OG image 메타 누락"
fi

# 4. 마스터플랜 sweep 후 dead 카테고리 부활 차단
for KW in "billing-toggle" "save-pill" "hero-stats" "hero-stat-num" "mobile-sticky-cta" "data-yr=" "setBilling"; do
    if grep -q "$KW" "$FILE"; then
        # 코멘트 안이면 OK (REMOVED 또는 REVERT 코멘트), HTML/CSS 실제 사용이면 FAIL
        NON_COMMENT=$(grep -v "^[[:space:]]*//" "$FILE" | grep -v "^[[:space:]]*/\*" | grep -c "$KW" || true)
        REMOVED_COMMENT=$(grep -E "REMOVED|REVERT" "$FILE" | grep -c "$KW" || true)
        if [ "$NON_COMMENT" -gt "$REMOVED_COMMENT" ]; then
            mark_fail "마스터플랜 sweep 후 dead 카테고리 부활: $KW"
        fi
    fi
done
mark_pass "dead 카테고리 부활 검증 완료"

# 5. Schema.org JSON 파싱
SCHEMA=$(awk '/<script type="application\/ld\+json">/,/<\/script>/' "$FILE" | sed 's/<[^>]*>//g')
if echo "$SCHEMA" | python3 -c "import json,sys; json.loads(sys.stdin.read())" 2>/dev/null; then
    mark_pass "Schema.org JSON-LD 파싱 OK"
else
    mark_fail "Schema.org JSON-LD 파싱 실패"
fi

# 6. 파일 사이즈 — D+24: visible 100% sweep 반영해 320-365KB
SIZE=$(wc -c < "$FILE")
if [ "$SIZE" -lt 180000 ]; then
    mark_warn "파일 사이즈 $SIZE byte — 너무 작음 (예상 320-365KB), 의도치 않게 컴포넌트 손실 가능"
elif [ "$SIZE" -gt 365000 ]; then
    mark_warn "파일 사이즈 $SIZE byte — 너무 큼 (예상 320-365KB), 디자인 빼는 방향 룰 위배 가능"
else
    mark_pass "파일 사이즈 $SIZE byte (정상 범위)"
fi

# 7. 외부 의존 부활 차단
if grep -q "formsubmit.co/ajax" "$FILE"; then
    mark_fail "formsubmit.co 외부 의존 부활 (자체 /leads 사용해야 함)"
else
    mark_pass "formsubmit.co 외부 의존 0건"
fi

# 8. Hero CTA primary = X-Ray (라인 grep + 다음 2줄 컨텍스트)
HERO_CTA_NEXT=$(grep -A2 'class="hero-ctas"' "$FILE" | head -10)
if echo "$HERO_CTA_NEXT" | grep -q 'btn-hero-primary' && echo "$HERO_CTA_NEXT" | grep -q "X-Ray"; then
    mark_pass "Hero primary CTA = X-Ray (마스터플랜 KPI 정합)"
else
    # 더 단순한 fallback: hero-ctas div 직후 첫 a/button이 X-Ray로 시작하는지
    if grep -B1 -A4 'class="hero-ctas"' "$FILE" | head -8 | grep -q "X-Ray a competitor"; then
        mark_pass "Hero primary CTA = X-Ray (마스터플랜 KPI 정합)"
    else
        mark_warn "Hero primary CTA가 X-Ray가 아닐 가능성 — 마스터플랜 STEP 1 KPI 위반 가능"
    fi
fi

# 9. 가짜 promo / scarcity 차단
for FAKE in "limited time" "X seats left" "Only [0-9]+ remaining" "Ends tonight"; do
    if grep -iE "$FAKE" "$FILE" >/dev/null; then
        mark_fail "가짜 promo/scarcity 감지: '$FAKE' (마스터플랜 명시 금지)"
    fi
done
mark_pass "가짜 promo 검증 완료"

# 10. footer 정책 페이지 링크 → 실 파일 존재 (라운드 23 발견)
#    Paddle MoR + GDPR 컴플라이언스 + 가입 절차 필수.
LANDING_DIR="$(dirname "$FILE")"
POLICY_MISSING=0
for policy_link in privacy.html terms.html refund.html; do
    if grep -qE "(\\./)?landing/$policy_link|/$policy_link" "$FILE"; then
        if [ ! -f "$LANDING_DIR/$policy_link" ]; then
            mark_fail "footer 링크된 정책 페이지 누락: $policy_link (Paddle/GDPR 결제 절차 차단)"
            POLICY_MISSING=1
        fi
    fi
done
[ "$POLICY_MISSING" -eq 0 ] && mark_pass "정책 페이지 존재 검증 완료"

# 11. D+20 L1.A — CSP meta 존재 (paddle iframe + onrender connect 허용)
if grep -q 'Content-Security-Policy' "$FILE"; then
    if grep -q 'buy.paddle.com' "$FILE" && grep -q 'onrender.com' "$FILE"; then
        mark_pass "CSP meta + paddle/onrender 화이트리스트"
    else
        mark_warn "CSP meta 있으나 paddle/onrender 화이트리스트 누락 가능 — 결제/X-Ray 실패 위험"
    fi
else
    mark_fail "CSP meta 누락 — XSS 방어 + Paddle iframe 정책 미적용"
fi

# 12. D+20 L1.B — Referrer-Policy meta 존재
if grep -qE 'name="referrer"' "$FILE"; then
    mark_pass "Referrer-Policy meta 존재"
else
    mark_warn "Referrer-Policy meta 누락 — referrer 누출"
fi

# 13. D+20 L1.D — Permissions-Policy meta 존재
if grep -q 'Permissions-Policy' "$FILE"; then
    mark_pass "Permissions-Policy meta 존재"
else
    mark_warn "Permissions-Policy meta 누락 — geolocation/camera/microphone 명시 차단 미적용"
fi

# 14. D+20 L1.C — xr-url input a11y (aria-label 또는 label[for])
if grep -A8 'id="xr-url"' "$FILE" | grep -qE 'aria-label='; then
    mark_pass "xr-url input aria-label 존재 (WCAG 2.1)"
else
    mark_fail "xr-url input aria-label 누락 — 스크린리더 사용자 분기 실종"
fi

# 15. D+20 L3 — URL validation client-side
if grep -q 'XR_URL_RE' "$FILE"; then
    mark_pass "X-Ray URL validation client-side"
else
    mark_warn "X-Ray URL validation client-side 누락 — invalid input 시 fake progress 노출"
fi

# 16. D+20 L2.A — /leads catch 분기 silent fail-open 차단
#    showSuccess 만 호출하고 console.error/gtag exception 없으면 silent loss
LEADS_CATCH=$(awk '/fetch\(API_BASE \+ .\/leads/,/}\);/' "$FILE" | tr -d '\n')
if echo "$LEADS_CATCH" | grep -qE "console\.error.*leads"; then
    mark_pass "/leads catch 분기 가시화 (console.error + gtag exception)"
else
    mark_warn "/leads catch 분기 silent fail — 네트워크 실패 시 sales 손실 invisible"
fi

# 17. D+20 i18n — STORESCOPE_I18N dict (EN/KO/JA) + data-i18n 속성 존재
I18N_DATA=$(grep -cE 'data-i18n=' "$FILE" || true)
HAS_DICT_EN=$(grep -c "^\s*en:\s*{" "$FILE" || true)
HAS_DICT_KO=$(grep -c "^\s*ko:\s*{" "$FILE" || true)
HAS_DICT_JA=$(grep -c "^\s*ja:\s*{" "$FILE" || true)
if [ "$HAS_DICT_EN" -gt 0 ] && [ "$HAS_DICT_KO" -gt 0 ] && [ "$HAS_DICT_JA" -gt 0 ] && [ "$I18N_DATA" -gt 0 ]; then
    mark_pass "i18n dict EN/KO/JA + data-i18n 속성 $I18N_DATA건"
else
    mark_fail "i18n 불완전 — EN dict=$HAS_DICT_EN KO=$HAS_DICT_KO JA=$HAS_DICT_JA data-i18n=$I18N_DATA"
fi

# 18. D+20 i18n — lang switcher 존재
if grep -q 'id="ss-lang-switch"' "$FILE"; then
    mark_pass "lang switcher (EN/KO/JA dropdown) 존재"
else
    mark_warn "lang switcher 누락 — i18n 사용자 분기 X"
fi

# 19. D+22 R2 — SEO sitemap.xml + robots.txt 존재
LANDING_DIR2="$(dirname "$FILE")"
if [ -f "$LANDING_DIR2/sitemap.xml" ] && [ -f "$LANDING_DIR2/robots.txt" ]; then
    mark_pass "SEO sitemap.xml + robots.txt 존재"
else
    mark_warn "SEO 자산 누락 — sitemap=$([ -f "$LANDING_DIR2/sitemap.xml" ] && echo Y || echo N) robots=$([ -f "$LANDING_DIR2/robots.txt" ] && echo Y || echo N)"
fi

# 20. D+22 R4 — X-Ray URL invalid error message DOM
if grep -q 'id="xr-url-error"' "$FILE"; then
    mark_pass "X-Ray URL invalid error message DOM (i18n)"
else
    mark_warn "X-Ray invalid 시 메시지 0 — UX confusion"
fi

# 21. D+22 R1 — Demo mode banner DOM
if grep -q 'id="xr-demo-banner"' "$FILE"; then
    mark_pass "X-Ray demo mode banner DOM (silent fail-open 차단)"
else
    mark_warn "demo banner 누락 — fail-open silent UX deception"
fi

# 22. D+22 R3 — scroll-to-top button
if grep -q 'id="ss-scroll-top"' "$FILE"; then
    mark_pass "scroll-to-top button (long page mobile UX)"
else
    mark_warn "scroll-to-top button 누락"
fi

# 23. D+23 — Mock API mode (window.fetch wrap + global demo banner)
if grep -q 'setupMockAPI' "$FILE" && grep -q 'STORESCOPE_DEMO_MODE' "$FILE"; then
    mark_pass "Mock API mode (window.fetch wrap + DEMO_MODE flag)"
else
    mark_warn "Mock API mode 누락 — trycloudflare 시 사용자 X-Ray flow 평가 불가"
fi

# 24. D+23 — FAQ 8 summary 전체 data-i18n
FAQ_I18N=$(awk '/<summary/,/<\/summary>/' "$FILE" | grep -c 'data-i18n=' || true)
if [ "$FAQ_I18N" -ge 8 ]; then
    mark_pass "FAQ summary data-i18n $FAQ_I18N건 (전체 번역)"
else
    mark_warn "FAQ summary data-i18n $FAQ_I18N/8 — 일부 영역 영어 잔재"
fi

# 25. D+23 — Plan card 전체 data-i18n (plan_* prefix 키 매칭)
PLAN_I18N=$(grep -cE 'data-i18n="plan_' "$FILE" || true)
if [ "$PLAN_I18N" -ge 30 ]; then
    mark_pass "Plan card data-i18n $PLAN_I18N건 (3 plan 전체)"
else
    mark_warn "Plan card data-i18n $PLAN_I18N건 — 일부 영역 영어 잔재"
fi

# ─────────────────────────────────────────────────────────────────────────────
# D+23 R3 영구 다국어 검증 룰 (전문가 페르소나 패턴 영구 주입):
# - Patrick McKenzie: "pricing trust statements 다국어 필수 (conversion 직격)"
# - Kenya Hara: "Before/After visible labels 다국어 (Japanese minimal 일관성)"
# - Brad Frost: "section별 i18n coverage 검증 (design system 다국어 정합)"
# - accessibility-tester: "ARIA label 다국어 (스크린리더 분기 정합)"
# - frontend-developer: "EN ↔ KO ↔ JA dict key 동기화 (silent translation gap 차단)"
# ─────────────────────────────────────────────────────────────────────────────

# 26. Before/After section data-i18n (Kenya Hara visible labels)
BA_I18N=$(grep -cE 'data-i18n="ba_' "$FILE" || true)
if [ "$BA_I18N" -ge 20 ]; then
    mark_pass "Before/After section data-i18n $BA_I18N건 (Kenya Hara minimal labels)"
else
    mark_fail "Before/After section data-i18n $BA_I18N건 — visible Mid-page 영어 잔재"
fi

# 27. Pricing trust badges data-i18n (Patrick McKenzie conversion 직격, -o로 모든 매칭 카운트)
PRICING_TRUST=$(grep -oE 'data-i18n="pricing_trust_[a-z_]+"' "$FILE" | wc -l | tr -d ' ')
if [ "$PRICING_TRUST" -ge 6 ]; then
    mark_pass "Pricing trust badges data-i18n $PRICING_TRUST건 (Patrick McKenzie)"
else
    mark_warn "Pricing trust badges data-i18n $PRICING_TRUST건 — 결제 결정 직전 영어 잔재"
fi

# 28. EN ↔ KO ↔ JA dict key count 동기화 (frontend-developer silent gap 차단)
EN_KEYS=$(awk '/en:\s*\{/,/^\s*},\s*$/' "$FILE" | grep -cE '^\s+[a-z_]+:\s' || true)
KO_KEYS=$(awk '/ko:\s*\{/,/^\s*},\s*$/' "$FILE" | grep -cE '^\s+[a-z_]+:\s' || true)
JA_KEYS=$(awk '/ja:\s*\{/,/^\s*}\s*$/' "$FILE" | grep -cE '^\s+[a-z_]+:\s' || true)
# 5% 허용오차
EN_LOW=$((EN_KEYS * 95 / 100))
EN_HIGH=$((EN_KEYS * 105 / 100))
if [ "$KO_KEYS" -ge "$EN_LOW" ] && [ "$KO_KEYS" -le "$EN_HIGH" ] && \
   [ "$JA_KEYS" -ge "$EN_LOW" ] && [ "$JA_KEYS" -le "$EN_HIGH" ]; then
    mark_pass "i18n dict key 동기화 EN=$EN_KEYS KO=$KO_KEYS JA=$JA_KEYS (±5%)"
else
    mark_warn "i18n dict key 미동기화 EN=$EN_KEYS KO=$KO_KEYS JA=$JA_KEYS (silent translation gap 위험)"
fi

# 29. ARIA i18n coverage (accessibility-tester 스크린리더 다국어)
ARIA_I18N=$(grep -cE 'data-aria-i18n=' "$FILE" || true)
if [ "$ARIA_I18N" -ge 5 ]; then
    mark_pass "ARIA i18n data-aria-i18n $ARIA_I18N건 (다국어 스크린리더)"
else
    mark_warn "ARIA i18n $ARIA_I18N건 — KO/JA 모드 영어 aria-label 잔재"
fi

# 30. 전체 i18n minimum coverage (Brad Frost section 정합)
TOTAL_I18N=$(grep -cE 'data-i18n=' "$FILE" || true)
if [ "$TOTAL_I18N" -ge 130 ]; then
    mark_pass "전체 data-i18n $TOTAL_I18N건 (Brad Frost coverage threshold)"
else
    mark_warn "전체 data-i18n $TOTAL_I18N건 — 130 미만, visible 영역 잔재 위험"
fi

# 31. D+24 R4 — visible English 잔재 sweep (영구 룰, sweep_residue.py 호출)
# h2/h3/p/span/div + SVG 자식 다음 텍스트까지 검출. KO/JA 모드 disjointed UX 차단.
# 제외: 제품명/도메인/브랜드 약어 (sweep_residue.py EXCLUDE 리스트).
SWEEP_SCRIPT="$(dirname "$0")/sweep_residue.py"
if [ -f "$SWEEP_SCRIPT" ]; then
    RESIDUE_COUNT=$(python3 "$SWEEP_SCRIPT" "$FILE" 2>/dev/null || echo 999)
    if [ "$RESIDUE_COUNT" -eq 0 ]; then
        mark_pass "visible English 잔재 0건 (D+24 sweep 영구 룰)"
    elif [ "$RESIDUE_COUNT" -le 3 ]; then
        mark_warn "visible English 잔재 $RESIDUE_COUNT건 — sweep_residue.py 실행으로 확인"
    else
        mark_fail "visible English 잔재 $RESIDUE_COUNT건 — KO/JA 모드 disjointed UX 차단 필요"
    fi
else
    mark_warn "sweep_residue.py 누락 — 영구 잔재 검증 skip"
fi

# 32. Live Demo + Email Preview section pill/badge data-i18n (dark section visible labels)
DEMO_EMAIL_I18N=$(grep -oE 'data-i18n="(demo_pill|email_preview_pill|email_pro_badge|email_chart_label|email_subject_label|email_preview_sub|demo_h2)"' "$FILE" | wc -l | tr -d ' ')
if [ "$DEMO_EMAIL_I18N" -ge 7 ]; then
    mark_pass "Live Demo + Email Preview data-i18n $DEMO_EMAIL_I18N건 (dark section labels)"
else
    mark_warn "Live Demo + Email Preview data-i18n $DEMO_EMAIL_I18N건 — pill/badge 영어 잔재"
fi

# 33. Competitor Pricing bento card data-i18n (visible body 번역 정합)
PRICE_CARD_I18N=$(grep -oE 'data-i18n="bento_extra_price[a-z0-9_]*"' "$FILE" | wc -l | tr -d ' ')
if [ "$PRICE_CARD_I18N" -ge 3 ]; then
    mark_pass "Competitor Pricing bento card data-i18n $PRICE_CARD_I18N건"
else
    mark_warn "Competitor Pricing bento card data-i18n $PRICE_CARD_I18N건 — body 영어 잔재"
fi

echo "----------"
echo "RESULT: PASS=$PASS FAIL=$FAIL WARN=$WARN"
[ "$FAIL" -eq 0 ] || exit 1
