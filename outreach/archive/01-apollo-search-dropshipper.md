# Apollo 검색 — 드롭시퍼 / e-commerce 운영자 (v4 회귀안)

목표: D1 25명 리스트. 드롭시퍼 + Shopify 운영 founder + e-commerce ops.

## 검색 필터 1 — Founder / Owner

```
Title contains ANY of:
  Founder, Co-Founder, Owner, CEO

Company keywords:
  dropshipping, e-commerce, Shopify

Company size:
  1-10 employees   (single-founder operations)

Country:
  United States, Canada, United Kingdom, Australia
  (영어권 + 결제 환경 안정)

Has email: Yes
Email status: Verified
```

→ Apollo Free credits/yr 한도 내에서 15명 추출.

## 검색 필터 2 — E-commerce ops manager

```
Title contains ANY of:
  E-commerce Manager, Shopify Manager, Operations Manager,
  Marketplace Manager, Digital Marketing Manager

Industry:
  Retail, Consumer Goods, Apparel & Fashion, Health, Wellness & Fitness

Company size:
  11-50 employees   (작은 Shopify 운영팀)

Country: 위와 동일
```

→ Apollo + Hunter 합쳐 추가 10명.

## 검색 필터 3 — Reddit/Discord 활동가 (수동 보강)

Apollo로 못 잡으면 수동 발굴:
- r/dropship, r/shopify 활성 OP/모더레이터 → 프로필 → 회사 도메인 → Hunter로 email
- Discord "Dropship Lifestyle", "Build with Bjorn" 채널 활성 사용자

목표: 25명 = 15(필터1) + 10(필터2) + 0~5(필터3 백업)

## 검증 게이트 (Apollo 1명당)

발송 전 30초 체크:
- [ ] 회사 도메인 살아있는가 (`curl -I {domain}`)
- [ ] 최근 30일 LinkedIn 활동 있는가 (post / repost / comment)
- [ ] 회사 description에 Shopify/e-commerce 언급되는가

3중 1개라도 아니면 제외. n=25 부족하면 필터 1을 30명으로 확장.

## 시간 한도

- 필터 1+2 검색·필터 적용: 30분
- 25명 검증 게이트: 15분
- 1명당 30초 × 25 = 12.5분
- 합: 1h. v3 D1 가계부 (5h) 중 1h를 여기 배정.

## 한도 도달 시 fallback

Apollo 무료(100 credits/yr) 빠르게 소진:
- Hunter.io 25 search/mo + 50 verify/mo 병용
- Clay Free trial (대량 enrichment 1회성)
- LinkedIn Sales Navigator 트라이얼 (1개월 무료)

> Apollo 한도 도달 즉시 다음 도구로 즉시 전환. 절대 카드 등록하지 말 것.
