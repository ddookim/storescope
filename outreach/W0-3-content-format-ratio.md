# W0-3 · 영상 포맷 비율 룰 (RR3 해소)

**문제**: 2025 TikTok/Reels/Shorts 알고리즘이 AI voiceover 영상 reach를 -40-65% 다운랭크. AI 단독 전략은 viral 확률 5-10% → 1-3%.

**룰**: 매주 5편 출고 시 **AI 영상 50% + CMO 본인 출연 영상 50%** 강제 비율 유지.

---

## 영상 포맷 2종 정의

### 포맷 A — Data-driven AI shorts (50%)

| 속성 | 값 |
|---|---|
| 길이 | 20-30초 |
| 구조 | Hook 3초 → 데이터 표시 15-20초 → CTA 5초 |
| 음성 | ElevenLabs free / OpenAI TTS (자연스러운 voice, robotic 톤 회피) |
| 화면 | StoreScope 데이터 화면 캡처 + 모션 그래픽 |
| 제작 시간 | 20-30분/편 (CapCut 템플릿 batch) |
| 사용 hook | "1 product. 660 stores. This week." / "Top 5 spiking products" |

**알고리즘 회피 기술**:
- 영상 첫 0-1초에 자막 한 줄로 hook (TTS 시작 전)
- 캡션에 #buildinpublic, #shopify, #ecommerce 등 인간 크리에이터 태그
- TTS 음성에 자연스러운 pause 삽입 (0.3-0.5초)
- 영상 끝에 CMO 본인 얼굴 1초 cameo (계정 신뢰도 가산점)

### 포맷 B — Human-presence shorts (50%)

| 속성 | 값 |
|---|---|
| 길이 | 30-60초 |
| 구조 | Talking head 또는 화면 + 본인 voiceover |
| 음성 | CMO 본인 (한국어 또는 영어, 채널별 분리) |
| 화면 | 본인 출연 + 데이터 화면 split-screen 또는 본인이 데모 |
| 제작 시간 | 45-90분/편 (촬영 + 편집) |
| 사용 hook | "Built this in 30 days, refund in 1 click" / "Why I made my refunds 24h auto-approve" |

**B 포맷 효과**: TikTok·Reels의 "personal brand" 가산 시그널. 1-2주 운영 후 채널 신뢰 baseline 형성 → 그 후 A 포맷 영상도 reach 회복.

---

## 주간 출고 스케줄

| 요일 | 포맷 | 채널 | 비고 |
|---|---|---|---|
| 월 | A — "이번 주 Top 5" | TikTok / Reels / YouTube Shorts | DB 자동 갱신 직후 |
| 화 | B — "behind the build" | TikTok / Reels | CMO 본업 작업 일상 |
| 수 | A — "X-Ray 도구 live demo" | YouTube Shorts | 5초 라이브 검색 |
| 목 | B — "솔직 후기 reaction" | TikTok / Reels | 어제 사용자 반응 코멘트 답변 |
| 금 | A — "이번 주 인사이트 1줄" | X (Twitter Video) / Reels | 데이터 인용 짧은 텍스트 영상 |

**총 5편/주 · A 3편 (60%) + B 2편 (40%)** — 첫 4주만 약간 A 편향, 5주차 이후 50:50 정규화.

---

## A/B 측정 비교 (4주차 시점)

각 영상별 KPI 누적:

| 지표 | A 평균 | B 평균 | 결정 |
|---|---|---|---|
| 첫 24h impression | ? | ? | B > A × 1.5 시 B 비중 60%로 |
| 첫 24h CTR | ? | ? | A 잘 받으면 A 유지 |
| Trial 가입 (영상 attribute) | ? | ? | LTV 가중 |
| Account follower 증가 | ? | ? | B 잘 받음 (인간 가산 시그널) |

**4주차 결정 룰**: A vs B 영상별 trial gain 비율 비교 → 70:30 이상 한쪽 우위 시 다음 4주 비율 조정. 50:50 균등 시 유지.

---

## 금지 사항

- ❌ AI voiceover만 100% 사용 (4주차까지) — 알고리즘 다운랭크 가속
- ❌ "I'm a solo founder" 언급 — 2인 체제 신뢰도 손상
- ❌ B 포맷에서 CMO가 가격 인하 약속 — §03-reply-scripts.md 룰 위반
- ❌ A 포맷에서 evidence URL 누락 — CTA에 반드시 evidence/top5-{date}.html 링크
- ❌ 동일 hook 2주 연속 사용 — 알고리즘 fatigue, freshness 압축

---

## 도구 비용 (월 합산)

| 도구 | 무료 한도 | 5편/주 = 20편/월 가능 여부 |
|---|---|---|
| ElevenLabs Free | 10k chars/월 | ✅ 충분 (편당 ~500자) |
| OpenAI TTS | $0.015/1k chars | $0.30/월 (50k chars) |
| CapCut Free | 워터마크 없음 | ✅ |
| Suno Free (BGM) | 10곡/일 | ✅ |
| Canva Free | 충분 | ✅ |
| **합계** | | **월 $0-1** |

→ M4의 "월 $0 인프라" 약속과 정합.
