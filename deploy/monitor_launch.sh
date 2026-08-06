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

_snapshot() {
    local ts=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
    local raw=$(netlify api listFormSubmissions --data "{\"form_id\":\"$FORM_ID\"}" 2>/dev/null)
    local count=$(echo "$raw" | grep -oE '"number": *[0-9]+' | wc -l | tr -d ' ')
    local organic=$(echo "$raw" | grep -oE '"email": *"[^"]+"' | cut -d'"' -f4 | grep -viE "$NON_ORGANIC_REGEX" | wc -l | tr -d ' ')
    local ih_refs=$(echo "$raw" | grep -oE '"referrer": *"[^"]*ref=ih[^"]*"' | wc -l | tr -d ' ')
    local last_email=$(echo "$raw" | grep -oE '"email": *"[^"]+"' | head -1 | cut -d'"' -f4)
    local last_ip=$(echo "$raw" | grep -oE '"ip": *"[^"]+"' | head -1 | cut -d'"' -f4)

    echo "═══════════════════════════════════════════════════════"
    echo "  📊 StoreScope Launch Monitor — $ts (D+8, IH channel reset)"
    echo "═══════════════════════════════════════════════════════"
    echo "  Total submissions:  $count"
    echo "  Organic submissions: $organic  (excludes @example.com + user test)"
    echo "  IH-attributed (?ref=ih): $ih_refs"
    echo "  Latest email: ${last_email:-none}"
    echo "  Latest IP: ${last_ip:-none}"

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

    # Kill switch alerts — organic-only (D+8 correction: total count includes user test)
    if [ "$organic" -eq 0 ]; then
        echo ""
        echo "  ⏳ Organic signup = 0 — IH channel test in flight"
        echo "  → D+10 (2026-08-09) checkpoint: organic ≥ 3 to continue"
    elif [ "$organic" -ge 10 ]; then
        echo ""
        echo "  🎉 KILL SWITCH: 10+ organic signups — Neon+Render wire-up trigger"
        echo "  → 다음 액션: bash deploy/launch_phase1.sh \"\$NEON_URL\""
    elif [ "$organic" -ge 5 ]; then
        echo ""
        echo "  📈 5+ organic — new D+14 kill switch PASSED (green)"
    elif [ "$organic" -ge 3 ]; then
        echo ""
        echo "  📊 3+ organic — D+10 IH channel PASSED, HN Show HN at D+11"
    else
        echo ""
        echo "  ⚠ Organic $organic < 3 — D+10 checkpoint red zone"
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
