#!/usr/bin/env bash
# landing-deploy.sh — Deploy landing/index.html to gh-pages + sync main
#
# Usage:
#   ./deploy/landing-deploy.sh                # uses landing/index.html as source
#   ./deploy/landing-deploy.sh path/to/x.html # uses custom source
#   ./deploy/landing-deploy.sh --regen-png    # also re-render og-image.png from svg
#
# What it does:
#   1. Renders og-image.svg → og-image.png (if --regen-png or PNG missing)
#   2. Copies source HTML + og-image.png to gh-pages branch root + landing/
#   3. Commits and pushes gh-pages
#   4. Mirrors same changes to main branch landing/
#   5. Polls live URL until updated, then prints HTTP status + meta sanity

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

SRC="${1:-landing/index.html}"
REGEN_PNG=0
[[ "${1:-}" == "--regen-png" ]] && { REGEN_PNG=1; SRC="landing/index.html"; }
[[ "${2:-}" == "--regen-png" ]] && REGEN_PNG=1

[[ -f "$SRC" ]] || { echo "[ERR] source not found: $SRC"; exit 1; }

LIVE_URL="https://ddookim.github.io/storescope/"
WORKTREE="/tmp/ss-ghpages-$$"

# ── 1. Render PNG og-image if requested or missing ───────────────────────────
if [[ $REGEN_PNG -eq 1 ]] || [[ ! -f landing/og-image.png ]]; then
  command -v rsvg-convert >/dev/null || { echo "[ERR] rsvg-convert not installed: brew install librsvg"; exit 1; }
  echo "[1/5] rendering og-image.png (1200x630)..."
  rsvg-convert -w 1200 -h 630 landing/og-image.svg -o landing/og-image.png
fi

# ── 2. gh-pages worktree + file copy ─────────────────────────────────────────
echo "[2/5] preparing gh-pages worktree..."
git fetch origin gh-pages --quiet
git worktree add "$WORKTREE" gh-pages >/dev/null
trap 'git worktree remove --force "$WORKTREE" 2>/dev/null || true' EXIT

cp "$SRC"               "$WORKTREE/index.html"
cp landing/og-image.png "$WORKTREE/landing/og-image.png"
cp landing/og-image.svg "$WORKTREE/landing/og-image.svg"
# FIX 2026-06-08 (라운드 23): 정책 페이지 누락 — footer 링크 ./landing/privacy.html 클릭 시 404.
# Paddle MoR 가입 절차 + GDPR/EU 컴플라이언스 + 신뢰 요건 필수.
for policy in privacy.html terms.html refund.html 404.html; do
  if [[ -f "landing/$policy" ]]; then
    cp "landing/$policy" "$WORKTREE/landing/$policy"
  fi
done
# D+22 R2: SEO sitemap.xml + robots.txt — 검색엔진 표준 root 위치.
# sitemap.xml 은 subdir (storescope/sitemap.xml) — Google Search Console 등록 시 명시.
for seo in sitemap.xml robots.txt; do
  if [[ -f "landing/$seo" ]]; then
    cp "landing/$seo" "$WORKTREE/$seo"
  fi
done

# ── 3. gh-pages commit + push ────────────────────────────────────────────────
cd "$WORKTREE"
if git diff --quiet && git diff --cached --quiet; then
  echo "[3/5] no changes for gh-pages — skipping"
else
  COMMIT_MSG="${COMMIT_MSG:-chore: landing deploy $(date +%Y-%m-%d)}"
  git add index.html landing/og-image.png landing/og-image.svg
  # 정책 페이지 + 404 git add (존재하는 것만)
  for policy in privacy.html terms.html refund.html 404.html; do
    [[ -f "landing/$policy" ]] && git add "landing/$policy"
  done
  # D+22: SEO 자산 (sitemap.xml + robots.txt) git add
  for seo in sitemap.xml robots.txt; do
    [[ -f "$seo" ]] && git add "$seo"
  done
  git commit -m "$COMMIT_MSG"
  echo "[3/5] pushing gh-pages..."
  git push origin gh-pages
fi
cd "$REPO_ROOT"

# ── 4. Mirror to main landing/ ───────────────────────────────────────────────
echo "[4/5] syncing main branch landing/..."
git fetch origin main --quiet
CURRENT_BRANCH="$(git rev-parse --abbrev-ref HEAD)"
if [[ "$CURRENT_BRANCH" != "main" ]]; then
  echo "[WARN] not on main (on $CURRENT_BRANCH) — skipping main sync. Run separately."
else
  cp "$SRC" landing/index.html
  if git diff --quiet landing/index.html landing/og-image.png landing/og-image.svg 2>/dev/null; then
    echo "       main already in sync"
  else
    git add landing/index.html landing/og-image.png landing/og-image.svg
    git commit -m "${COMMIT_MSG:-chore: landing deploy $(date +%Y-%m-%d)} (main mirror)"
    # Rebase in case remote main advanced
    git pull --rebase origin main || {
      echo "[WARN] rebase failed — resolve conflicts manually, then: git push origin main"
      exit 1
    }
    git push origin main
  fi
fi

# ── 5. Poll live URL + sanity ────────────────────────────────────────────────
echo "[5/5] waiting for GH Pages rebuild..."
TIMEOUT=180
DEADLINE=$(($(date +%s) + TIMEOUT))
while (( $(date +%s) < DEADLINE )); do
  if curl -sf -o /dev/null "$LIVE_URL"; then break; fi
  sleep 6
done

echo ""
echo "─── LIVE ─────────────────────────────────────────────"
curl -sI "$LIVE_URL" | head -1
echo "og:image → $(curl -s "$LIVE_URL" | grep -oE 'og-image\.(png|svg)' | head -1)"
echo "JSON-LD FAQPage: $(curl -s "$LIVE_URL" | grep -c '"@type": "FAQPage"')"
echo "PNG og-image: $(curl -sI "${LIVE_URL}landing/og-image.png" | head -1)"
echo "─────────────────────────────────────────────────────"
echo "Done. View: $LIVE_URL"
