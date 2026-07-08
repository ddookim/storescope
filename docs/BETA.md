# StoreScope Beta — Open Beta 외부 유저 테스트 → 정식 출시

**현재 상황 자각**: 미완성 + 버그 다수. → 베타로 점진적 외부 노출 + 버그 / UX feedback 수집 + 후기 / 영상 확보 → 정식 출시.

**3 단계 path**:

1. **Phase 1 — Closed alpha** (5-10명, 1주): 본인 + 친구 / 지인 → critical bug fix
2. **Phase 2 — Open beta** (30-50명, 2-3주): 외부 모집 채널 → broader UX feedback + 후기
3. **Phase 3 — 정식 출시** (Reddit + 인스타 + Product Hunt)

**핵심 안전**: Phase 1-2 모두 Paddle **sandbox** 강제 (`STORESCOPE_BETA_MODE = true` 활성) — 실 결제 X. 베타 단계 사용자 손해 0.

---

## Phase 0 — 인프라 셋업 (1-2일)

`docs/DEPLOY.md` Step 1-10 동일하게 진행. 단:

### Paddle 가입 = Sandbox 만

`docs/DEPLOY.md` Phase 6 의 vendors.paddle.com 대신 **sandbox-vendors.paddle.com** 사용.

| 단계 | sandbox 액션 |
|------|-------------|
| Sign up | sandbox-vendors.paddle.com (별개 계정) |
| API key | `pdl_sdbx_apikey_...` 복사 |
| Client token | `test_...` 복사 |
| Catalog 생성 | Starter $19/mo + Pro $49/mo → price ID 복사 |
| Webhook URL | `https://storescope-api-xxxx.onrender.com/billing/webhook` (Render URL — live 와 동일) |
| Webhook secret | sandbox 별도 secret → Render env `PADDLE_WEBHOOK_SECRET` |

Render env 의 PADDLE_* 4개는 sandbox 값 입력 (API key / Client token / Starter price / Pro price). 나머지 env (DATABASE_URL, ADMIN_SECRET, BASE_URL 등) 는 변경 X.

### 랜딩 — Sandbox token swap (필수)

```bash
cd "/Users/dodokim/Documents/Claude/Projects/Auto-Biz Factory/StoreScope"
grep -n "_PADDLE_SANDBOX_TOKEN = " landing/index.html
```

찾은 라인의 `'test_REPLACE_AFTER_PADDLE_SANDBOX_SIGNUP'` 을 본인 sandbox client token (`test_xxx...`) 으로 교체. 코드 의 `_PADDLE_LIVE_TOKEN` 은 그대로 둠 (정식 출시 시 사용).

```bash
git add landing/index.html
git commit -m "chore(beta): paddle sandbox client token"
git push origin main
bash deploy/landing-deploy.sh
```

### 베타 모드 확인

라이브 (`https://ddookim.github.io/storescope/`) 새 탭 → 페이지 상단 🧪 **오픈 베타** 노란 배너 보여야 함.

배너 안 보이면 → `landing/index.html` 의 `window.STORESCOPE_BETA_MODE = true;` 확인.

### E2E sandbox 검증

본인 브라우저:
1. 랜딩 → 가격 → "Start 7-day free trial" 클릭
2. Paddle Checkout 모달 → 우측 상단 "**Test Mode**" 워터마크 확인
3. Test card 입력:
   - Number: `4242 4242 4242 4242`
   - Expiry: `12/30` (미래 어떤 날짜)
   - CVC: `100`
   - Name / Email: 본인
4. Submit → 결제 완료 → redirect → API key 발급

DB 검증:
```bash
psql "$NEON_URL" -c "SELECT email, plan, customer_id, is_active FROM api_keys ORDER BY id DESC LIMIT 3;"
```

Telegram 알림 (활성화 시) — `subscription.activated` 도착.

---

## Phase 1 — Closed Alpha (1주, 5-10명)

본인 + 신뢰하는 친구 / 지인. **외부 광고 X**. critical bug 1차 fix.

### 1-A. 자체 QA (본인, 2-3일)

| 검증 | 명령 |
|------|------|
| 30+ store X-Ray (gymshark / allbirds / glossier / bombas / mvmt / harrys / casper / warbyparker 등) | 본인 X-Ray |
| 결제 4 분기 — 성공 / 3DS / 거절 / 환불 | test card 4242 / 4000 0038 / 4000 0000 |
| /leads + /unsubscribe | curl + DB 확인 |
| Telegram alert / Sentry 정합 | 모든 분기 trigger 후 확인 |
| KO/JA i18n 잔재 0건 (의도적 keep 9 제외) | `node tools/i18n_sweep.js` |

발견 bug → fix → git push → 재 deploy.

### 1-B. 친구 / 지인 5-10명 DM (1주)

타겟: e-commerce 관심자 / Shopify 운영자 / SaaS 시도 의향자.

DM 템플릿:
```
[이름] 비공개 베타 테스트 부탁할게.

Shopify 트렌딩 상품 자동 분석 SaaS — 30분 사용 + 5문항 응답.

- 카드 결제 X (test 모드)
- 베타 후기 주면 정식 출시 후 Pro 1개월 무료

링크: https://ddookim.github.io/storescope/?utm_source=alpha&beta=[이름]

피드백 폼 (5문항): [Google Form URL]
```

### 1-C. Closed alpha 종료 조건

- Critical bug 0 (결제 fail / 데이터 손실 / 보안)
- 만족도 평균 ≥ 3/5
- 5명 이상 E2E 완료

→ Phase 2 (open beta) 진입.

---

## Phase 2 — Open Beta (2-3주, 30-50명)

외부 잠재 사용자에게 broader 노출. Paddle 여전히 **sandbox 강제** (`BETA_MODE=true` 유지).

### 2-A. 베타 모집 채널 (시간 / 효과 순)

| 채널 | 시간 | 예상 inflow | 주의 |
|------|------|-----------|------|
| **BetaList.com** (free + $129 paid) | 가입 15분 + 승인 1-2주 대기 | 100-300 visit, 10-30 sign-up | 무료는 wait list 길고, paid 가 빠름 |
| **Indie Hackers** ([Show IH](https://www.indiehackers.com/group/show-ih)) | 글 작성 30분 | 50-200 visit, 5-20 sign-up | 진솔한 build-in-public 톤, 자체 자랑 X |
| **r/SideProject** (Reddit, 215k members) | 글 30분 + 댓글 응답 | 100-500 visit | 첫 글이면 karma 부족 = remove risk |
| **r/IndieDev** / **r/SaaS** (각 100k+) | 위와 동일 | 50-200 visit | 마찬가지 |
| **Hacker News** Show HN | 글 30분 + 시점 (PT 화수목 6am) | 0-500 visit | 단기 spike, 첫 결과 안 좋으면 즉시 사라짐 |
| **Twitter / Threads / LinkedIn** (본인 SNS) | 컨텐츠 + 정기 | 본인 팔로워 수 의존 | 빌딩 인 퍼블릭 톤 |
| **Discord** (한국 SaaS / 인디 / 드롭쉬핑 그룹) | 가입 + 대화 + 비공개 DM | 10-50 | 광고 룰 엄격 — 가치 제공 먼저 |
| **Slack** 커뮤니티 (Indie Worldwide / MicroConf) | 가입 + 자기소개 | 5-30 | 의 BetaList 비슷한 톤 |

### 2-B. 권장 launch 순서 (1주)

**Day 1 (월요일)**:
- BetaList 무료 submit (1주 후 wait list 노출 시작)
- Indie Hackers "Show IH" 글
- 본인 Twitter / Threads / LinkedIn 1차 post

**Day 3 (수요일)**:
- r/SideProject 글 (HN/Reddit 모두 PT 6-9am 화수목 가장 활성)
- 본인 SNS 2차 (제품 데모 영상)

**Day 5 (금요일)**:
- 모은 베타 사용자에게 첫 weekly digest (test 발송)
- 발견 bug summary post (build in public 진정성)

**Day 7-14**:
- 베타 사용자 사용 패턴 모니터링
- 피드백 응답 + 즉시 fix

### 2-C. Show IH 글 초안

```
Title: I built a tool that watches 1,400 Shopify stores every Monday — open beta now

Body:
Spent 3+ hrs every Monday opening 30+ competitor tabs to find what's trending.
Built StoreScope — automated weekly scan + product clustering across stores.

What it does:
- 1,400+ Shopify stores indexed weekly (public products.json, opt-out respected)
- Same product across stores = one cluster + price spread + 30-day adoption curve
- Filter Trend Score 80+ for "Monday's shortlist"

Built solo. Open beta now (test mode, no real billing).

Link: https://ddookim.github.io/storescope/

Looking for:
- Dropshippers / Shopify operators feedback
- Beta-stage bugs (UI, weird data, slow queries)
- Honest "would you pay" reactions

Free Pro for 6 months for first 30 beta testers who finish the feedback form (5 questions, 5 min).

Background: solo founder, $0 marketing budget. Not VC-backed.

What's broken (transparent):
- Email digest depends on SMTP — currently optional, sending manually for now
- Render free tier = first request takes 30-60s cold start
- ...

Happy to AMA.
```

### 2-D. r/SideProject 글 (위와 비슷, 단 더 짧고 데모 영상 강조)

### 2-E. 피드백 도구 (베타 단계 필수)

| 도구 | 설정 시간 | 용도 |
|------|----------|------|
| **Google Form** (5-7 문항) | 10분 | 정량 피드백 수집 — 만족도 / 기능별 점수 / "would you pay" |
| **Sentry SDK** (Render env `SENTRY_DSN`) | 5분 가입 + env 설정 | runtime exception 자동 캡처 |
| **Telegram bot** (BotFather + chat ID) | 10분 | 모든 webhook / alert 실시간 |
| **Plausible / GA** (이미 GA 적용 — `G-3YR22V5LW4`) | 0 (이미 적용) | page_view / 결제 진입 / 결제 완료 funnel |
| **(선택) Hotjar** free (35 daily sessions) | 10분 | 사용자 클릭 heatmap + session recording |

### 2-F. 베타 사용자 모니터링 (매일 5분)

```bash
# 매일 아침 DB summary
psql "$NEON_URL" <<'EOF'
SELECT
  (SELECT COUNT(*) FROM email_leads WHERE created_at > NOW() - INTERVAL '1 day') AS leads_today,
  (SELECT COUNT(*) FROM email_leads) AS leads_total,
  (SELECT COUNT(*) FROM api_keys WHERE created_at > NOW() - INTERVAL '1 day') AS subs_today,
  (SELECT COUNT(*) FROM api_keys WHERE is_active = TRUE AND unsubscribed_at IS NULL) AS subs_active,
  (SELECT COUNT(*) FROM paddle_processed_events WHERE processed_at > NOW() - INTERVAL '1 day') AS webhook_events_today;
EOF
```

Telegram alert 받은 CRITICAL / WARNING 즉시 처리.
Sentry 에서 발견된 unexpected exception 매일 1회 review.

### 2-G. Open beta 종료 조건

| 기준 | 측정 |
|------|------|
| ✅ 30 명 이상 베타 sign-up | DB `email_leads.source` count |
| ✅ 15 명 이상 sandbox 결제 완료 (full E2E) | `api_keys` count |
| ✅ Critical bug 0 + High bug ≤ 3 | Sentry + Telegram |
| ✅ Google Form 응답 20건 이상 + 만족도 평균 ≥ 3.5/5 | Form summary |
| ✅ 마케팅용 후기 5 건 이상 (스크린샷 / 인용 가능) | 본인 정리 + 동의 |
| ✅ Demo 영상 + 5 스크린샷 + Reddit / 인스타 초안 완료 | 본인 |

→ Phase 3 (정식 출시) 진입.

---

## Phase 3 — 정식 출시 (1일 + 7일 캠페인)

### 3-A. Paddle Live 가입 + 도메인 검증 (1-2주, 미리 시작)

→ `docs/DEPLOY.md` Phase 6 + Phase 8 옵션 A (Resend) + GH Student Pack `.me` 도메인.

### 3-B. 베타 → 정식 전환 (코드 변경)

```bash
cd "/Users/dodokim/Documents/Claude/Projects/Auto-Biz Factory/StoreScope"
# landing/index.html 의 베타 mode flag 비활성:
# 찾기: window.STORESCOPE_BETA_MODE = true;
# 변경: window.STORESCOPE_BETA_MODE = false;  // 또는 줄 삭제

git add landing/index.html
git commit -m "feat(launch): 정식 출시 — BETA_MODE off, production paddle"
git push origin main
bash deploy/landing-deploy.sh
```

Render env: `PADDLE_API_KEY` / `PADDLE_CLIENT_TOKEN` / `PADDLE_*_PRICE_ID` / `PADDLE_WEBHOOK_SECRET` → live 값으로 교체.

`_PADDLE_LIVE_TOKEN` 도 live 값으로 코드 update (현재 placeholder 가 본인 것).

### 3-C. 정식 출시 캠페인 launch (Day 0)

**채널 + 시점**:
- **Reddit** r/dropship + r/shopify + r/IndieHackers / r/SaaS 글 (각 1) — PT 화수목 6-9am
- **Product Hunt** launch — 미리 hunter / supporter 동원, 0:00 PT 자정
- **인스타** 첫 릴스 + 스토리 + 피드 — 한국시간 저녁 8-10시
- **본인 SNS** — Twitter / Threads / LinkedIn 각 1 포스트

**캠페인 첫 24h 주의사항**:
- Telegram alert 모니터링 (1시간 이내 응답)
- Reddit / PH 댓글 응답 (1시간 이내)
- 사이트 health (Sentry + `/health` ping) 매시 체크

### 3-D. D+30 measurement baseline 활성

`project_kill_switch_measurement.md` 룰 — 다중 지표 교차 검증.

---

## 베타 → 정식 출시 체크리스트

### Phase 0 (인프라)
- [ ] `docs/DEPLOY.md` Step 1-10 완료 (Paddle 만 sandbox 값)
- [ ] 랜딩 sandbox token swap + deploy
- [ ] BETA 배너 라이브 표시 확인
- [ ] E2E sandbox 결제 → API key 발급 검증

### Phase 1 (Closed alpha, 1주)
- [ ] 본인 30+ store QA
- [ ] 친구 / 지인 5-10명 DM
- [ ] 5명 이상 E2E 완료
- [ ] Critical bug 0
- [ ] Google Form 응답 5건 이상

### Phase 2 (Open beta, 2-3주)
- [ ] BetaList 무료 submit
- [ ] Indie Hackers "Show IH" 글
- [ ] r/SideProject + r/IndieDev / r/SaaS 글
- [ ] 본인 SNS 컨텐츠
- [ ] 30+ sign-up, 15+ E2E
- [ ] 마케팅 자료 (Demo 영상 + 스크린샷 + 후기 5건)
- [ ] Google Form 응답 20건 + 만족도 ≥ 3.5/5

### Phase 3 (정식 출시)
- [ ] Paddle Live 가입 + 도메인 검증
- [ ] BETA_MODE = false + live token swap
- [ ] SMTP (Resend) 설정
- [ ] Reddit r/dropship + r/shopify 글
- [ ] Product Hunt launch
- [ ] 인스타 캠페인 (릴스 + 스토리 + 피드)
- [ ] D+30 measurement 활성

---

## 주의사항 (베타 단계 = 사용자 신뢰 형성기)

### 1. 약속 정직성 (표시·광고법 §3)

- 랜딩 카피의 모든 약속 (Pro 다이제스트 / X-Ray report 이메일 / API 키) 이 실 backend 에 wired up 됐는지 검증.
- 베타 단계엔 SMTP 미설정 가능 (`docs/DEPLOY.md` Phase 8 옵션 C) — 이 경우 환영 / 다이제스트 메일 자동 발송 X, 본인이 수동 처리.
- BETA 배너에 명시되어 있어 사용자 인지 가능.

### 2. 베타 사용자 데이터 보호

- DB 의 베타 lead / api_keys 는 정식 출시 후 정리 옵션 또는 marketing-allowed flag 추가.
- 베타 후기 사용 시 명시 동의 받고 인용.

### 3. 빌딩 인 퍼블릭 톤 (베타 = 진정성)

- "이건 미완성이야, 도와줘 같이 만들자" 메시지 ≫ "완벽한 SaaS"
- bug 발견 → 공개 사과 + 즉시 fix + thank you ≫ silent fix
- 베타 사용자 = 미래 evangelist + 정식 출시 시 직접 share 가능성 ↑

### 4. 베타 시간 한정

- Phase 2 open beta = 2-3주 시한. 한정하면 sign-up urgency ↑.
- 시한 끝나면 "Beta closed, official launch in 1 week" 카피 → 정식 출시 anticipation.

### 5. Paddle sandbox 한계

- Sandbox webhook = 3회 retry / 15분 (live 60회 / 3일). 베타 단계엔 짧은 retry 가 빠른 fix 유리.
- Sandbox 이메일 = 본인 계정 도메인만 수신 (외부 베타 테스터에게 자동 이메일 발송 X)
- Sandbox 환불 = 자동 10분 (수동 검증 빠름)
