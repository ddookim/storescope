"""
Storm Score V2 회귀 테스트 — D+20 알고리즘 업그레이드.

기존 trend_score = delta / avg_30d 의 6개 결함 fix 검증:
  1) age decay (오래된 클러스터 자연 demotion)
  2) velocity boost (급가속 클러스터 우대)
  3) noise 감쇄 (EMA, 단일 outlier dominate 방지)
  4) log scaling (HN sublinear, 거대 monopoly 차단)
  5) small_count penalty (sc<3 noise filter)
  6) 분모 0 안전 (분기 처리)

공식: S = log₁₀(sc+1) × EMA_α(δ) × (1 + tanh(v)) / log₁₀(age+2)
       × 0.3   if store_count < 3
"""

import math
import sys
from pathlib import Path

_here = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_here))

from pipeline.load_to_db import (  # noqa: E402
    _storm_score,
    _STORM_EMA_ALPHA,
    _STORM_SMALL_COUNT_THRESH,
    _STORM_SMALL_COUNT_FACTOR,
)


def test_storm_zero_inputs():
    """0/빈 입력 → 0.0 (예외 없음)."""
    assert _storm_score(0, 0, [], 0.0) == 0.0
    assert _storm_score(0, 5, [1, 2, 3], 30.0) == 0.0
    assert _storm_score(10, 5, [], 30.0) == 0.0


def test_storm_negative_safe():
    """음수 history → max(0, d) clamp, 예외 없음."""
    s = _storm_score(10, 5, [-3, -1, 2, 5], 30.0)
    assert s >= 0.0


def test_storm_monotonic_in_store_count():
    """동일 momentum, 큰 store_count → 큰 score (log scaling 확인)."""
    small = _storm_score(10, 5, [3, 4, 4, 5], 30.0)
    large = _storm_score(100, 5, [3, 4, 4, 5], 30.0)
    assert large > small, f"log scaling 미작동: sc=10→{small}, sc=100→{large}"


def test_storm_age_decay():
    """동일 input, 더 오래된 클러스터 → 낮은 score (Reddit gravity)."""
    young = _storm_score(50, 5, [3, 4, 5, 5], 7.0)
    middle = _storm_score(50, 5, [3, 4, 5, 5], 30.0)
    old = _storm_score(50, 5, [3, 4, 5, 5], 180.0)
    assert young > middle > old, f"age decay 미작동: 7d={young}, 30d={middle}, 180d={old}"


def test_storm_velocity_boost():
    """급가속 클러스터 > 평탄 클러스터 (tanh velocity boost)."""
    accelerating = _storm_score(10, 8, [1, 2, 4, 8], 14.0)
    flat = _storm_score(10, 5, [5, 5, 5, 5], 14.0)
    assert accelerating > flat, f"velocity boost 미작동: accel={accelerating}, flat={flat}"


def test_storm_velocity_bounded():
    """velocity tanh 으로 ∈ [-1, 1] → multiplier ∈ [0, 2]. unbounded 폭주 차단."""
    moderate = _storm_score(10, 10, [1, 2, 4, 8], 14.0)
    extreme = _storm_score(10, 10000, [1, 2, 4, 8], 14.0)
    # delta_t 가 1000x 커도 score 가 1000x 커지지 않아야 함 (tanh 포화)
    assert extreme < moderate * 3.0, (
        f"velocity unbounded: moderate={moderate}, extreme={extreme} (ratio={extreme/moderate:.1f}x)"
    )


def test_storm_small_count_penalty():
    """store_count < 3 → ×0.3 penalty (noise filter)."""
    sc_2 = _storm_score(2, 1, [1, 1, 1, 1], 7.0)
    sc_3 = _storm_score(3, 1, [1, 1, 1, 1], 7.0)
    # 같은 history, store_count 2 vs 3 — penalty 가 적용된 sc_2 < (sc_3 × log10(3)/log10(4))
    # 즉 단순히 sc_3 > sc_2 보다 큰 갭이어야 함
    raw_ratio = math.log10(3) / math.log10(4)  # sc_2 / sc_3 if no penalty
    expected_with_penalty = raw_ratio * _STORM_SMALL_COUNT_FACTOR
    actual_ratio = sc_2 / sc_3 if sc_3 > 0 else 0
    assert actual_ratio < raw_ratio, (
        f"penalty 미작동: raw_ratio={raw_ratio:.3f}, actual={actual_ratio:.3f}"
    )
    assert abs(actual_ratio - expected_with_penalty) < 0.05, (
        f"penalty factor mismatch: expected ≈ {expected_with_penalty:.3f}, got {actual_ratio:.3f}"
    )


def test_storm_ema_noise_attenuation():
    """단일 outlier week 가 4주 EMA 에 dominate 하지 않음."""
    # spike: 1개 outlier 50, 나머지 1
    spike = _storm_score(10, 1, [1, 1, 50, 1], 14.0)
    # consistent: 4주 평균 13
    consistent = _storm_score(10, 13, [10, 12, 13, 13], 14.0)
    # outlier 가 1번 있어도 consistent 가 우세해야 함 (EMA noise 감쇄)
    assert consistent > spike, (
        f"EMA noise 감쇄 실패: spike={spike}, consistent={consistent}"
    )


def test_storm_returns_non_negative():
    """모든 유효 입력에서 score >= 0."""
    cases = [
        (1, 0, [0], 1.0),
        (10, 5, [3, 4, 5], 30.0),
        (1000, 100, [50, 75, 100], 180.0),
        (5, -2, [3, 2, 1], 90.0),  # 감소 추세
    ]
    for sc, dt, dh, age in cases:
        s = _storm_score(sc, dt, dh, age)
        assert s >= 0, f"음수 score: ({sc}, {dt}, {dh}, {age}) → {s}"


def test_storm_constants_sane():
    """알고리즘 상수가 합리적 범위."""
    assert 0 < _STORM_EMA_ALPHA <= 1, f"EMA alpha 비정상: {_STORM_EMA_ALPHA}"
    assert _STORM_SMALL_COUNT_THRESH >= 2, f"small threshold 비정상: {_STORM_SMALL_COUNT_THRESH}"
    assert 0 < _STORM_SMALL_COUNT_FACTOR < 1, f"penalty factor 비정상: {_STORM_SMALL_COUNT_FACTOR}"


def test_storm_real_world_scenario():
    """실 시나리오 sanity — 4 클러스터 비교.

    case A: 신규 dropshipper hit (12 stores, 4주 [1,2,3,4], 14일) — sustained growth
    case B: 정체 거대 클러스터 (200 stores, 4주 [2,2,2,2], 180일) — stale large
    case C: 신규 emerging (10 stores, 4주 [0,0,0,30] → 마지막 polish) — winsor 후 [0,0,0,15]
    case D: 안정 중형 클러스터 (50 stores, 4주 [3,4,5,6], 90일) — steady mid-tier

    어설션:
        - 모든 score > 0 (의미 있는 값)
        - A (sustained growth) > B (정체 거대) — gravity decay 우위
        - C (last-week burst, winsor 후 emerging signal) 는 알고리즘이 trend 로 잡는게 의도.
          단 EMA 가 1 step 만 spike 반영하므로 D 의 sustained growth 와 비슷 또는 우세 가능 — 약 어설션.
    """
    a = _storm_score(12, 4, [1, 2, 3, 4], 14.0)
    b = _storm_score(200, 2, [2, 2, 2, 2], 180.0)
    c = _storm_score(10, 30, [0, 0, 0, 30], 14.0)
    d = _storm_score(50, 6, [3, 4, 5, 6], 90.0)

    assert all(x > 0 for x in (a, b, c, d)), f"a={a} b={b} c={c} d={d}"
    # sustained growth (A) > 정체 거대 (B) — age decay 위주 검증
    assert a > b, f"sustained growth (A={a}) < 정체 거대 (B={b}) — age decay 미작동"
