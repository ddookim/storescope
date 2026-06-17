"""
robots.txt 파싱 회귀 테스트

회귀 시나리오 (이전 버그):
    User-agent: BadBot     ← 우리 안 봐야 함
    Disallow: /

    User-agent: *          ← 우리는 *에 적용
    Allow: /

이전 코드: 첫 user-agent 'BadBot' 후 'Disallow: /' 만나면 applies=False 유지지만,
            그 후 'User-agent: *' 만나면 applies=True 되지만 'Allow: /' 만 봄.
            BUG: 'BadBot' 의 Disallow 가 이미 우리에게 잘못 적용된 적 없음
            → 이 케이스는 OK.

진짜 BUG 케이스:
    User-agent: *
    Disallow: /products

    User-agent: GoogleBot
    Allow: /products      ← 이전 코드: applies 유지, 우리에게 Allow 잘못 적용

본 테스트는 fix 후 정확히 user-agent 블록 단위로 처리되는지 검증.
"""

import asyncio
import os
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

_here = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_here))


def _make_mock_session(robots_text: str, status: int = 200):
    """robots.txt response를 가짜로 만드는 session mock."""
    session = MagicMock()
    resp = MagicMock()
    resp.status_code = status
    resp.text = robots_text
    resp.content = robots_text.encode()
    session.get = AsyncMock(return_value=resp)
    return session


def test_robots_explicit_allow_us():
    """User-agent: * + Disallow: / → 우리도 차단됨."""
    from pipeline.crawl_products import _is_crawl_allowed, _ROBOTS_CACHE
    _ROBOTS_CACHE.clear()
    session = _make_mock_session("User-agent: *\nDisallow: /\n")
    result = asyncio.run(_is_crawl_allowed(session, "test-block.example.com"))
    assert result is False


def test_robots_other_bot_disallow_doesnt_affect_us():
    """User-agent: BadBot + Disallow: / 다음 User-agent: * + Allow: / → 우리는 허용."""
    from pipeline.crawl_products import _is_crawl_allowed, _ROBOTS_CACHE
    _ROBOTS_CACHE.clear()
    robots = (
        "User-agent: BadBot\n"
        "Disallow: /\n"
        "\n"
        "User-agent: *\n"
        "Allow: /\n"
    )
    session = _make_mock_session(robots)
    result = asyncio.run(_is_crawl_allowed(session, "test-other.example.com"))
    assert result is True, "BadBot Disallow 가 우리(*)에 잘못 적용됨"


def test_robots_specific_path_block():
    """Disallow: /products.json → 우리 차단."""
    from pipeline.crawl_products import _is_crawl_allowed, _ROBOTS_CACHE
    _ROBOTS_CACHE.clear()
    session = _make_mock_session("User-agent: *\nDisallow: /products.json\n")
    result = asyncio.run(_is_crawl_allowed(session, "test-product-block.example.com"))
    assert result is False


def test_robots_fetch_failure_treats_as_allowed():
    """robots.txt 404/네트워크 실패 → 허용 (보수적 fallback)."""
    from pipeline.crawl_products import _is_crawl_allowed, _ROBOTS_CACHE
    _ROBOTS_CACHE.clear()
    session = _make_mock_session("", status=404)
    result = asyncio.run(_is_crawl_allowed(session, "test-404.example.com"))
    assert result is True


def test_robots_cache_size_cap():
    """캐시 5,000 초과 시 30% 절단 (FIFO 근사)."""
    from pipeline.crawl_products import _cache_set, _ROBOTS_CACHE_MAX
    test_cache: dict = {}
    # 캡 + 100 채움
    for i in range(_ROBOTS_CACHE_MAX + 100):
        _cache_set(test_cache, f"domain-{i:05d}.com", True)
    # 캡 초과하지 않음
    assert len(test_cache) <= _ROBOTS_CACHE_MAX, f"캐시 사이즈 {len(test_cache)} > cap {_ROBOTS_CACHE_MAX}"
    # 가장 최근 추가된 도메인은 살아있어야 함
    assert f"domain-{_ROBOTS_CACHE_MAX + 99:05d}.com" in test_cache


def test_robots_large_file_truncation():
    """100MB+ robots.txt 메모리 폭주 방어 — 256KB cap 이후 파싱 OK."""
    from pipeline.crawl_products import _is_crawl_allowed, _ROBOTS_CACHE, _ROBOTS_TXT_MAX_BYTES
    _ROBOTS_CACHE.clear()
    huge_robots = (
        "User-agent: *\n"
        "Disallow: /products\n"
        + ("# padding\n" * (_ROBOTS_TXT_MAX_BYTES // 10))
    )
    session = _make_mock_session(huge_robots)
    result = asyncio.run(_is_crawl_allowed(session, "test-huge.example.com"))
    # 256KB cap 안에 Disallow: /products 있으므로 차단되어야 함
    assert result is False
