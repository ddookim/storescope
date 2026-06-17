# 응답자 응대 표준 답변 — v3 Section 06 deployable

**Rule (v3 H4)**: founding 가격 "영구 락" 금지. 항상 "12개월 락 + 이후 표준 가격".

**Rule (v3 R15)**: "기능 추가하면 사겠다" 진입 시 즉흥 답변 절대 금지. 표준 답변으로 30초 내 응대 → 30h 한도 보호.

---

## §1 — 가격 / Trial 문의

**받음**:
> "How much is it? / Free trial?"

**보냄**:
```
$49 Pro 14-day free trial — no card required.
{TrialURL}
First month? Full refund if not useful.
```

후속: LemonSqueezy trial 링크 전송. 응답 대화 길어지면 Loom 데모 안내.

---

## §2 — 기능 요청

**받음**:
> "Can you add {feature X}?"
> "Does it work with {platform Y}?"

**보냄**:
```
Great call. Logged on the v2 roadmap.
Founding member price ($19 Starter / $49 Pro) is locked for the first 12 months,
so signing up now locks in the price even if {feature} ships later.

{TrialURL}
```

후속: 기능 요청 로그(`outreach/feature-requests.csv`)에 추가만. 구현 절대 안 함.

---

## §3 — 커스텀 데이터 요청

**받음**:
> "Can you pull data for {specific category / region}?"
> "I need a custom export of X."

**보냄**:
```
Custom data sits under our Enterprise tier — quoted per scope.
The honest path: start with Pro for 6 weeks to see if the standard data 
covers 80% of your case. If you still need custom, we can discuss Enterprise.

{TrialURL}
```

후속: Enterprise 분리. Pro 결제 후 6주 데이터 sufficient 가설 검증.

---

## §4 — 가격 협상

**받음**:
> "Can you do $9 / $10? / $19 too high for me."

**보냄**:
```
Founding price is locked for the first 12 months at this rate — 
no further discount available.

If you decide it's not useful in the first month, 
full refund, no questions.

{TrialURL}
```

후속: 환불 보장이 부담 제거. 추가 협상 즉시 종료.

---

## §5 — 기술 / 메소드 질문

**받음**:
> "How do you crawl 1,400 stores? Is this legit?"
> "What's the algorithm?"

**보냄**:
```
Public Shopify endpoints (/products.json) — well within Shopify TOS.
1,400 stores indexed weekly using perceptual hashing for cross-store dedup.
Happy to share more under NDA once you're on a paid plan.

{TrialURL}
```

후속: 기술 IP 보호 + 시간 보호. 추가 기술 질문 들어오면 동일 표준 답변 반복.

---

## §6 — 부정 / 거절 / 스팸

**받음**:
> "Not interested."
> "Stop emailing."
> "{insult}"

**보냄**:
```
Got it — unsubscribed. 
Happy selling.
```

후속: 즉시 종료, 5분 이내. 추가 정당화/사과/설득 금지.

---

## §7 — Loom 데모 요청

**받음**:
> "Can you show me how it works?"

**보냄**:
```
2-minute Loom — what the Monday list looks like + how you'd use it:
{LoomURL}

If you want the next list when it goes out Monday, just reply "yes".
```

후속: Loom 1회 녹화 (D5에 제작), 모든 응답자에게 동일 링크 재사용.

---

## §8 — 통합 / API 문의

**받음**:
> "Do you have an API? / Webhook?"

**보냄**:
```
API endpoints exist (Pro tier). Docs:
{API_DOCS_URL}      # = post-launch API base + /docs (Render: https://storescope-api.onrender.com/docs)

REST + JSON. Auth via API key generated after Pro signup.
{TrialURL}
```

후속: API Docs 공개됐으므로 자가-온보딩 가능. 추가 질문 시 §2 적용.

---

## 시간 예산 (응답 1건당)

| 단계 | 소요 |
|---|---|
| 메일 읽기 + 분류 | 1분 |
| 위 §1-§8 답변 검색 + 발송 | 5분 |
| Trial 링크 클릭 → 결제 화면 도달 확인 (수동) | 10분 |
| Loom 안내 후 시청 추적 (선택) | 5분 |
| 후속 메일 (24-48h 무응답 시 1회) | 10분 |

총 30분-1.5h/건. v3 L1 보정 (1.5h × n) 일치.

---

## 절대 금지

- ❌ 즉흥 답변 ("어... 음... 가능할 것 같은데요")
- ❌ 기능 즉시 구현 약속
- ❌ 가격 추가 할인 ("이번 한 번만"도 안 됨)
- ❌ 영구 락 약속 ("영원히 $19" 금지, "12개월" 명시)
- ❌ 답변 길이 100단어 초과
- ❌ 응답 1건에 1시간 초과 사용 (sunk cost trap)
