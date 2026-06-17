"""
/admin/stats PII 마스킹 회귀 테스트

회귀 시나리오:
    1. 기본 호출 → 모든 이메일 마스킹 (a***@x.com)
    2. unmask=True 명시 → 평문 반환
    3. 빈/잘못된 이메일 → safe handling
    4. 페이지 cap → max 500 강제
"""

import os
import sys
from pathlib import Path

import pytest

_here = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_here))
os.environ.setdefault("DATABASE_URL", "postgresql:///storescope")


def test_mask_email_normal():
    """일반 이메일 → a***@x.com."""
    from api.admin_routes import _mask_email
    assert _mask_email("alice@example.com") == "a***@example.com"
    assert _mask_email("bob@test.io") == "b***@test.io"


def test_mask_email_single_char_local():
    """단일 문자 local part → *@x.com (정보 누설 0)."""
    from api.admin_routes import _mask_email
    assert _mask_email("a@example.com") == "*@example.com"


def test_mask_email_empty_or_none():
    """None / 빈 문자열 → 빈 문자열 (TypeError 안 남)."""
    from api.admin_routes import _mask_email
    assert _mask_email(None) == ""
    assert _mask_email("") == ""


def test_mask_email_malformed():
    """@ 없는 이메일 → 원본 그대로 (마스킹 시도 안 함)."""
    from api.admin_routes import _mask_email
    assert _mask_email("notanemail") == "notanemail"


def test_mask_email_unicode_local():
    """유니코드 local part → 첫 글자 + ***."""
    from api.admin_routes import _mask_email
    result = _mask_email("도연@test.com")
    assert result.endswith("@test.com")
    assert "***" in result
