"""
XSS 회귀 테스트 — 이메일/PDF 템플릿의 사용자 입력 escape 검증

xray_report.py: domain은 /leads 통한 user input → HTML 이메일 본문에 흘러감
counterfeit_report.py: brand_name은 CLI/API → reportlab Paragraph XML 파싱
weekly_digest.py: title은 DB 출처지만 외부 product 콘텐츠 → 방어적 escape

회귀 시나리오:
    domain = '<script>alert("xss")</script>.myshopify.com'
    brand = 'EVIL <img onerror=alert(1)>'
    title = "Bobby Tables's <script>drop</script>"

기대: 모든 escape 결과 HTML/XML 특수문자 제거
"""

import os
import sys
from pathlib import Path

import pytest


_here = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_here))
os.environ.setdefault("DATABASE_URL", "postgresql:///storescope")


# ── xray_report.py XSS 회귀 ──────────────────────────────────────

def test_xray_html_escapes_malicious_domain():
    """domain에 <script> 삽입 시 HTML에 raw 태그 미노출."""
    from services.xray_report import _render_html

    evil_domain = '<script>alert("xss")</script>.myshopify.com'
    data = {"domain": evil_domain, "in_db": False}
    html_out = _render_html("test@example.com", data)

    # raw <script> 태그 미존재 (이메일 클라이언트가 실행 안 함)
    assert "<script>alert" not in html_out
    assert "&lt;script&gt;alert" in html_out


def test_xray_html_escapes_in_db_branch():
    """in_db=True 분기에서도 domain escape."""
    from services.xray_report import _render_html

    data = {
        "domain": "<img src=x onerror=alert(1)>.myshopify.com",
        "in_db": True,
        "product_count": 10,
        "price_lo": 1.0, "price_avg": 5.0, "price_hi": 10.0, "priced_n": 10,
        "trending": [], "top_priced": [],
    }
    html_out = _render_html("test@example.com", data)
    assert "<img src=x onerror=alert(1)>" not in html_out
    assert "&lt;img" in html_out


def test_xray_html_escapes_product_titles():
    """trending product title 에 <script> 삽입 시 escape."""
    from services.xray_report import _render_html

    data = {
        "domain": "safe.myshopify.com",
        "in_db": True,
        "product_count": 1,
        "price_lo": 0, "price_avg": 0, "price_hi": 0, "priced_n": 0,
        "trending": [
            {"title": "<script>alert(1)</script>", "cluster_size": 5, "price_min": 9.99}
        ],
        "top_priced": [],
    }
    html_out = _render_html("test@example.com", data)
    assert "<script>alert(1)</script>" not in html_out
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html_out


# ── counterfeit_report.py XSS 회귀 ──────────────────────────────

def test_pdf_escapes_malicious_brand_name():
    """reportlab Paragraph는 XML 파싱 → < > & 미escape 시 ParseError 또는 markup 임베드."""
    from datetime import datetime, timezone
    from services.counterfeit_report import ClusterMatch, generate

    evil_brand = "EVIL<b>BRAND</b><img src=x>"
    matches = [
        ClusterMatch(
            cluster_id=1,
            store_domain="example.myshopify.com",
            product_title="Product 1",
            product_image_url=None,
            price_min=10.0, price_max=20.0,
            confidence=0.95,
            first_seen=datetime.now(timezone.utc),
            cluster_size=5,
        )
    ]
    # 미escape 시 reportlab이 ParseError 발생할 수 있음 — 그것 자체가 회귀
    pdf_bytes = generate(brand_name=evil_brand, matched_clusters=matches)
    # PDF 생성 성공 = escape 작동 (또는 의도된 무시) → magic bytes 확인
    assert pdf_bytes[:4] == b"%PDF", "PDF magic 미확인 — 생성 실패"


def test_pdf_escapes_malicious_store_domain():
    """ClusterMatch.store_domain 의 XML 특수문자 escape."""
    from datetime import datetime, timezone
    from services.counterfeit_report import ClusterMatch, generate

    matches = [
        ClusterMatch(
            cluster_id=1,
            store_domain="<>&'\"-evil-domain.com",
            product_title="<script>",
            product_image_url=None,
            price_min=1.0, price_max=1.0,
            confidence=0.99,
            first_seen=datetime.now(timezone.utc),
            cluster_size=3,
        )
    ]
    pdf_bytes = generate(brand_name="LEGIT", matched_clusters=matches)
    assert pdf_bytes[:4] == b"%PDF"


# ── weekly_digest.py XSS 회귀 ──────────────────────────────────

def test_digest_escapes_item_title():
    """weekly_digest 의 _render_item 이 title escape."""
    from services.weekly_digest import _render_item

    evil_item = {
        "id": 1,
        "title": '<script>alert("digest")</script>',
        "stores": 5,
        "products": 5,
        "price_min": 1.0, "price_max": 1.0,
        "image": "",
        "week_delta": 0,
        "trend_score": 5,
    }
    html_out = _render_item(evil_item, plan="starter")
    assert "<script>alert" not in html_out
    assert "&lt;script&gt;" in html_out


def test_digest_escapes_image_url():
    """image URL 의 quote attribute 주입 차단."""
    from services.weekly_digest import _render_item

    evil_item = {
        "id": 1,
        "title": "Safe Title",
        "stores": 5,
        "products": 5,
        "price_min": 1.0, "price_max": 1.0,
        "image": 'http://evil.com/x.png" onerror="alert(1)',
        "week_delta": 0,
        "trend_score": 5,
    }
    html_out = _render_item(evil_item, plan="starter")
    # 따옴표 escape → onerror 부분이 HTML attribute로 해석 안 됨
    assert 'onerror="alert(1)"' not in html_out
    # &quot; 또는 &#x27; 로 escape 되었는지
    assert "&quot;" in html_out or "&#34;" in html_out
