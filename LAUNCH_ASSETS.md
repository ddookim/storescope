# StoreScope Launch Assets

## Reddit Post — r/dropship / r/shopify

**Title:**
I built a free tool that shows you what products are trending across 1,400+ Shopify stores right now

**Body:**
I scraped and clustered 140k+ products from 1,400 real Shopify stores using perceptual image hashing (pHash).

The idea: if the same product image appears across 30+ independent stores, that's a reliable demand signal — not a paid ad, not a trend prediction, actual sell-through data.

**What it shows:**
- Products being sold simultaneously across the most stores (top cluster: 660 stores selling the same item)
- Price spread across competitors (useful for sourcing decisions)
- Which stores are undercutting each other

**Free to use:** [storescope-app.netlify.app](https://storescope-app.netlify.app) — just paste any Shopify store URL, no signup.

The clustering runs weekly on fresh crawl data, so the trends update automatically.

Happy to answer questions about the tech stack (pHash + BK-Tree for nearest-neighbor clustering, ~13s to cluster 140k products).

---

## ProductHunt Launch Description

**Tagline:** Cross-store Shopify product intelligence — see what's actually trending

**Description:**
StoreScope crawls 1,400+ Shopify stores weekly and clusters products by visual similarity using pHash. The result: a ranked list of products appearing across the most stores — a ground-truth demand signal for dropshippers, sourcing agents, and e-commerce researchers.

Free Store X-Ray tool: paste any Shopify URL and instantly see competing stores selling the same supplier products, with price comparison.

API plans available for automated access.

**First comment (Maker comment):**
Hey PH — I'm the solo founder. Built this because I kept seeing the same "trending product" tools that just scrape TikTok hashtags or AliExpress bestsellers. Those are lagging indicators. Cross-store Shopify presence is a leading indicator — if 50 independent dropship stores are already stocking something, the supplier relationship exists and the unit economics work.

The clustering uses pHash (perceptual hash) + BK-Tree for O(n log n) nearest-neighbor search on 140k product images. Happy to go deep on the tech if anyone's interested.

---

## ADMIN_SECRET 생성 명령어
```bash
openssl rand -hex 32
```

---

## Telegram 봇 Chat ID 조회
```bash
# 봇 토큰으로 업데이트 조회 → chat.id 값 확인
curl "https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates" | python3 -m json.tool | grep '"id"' | head -5
```
