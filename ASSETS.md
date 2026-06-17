# StoreScope Asset Map

이번 자율 진행 산출물 인덱스 + 사용 시점 + Path 매핑.

## Path × Asset 매트릭스

| Asset | Path A (CONTINUE 22%) | Path B (PIVOT 45-55%) | Path C (ARCHIVE 22%) | Path D (EMERGENCY 5-10%) |
|---|---|---|---|---|
| `services/weekly_digest.py` | **핵심** (유료 산출물) | 사이드 가치 | — | — |
| `services/xray_report.py` | **핵심** (lead 가치 이행) | 사이드 (브랜드 owner 활용 가능) | — | — |
| `services/counterfeit_report.py` | — | **핵심** ($499 무기) | — | — |
| `scripts/brand_scan.py` | — | **핵심** (콜드메일 자동화) | — | — |
| `scripts/build_seo_pages.py` | 사이드 (검색 유입) | 사이드 (`/brand/*` 페이지) | — | — |
| `scripts/export_dataset.py` | — | — | **핵심** (HF 업로드) | 백업 |
| `pages/01_Admin_Dashboard.py` | 모니터링 | D+30 자동 판정 | — | 수동 검토 |
| `outreach/cold_email_sequences.md` | — | **핵심** (EMAIL #1/#2/#3) | — | — |
| `tests/test_paddle_*` | 회귀 차단 | 회귀 차단 | — | — |
| `deploy/verify_landing.sh` + `.github/workflows/verify_landing.yml` | 회귀 차단 | 회귀 차단 | 회귀 차단 | — |
| `.github/workflows/weekly_pipeline.yml` | **핵심** (해자 형성) | **핵심** | **핵심** (HF 갱신) | — |
| `.github/workflows/keep_warm.yml` | 인프라 | 인프라 | — | — |

## 즉시 사용 예시 (D+30 자동 분기 후)

### Path A 가동 시 (유료 ≥ 3 AND 이메일 ≥ 200)
```bash
# 매주 일요일 자동 — GitHub Actions weekly_pipeline.yml이 처리
# 수동 미리보기:
DATABASE_URL=postgresql:///storescope PYTHONPATH=. python services/weekly_digest.py --plan pro --limit 50
# Streamlit 모니터링:
streamlit run pages/01_Admin_Dashboard.py
```

### Path B 가동 시 (유료 0-2 AND DAU ≥ 20)
```bash
# 마스터플랜 §5 T+0 ~ T+72h 플랜
# T+50: Apollo로 25 DTC 브랜드 추출 → brands.txt 작성
python scripts/brand_scan.py --batch brands.txt --min-stores 5
# T+54: 콜드메일 발송 (outreach/cold_email_sequences.md EMAIL #1)
# T+58: Hunter.io 추가 25 + 보충
# T+62: LinkedIn 직접 10
# 출력물 → cold_email_sequences.md 의 자리표시자 채우기
```

### Path C 가동 시 (유료 0-2 AND DAU < 20)
```bash
# Hugging Face dataset upload (분기별 갱신 cron)
python scripts/export_dataset.py --output out/storescope-$(date +%Y-W%V)
cd out/storescope-*
pip install huggingface-cli
huggingface-cli upload ddookim/storescope-shopify-cross-store .
# 학업 집중 모드 — weekly_pipeline만 가동, 코드 변경 안 함
```

### Path D 가동 시 (Paddle 동결 또는 채널 밴)
```bash
# 사전 등록만 — 마스터플랜 §6에 따라 LemonSqueezy 백업 계정 활성
# 본 산출물은 직접 영향 없음 (랜딩 결제 버튼 1줄 교체로 5분 전환)
```

## 회귀 차단 게이트 (매 push 자동)

```bash
# 로컬 검증:
bash deploy/verify_landing.sh
DATABASE_URL=postgresql:///storescope PYTHONPATH=. pytest tests/ -v

# GitHub Actions 자동:
# - verify_landing.yml: landing/* push 시
# - weekly_pipeline.yml: 매주 일요일 23:00 UTC
# - keep_warm.yml: 14분마다 (Render free 15분 sleep 직전)
```

## 사용자 액션 의존도 0 — 자율 산출물만 (이번 라운드)

| 항목 | 자율 가능 | 사용자 필수 |
|---|---|---|
| 코드 생성 + 테스트 + 게이트 | ✅ 13개 산출물 모두 | — |
| GH Actions 활성 | ✅ workflow 파일 git에 있음 | — |
| Hugging Face dataset 생성 | ✅ 로컬 export 완료 | 실제 업로드는 사용자 (HF 계정 필요) |
| Sentry / Render / Neon 설정 | — | ✅ 사용자만 가능 |
| 토큰 revoke / sudo upgrade | — | ✅ 사용자만 가능 |
| GH Student Pack 신청 | — | ✅ 사용자만 가능 |

## 산출물 LOC 누계 (이번 자율 라운드)

| 파일 | LOC | 역할 |
|---|---|---|
| `services/counterfeit_report.py` | 333 | Brand IP PDF 생성기 |
| `services/weekly_digest.py` | 303 | Paid digest HTML+JSON |
| `services/xray_report.py` | ~210 | X-Ray report email |
| `scripts/brand_scan.py` | 171 | Brand scan CLI |
| `scripts/build_seo_pages.py` | 369 | Programmatic SEO |
| `scripts/export_dataset.py` | ~350 | Path C HF dataset |
| `pages/01_Admin_Dashboard.py` | 246 | KPI + D+30 판정 |
| `outreach/cold_email_sequences.md` | 176 | EMAIL #1/#2/#3 |
| `deploy/verify_landing.sh` | 124 | 9 게이트 |
| `.github/workflows/verify_landing.yml` | 52 | 자동 CI |
| `.github/workflows/weekly_pipeline.yml` | 87 | 주간 cron |
| `.github/workflows/keep_warm.yml` | 38 | 슬립 방지 |
| `tests/test_paddle_idempotency.py` | 120 | Revenue-critical 회귀 |
| `tests/test_paddle_unit.py` | 70 | 기존 + 신규 보안 회귀 |
| **합계** | **~2,649** | 전체 자율 라운드 |

## 코드 안전성 확인

- Python syntax: 13/13 파일 OK
- pytest: 20/20 PASS
- verify_landing: 9/9 PASS
- E2E 검증: PDF (3p) / HTML digest (12.5KB) / SEO page (443단어 11링크) / JSONL (144k records) 모두 통과
