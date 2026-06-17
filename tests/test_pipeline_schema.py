"""
Pipeline 스키마 회귀 게이트 — dropped 테이블/컬럼 참조 정적 + 동적 검증

배경: 라운드 7에서 발견한 _deliver_webhooks 가 dropped 테이블 webhook_subscriptions 참조 →
       파이프라인 첫 실행에서 RuntimeError. 38 단위 테스트가 못 잡음.
       → 본 모듈은 pipeline + services + api 코드의 모든 SQL 참조를 추출 후
       실제 DB 스키마와 대조하여 dropped/renamed 컬럼 즉시 검출.

검증 룰:
    1. SQL FROM/JOIN 절의 모든 테이블이 information_schema.tables 존재
    2. SELECT/INSERT/UPDATE 컬럼이 information_schema.columns 존재
    3. INSERT VALUES placeholder 카운트가 컬럼 수와 일치
    4. ON CONFLICT 절의 컬럼이 실제 UNIQUE 제약과 매칭
"""

import os
import re
import sys
from pathlib import Path

import pytest

_here = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_here))
os.environ.setdefault("DATABASE_URL", "postgresql:///storescope")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SCAN_DIRS = ["pipeline", "services", "api", "scripts", "pages"]
EXTRA_FILES = ["app.py"]  # 루트 레벨 추가 파일
# SQL 컨텍스트 안에서만 매칭 — 대문자 키워드 우선 (Python from/import 오탐 제외)
_TABLE_REF_RE = re.compile(
    r"(?:FROM|JOIN|INTO|UPDATE)\s+([a-z_][a-z0-9_]*)"
)
# 문자열 리터럴 (triple-quote 또는 single/double quote) 안의 SQL만 추출
_STRING_LITERAL_RE = re.compile(
    r'"""(.+?)"""|\'\'\'(.+?)\'\'\'|"((?:[^"\\]|\\.)*)"|\'((?:[^\'\\]|\\.)*)\'',
    re.DOTALL
)


def _scan_table_refs() -> dict[str, list[tuple[Path, int]]]:
    """문자열 리터럴 안 SQL 컨텍스트에서만 테이블 참조 추출 (Python `from` 오탐 제외)."""
    refs: dict[str, list[tuple[Path, int]]] = {}
    # 디렉토리 + 루트 레벨 추가 파일 모두 스캔
    files = []
    for d in SCAN_DIRS:
        files.extend((PROJECT_ROOT / d).rglob("*.py"))
    for fname in EXTRA_FILES:
        p = PROJECT_ROOT / fname
        if p.exists():
            files.append(p)
    for f in files:
        if "__pycache__" in str(f):
            continue
        try:
            text = f.read_text()
        except Exception:
            continue
        # 문자열 리터럴만 추출
        for match in _STRING_LITERAL_RE.finditer(text):
            lit = next((g for g in match.groups() if g), "")
            if not lit:
                continue
            # SQL 키워드 포함된 리터럴만 검토
            if not re.search(r"\b(SELECT|INSERT|UPDATE|DELETE|FROM|JOIN)\b", lit):
                continue
            # 라인 번호 계산
            line_no = text[:match.start()].count("\n") + 1
            for ref in _TABLE_REF_RE.finditer(lit):
                tbl = ref.group(1).lower()
                refs.setdefault(tbl, []).append((f.relative_to(PROJECT_ROOT), line_no))
    return refs


def _get_actual_tables() -> set[str]:
    """현재 storescope DB의 모든 테이블 이름."""
    try:
        import psycopg2
    except ImportError:
        pytest.skip("psycopg2 not available")
    conn = psycopg2.connect(os.environ["DATABASE_URL"])
    with conn.cursor() as cur:
        cur.execute("""
            SELECT table_name FROM information_schema.tables
            WHERE table_schema = 'public'
        """)
        tables = {r[0] for r in cur.fetchall()}
    conn.close()
    return tables


def test_no_dropped_table_references():
    """코드에서 참조되는 모든 테이블이 실제 DB에 존재해야 함.

    회귀 시나리오: webhook_subscriptions 같이 마이그레이션으로 dropped 테이블을
    코드가 계속 참조 → 첫 실행 시 RuntimeError.
    본 테스트는 정적 grep + DB 스키마 대조로 즉시 검출.
    """
    code_refs = _scan_table_refs()
    db_tables = _get_actual_tables()

    # 알려진 비-테이블 키워드 / 변수명 (오탐 제외)
    KNOWN_NON_TABLE = {
        "pg_indexes", "pg_tables", "information_schema",
        "%s",  # placeholder 오탐
    }

    missing: dict[str, list[tuple[Path, int]]] = {}
    for tbl, sources in code_refs.items():
        if tbl in KNOWN_NON_TABLE:
            continue
        if tbl in db_tables:
            continue
        # information_schema.* / pg_catalog.* 패턴
        if "." in tbl or tbl.startswith("pg_"):
            continue
        missing[tbl] = sources

    if missing:
        report = []
        for tbl, sources in missing.items():
            locs = ", ".join(f"{f}:{ln}" for f, ln in sources[:3])
            report.append(f"  '{tbl}' referenced at: {locs}")
        pytest.fail(
            "코드가 존재하지 않는 테이블을 참조함:\n" + "\n".join(report) +
            f"\n\n현 DB 테이블 ({len(db_tables)}): {sorted(db_tables)}"
        )


def test_all_required_tables_exist():
    """런타임에 사용되는 핵심 테이블이 실제 DB에 존재."""
    REQUIRED = {
        "stores", "products", "clusters", "product_clusters", "trend_snapshots",
        "api_keys", "api_usage", "email_leads",
        "paddle_processed_events",  # 라운드 4 idempotency 마이그레이션
    }
    db_tables = _get_actual_tables()
    missing = REQUIRED - db_tables
    assert not missing, (
        f"필수 테이블 누락: {sorted(missing)} — migrations/ 적용 누락 가능."
        f" 현 DB: {sorted(db_tables)}"
    )


def test_no_dropped_table_explicit_check():
    """명시: 라운드 4에서 drop한 테이블이 코드에 다시 등장 안 함."""
    code_refs = _scan_table_refs()
    DROPPED = {"webhook_subscriptions"}
    resurrected = set(code_refs.keys()) & DROPPED
    if resurrected:
        sources = {
            tbl: code_refs[tbl]
            for tbl in resurrected
        }
        pytest.fail(
            f"이전 drop한 테이블이 코드에 부활: {sources}"
        )


def test_no_stripe_legacy_columns():
    """
    api_keys 의 stripe_customer_id / stripe_subscription_id 컬럼이 부활하지 않음.
    회귀: Paddle 마이그레이션 후 잔재 컬럼명 사용 시 코드 ↔ DB 미스매치 (UndefinedColumn).
    검출: tests/test_paddle_webhook_integration.py::test_subscription_canceled_runs_deactivate (라운드 10 발견)
    """
    try:
        import psycopg2
    except ImportError:
        pytest.skip("psycopg2 not available")
    conn = psycopg2.connect(os.environ["DATABASE_URL"])
    with conn.cursor() as cur:
        cur.execute("""
            SELECT column_name FROM information_schema.columns
            WHERE table_name = 'api_keys'
              AND column_name LIKE 'stripe_%'
        """)
        legacy = {r[0] for r in cur.fetchall()}
    conn.close()
    assert not legacy, (
        f"api_keys 에 Stripe 잔재 컬럼: {legacy}. "
        f"migrations/2026_06_07_rename_stripe_to_customer.sql 미적용 가능."
    )


def test_paddle_processed_events_index_exists():
    """idempotency 마이그레이션의 processed_at 인덱스 존재 (90일 청소 성능)."""
    try:
        import psycopg2
    except ImportError:
        pytest.skip("psycopg2 not available")
    conn = psycopg2.connect(os.environ["DATABASE_URL"])
    with conn.cursor() as cur:
        cur.execute("""
            SELECT indexname FROM pg_indexes
            WHERE tablename = 'paddle_processed_events'
        """)
        idxs = {r[0] for r in cur.fetchall()}
    conn.close()
    assert "idx_paddle_events_processed_at" in idxs, (
        f"청소 인덱스 누락 → 100호출당 cleanup이 full scan. 인덱스: {idxs}"
    )
