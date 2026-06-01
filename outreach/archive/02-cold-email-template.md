# 콜드메일 템플릿 — 드롭시퍼 angle (v4)

**Constraint per v3 / 회귀 A안**:
- 1통당 작성 30분 (개인화 + 증거링크) → D3 5통 = 2.5h, D6 20통 = 12h
- 응답자 응대 표준 답변(03-reply-scripts.md) 준비된 후만 발송
- 120 단어 이내 본문, founding 12개월 락 (v3 H4)
- 데이터 신선도 표시: stale 상태에서 발송 시 첫 응답이 "데이터 너무 오래됨" 반박 위험 → 발송 전 pipeline 재가동 필수

---

## TEMPLATE A — 1st touch (D3 발송)

```
Subject: {FirstName}, 1,400 store sample for last week

Hi {FirstName},

Each Monday I run a script across 1,400+ Shopify stores 
and rank the products that just hit 5+ new stores in the past 7 days.

Last week 1 product jumped from 38 to 47 stores in 4 days
(name in screenshot). It's not on TikTok ads yet.

If you sell on Shopify, want the next list when it goes out Monday?
2-line yes / no is all I need.

Free, no signup, no card.
{LandingURL}?utm_source=cold&utm_campaign=d1&utm_content={cluster_id}

— Dodo
StoreScope · cross-store product intelligence
{TwitterHandle if any} | dodo@storescope.com

P.S. I read every reply.
```

### 개인화 변수 (메일당)

- `{FirstName}` — Apollo verified
- `{LandingURL}` — `https://ddookim.github.io/storescope/`
- `{cluster_id}` — 해당 받는 사람과 연관 가능한 상위 클러스터 id (예: 1774)
- screenshot URL → 추후 03 evidence page 링크로 교체

### 발송 전 체크리스트

- [ ] {FirstName} 실명 확인 (verified)
- [ ] domain 살아있는지 (`curl -I`)
- [ ] 메일 본문 단어수 ≤ 120
- [ ] UTM 파라미터 포함
- [ ] 발신자 이름 = "Dodo Kim" (founding member 신뢰)
- [ ] 회신 주소 = dodo@storescope.com 단일 (분산 X)

---

## TEMPLATE B — D+4 reminder (1st touch 무응답 시, D7에 발송)

```
Subject: Re: 1,400 store sample for last week

Quick bump in case the first one didn't make it through.

This week's Monday list went out — 12 products with 7-day store-count growth.
Top one jumped 38 → 47 stores. Not on ads yet.

Reply "yes" and I'll send the full list (PDF, 1 page). 
No signup. 

If you'd rather pass, no follow-up.

— Dodo
```

D7 발송 — 무응답자 대상. 응답 0건이면 v3 분기 룰 → 카피 교체 후 추가 7일.

---

## TEMPLATE C — 응답자에게 자료 송부 (응답 시 즉시)

```
Subject: Re: 1,400 store sample — here's this week's

{FirstName},

Attached: this week's top 5 products spiking across 1,400+ Shopify stores
(PDF, 1 page, 7-day delta).

If useful, full weekly list lands every Monday 6am UTC. 
$19/mo founding price, locked 12 months. 14-day trial, card not required to start.

{TrialURL}

Have a great day.

— Dodo
```

- `{TrialURL}` = LemonSqueezy 14일 trial 링크 (생성 필요, D2 작업)
- PDF 첨부 = D4 응답 발생 시 D4-D5에 1회 제작 (재사용)

---

## 응답 분기 (v3 D+14 분기 룰)

| 응답 양상 | D+7 액션 | D+14 액션 |
|---|---|---|
| 진심 관심 ("send list please") | TEMPLATE C + Loom 링크 | 응답 처리 1.5h/명 |
| 가격 협상 ("$19 too high?") | 03-reply-scripts §2 표준 답변 | founding 12개월 락 강조 |
| 기능 요청 ("can you add X?") | 03-reply-scripts §1 표준 답변 | 로드맵 로그, 구현 NO |
| 부정/스팸 | 03-reply-scripts §6 (5분) | 종료 |

---

## 시간 회계 (v3 D1-D7 가계부 기준)

| 시점 | 작업 | 소요 |
|---|---|---|
| D3 | 5통 작성·발송 | 2.5h (30분/통) |
| D6 | 20통 작성·발송 | 12h (36분/통 피로도) |
| D4-D7 | 응답 1건당 처리 | 1.5h × n |
| D7 | 측정 + 분기 판단 | 1h |

**D+14 분기 임계 = 응답 n ≥ 10**.
