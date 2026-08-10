#!/bin/bash
# StoreScope launch 모니터링 — Netlify Forms + Reddit metric
# ============================================================
# Launch (new D0 = 2026-07-30) 후 signup + Reddit metric 폴링.
#
# 사용법:
#   bash deploy/monitor_launch.sh                # 1회 스냅샷
#   bash deploy/monitor_launch.sh --watch        # 30분 간격 지속 (nohup 권장)
#   bash deploy/monitor_launch.sh --reset        # counter 초기화

set -euo pipefail

FORM_ID="6a69f8418830ee0008b52453"
STATE_FILE="/tmp/storescope_launch_state.txt"
# Non-organic filter: exclude test emails + known user personal accounts (D+8 correction)
NON_ORGANIC_REGEX='@example\.com|doyeon2328@'
# HN Algolia search — auth-free, tracks Show HN metric
HN_ALGOLIA_URL='https://hn.algolia.com/api/v1/search?query=StoreScope&tags=show_hn'

_snapshot() {
    local ts=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
    local raw=$(netlify api listFormSubmissions --data "{\"form_id\":\"$FORM_ID\"}" 2>/dev/null)
    local count=$(echo "$raw" | grep -oE '"number": *[0-9]+' | wc -l | tr -d ' ')
    local organic=$(echo "$raw" | grep -oE '"email": *"[^"]+"' | cut -d'"' -f4 | grep -viE "$NON_ORGANIC_REGEX" | wc -l | tr -d ' ')
    local ih_refs=$(echo "$raw" | grep -oE '"referrer": *"[^"]*ref=ih[^"]*"' | wc -l | tr -d ' ')
    local last_email=$(echo "$raw" | grep -oE '"email": *"[^"]+"' | head -1 | cut -d'"' -f4)
    local last_ip=$(echo "$raw" | grep -oE '"ip": *"[^"]+"' | head -1 | cut -d'"' -f4)

    # D-Day 자동 계산 (new D0 = 2026-07-30)
    local d0=$(date -j -f "%Y-%m-%d" "2026-07-30" +%s 2>/dev/null || date -d "2026-07-30" +%s)
    local now_s=$(date +%s)
    local dplus=$(( (now_s - d0) / 86400 ))
    echo "═══════════════════════════════════════════════════════"
    echo "  📊 StoreScope Launch Monitor — $ts (D+$dplus from new D0 2026-07-30)"
    echo "═══════════════════════════════════════════════════════"
    echo "  Total submissions:  $count"
    echo "  Organic submissions: $organic  (excludes @example.com + user test)"
    echo "  IH-attributed (?ref=ih): $ih_refs"
    echo "  Latest email: ${last_email:-none}"
    echo "  Latest IP: ${last_ip:-none}"

    # HN Algolia — polls Show HN posts mentioning StoreScope (no auth needed)
    # Algolia fuzzy-matches (StarScope, StoryScape 등) — Python 에서 exact 'storescope' 문자열 필터
    local hn_json=$(curl -sf --max-time 5 "$HN_ALGOLIA_URL" 2>/dev/null || echo '{"hits":[]}')
    local hn_hits=$(echo "$hn_json" | python3 -c "
import sys, json
try:
    d = json.load(sys.stdin); hits = d.get('hits', [])
    exact = [h for h in hits if 'storescope' in (h.get('title','') + ' ' + h.get('url','')).lower()]
    if not exact: print('  HN Show HN (storescope): not submitted yet')
    else:
        for h in exact[:3]:
            print(f\"  HN | pts={h.get('points',0):>3} comments={h.get('num_comments',0):>3} | {h.get('title','')[:55]}\")
except Exception: print('  HN Algolia poll failed')" 2>/dev/null)
    echo "$hn_hits"

    # State delta
    if [ -f "$STATE_FILE" ]; then
        local prev=$(cat "$STATE_FILE")
        local delta=$((count - prev))
        if [ "$delta" -gt 0 ]; then
            echo "  🔔 NEW: +$delta submissions since last check"
        elif [ "$delta" -lt 0 ]; then
            echo "  ⚠ Count decreased (spam deletion?)"
        else
            echo "  = no new submissions"
        fi
    fi
    echo "$count" > "$STATE_FILE"

    # Kill switch alerts — organic-only + D+day 자동 반영
    echo ""
    echo "  ── Kill switch schedule (new D0 = 2026-07-30) ──"
    if [ "$dplus" -ge 30 ]; then
        if [ "$organic" -lt 15 ]; then
            echo "  🚨 D+$dplus / D+30 KILL SWITCH FIRED — organic $organic < 15 + paid 0. 이행 실험 2."
        else
            echo "  🎉 D+$dplus / D+30 PASSED — organic $organic ≥ 15. Continue."
        fi
    elif [ "$dplus" -ge 14 ]; then
        if [ "$organic" -lt 10 ]; then
            echo "  🚨 D+$dplus / D+14 organic < 10 — 대안 검토 (kill 후보). $((30 - dplus))일 남음 → D+30."
        else
            echo "  ✅ D+$dplus / D+14 PASSED — organic $organic ≥ 10. On track."
        fi
    elif [ "$dplus" -ge 10 ]; then
        if [ "$organic" -lt 3 ]; then
            echo "  🚨 D+$dplus / D+10 organic < 3 — D+14 kill 후보 궤도. $((14 - dplus))일 남음."
        else
            echo "  ✅ D+$dplus / D+10 PASSED — organic $organic ≥ 3."
        fi
    elif [ "$dplus" -ge 7 ]; then
        if [ "$organic" -lt 5 ]; then
            echo "  🚨 D+$dplus / D+7 organic < 5 — hook 재작성 or channel 변경 검토."
        else
            echo "  ✅ D+$dplus / D+7 PASSED — organic $organic ≥ 5."
        fi
    else
        echo "  ⏳ D+$dplus — kill switch 아직 전. Reddit/IH 발행 결과 대기."
    fi
    # 다음 checkpoint 정보
    if [ "$dplus" -lt 14 ]; then
        echo "  → 다음 checkpoint: D+14 (2026-08-13) — organic ≥ 10 필요, $((14 - dplus))일 남음"
    elif [ "$dplus" -lt 30 ]; then
        echo "  → 다음 checkpoint: D+30 (2026-08-29) — organic ≥ 15 필요, $((30 - dplus))일 남음"
    fi
    echo "═══════════════════════════════════════════════════════"
}

if [ "${1:-}" = "--reset" ]; then
    rm -f "$STATE_FILE"
    echo "Counter reset."
    exit 0
fi

if [ "${1:-}" = "--watch" ]; then
    echo "Watch mode — 30분 간격 폴링. Ctrl+C 로 중단."
    while true; do
        _snapshot
        echo "Next check in 30 minutes..."
        sleep 1800
    done
else
    _snapshot
fi
