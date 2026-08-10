"""
Weekly Digest 발송 — JSON-based (Neon 없이 동작).

DB-only send_weekly_digests.py 의 대안. Neon 미배포 상태에서도
Netlify Forms + data/trending.json 만으로 월요 digest 이행 가능.

DB source ─────────── 대체 ──────────────
api_keys 테이블 ────→ Netlify Forms API
data DB rows ──────→ data/trending.json
email_deliveries ──→ data/sent_log.json (해시 dedup)

실행:
    python services/send_json_digest.py                # 발송
    python services/send_json_digest.py --dry-run      # 콘솔만
    python services/send_json_digest.py --limit 3      # 상위 N개 subscriber만

Env (필수):
    NETLIFY_AUTH_TOKEN
    NETLIFY_FORM_ID       (default: hero-email-form ID)
Env (선택 — 없으면 graceful skip):
    SMTP_HOST / SMTP_PORT / SMTP_USER / SMTP_PASS / SMTP_FROM
    ADMIN_SECRET          (HMAC unsub token)
    BASE_URL              (default: ddookim.github.io/storescope)

호출 위치:
    .github/workflows/weekly_pipeline.yml — DB-less 모드 pipeline 성공 후 step.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import logging
import os
import smtplib
import sys
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from urllib import request as _urlreq
from urllib.error import HTTPError, URLError

_HERE = Path(__file__).resolve().parent.parent

_log = logging.getLogger("send_json_digest")
logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

# ── config ──────────────────────────────────────────────────
# GitHub secrets can be set to empty string ("") — os.environ.get() returns "" not default.
# Use `or fallback` pattern to coerce empty strings to defaults before int() conversion.
def _env(name: str, default: str = "") -> str:
    return os.environ.get(name) or default

NETLIFY_TOKEN     = _env("NETLIFY_AUTH_TOKEN")
NETLIFY_FORM_ID   = _env("NETLIFY_FORM_ID", "6a69f8418830ee0008b52453")
SMTP_HOST         = _env("SMTP_HOST")
SMTP_PORT         = int(_env("SMTP_PORT", "587"))
SMTP_USER         = _env("SMTP_USER")
SMTP_PASS         = _env("SMTP_PASS")
FROM_EMAIL        = _env("SMTP_FROM", "noreply@storescope.com")
BASE_URL          = _env("BASE_URL", "https://ddookim.github.io/storescope").rstrip("/")
_UNSUB_SECRET     = _env("ADMIN_SECRET") or _env("PADDLE_WEBHOOK_SECRET")
SMTP_TIMEOUT      = 10

TRENDING_PATH        = _HERE / "data" / "trending.json"
LATEST_PRODUCTS_PATH = _HERE / "data" / "latest_products.json"   # D+11: pipeline step 4 output
SENT_LOG_PATH        = _HERE / "data" / "sent_log.json"

# Non-organic + quality filters (D+8 audit — H1 signal quality remediation)
NON_ORGANIC_EMAILS = ("@example.com", "doyeon2328@")
WHOLESALE_TITLE_PATTERNS = (
    "davines", "polo 998", "pvc white end cap", "safety sign",
    "jutebeutel", "novena", "sticky notes", "beanie", "stoppers",
)  # Known-junk from D+8 real-data spot-check
MIN_STORE_COUNT     = int(_env("DIGEST_MIN_STORES", "5"))     # ≥5 stores = real distribution
MIN_PRODUCT_COUNT   = int(_env("DIGEST_MIN_PRODUCTS", "20"))  # ≥20 dupes = real spread
MIN_PRICE           = float(_env("DIGEST_MIN_PRICE", "5"))    # <$5 = giveaway/junk
MAX_PRICE           = float(_env("DIGEST_MAX_PRICE", "200"))  # >$200 = luxury tail
ENGLISH_ONLY        = _env("DIGEST_ENGLISH_ONLY", "true").lower() == "true"
TOP_N_CLUSTERS      = int(_env("DIGEST_TOP_N", "10"))
MIN_CLUSTERS_TO_SEND = int(_env("DIGEST_MIN_CLUSTERS", "3"))   # <3 qualifying → skip week


def _iso_week() -> str:
    now = datetime.now(timezone.utc)
    y, w, _ = now.isocalendar()
    return f"{y}-W{w:02d}"


def _email_hash(email: str) -> str:
    return hashlib.sha256(email.strip().lower().encode()).hexdigest()[:16]


# ── subscribers ─────────────────────────────────────────────
def _fetch_subscribers() -> list[dict]:
    if not NETLIFY_TOKEN:
        _log.error("NETLIFY_AUTH_TOKEN 없음 — subscriber fetch 불가")
        return []
    url = f"https://api.netlify.com/api/v1/forms/{NETLIFY_FORM_ID}/submissions"
    req = _urlreq.Request(url, headers={"Authorization": f"Bearer {NETLIFY_TOKEN}"})
    try:
        with _urlreq.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())
    except (HTTPError, URLError) as e:
        _log.error("Netlify API 실패: %s", e)
        return []
    subs = []
    for s in data:
        email = (s.get("data") or {}).get("email", "").strip().lower()
        if not email or "@" not in email:
            continue
        if any(pat in email for pat in NON_ORGANIC_EMAILS):
            continue
        subs.append({"email": email, "id": s.get("id"), "created": s.get("created_at")})
    return subs


# ── dedup log ───────────────────────────────────────────────
def _load_sent_log() -> dict:
    if not SENT_LOG_PATH.exists():
        return {}
    try:
        return json.loads(SENT_LOG_PATH.read_text())
    except json.JSONDecodeError:
        _log.warning("sent_log.json 파손 — 초기화")
        return {}


def _save_sent_log(log: dict) -> None:
    SENT_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    SENT_LOG_PATH.write_text(json.dumps(log, indent=2, sort_keys=True))


# ── content ─────────────────────────────────────────────────
def _price_float(p) -> float | None:
    """price 는 str '2.99' 또는 float 또는 None. sanity: 반환 float or None."""
    if p is None:
        return None
    try:
        return float(p)
    except (TypeError, ValueError):
        return None


def _cluster_passes_filter(c: dict) -> tuple[bool, str]:
    """returns (pass, reason_if_reject)."""
    if c.get("store_count", 0) < MIN_STORE_COUNT:
        return False, f"stores<{MIN_STORE_COUNT}"
    if c.get("product_count", 0) < MIN_PRODUCT_COUNT:
        return False, f"products<{MIN_PRODUCT_COUNT}"
    price = _price_float(c.get("representative_price"))
    if price is None or price < MIN_PRICE or price > MAX_PRICE:
        return False, f"price_out_of_range({price})"
    title = (c.get("representative_title") or "").lower()
    if not title.strip():
        return False, "empty_title"
    if any(pat in title for pat in WHOLESALE_TITLE_PATTERNS):
        return False, "wholesale_pattern"
    if ENGLISH_ONLY and not all(ord(ch) < 128 for ch in title):
        return False, "non_ascii"
    return True, ""


def _load_top_clusters() -> tuple[list[dict], dict]:
    """Returns (top_clusters, filter_stats)."""
    if not TRENDING_PATH.exists():
        _log.error("trending.json 없음 — pipeline 미실행")
        return [], {}
    clusters = json.loads(TRENDING_PATH.read_text())
    stats = {"total": len(clusters), "passed": 0, "reject_reasons": {}}
    filtered = []
    for c in clusters:
        ok, reason = _cluster_passes_filter(c)
        if ok:
            filtered.append(c)
            stats["passed"] += 1
        else:
            stats["reject_reasons"][reason] = stats["reject_reasons"].get(reason, 0) + 1
    filtered.sort(key=lambda c: c.get("store_count", 0), reverse=True)
    return filtered[:TOP_N_CLUSTERS], stats


# ── HMAC unsubscribe (api/main.py 와 동일 로직) ───────────
def _unsub_token(email_hash: str) -> str:
    if not _UNSUB_SECRET:
        return ""
    mac = hmac.new(_UNSUB_SECRET.encode(), email_hash.encode(), hashlib.sha256).digest()[:8]
    return base64.urlsafe_b64encode(mac).decode().rstrip("=")


def _unsubscribe_link(email_hash: str) -> str:
    tok = _unsub_token(email_hash)
    return f"{BASE_URL}/landing/unsubscribe.html?h={email_hash}&t={tok}" if tok else f"{BASE_URL}/#contact"


# ── render ──────────────────────────────────────────────────
def _render_html(clusters: list[dict], week: str, unsub_link: str) -> str:
    rows = []
    for i, c in enumerate(clusters, 1):
        title = (c.get("representative_title") or "Untitled")[:80]
        stores = c.get("store_count", 0)
        products = c.get("product_count", 0)
        price = c.get("representative_price") or "—"
        rows.append(f"""
            <tr>
              <td style="padding:12px 8px;border-bottom:1px solid #E5E5E5;color:#57534E;font-weight:600;">{i}</td>
              <td style="padding:12px 8px;border-bottom:1px solid #E5E5E5;color:#1C1917;">{title}<br><span style="color:#A29E98;font-size:12px">{products} product variants</span></td>
              <td style="padding:12px 8px;border-bottom:1px solid #E5E5E5;color:#57534E;text-align:right;">{stores}</td>
              <td style="padding:12px 8px;border-bottom:1px solid #E5E5E5;color:#57534E;text-align:right;">${price}</td>
            </tr>""")
    table_html = "".join(rows) or "<tr><td colspan=4 style='padding:24px;text-align:center;color:#78716C'>No qualifying trends this week (all clusters below threshold).</td></tr>"
    return f"""<!doctype html>
<html><body style="font-family:-apple-system,system-ui,sans-serif;max-width:600px;margin:0 auto;padding:24px;color:#1C1917;background:#F9F8F6">
<h1 style="font-size:22px;letter-spacing:-0.02em;margin:0 0 4px">StoreScope Weekly · {week}</h1>
<p style="color:#78716C;margin:0 0 24px;font-size:14px">Top {len(clusters)} product clusters by cross-store presence</p>

<table style="width:100%;border-collapse:collapse;margin:16px 0;font-size:14px">
  <thead><tr style="background:#F3F1EE">
    <th style="text-align:left;padding:10px 8px;color:#57534E;font-weight:700">#</th>
    <th style="text-align:left;padding:10px 8px;color:#57534E;font-weight:700">Product</th>
    <th style="text-align:right;padding:10px 8px;color:#57534E;font-weight:700">Stores</th>
    <th style="text-align:right;padding:10px 8px;color:#57534E;font-weight:700">Price</th>
  </tr></thead>
  <tbody>{table_html}</tbody>
</table>

<p style="color:#78716C;font-size:12px;margin-top:32px;border-top:1px solid #E5E5E5;padding-top:16px">
  StoreScope crawls 50+ curated DTC brands weekly. Signal quality: clusters with ≥{MIN_STORE_COUNT} stores.
  <br><a href="{unsub_link}" style="color:#78716C">Unsubscribe</a> · <a href="{BASE_URL}" style="color:#4338ca">View on web</a>
</p>
</body></html>"""


def _render_text(clusters: list[dict], week: str, unsub_link: str) -> str:
    lines = [f"StoreScope Weekly · {week}", f"Top {len(clusters)} product clusters", ""]
    for i, c in enumerate(clusters, 1):
        title = (c.get("representative_title") or "Untitled")[:60]
        lines.append(f"{i:>2}. {title} — {c.get('store_count',0)} stores · ${c.get('representative_price') or '—'}")
    lines.extend(["", f"Unsubscribe: {unsub_link}", f"Web: {BASE_URL}"])
    return "\n".join(lines)


# D+11 fallback renderer — latest_products.json schema.
# Used when clusters are empty (DTC-only seed) but latest_products has content.
def _render_latest_html(products: list[dict], week: str, unsub_link: str) -> str:
    rows = []
    for i, p in enumerate(products, 1):
        title = (p.get("title") or "Untitled")[:80]
        vendor = p.get("vendor") or "—"
        ptype = p.get("product_type") or ""
        price = p.get("price")
        price_str = f"${price:.2f}" if isinstance(price, (int, float)) and price > 0 else "—"
        age = p.get("age_days", "?")
        rows.append(f"""
            <tr>
              <td style="padding:12px 8px;border-bottom:1px solid #E5E5E5;color:#57534E;font-weight:600;">{i}</td>
              <td style="padding:12px 8px;border-bottom:1px solid #E5E5E5;color:#1C1917;">{title}<br><span style="color:#A29E98;font-size:12px">{vendor} · {ptype}</span></td>
              <td style="padding:12px 8px;border-bottom:1px solid #E5E5E5;color:#57534E;text-align:right;">{age}d</td>
              <td style="padding:12px 8px;border-bottom:1px solid #E5E5E5;color:#57534E;text-align:right;">{price_str}</td>
            </tr>""")
    table_html = "".join(rows)
    return f"""<!doctype html>
<html><body style="font-family:-apple-system,system-ui,sans-serif;max-width:600px;margin:0 auto;padding:24px;color:#1C1917;background:#F9F8F6">
<h1 style="font-size:22px;letter-spacing:-0.02em;margin:0 0 4px">StoreScope Weekly · {week}</h1>
<p style="color:#78716C;margin:0 0 24px;font-size:14px">Newest {len(products)} products across 50+ curated DTC brands (last 30 days)</p>

<table style="width:100%;border-collapse:collapse;margin:16px 0;font-size:14px">
  <thead><tr style="background:#F3F1EE">
    <th style="text-align:left;padding:10px 8px;color:#57534E;font-weight:700">#</th>
    <th style="text-align:left;padding:10px 8px;color:#57534E;font-weight:700">Product</th>
    <th style="text-align:right;padding:10px 8px;color:#57534E;font-weight:700">Age</th>
    <th style="text-align:right;padding:10px 8px;color:#57534E;font-weight:700">Price</th>
  </tr></thead>
  <tbody>{table_html}</tbody>
</table>

<p style="color:#78716C;font-size:12px;margin-top:32px;border-top:1px solid #E5E5E5;padding-top:16px">
  Fresh product launches from curated DTC brands, sorted by newest first.
  <br><a href="{unsub_link}" style="color:#78716C">Unsubscribe</a> · <a href="{BASE_URL}" style="color:#4338ca">View on web</a>
</p>
</body></html>"""


def _render_latest_text(products: list[dict], week: str, unsub_link: str) -> str:
    lines = [f"StoreScope Weekly · {week}", f"Newest {len(products)} products across 50+ curated DTC brands", ""]
    for i, p in enumerate(products, 1):
        title = (p.get("title") or "Untitled")[:60]
        vendor = p.get("vendor") or "?"
        age = p.get("age_days", "?")
        price = p.get("price")
        price_str = f"${price:.2f}" if isinstance(price, (int, float)) and price > 0 else "—"
        lines.append(f"{i:>2}. {title} — {vendor} · {age}d old · {price_str}")
    lines.extend(["", f"Unsubscribe: {unsub_link}", f"Web: {BASE_URL}"])
    return "\n".join(lines)


# ── send ────────────────────────────────────────────────────
def _send_smtp(to_email: str, subject: str, html: str, text: str) -> str:
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = FROM_EMAIL
    msg["To"]      = to_email
    msg.attach(MIMEText(text, "plain", "utf-8"))
    msg.attach(MIMEText(html, "html", "utf-8"))
    with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=SMTP_TIMEOUT) as s:
        s.starttls()
        s.login(SMTP_USER, SMTP_PASS)
        s.send_message(msg)
    return msg["Message-ID"] or ""


def main(dry_run: bool = False, limit: int | None = None) -> int:
    week = _iso_week()
    _log.info("=== JSON digest run · week=%s dry_run=%s ===", week, dry_run)

    subs = _fetch_subscribers()
    _log.info("Subscribers fetched: %d organic", len(subs))
    if limit:
        subs = subs[:limit]

    clusters, fstats = _load_top_clusters()
    _log.info("Filter stats: total=%d passed=%d rejects=%s",
              fstats.get("total", 0), fstats.get("passed", 0), fstats.get("reject_reasons", {}))
    _log.info("Qualifying clusters: %d (thresholds: stores≥%d products≥%d price=$%s-$%s english_only=%s)",
              len(clusters), MIN_STORE_COUNT, MIN_PRODUCT_COUNT, MIN_PRICE, MAX_PRICE, ENGLISH_ONLY)

    # D+11 fallback: DTC seed 에서 clusters 0 이지만 latest_products 는 실 신호.
    # cluster 못하면 최신 상품 리스트로 대체 (content_type 표기).
    fallback_products: list[dict] = []
    if len(clusters) < MIN_CLUSTERS_TO_SEND and LATEST_PRODUCTS_PATH.exists():
        try:
            fallback_products = json.loads(LATEST_PRODUCTS_PATH.read_text())
            _log.info("Fallback: latest_products.json 사용 (%d items)", len(fallback_products))
        except json.JSONDecodeError:
            _log.warning("latest_products.json 파손 — 무시")

    if len(clusters) < MIN_CLUSTERS_TO_SEND and len(fallback_products) < MIN_CLUSTERS_TO_SEND:
        _log.warning("Content 부족 (clusters=%d, fallback=%d < MIN_CLUSTERS_TO_SEND=%d) — skip week",
                     len(clusters), len(fallback_products), MIN_CLUSTERS_TO_SEND)
        return 0

    sent_log = _load_sent_log()
    week_log = sent_log.setdefault(week, {})

    stats = {"total": len(subs), "sent": 0, "skipped_dedup": 0, "failed": 0, "smtp_missing": 0}

    smtp_ok = all([SMTP_HOST, SMTP_USER, SMTP_PASS])
    if not smtp_ok and not dry_run:
        _log.critical("SMTP env missing — no emails will be sent. Falling back to dry-run mode.")
        dry_run = True

    # D+11: use fallback content when clusters insufficient. Content flag drives renderer.
    use_fallback = len(clusters) < MIN_CLUSTERS_TO_SEND
    content_type = "latest_products" if use_fallback else "clusters"
    _log.info("Digest content_type=%s (clusters=%d, fallback_products=%d)",
              content_type, len(clusters), len(fallback_products))

    for sub in subs:
        email = sub["email"]
        h = _email_hash(email)
        if h in week_log:
            stats["skipped_dedup"] += 1
            continue
        unsub = _unsubscribe_link(h)
        if use_fallback:
            html = _render_latest_html(fallback_products, week, unsub)
            text = _render_latest_text(fallback_products, week, unsub)
        else:
            html = _render_html(clusters, week, unsub)
            text = _render_text(clusters, week, unsub)
        subject = f"StoreScope Weekly · {week} · {len(clusters)} trends"

        if dry_run:
            print(f"[dry-run] would send to {email} ({h}) — {subject}")
            week_log[h] = {"dry_run": True, "at": datetime.now(timezone.utc).isoformat()}
            continue

        try:
            mid = _send_smtp(email, subject, html, text)
            week_log[h] = {"sent_at": datetime.now(timezone.utc).isoformat(), "message_id": mid}
            stats["sent"] += 1
            _log.info("Sent to %s", email)
        except Exception as e:
            week_log[h] = {"failed_at": datetime.now(timezone.utc).isoformat(), "error": str(e)[:200]}
            stats["failed"] += 1
            _log.exception("SMTP fail for %s", email)

    _save_sent_log(sent_log)
    _log.info("=== Done · %s ===", stats)
    return 0 if stats["failed"] == 0 else 1


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true", help="No SMTP send, log only")
    p.add_argument("--limit", type=int, metavar="N", help="Send to first N subscribers only")
    args = p.parse_args()
    sys.exit(main(dry_run=args.dry_run, limit=args.limit))
