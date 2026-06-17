"""
Trend Score 정규화 회귀 테스트 — 랜딩 약속 정합

이전 결함 (라운드 11 발견):
    weekly_digest 출력 trend_score 가 unbounded (max 2640)
    → 랜딩 "Trend Score (0-100)" 약속 위반
    → 고객 이메일에 "Trend Score: 2640" 표시 = 혼란

본 테스트 회귀 차단:
    - 모든 입력에서 출력 ∈ [0, 100]
    - 단조 증가 (raw 클수록 score 큼)
    - 0/음수 입력 안전 처리
    - 80+ 가 상위 momentum (랜딩 카피 정합)
"""

import sys
from pathlib import Path

import pytest

_here = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_here))


def test_trend_score_bounded_0_100():
    """모든 입력에서 0 ≤ score ≤ 100."""
    from services.weekly_digest import _trend_score_0_100
    # 다양한 입력
    inputs = [
        (0, 0), (1, 1), (10, 5), (100, 50), (500, 500), (1000, 1000),
        (10000, 10000), (1_000_000, 1_000_000),
    ]
    for wd, sc in inputs:
        s = _trend_score_0_100(wd, sc)
        assert 0 <= s <= 100, f"({wd}, {sc}) → {s} (범위 위반)"


def test_trend_score_monotonic():
    """입력 raw 가 클수록 score 가 크거나 같음 (단조 증가)."""
    from services.weekly_digest import _trend_score_0_100
    raws = []
    for wd in [0, 10, 100, 1000, 10000]:
        s = _trend_score_0_100(wd, 0)
        raws.append((wd, s))
    # 모든 인접 쌍 단조 증가
    for i in range(len(raws) - 1):
        assert raws[i][1] <= raws[i+1][1], f"단조 위반: {raws[i]} → {raws[i+1]}"


def test_trend_score_zero_input():
    """0 입력 → 0 score."""
    from services.weekly_digest import _trend_score_0_100
    assert _trend_score_0_100(0, 0) == 0


def test_trend_score_negative_safe():
    """음수 입력 → 0 (clamp, 예외 없음)."""
    from services.weekly_digest import _trend_score_0_100
    assert _trend_score_0_100(-5, 0) == 0
    assert _trend_score_0_100(0, -10) == 0
    assert _trend_score_0_100(-100, -100) == 0


def test_trend_score_landing_promise_80_plus():
    """랜딩 카피 '80+ signals strong momentum' 정합 — 80 이상은 희소해야 함.
    raw = 1000+ 일 때만 75+ 진입 (대형 dropshipper 신호).
    """
    from services.weekly_digest import _trend_score_0_100
    # 평균 클러스터 (5 stores, week_delta 5) — strong momentum 아님
    avg = _trend_score_0_100(5, 5)
    assert avg < 80, f"평균 클러스터가 strong momentum (>=80)으로 잘못 분류: {avg}"

    # 대형 클러스터 (week_delta 1000, store 1000) — 80+ 진입
    large = _trend_score_0_100(1000, 1000)
    assert large >= 80, f"대형 클러스터가 80+ 미달: {large}"


def test_trend_score_top_cluster_caps_at_100():
    """현 데이터셋 top (Davines 클러스터, week_delta=660 store=660) ≈ 85 점."""
    from services.weekly_digest import _trend_score_0_100
    davines_like = _trend_score_0_100(660, 660)
    # 80~95 범위 — top tier 이지만 다른 잠재 large 클러스터 여지 남김
    assert 80 <= davines_like <= 95, f"Top 클러스터 점수가 비현실적: {davines_like}"


def test_trend_score_fetch_real_data_all_valid():
    """실 DB E2E — _fetch_top_trending 출력의 모든 trend_score ∈ [0, 100]."""
    import os
    os.environ.setdefault("DATABASE_URL", "postgresql:///storescope")
    from services.weekly_digest import _fetch_top_trending
    items = _fetch_top_trending(limit=50, min_stores=3)
    if not items:
        pytest.skip("DB에 데이터 없음")
    for it in items:
        s = it["trend_score"]
        assert 0 <= s <= 100, (
            f"cluster_id={it['id']} title={it['title'][:30]} → trend_score={s} "
            f"(랜딩 약속 0-100 위반)"
        )
