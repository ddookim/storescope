# Indie Hackers Milestones Post — D+8 Channel Reset

**Trigger**: new D0 (2026-07-30) Reddit path → 8 days → organic signup 0 → D+7 kill switch fired.
**Decision**: 1 forgiving channel (IH) → 48h data → D+10 decision point.

**Publish target**: https://www.indiehackers.com/post — Milestones category.
**Tracking**: link uses `?ref=ih` for channel attribution in Netlify Forms UA/referer.
**Kill switch reset**: IH post → D+10 (2026-08-09) email signup < 3 → HN Show attempt (last channel).

---

## Title (60 chars)

Built a Shopify product-trend scanner in 6 weeks — need 10 beta users to go live

## Body

Solo indie founder, bootstrapped, $0 infra budget, no team, no investors. Spent the last 6 weeks building **StoreScope** — a Shopify product-trend scanner that surfaces items gaining traction across independent stores a week before they hit mainstream trend feeds.

**The honest state** (this is not a launch post, it's a "help me decide if this ships" post):

- Code: done. 5,000+ LOC Python/FastAPI, 80 tests passing, weekly crawl pipeline, HMAC-signed Paddle webhook, dead-man freshness switch that blocks payments if data goes stale > 48h.
- Site: live at https://ddookim.github.io/storescope/?ref=ih — but it runs in **demo mode** with mock data right now (banner says so).
- Real infra (Neon PG + Render): I only wire it up when 10 people actually sign up. Spending compute on a graveyard product is how bootstrapped SaaS dies quietly.
- Traction: 0 organic signups in 8 days after posting to r/shopify (post got no traction).

**What I'm asking IH for:**

1. Brutal feedback on the landing (hook, pricing, positioning).
2. If it clicks for you, drop your email on the site — that's my go/no-go signal for wiring up the real backend.
3. If it doesn't, tell me why. I'd rather kill this at $0 than sink another 6 weeks into distribution.

**Why "a week early" is not marketing fluff**: my pipeline aggregates product-appearance velocity across a fixed sample of 1,400+ Shopify stores. A cluster crossing the momentum threshold (velocity × spread) usually shows up in TikTok trend feeds 5–9 days later. It's not magic — it's just watching supply-side signal instead of demand-side signal.

**Kill switch commitment**: if I don't hit 10 signups by 2026-08-13, I retire the URL and open-source the crawler. No zombie SaaS.

Site: https://ddookim.github.io/storescope/?ref=ih

Thanks for reading. Even a downvote with a comment is more useful than silence.

---

## Post-publish tracking (auto)

```bash
bash deploy/monitor_launch.sh --watch    # 30m interval — Netlify Forms only
# manual IH engagement metric — I'll poll IH post URL every 6h and grep upvote count
```

## D+10 (2026-08-09) checkpoint

| Metric | Green | Yellow | Red |
|---|---|---|---|
| IH upvotes | ≥ 15 | 5-14 | < 5 |
| IH comments | ≥ 3 | 1-2 | 0 |
| Netlify signup | ≥ 5 | 2-4 | < 2 |
| ref=ih attribution | ≥ 40% of signups | — | 0 |

**Green** → continue, wire up Neon+Render (Phase 1-2 launcher already done).
**Yellow** → HN Show HN attempt at D+11.
**Red** → D+14 pre-emptive kill + start实验 2 scoping.
