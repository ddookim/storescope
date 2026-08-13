# StoreScope

**Weekly digest of new products from 58 curated independent DTC Shopify brands** — delivered every Monday morning.

**Live site**: https://ddookim.github.io/storescope/

---

## What this is

A supply-side signal for people who follow DTC brands (Everlane, Taylor Stitch, Allbirds, Beardbrand, Gymshark, ColourPop, Lunya, and 51 more). Every Sunday night the pipeline crawls each brand's public `/products.json` feed, deduplicates by perceptual hash + title similarity, applies vendor/category diversity caps, and produces a digest of what's new — grouped by category, sorted by first-appearance freshness.

Not a spy tool. Not an ad-library scraper. Not ML trend prediction. Just: *what did these 58 brands ship this week*.

## Who it's for

- **Sourcing agents** tracking indie DTC launches for buyer clients
- **Brand operators** benchmarking new-product cadence against peers
- **Trend researchers** who want a low-noise curated feed vs a firehose

Not for: dropshippers running FB ads (see [alternatives](https://ddookim.github.io/storescope/compare/)).

## What's honest about the state

- Code: **5,000+ LOC Python/FastAPI**, 80 tests passing, weekly crawl pipeline, HMAC-SHA256 Paddle webhook with idempotency + replay defense, dead-man freshness switch that blocks payments if data stales > 48h.
- Site: **live in demo mode** (real weekly-refreshed data — 58 brands crawled every Sunday, 20 latest products + 8 categories auto-committed to gh-pages).
- Real backend infra (Neon PostgreSQL + Render): **only wire up when 10 signups arrive**. No point paying for compute on a graveyard product.
- Traction: **0 organic signups in 14 days** as of D+14 (2026-08-13). Kill switch fires D+30 if organic <10 AND paid <1.

## Stack

- **Backend**: Python 3.11+, FastAPI, psycopg2 (ThreadedConnectionPool singleton), curl_cffi
- **DB**: PostgreSQL (Neon free tier, wire-up pending)
- **Frontend**: Vanilla HTML + inline CSS/JS (single-file 380KB landing, no framework, no build step)
- **Infra**: GitHub Pages (landing) + Render free web service + UptimeRobot 5-min ping (planned)
- **Payments**: Paddle (Merchant of Record) — HMAC webhook + idempotency + fail-closed
- **Automation**: GitHub Actions weekly cron (Sunday 23:00 UTC) — crawl → dedupe → cluster → digest → sync → IndexNow ping

## Content surface (auto-generated weekly)

- [Weekly digest sample](https://ddookim.github.io/storescope/digest-sample.html) — 20 latest products
- [Browse by category](https://ddookim.github.io/storescope/category/) — 7 programmatic pages (Dresses, Sweaters, Bottoms, Knit Tops, Denim, Woven Tops, Outerwear)
- [Compare with alternatives](https://ddookim.github.io/storescope/compare/) — Koala Inspector, Dropship.io, PPSpy, Store Leads, Ecomhunt (honest positioning)
- [Data reports](https://ddookim.github.io/storescope/report/) — State of DTC Shopify weekly snapshot (free to cite, embed, republish)
- [Curated brand index](https://ddookim.github.io/storescope/brands/) — individual brand launch pages
- [Blog](https://ddookim.github.io/storescope/blog/) — technical + positioning posts

## SEO / open standards

- Sitemap: [sitemap.xml](https://ddookim.github.io/storescope/sitemap.xml) (48+ URLs, weekly auto-lastmod refresh)
- RSS: [feed.xml](https://ddookim.github.io/storescope/feed.xml) (auto-discovery via `<link rel="alternate">`)
- IndexNow: Bing / Yandex / Naver / Seznam auto-ping on every deploy
- Schema.org: SoftwareApplication, Article, CollectionPage, Report, FAQPage, BreadcrumbList
- Hreflang: `en` / `ko` / `ja` / `x-default`
- WCAG 2.1 AA: contrast verified, keyboard nav, screen reader support

## Project structure

```
StoreScope/
├── api/                # FastAPI backend (main, paddle_routes, auth, admin, rate_limit)
├── pipeline/           # Weekly crawl → cluster → digest → SEO page generators
│   └── latest_products.py   # _write_brand_profiles + _write_category_pages + _write_state_report
├── services/           # Weekly digest email + JSON digest + X-Ray report
├── landing/            # Single-file HTML + subdirs (blog, brands, category, compare, report)
├── deploy/             # launch_phase1.sh / launch_phase2.sh / verify_landing.sh / IndexNow key
├── migrations/         # PostgreSQL DDL (paddle idempotency, customer_id UNIQUE, etc.)
├── tests/              # 17 test files (paddle, auth, rate limit, XSS, storm score, etc.)
└── .github/workflows/  # tests, red_team_ci, weekly_pipeline, sync_gh_pages, verify_landing
```

## Kill switch commitments

Data-driven decisions posted publicly. New D0 = 2026-07-30.

| Checkpoint | Condition | Action |
|---|---|---|
| D+7 | organic signup < 5 | (fired) — channel reset |
| D+14 | organic signup < 10 | (red zone — 2026-08-13) |
| D+30 (2026-08-29) | paid 0 AND organic < 15 | **KILL** + retire URL + open-source crawler |

No zombie SaaS. If it doesn't earn its keep in 30 days, it ends.

## License

Not open-sourced during private beta. If the kill switch fires D+30, the crawler stack ships MIT.

---

**Contact**: [Contact form on live site](https://ddookim.github.io/storescope/#contact) (custom domain launches Q4 2026)
