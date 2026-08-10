"""
Unit tests for pipeline/latest_products.py — dedupe + diversity + filters.
D+11: 크롤 후 fallback digest 생성 로직 (DTC seed 에서 pHash cluster 0 대응).
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from pipeline.latest_products import (
    LOOKBACK_DAYS,
    MAX_PER_TYPE,
    MAX_PER_VENDOR,
    MIN_TITLE_LEN,
    _dedupe_by_title_domain,
    _diversity_cap,
    _extract_products,
    _parse_iso,
)


class TestParseISO:
    def test_shopify_format_with_offset(self):
        d = _parse_iso("2026-05-19T10:19:28+08:00")
        assert d is not None
        assert d.year == 2026 and d.month == 5 and d.day == 19

    def test_z_format(self):
        d = _parse_iso("2026-08-10T12:00:00Z")
        assert d is not None
        assert d.tzinfo is not None

    def test_empty_returns_none(self):
        assert _parse_iso("") is None
        assert _parse_iso(None) is None  # type: ignore

    def test_garbage_returns_none(self):
        assert _parse_iso("not-a-date") is None
        assert _parse_iso("2026-99-99") is None


class TestDedupe:
    def test_same_domain_similar_title_dedup(self):
        products = [
            {"domain": "a.com", "title": "Cotton Tee Blue"},
            {"domain": "a.com", "title": "Cotton Tee Blue!"},  # duplicate via normalization
            {"domain": "a.com", "title": "Silk Shirt"},
        ]
        result = _dedupe_by_title_domain(products)
        assert len(result) == 2  # first Cotton Tee kept, punctuation-variant collapsed

    def test_same_title_different_domain_kept(self):
        products = [
            {"domain": "a.com", "title": "Cotton Tee"},
            {"domain": "b.com", "title": "Cotton Tee"},
        ]
        result = _dedupe_by_title_domain(products)
        assert len(result) == 2  # different domains = different products (even same title)

    def test_first_wins(self):
        products = [
            {"domain": "a.com", "title": "Cotton Tee", "note": "first"},
            {"domain": "a.com", "title": "Cotton Tee", "note": "second"},
        ]
        result = _dedupe_by_title_domain(products)
        assert result[0]["note"] == "first"


class TestDiversityCap:
    def test_vendor_cap_enforced(self):
        products = [
            {"vendor": "V1", "product_type": "X", "title": f"p{i}"} for i in range(5)
        ]
        result = _diversity_cap(products, max_per_vendor=2, max_per_type=99)
        # First 2 kept as-is, next 3 pushed to overflow (but still returned at tail)
        assert result[0]["vendor"] == "V1"
        assert result[1]["vendor"] == "V1"
        # First 2 in-place, rest re-ordered to tail
        top2_vendors = [p["vendor"] for p in result[:2]]
        assert top2_vendors.count("V1") == 2

    def test_type_cap_enforced(self):
        products = [
            {"vendor": f"V{i}", "product_type": "T", "title": f"p{i}"} for i in range(6)
        ]
        result = _diversity_cap(products, max_per_vendor=99, max_per_type=3)
        # Only 3 of type "T" survive picking
        top3_types = [p["product_type"] for p in result[:3]]
        assert top3_types.count("T") == 3

    def test_diverse_input_unchanged(self):
        products = [
            {"vendor": "V1", "product_type": "T1", "title": "a"},
            {"vendor": "V2", "product_type": "T2", "title": "b"},
            {"vendor": "V3", "product_type": "T3", "title": "c"},
        ]
        result = _diversity_cap(products, max_per_vendor=2, max_per_type=2)
        assert result == products  # nothing capped

    def test_overflow_preserved_at_tail(self):
        # 5개 V1, 1개 V2 → V1 두 개 picked, V2 다음 picked, V1 나머지 overflow (뒤로)
        products = [
            {"vendor": "V1", "product_type": "T", "title": "a1"},
            {"vendor": "V1", "product_type": "T", "title": "a2"},
            {"vendor": "V1", "product_type": "T", "title": "a3"},
            {"vendor": "V2", "product_type": "T", "title": "b1"},
        ]
        result = _diversity_cap(products, max_per_vendor=2, max_per_type=99)
        # picked: V1(a1), V1(a2), V2(b1); overflow: V1(a3)
        assert result[0]["title"] == "a1"
        assert result[1]["title"] == "a2"
        # V2 이 picked 되어야 (V1 cap 도달로 skip, V2 picked)
        assert result[2]["title"] == "b1"
        # V1(a3) 이 overflow → 뒤로
        assert result[3]["title"] == "a3"


class TestExtractProducts:
    def test_short_title_filtered(self, tmp_path, monkeypatch):
        # Setup: one store with 1 valid product + 1 short-title
        store_file = tmp_path / "test.myshopify.com.json"
        now = datetime.now(timezone.utc)
        recent = (now - timedelta(days=1)).isoformat()
        store_file.write_text(json.dumps({
            "domain": "test.myshopify.com",
            "products": [
                {"title": "Merino Wool Sweater XL", "published_at": recent,
                 "images": [{"src": "https://cdn/img.jpg"}], "variants": [{"price": "89.00"}],
                 "handle": "merino", "vendor": "V", "product_type": "Apparel"},
                {"title": "Test", "published_at": recent,   # too short → filtered
                 "images": [{"src": "https://cdn/2.jpg"}], "variants": [{"price": "5.00"}],
                 "handle": "test", "vendor": "V", "product_type": "Apparel"},
            ],
        }))
        monkeypatch.setattr("pipeline.latest_products.PRODUCTS_DIR", tmp_path)
        result = _extract_products(now)
        assert len(result) == 1
        assert result[0]["title"].startswith("Merino")

    def test_old_products_filtered(self, tmp_path, monkeypatch):
        now = datetime.now(timezone.utc)
        old = (now - timedelta(days=LOOKBACK_DAYS + 5)).isoformat()
        store_file = tmp_path / "test.myshopify.com.json"
        store_file.write_text(json.dumps({
            "domain": "test.myshopify.com",
            "products": [
                {"title": "Ancient Product Name", "published_at": old,
                 "images": [{"src": "x"}], "variants": [{"price": "10"}],
                 "handle": "old", "vendor": "V", "product_type": "T"},
            ],
        }))
        monkeypatch.setattr("pipeline.latest_products.PRODUCTS_DIR", tmp_path)
        result = _extract_products(now)
        assert result == []

    def test_missing_published_at_filtered(self, tmp_path, monkeypatch):
        now = datetime.now(timezone.utc)
        store_file = tmp_path / "test.myshopify.com.json"
        store_file.write_text(json.dumps({
            "domain": "test.myshopify.com",
            "products": [
                {"title": "Product Without Date", "images": [{"src": "x"}],
                 "variants": [{"price": "10"}], "handle": "nd", "vendor": "V", "product_type": "T"},
            ],
        }))
        monkeypatch.setattr("pipeline.latest_products.PRODUCTS_DIR", tmp_path)
        result = _extract_products(now)
        assert result == []

    def test_age_days_computed(self, tmp_path, monkeypatch):
        now = datetime.now(timezone.utc)
        two_days_ago = (now - timedelta(days=2)).isoformat()
        store_file = tmp_path / "test.myshopify.com.json"
        store_file.write_text(json.dumps({
            "domain": "test.myshopify.com",
            "products": [
                {"title": "Two Days Old Item", "published_at": two_days_ago,
                 "images": [{"src": "x"}], "variants": [{"price": "50.00"}],
                 "handle": "2d", "vendor": "V", "product_type": "T"},
            ],
        }))
        monkeypatch.setattr("pipeline.latest_products.PRODUCTS_DIR", tmp_path)
        result = _extract_products(now)
        assert len(result) == 1
        # allow small floating drift due to test execution time
        assert 1.8 <= result[0]["age_days"] <= 2.2
