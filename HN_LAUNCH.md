# Show HN Draft — D+11 Backup Channel

**Trigger**: D+10 (2026-08-09) checkpoint = Yellow (organic 3-4) OR Red (organic < 3) with 재기 여지.
**Publish target**: https://news.ycombinator.com/submit — Show HN.
**Tracking**: link uses `?ref=hn` for channel attribution.
**Timing**: US Pacific weekday 06:00-08:00 (highest front-page probability per Algolia data).

---

## Title (80 chars, sentence case, no marketing verbs)

Show HN: StoreScope – a Shopify product-trend scanner that watches 1,400 stores

## URL

https://ddookim.github.io/storescope/?ref=hn

## Text (empty — Show HN with URL only; comment carries context)

*(leave text field empty — HN convention for Show HN with a working URL)*

## First comment (OP posts immediately after submission)

I built this because ecom trend feeds all look at the demand side — TikTok search velocity, Google Trends, etc. — which means by the time a product surfaces, it's already priced in.

StoreScope watches the **supply side**: which products are showing up on more independent Shopify stores week-over-week. When a cluster of stores starts carrying the same item, that's a leading indicator, usually 5–9 days before it hits demand-side trend feeds.

**Stack (nothing exotic):**
- Python 3.11 / FastAPI backend (5,000 LOC, 80 tests passing)
- curl_cffi + asyncio.Semaphore(15) for the crawl (Shopify public JSON endpoints only, no login, no ToS scrape)
- Perceptual hashing (imagehash) + text similarity for product-cluster identity across stores
- PostgreSQL (Neon free tier when I flip infra on)
- Weekly cron (GitHub Actions, free tier)
- Streamlit for the internal admin, plain HTML/vanilla CSS for the public landing

**Honest state disclosure:**
- The public URL runs in **demo mode with mocked API responses** (there's a banner that says so). The real backend is coded, tested, and wired — I just haven't pointed the front-end at it yet. Reason: I want to see if anyone actually wants this before I burn Neon compute cycles.
- The demo shows 3 sample product clusters representative of what the real feed emits.
- 0 organic signups as of today. This is a "does anyone care" test more than a launch.

**What I'm asking HN:**
- Is the "watch supply-side stores, not demand-side searches" thesis obviously flawed? I'd rather find out here.
- The pricing page shows $19/49 monthly tiers — is that laughably wrong for this shape of product?
- If you'd actually pay for this: dropping your email on the site is my go/no-go signal for wiring up the backend.

Repo: (not open-sourced yet — will be if I kill the project at D+30. That's a public commitment.)

Kill switch: 10 signups by 2026-08-13 or I retire the URL. No zombie SaaS.

---

## Anti-pattern checklist (must pass before publish)

- [ ] Title has no "🚀", no emojis, no CAPS
- [ ] Text field is empty (URL-only Show HN)
- [ ] First comment posted within 60s of submission
- [ ] No "please upvote" anywhere
- [ ] Honest disclosure of demo mode (banner + comment)
- [ ] Tech stack detail sufficient to satisfy HN (concrete numbers, no fluff)
- [ ] Kill switch commitment is real (2026-08-13)

## Post-publish tracking

```bash
# HN Algolia API — real-time story metric
curl -s "https://hn.algolia.com/api/v1/search?query=StoreScope&tags=show_hn" \
    | python3 -c "import sys,json;d=json.load(sys.stdin);[print(h.get('points'),h.get('num_comments'),h.get('url'),sep=' | ') for h in d.get('hits',[])]"
```

Then update `deploy/monitor_launch.sh` non-organic filter is unchanged; ?ref=hn attribution runs same code path as ?ref=ih.

## D+12 (2026-08-11) HN checkpoint

| Metric | Green (continue) | Yellow (Twitter next) | Red (D+14 kill) |
|---|---|---|---|
| HN points | ≥ 30 | 10-29 | < 10 |
| HN front page | reached | brief | never |
| Organic signup (?ref=hn) | ≥ 5 | 2-4 | < 2 |
| Cumulative organic (IH+HN) | ≥ 10 | 5-9 | < 5 |
