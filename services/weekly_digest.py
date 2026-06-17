"""
StoreScope Weekly Digest — Path A 유료 구독자의 실제 가치 산출물

마스터플랜 STEP 1 KPI #1 정합: "월요일 아침 winners 리스트 약속" 자동 충족.
매주 일요일 23:00 UTC 파이프라인 → 월요일 08:00 KST 고객 인박스 도착.

생성:
    digest_YYYYWNN.html  — Pro/Starter 차등 (Pro: 30일 history + 무제한 X-Ray, Starter: 20 trending only)
    digest_YYYYWNN.json  — 동일 데이터 머신 가독성 (Pro API 호출 결과와 일치)

사용:
    # 단일 디지스트 미리보기 (마케팅 자료/스크린샷)
    python services/weekly_digest.py --preview --plan starter

    # 실제 발송 (Path A 가동 후)
    python services/weekly_digest.py --send --plan pro --recipient pro@example.com

마스터플랜 Marketing Playbook §1.3 정합:
    퍼널 4단계 — X-Ray → 이메일 → digest 미리보기 → 유료
    "이 데이터를 매주 자동으로 받고 싶다"는 욕구 발생 지점 정확
"""

import argparse
import html
import json
import math
import os
import sys
from datetime import datetime, timezone
from pathlib import Path


def _trend_score_0_100(week_delta: int, store_count: int) -> int:
    """
    Trend Score 0-100 정규화 — 랜딩 약속 정합 (이전: unbounded 2640).

    공식: round(min(100, 25 * log10(raw + 1)))
        raw = week_delta * 3 + store_count
    분포 (검증):
        raw=100  → 50  (1주 33 stores 신규 or 100 stores 안정)
        raw=1000 → 75  (대형 dropshipper 신호)
        raw=2640 → 85  (현 데이터셋 top — Davines 클러스터)
        raw=10000+ → 100

    랜딩 카피 "80+ signals strong momentum" 와 정합:
        실 데이터셋에서 80 초과 = 상위 1-3% 클러스터 (희소).
    """
    raw = max(0, week_delta) * 3 + max(0, store_count)
    if raw <= 0:
        return 0
    return max(0, min(100, round(25 * math.log10(raw + 1))))


def _setup_paths():
    here = Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(here))
    os.environ.setdefault("DATABASE_URL", "postgresql:///storescope")


def _iso_week() -> str:
    """현 ISO week label (YYYY-WNN). 마스터플랜 'Week N' 정합."""
    return datetime.now(timezone.utc).strftime("%Y-W%V")


def _fetch_top_trending(limit: int = 20, min_stores: int = 5) -> list:
    """이번 주 트렌딩 N개 — rising score 정렬."""
    from api.auth import get_conn
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT
                    c.id,
                    c.store_count,
                    c.product_count,
                    p.title           AS rep_title,
                    p.price_min       AS rep_price_min,
                    p.price_max       AS rep_price_max,
                    p.image_url       AS rep_image,
                    COALESCE(ts.week_delta, 0)    AS week_delta
                FROM clusters c
                LEFT JOIN LATERAL (
                    SELECT week_delta FROM trend_snapshots
                    WHERE cluster_id = c.id
                    ORDER BY snapshot_at DESC LIMIT 1
                ) ts ON true
                LEFT JOIN LATERAL (
                    SELECT p2.title, p2.price_min, p2.image_url, p2.price_max
                    FROM product_clusters pc2
                    JOIN products p2 ON p2.id = pc2.product_id
                    WHERE pc2.cluster_id = c.id
                    -- 0 가격 후순위 (스토어가 inquiry/test 상품으로 0 입력 가능)
                    ORDER BY (p2.price_min > 0) DESC, p2.price_min ASC NULLS LAST
                    LIMIT 1
                ) p ON true
                WHERE c.store_count >= %s
                ORDER BY (COALESCE(ts.week_delta, 0) * 3 + c.store_count) DESC, c.id ASC
                LIMIT %s
            """, (min_stores, limit))
            return [
                {
                    "id": r[0], "stores": r[1], "products": r[2],
                    "title": r[3] or f"Cluster {r[0]}",
                    "price_min": float(r[4]) if r[4] is not None else None,
                    "price_max": float(r[5]) if r[5] is not None else None,
                    "image": r[6],
                    "week_delta": int(r[7]),
                    # FIX 2026-06-07: 0-100 정규화 (이전: unbounded 2640).
                    # 랜딩 약속 "Trend Score (0-100)" 와 정합.
                    "trend_score": _trend_score_0_100(int(r[7]), int(r[1])),
                }
                for r in cur.fetchall()
            ]


def _fetch_history(cluster_id: int, days: int = 30) -> list:
    """Pro 전용: 30-day trend history per cluster."""
    from api.auth import get_conn
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT snapshot_at, store_count, week_delta
                FROM trend_snapshots
                WHERE cluster_id = %s AND snapshot_at >= NOW() - INTERVAL '%s days'
                ORDER BY snapshot_at ASC
            """, (cluster_id, days))
            return [
                {"date": r[0].strftime("%m-%d"), "stores": r[1], "delta": r[2]}
                for r in cur.fetchall()
            ]


_HTML_TPL = """<!DOCTYPE html>
<html><head>
<meta charset="UTF-8">
<meta name="color-scheme" content="light dark">
<meta name="supported-color-schemes" content="light dark">
<title>{title}</title>
<style>
/* FIX 2026-06-07: 다크모드 호환 — Apple Mail / Gmail iOS / Outlook 다크모드. */
@media (prefers-color-scheme: dark) {{
  body {{ background:#1C1917 !important; color:#F3F1EE !important; }}
  h1 {{ color:#F3F1EE !important; }}
  .item {{ border-bottom-color:#3A3633 !important; }}
  .item-img {{ background:#3A3633 !important; }}
  .item-title {{ color:#F3F1EE !important; }}
  .item-stats, .meta, .eyebrow, .footer {{ color:#A8A29E !important; }}
  .cta {{ background:#000 !important; }}
  .footer {{ border-top-color:#3A3633 !important; }}
}}
body{{font-family:-apple-system,sans-serif;max-width:680px;margin:0 auto;padding:24px;background:#F9F8F6;color:#1C1917;line-height:1.55}}
.eyebrow{{font-size:12px;letter-spacing:0.5px;color:#78716C;text-transform:uppercase;margin-bottom:8px}}
h1{{font-size:28px;letter-spacing:-0.5px;margin-bottom:8px;color:#1C1917}}
.meta{{color:#78716C;font-size:13px;margin-bottom:32px}}
.item{{padding:18px 0;border-bottom:1px solid #E2E2E2}}
.item-row{{display:flex;gap:16px;align-items:center}}
.item-img{{width:60px;height:60px;border-radius:8px;background:#E2E2E2;flex-shrink:0;object-fit:cover}}
.item-meta{{flex:1}}
.item-title{{font-weight:700;font-size:15px;color:#1C1917;margin-bottom:4px}}
.item-stats{{font-size:12px;color:#78716C}}
.stat-up{{color:#059669;font-weight:600}}
.stat-down{{color:#B91C1C;font-weight:600}}
.history{{display:flex;gap:2px;margin-top:8px;height:24px}}
.bar{{flex:1;background:#4F46E5;opacity:0.4;border-radius:1px;min-height:2px}}
.cta{{padding:24px;background:#1C1917;color:#F9F8F6;border-radius:12px;margin-top:32px;text-align:center}}
.cta a{{color:#fff;text-decoration:none;font-weight:700;display:inline-block;padding:10px 22px;background:#4F46E5;border-radius:8px;margin-top:8px}}
.footer{{margin-top:32px;padding-top:16px;border-top:1px solid #E2E2E2;font-size:11px;color:#78716C;text-align:center}}
.plan-badge{{display:inline-block;padding:3px 10px;background:#4F46E5;color:#fff;font-size:10px;font-weight:800;letter-spacing:0.6px;border-radius:100px;text-transform:uppercase}}
</style></head>
<body>
<div class="eyebrow">StoreScope · Week {week}</div>
<h1>{title}</h1>
<div class="meta">
<span class="plan-badge">{plan_label}</span> · Refreshed {refreshed} ·
<a href="https://ddookim.github.io/storescope/">View on web</a>
</div>

{intro}

<h2 style="font-size:18px;margin:24px 0 12px">This week's top {n} trending products</h2>
{items}

{enterprise_cta}

<div class="cta">
<div>New competitors entered the index this week. See them all:</div>
<a href="https://ddookim.github.io/storescope/#xray">X-Ray any store →</a>
</div>

<div class="footer">
StoreScope · Shopify cross-store intelligence · pHash perceptual clustering<br>
You're receiving this because you subscribed to the {plan_label} plan.<br>
<a href="mailto:dodo@storescope.com?subject=Unsubscribe">Unsubscribe</a> ·
<a href="mailto:dodo@storescope.com">Contact</a>
</div>
</body></html>
"""


def _render_item(item: dict, plan: str) -> str:
    """단일 트렌딩 아이템 HTML."""
    delta = item["week_delta"]
    delta_html = (
        f'<span class="stat-up">+{delta} stores this week</span>' if delta > 0
        else f'<span class="stat-down">{delta} stores this week</span>' if delta < 0
        else '<span style="color:#78716C">No change this week</span>'
    )
    price = (
        f"${item['price_min']:.2f}" if item['price_min'] == item['price_max']
        else f"${item['price_min']:.2f}–${item['price_max']:.2f}"
    ) if item['price_min'] is not None else "n/a"

    # SEC: image URL은 Shopify CDN이지만 product title은 user 제출 콘텐츠 → escape 필수.
    # img src=는 attribute escape (html.escape 충분 — quote=True가 기본).
    img_src = html.escape(item["image"] or "", quote=True)
    img_html = (
        f'<img class="item-img" src="{img_src}" alt="">'
        if item["image"] else
        '<div class="item-img"></div>'
    )
    safe_title = html.escape((item["title"] or "")[:80])

    # Pro plan: 30-day history bars
    history_html = ""
    if plan == "pro":
        hist = _fetch_history(item["id"], days=30)
        if hist:
            max_stores = max(h["stores"] for h in hist) or 1
            bars = "".join(
                f'<div class="bar" style="height:{(h["stores"] / max_stores) * 100}%"></div>'
                for h in hist
            )
            history_html = f'<div class="history">{bars}</div><div style="font-size:11px;color:#78716C;margin-top:4px">30-day store-count trend</div>'

    return f"""
<div class="item">
  <div class="item-row">
    {img_html}
    <div class="item-meta">
      <div class="item-title">{safe_title}</div>
      <div class="item-stats">
        <strong>{item['stores']} stores</strong> · {price} · {delta_html} · Trend Score: {item['trend_score']}
      </div>
    </div>
  </div>
  {history_html}
</div>
"""


def generate(plan: str = "starter", limit: int = 20, output_html: bool = True) -> dict:
    """
    Weekly digest 생성 (HTML + JSON).

    Args:
        plan: "starter" | "pro" — Pro는 30-day history 포함
        limit: top N 아이템 (Starter 20, Pro 50)

    Returns:
        {"html": str, "json": dict, "week": str, "count": int}
    """
    if plan not in ("starter", "pro"):
        raise ValueError(f"plan must be 'starter' or 'pro', got {plan!r}")

    week = _iso_week()
    actual_limit = limit if plan == "starter" else max(limit, 50)
    items = _fetch_top_trending(limit=actual_limit, min_stores=5 if plan == "starter" else 3)
    refreshed = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    intro = (
        "<p>Here's what's spreading across the Shopify ecosystem this week. "
        "Each item shows store adoption, price range, and week-over-week change.</p>"
        if plan == "starter" else
        "<p>Pro digest — your weekly intelligence brief. "
        "Each entry includes 30-day store-count history and full price spread analytics.</p>"
    )

    enterprise_cta = (
        ""
        if plan == "pro" else
        """
<div style="background:#FFF7ED;border:1px solid rgba(245,158,11,0.3);border-radius:8px;padding:16px;margin-top:24px">
<strong>Upgrade to Pro</strong> for 30-day trend history, direct API access, and unlimited X-Ray lookups.
<a href="https://ddookim.github.io/storescope/#pricing" style="color:#D97706;font-weight:700">See Pro plan →</a>
</div>
"""
    )

    items_html = "\n".join(_render_item(it, plan) for it in items)

    html_str = _HTML_TPL.format(
        title=f"This week's winning Shopify products — Week {week}",
        week=week,
        plan_label=plan.upper(),
        refreshed=refreshed,
        intro=intro,
        n=len(items),
        items=items_html,
        enterprise_cta=enterprise_cta,
    )

    return {
        "week": week,
        "plan": plan,
        "count": len(items),
        "refreshed": refreshed,
        "html": html_str if output_html else None,
        "items": items,
    }


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--plan", choices=["starter", "pro"], default="starter")
    p.add_argument("--limit", type=int, default=20)
    p.add_argument("--output", default="/tmp", help="output directory")
    p.add_argument("--preview", action="store_true", help="브라우저 미리보기용")
    args = p.parse_args()

    _setup_paths()
    digest = generate(plan=args.plan, limit=args.limit)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    out_dir = Path(args.output)

    html_path = out_dir / f"digest_{args.plan}_{ts}.html"
    html_path.write_text(digest["html"])

    json_path = out_dir / f"digest_{args.plan}_{ts}.json"
    json_path.write_text(json.dumps({
        "week": digest["week"],
        "plan": digest["plan"],
        "count": digest["count"],
        "refreshed": digest["refreshed"],
        "items": digest["items"],
    }, indent=2, default=str))

    print(f"[{digest['plan']}] Week {digest['week']} digest — {digest['count']} items")
    print(f"  HTML: {html_path}")
    print(f"  JSON: {json_path}")
    if args.preview:
        print(f"\n  preview: file://{html_path}")


if __name__ == "__main__":
    main()
