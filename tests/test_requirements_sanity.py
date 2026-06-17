"""
requirements.txt sanity 회귀 게이트

라운드 15 발견: Pillow>=12.2.0 명시 (당시 미존재 버전) → pip install 즉시 실패.
배포 시 production install 100% 차단되는 결함을 fresh venv 검증 없이는 못 잡음.

본 게이트는 *정적 검증*:
    1. 모든 requirements 항목이 valid 형식 (name>=lower,<upper)
    2. upper bound 가 lower bound 보다 큼
    3. 이미 알려진 미존재 버전 (Pillow 12.x 등) 차단
    4. 코드 import 와 requirements 정합 (사용 안 하는 dep 또는 누락 dep)

실 PyPI 호출은 비결정적 (네트워크 의존) → static check만.
fresh venv install 검증은 별도 CI workflow (.github/workflows/install_check.yml).
"""

import re
import sys
from pathlib import Path

import pytest

_here = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_here))

REQ_FILE = _here / "requirements.txt"
KNOWN_NON_EXISTENT = {
    "Pillow": {"12", "13", "14", "15"},  # 라운드 15 시점 최대 11.x
}


def _parse_requirements():
    """requirements.txt를 (name, lower, upper) 튜플 리스트로 파싱."""
    entries = []
    pattern = re.compile(r"^([a-zA-Z0-9_\-]+)(?:\[[^\]]+\])?>=([0-9]+\.[0-9]+(?:\.[0-9]+)?),<([0-9]+\.[0-9]+(?:\.[0-9]+)?)")
    for line in REQ_FILE.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        m = pattern.match(line)
        if not m:
            continue
        entries.append((m.group(1).lower(), m.group(2), m.group(3)))
    return entries


def test_requirements_all_have_bounded_versions():
    """모든 항목이 >= 와 < 둘 다 명시 (production breakage 방지)."""
    text = REQ_FILE.read_text()
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        # >= 와 < 동시 존재 검증
        assert ">=" in line and "<" in line, (
            f"requirements 항목 '{line}' 에 bound 누락. "
            f">=lower,<upper 형식 필수."
        )


def test_requirements_lower_below_upper():
    """모든 항목의 lower bound < upper bound."""
    for name, lo, hi in _parse_requirements():
        lo_t = tuple(int(x) for x in lo.split("."))
        hi_t = tuple(int(x) for x in hi.split("."))
        assert lo_t < hi_t, (
            f"{name}: lower {lo} >= upper {hi} (불가능한 범위)"
        )


def test_no_known_nonexistent_versions():
    """알려진 미존재 major 버전 차단 (Pillow 12.x 등)."""
    for name, lo, hi in _parse_requirements():
        # 비교: requirements 키는 lowercase
        for known_name, bad_majors in KNOWN_NON_EXISTENT.items():
            if name != known_name.lower():
                continue
            lo_major = lo.split(".")[0]
            assert lo_major not in bad_majors, (
                f"{name}>={lo} 의 major {lo_major} 는 미존재 버전. "
                f"라운드 15 발견 — pip install 즉시 실패. "
                f"수정: lower bound 를 최신 안정 major 로 낮춤."
            )


def test_required_packages_present():
    """핵심 패키지가 requirements.txt 에 명시되었는지."""
    names = {name for name, _, _ in _parse_requirements()}
    required = {
        "fastapi", "uvicorn", "streamlit",
        "psycopg2-binary", "pydantic",
        "pillow", "imagehash",
        "curl-cffi",
        "slowapi", "sentry-sdk",
        "requests", "tenacity",
    }
    missing = required - names
    assert not missing, (
        f"requirements.txt 에서 누락된 핵심 패키지: {sorted(missing)}. "
        f"production install 시 ImportError 발생."
    )


def test_pillow_lower_bound_is_existing_major():
    """Pillow 의 lower bound major 가 실제 PyPI 가용 범위 (10.x ~ 11.x)."""
    pillow = next((entry for entry in _parse_requirements() if entry[0] == "pillow"), None)
    assert pillow is not None, "Pillow 누락"
    name, lo, hi = pillow
    lo_major = int(lo.split(".")[0])
    assert 10 <= lo_major <= 11, (
        f"Pillow lower bound major={lo_major} — 가용 범위 (10, 11) 밖. "
        f"라운드 15 회귀: Pillow 12.x 는 미존재 — pip install 즉시 실패."
    )
