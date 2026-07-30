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

_snapshot() {
    local ts=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
    local count=$(netlify api listFormSubmissions --data "{\"form_id\":\"$FORM_ID\"}" 2>/dev/null | grep -oE '"number": *[0-9]+' | wc -l | tr -d ' ')
    local last_email=$(netlify api listFormSubmissions --data "{\"form_id\":\"$FORM_ID\"}" 2>/dev/null | grep -oE '"email": *"[^"]+"' | head -1 | cut -d'"' -f4)
    local last_ip=$(netlify api listFormSubmissions --data "{\"form_id\":\"$FORM_ID\"}" 2>/dev/null | grep -oE '"ip": *"[^"]+"' | head -1 | cut -d'"' -f4)

    echo "═══════════════════════════════════════════════════════"
    echo "  📊 StoreScope Launch Monitor — $ts"
    echo "═══════════════════════════════════════════════════════"
    echo "  Netlify Forms submissions: $count"
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

    # Kill switch alerts
    if [ "$count" -eq 0 ]; then
        echo ""
        echo "  ⏳ Awaiting first signup..."
    elif [ "$count" -ge 10 ]; then
        echo ""
        echo "  🎉 KILL SWITCH: 10+ signups reached — weekly refresh activation trigger"
        echo "  → 다음 액션: Neon+Render infra 진행 검토"
    elif [ "$count" -ge 5 ]; then
        echo ""
        echo "  📈 5+ signups — new D+7 kill switch PASSED (target)"
    elif [ "$count" -ge 2 ]; then
        echo ""
        echo "  📊 2+ signups — new D+3 kill switch PASSED"
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
