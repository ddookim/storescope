# Cold Email Sequences — DTC Brand IP Outreach

마스터플랜 Marketing Playbook §3 + 평가제안서 #1 처방 (콜드메일 = 첫 10명 결정론적 채널) 정합.
EMAIL #1/#2/#3 시퀀스 + 응답 분기 + 발송 설정 통합.

**사전 준비**:
1. `python scripts/brand_scan.py BRAND_NAME` 실행 → `/tmp/storescope_scan_*/` 에 PDF + summary + targets CSV 생성
2. Apollo.io (Free 25/월) 또는 Hunter.io로 brand IP/Legal 담당자 이메일 추출
3. 본 템플릿의 `{...}` 자리표시자를 brand_scan 출력으로 채워넣기

**KPI 임계**: Open 50% / Reply 12% (E1), Open 40% / Reply 8% (E2), Open 35% / Reply 5% (E3)

---

## EMAIL #1 — Day 0 (월/화 09:30 현지 시간)

**소요**: 5분 (개인화), 25통/주
**핵심**: 첫 줄 = 데이터 (그 회사 brand_scan 결과). Subject = "정확한 N"

### Subject (3 option, A/B 테스트)

```
{N} Shopify stores currently using your product images
Quick {Brand} brand audit — found {N} unauthorized listings
{Brand}: {N}-store counterfeit cluster report (5 min read)
```

### Body (plain text, 120 words max)

```
Hi {First Name},

I scanned 1,400+ Shopify stores last weekend and {Brand}'s product images
came back on {N} unauthorized stores. Sample below:

  {unauthorized-store-1}.com — listing your {product-name-1} at ${price-1}
    (your retail: ${retail-1})
  {unauthorized-store-2}.com — your {product-name-2}, same hero image
  {unauthorized-store-3}.com — entire {product-line} collection mirrored

I'm matching by perceptual image hash, not text, so renamed listings
still trigger. Full report (15 stores) is here:

  https://storescope.com/brand/{brand-slug}-unauthorized-sellers
  (No signup. Free for your team to verify.)

If your brand protection team wants weekly alerts when a new store picks
up your images, I can set that up. Otherwise the URL above is yours to
forward.

— Dodo
Founder, StoreScope
storescope.com
```

### 발송 설정

- **From**: dodo@storescope.com (자체 도메인 SPF/DKIM 통과 후만)
- **첨부 X** (PDF 첨부는 스팸 필터 트리거)
- **링크 1개만** (storescope.com 자체 도메인 brand 페이지). 다중 링크 = 스팸 신호
- **추적 이미지 X** (privacy-conscious DTC brand 거부감)

---

## EMAIL #2 — Day 3 (응답 없음 follow-up)

**소요**: 1분
**핵심**: 새 데이터 추가 (Day 0 이후 신규 매칭). "movement"가 핵심 신호

### Subject

```
Re: {N} unauthorized sellers — quick proof
```
(`Re:` prefix로 기존 thread 재진입 — open rate 직접 영향)

### Body (60 words max)

```
Hi {First Name},

Following up on Monday. Two new stores added your images since I emailed:

  {new-store-1}.com (listed {product} 2 days ago)
  {new-store-2}.com (entire {collection} mirrored yesterday)

The full live count is at:
  https://storescope.com/brand/{brand-slug}-unauthorized-sellers

If brand protection isn't owned in-house, no problem — happy to send
this to whoever handles it. Just point me there.

— Dodo
```

---

## EMAIL #3 — Day 7 (Breakup, 회수 트리거)

**소요**: 1분
**핵심**: "I'll stop after this" 명시 + 영구 가치 제공 (URL stays online).

### Subject

```
Closing the loop — last note
```

### Body (50 words max)

```
Hi {First Name},

Last note from me — I'll stop after this.

Your live unauthorized-seller dashboard stays online regardless:
  https://storescope.com/brand/{brand-slug}-unauthorized-sellers

I update it weekly. No login needed. If the situation changes and you
want alerts, my inbox is open.

— Dodo
```

---

## 응답 분기 — Reply 후속

| Reply 패턴 | 즉시 액션 (15분 내) | 다음 단계 |
|---|---|---|
| "Interesting, send more" | 무료 PDF + 향후 4주 weekly digest 무료 약속 | 4주차 종료 시점 $499 evidence pack 제안 |
| "We have a vendor for this" | "기존 vendor와 충돌하지 않음 — 알림만 무료 등록" 제안 | 분기당 1회 follow-up (자동화) |
| "Who's responsible internally?" | 추천받은 사람에게 EMAIL #1 forward (Re: chain 유지) | 새 thread로 시작 안 함 |
| "Send us pricing" | $499 one-shot evidence pack + Quarterly Monitor $99/mo 가격 | Stripe Payment Link 직접 전송 |
| Negative / unsubscribe | 즉시 ack ("Removed, no further contact") + DB 추가 | 90일간 같은 도메인 미발송 룰 |
| No response 14일 | Lemlist/Apollo "sequence ended" 자동 처리 | 6개월 후 새 data point 발견 시 1회 재진입 가능 |

---

## 발송 도구 + 비용 (마스터플랜 정합)

| 도구 | Free tier | 한도 | 사용 시점 |
|---|---|---|---|
| Apollo.io | Free | 25 contacts/월 + 250 emails/월 | Month 1-2 (50명/월 한계) |
| Hunter.io | Free | 25 검증/월 | Apollo 보완 |
| Lemlist | 14일 trial | 일 50통 (도메인 워밍 1주 필요) | Month 3+ (스케일링 시) |
| 자체 SMTP (Gmail) | $0 | 일 500통 | 모든 단계 |

**솔로 한도**: 주 30-50통 (마스터플랜 §3.1 기준). E1 25통 + E2 follow-up 15 + E3 10 ≈ 주 50통.

---

## 추적 KPI (Admin Dashboard 정합)

`pages/01_Admin_Dashboard.py` 의 KPI #2 "Total email captures" 와 별개로 콜드메일 trackings:

- Sent count (수동 입력)
- Open rate (Lemlist 자동 / Gmail 수동)
- Reply rate (Gmail "Cold-Reply" 라벨 카운트)
- Meeting booked count
- Closed deals ($499 one-shot 누적)

**임계 (마스터플랜 §3.2)**:
- Open < 30%: 서브젝트 재작성
- Reply < 5%: 본문 첫 줄 재작성 (data specificity 부족)
- Closed = 0 / 50통: ICP 재정의 (브랜드 카테고리 변경)

---

## 법적 면책 (CAN-SPAM / GDPR)

- 첫 이메일 = 법적으로 "transactional" 분류 가능 (사실 데이터 + 사업 관계 제안)
- US 발송: CAN-SPAM 정합 — sender ID 명시 (Dodo Kim), 물리 주소 미요구 (개인 sole proprietor)
- EU 발송: GDPR Legitimate Interest 가능 (B2B + brand owner = data subject 본인 이익)
- 명시 unsubscribe 요청 시 90일간 도메인 차단
- 본 시퀀스는 외부 변호사 자문 미받음 → 실제 발송 전 LEGAL 검토 권장
