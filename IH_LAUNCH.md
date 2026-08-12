# Indie Hackers Milestones Post — D+13 Channel Reset

**Trigger**: new D0 (2026-07-30) Reddit path → 13 days → organic signup 0 → D+7 kill fired, D+10 red, D+14 red zone (tomorrow).
**Decision**: 1 forgiving channel (IH) → publish today → D+14 (2026-08-13) organic ≥ 10 checkpoint.

**Publish target**: https://www.indiehackers.com/post — Milestones category.
**Tracking**: link uses `?ref=ih` for channel attribution in Netlify Forms UA/referer.
**Kill switch commitment**: IH post → D+14 (2026-08-13) email signup < 10 → 실험 2 이행 + retire URL + open-source crawler.

---

## Title (60 chars)

Built a Shopify product-trend scanner in 6 weeks — need 10 beta users to go live

## Body

Solo indie founder, bootstrapped, $0 infra budget, no team, no investors. Spent the last 6 weeks building **StoreScope** — a weekly digest of new products from 58 curated independent DTC brands (Everlane, Taylor Stitch, Allbirds, Beardbrand, etc.).

**The honest state** (this is not a launch post, it's a "help me decide if this ships" post):

- Code: done. 5,000+ LOC Python/FastAPI, 80 tests passing, weekly crawl pipeline, HMAC-signed Paddle webhook, dead-man freshness switch that blocks payments if data stales > 48h.
- Site: live at https://ddookim.github.io/storescope/?ref=ih — runs in **demo mode with real weekly-refreshed data** (58 brands crawled every Sunday, 20 latest products + 8 categories auto-committed to gh-pages).
- Real infra (Neon PG + Render): I only wire it up when 10 people actually sign up. Spending compute on a graveyard product is how bootstrapped SaaS dies quietly.
- Traction: **0 organic signups in 13 days** after posting to r/shopify. IH is the last forgiving channel before I fire the kill switch.

**What I'm asking IH for:**

1. Brutal feedback on the landing (hook, pricing, positioning).
2. If it clicks for you, drop your email on the site — that's my go/no-go signal for wiring up the real backend.
3. If it doesn't, tell me why. I'd rather kill this at $0 than sink another 6 weeks into distribution.

**What the product actually does** (no marketing fluff): every Sunday the pipeline crawls 58 known-active DTC Shopify brands' `/products.json` endpoints, dedupes by pHash, and produces a weekly digest of what's new by category (Bottoms, Sweaters, Denim, etc.). If you follow niche DTC brands for sourcing/trend research, this saves you the "check 58 sites manually" tax. That's the entire pitch — no ML trend prediction, no supply-side signal magic.

**Kill switch commitment**: if I don't hit 10 signups by 2026-08-13, I retire the URL and open-source the crawler. No zombie SaaS.

Site: https://ddookim.github.io/storescope/?ref=ih

Thanks for reading. Even a downvote with a comment is more useful than silence.

---

## Post-publish tracking (auto)

```bash
bash deploy/monitor_launch.sh --watch    # 30m interval — Netlify Forms only
# manual IH engagement metric — I'll poll IH post URL every 6h and grep upvote count
```

## D+14 (2026-08-13) checkpoint — 24h after publish

| Metric | Green | Yellow | Red |
|---|---|---|---|
| IH upvotes | ≥ 15 | 5-14 | < 5 |
| IH comments | ≥ 3 | 1-2 | 0 |
| Netlify signup (post-IH) | ≥ 10 | 3-9 | < 3 |
| ref=ih attribution | ≥ 40% of signups | — | 0 |

**Green** → wire up Neon+Render (Phase 1-2 launcher ready), promote to real backend.
**Yellow** → HN Show HN attempt within 48h (last channel).
**Red** → **KILL fire on D+14**: retire URL, open-source crawler, start 실험 2 scoping.
