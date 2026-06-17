"""
CORS regex 회귀 테스트 — Render 무료 URL hash suffix 호환

라운드 17 발견: ALLOWED_ORIGINS는 정확 매칭. Render free는 URL hash suffix 부여
(예: storescope-api-a3xc.onrender.com) → 정확 매칭 실패 → CORS 차단 → launch 후
Streamlit ↔ API 통신 0.

본 테스트는 regex 패턴이 모든 Render suffix 변형을 cover하는지 검증.
"""

import re
import sys
from pathlib import Path

import pytest

_here = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_here))


def _get_cors_regex_from_code() -> str:
    """api/main.py 에서 allow_origin_regex 추출."""
    main_py = (_here / "api" / "main.py").read_text()
    m = re.search(r'allow_origin_regex=r"([^"]+)"', main_py)
    assert m, "allow_origin_regex 미정의 — CORS 정확 매칭만 사용 (Render URL hash 차단)"
    return m.group(1)


def test_cors_regex_matches_render_default_pattern():
    """Render 무료 기본 URL: storescope-api.onrender.com (suffix 없음)."""
    regex = re.compile(_get_cors_regex_from_code())
    assert regex.fullmatch("https://storescope-api.onrender.com")
    assert regex.fullmatch("https://storescope-app.onrender.com")


def test_cors_regex_matches_render_hash_suffix():
    """Render 무료 hash suffix: storescope-api-a3xc.onrender.com."""
    regex = re.compile(_get_cors_regex_from_code())
    test_cases = [
        "https://storescope-api-a3xc.onrender.com",
        "https://storescope-app-b9k2.onrender.com",
        "https://storescope-api-deadbeef.onrender.com",
    ]
    for url in test_cases:
        assert regex.fullmatch(url), f"Render hash URL 미허용: {url}"


def test_cors_regex_rejects_attacker_origins():
    """공격자 도메인 차단 — 일반화 너무 넓으면 안 됨."""
    regex = re.compile(_get_cors_regex_from_code())
    test_cases = [
        "https://evil.com",
        "https://storescope.attacker.com",                 # 다른 도메인
        "https://fakestorescope-api.onrender.com",        # 다른 prefix
        "https://storescope-fakebot.onrender.com",        # 다른 service name
        "http://storescope-api.onrender.com",             # http (https만 허용)
        "https://storescope-api.onrender.com.evil.com",   # suffix attack
    ]
    for url in test_cases:
        assert not regex.fullmatch(url), f"공격자 URL 허용됨: {url}"


def test_allowed_origins_list_has_safe_defaults():
    """ALLOWED_ORIGINS 정확 매칭 리스트가 안전한 default."""
    main_py = (_here / "api" / "main.py").read_text()
    # default fallback이 localhost (dev 환경) 인지 검증
    m = re.search(r'os\.environ\.get\(\s*"ALLOWED_ORIGINS",\s*"([^"]+)"', main_py)
    assert m, "ALLOWED_ORIGINS default 누락"
    default = m.group(1)
    assert "localhost" in default, (
        f"default ALLOWED_ORIGINS '{default}' — dev 환경에서 안전한 localhost가 default여야 함"
    )
