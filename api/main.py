"""
StoreScope — FastAPI 서버
============================
실행:
    uvicorn api.main:app --reload --port 8000

엔드포인트:
    GET /trending              - 트렌드 상품 목록
    GET /store/{domain}        - 스토어 상세 + 상품 목록
    GET /cluster/{cluster_id}  - 클러스터 상세 + 동일 상품 판매 스토어
    GET /search?q=...          - 상품명 검색
    GET /health                - 헬스체크
"""

import csv
import io
import logging
import os
import psycopg2.extras

# Sentry SDK — Render 휘발성 로그 대체. SENTRY_DSN 미설정 시 자동 no-op.
import sentry_sdk
from sentry_sdk.integrations.fastapi import FastApiIntegration
_SENTRY_DSN = os.environ.get("SENTRY_DSN", "")
if _SENTRY_DSN:
    sentry_sdk.init(
        dsn=_SENTRY_DSN,
        # Free tier 5K errors/mo. traces_sample_rate=0.01 = 1% 샘플링으로 5M spans 한도 안전.
        traces_sample_rate=float(os.environ.get("SENTRY_TRACES_SAMPLE_RATE", "0.01")),
        environment=os.environ.get("RENDER_SERVICE_NAME", "local"),
        integrations=[FastApiIntegration()],
        # PII (개인정보 식별) 차단 — Paddle 고객 이메일이 Sentry로 흘러가지 않게.
        send_default_pii=False,
    )

# slowapi rate limiter — in-memory 백엔드 (Redis 불필요).
# 무료티어 DDoS + 봇 폭주 1차 방어.
# 한도는 env var로 분리 — 마케팅 캠페인 burst 시 임시 상향 가능.
# Render 1 worker 영구 가정 (workers 명시 안 함). multi-worker 시 limit = N × 정의값.
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

RATE_LIMIT_TRENDING  = os.environ.get("RATE_LIMIT_TRENDING",  "60/minute")
RATE_LIMIT_LEADS     = os.environ.get("RATE_LIMIT_LEADS",      "5/minute")
RATE_LIMIT_OPTOUT    = os.environ.get("RATE_LIMIT_OPTOUT",    "10/minute")  # typo retry 완화
RATE_LIMIT_FRESHNESS = os.environ.get("RATE_LIMIT_FRESHNESS", "30/minute")  # 랜딩 핑 무방어 차단

limiter = Limiter(key_func=get_remote_address, default_limits=["200/minute"])
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Optional

from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from api.paddle_routes import router as billing_router
from api.admin_routes import router as admin_router
from api.auth import get_conn, require_api_key

logger = logging.getLogger(__name__)

ALLOWED_ORIGINS = os.environ.get(
    "ALLOWED_ORIGINS", "http://localhost:8501"
).split(",")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # OPTIMIZE: ALTER TABLE 제거 — migrations/*.sql 일회성으로 이전 (cold wake당 1회 무의미 DDL 제거).
    # 풀 사전 warm-up만 유지 (cold start latency 감소).
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
        logger.info("DB pool warmed up")
    except Exception as e:
        logger.error("DB warmup failed: %s", e)
    yield


app = FastAPI(
    title="StoreScope API",
    description="Shopify 크로스스토어 제품 인텔리전스",
    version="0.1.0",
    lifespan=lifespan,
)

# slowapi 등록 — RateLimitExceeded는 자동 429 응답으로 변환.
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# OPTIMIZE: GZip 압축으로 JSON 응답 ~70% 축소. Render free 대역폭 절감 + cold start 응답 시간 ↓.
# minimum_size=1000: 작은 응답은 GZip 오버헤드 회피.
from fastapi.middleware.gzip import GZipMiddleware
app.add_middleware(GZipMiddleware, minimum_size=1000)

# FIX 2026-06-08: Render 무료 서비스는 URL hash suffix 부여 (storescope-app-XXXX.onrender.com).
# 정확 매칭만 쓰면 사용자가 launch 후 실 URL을 ALLOWED_ORIGINS에 수동 추가해야 함 (UX 사고).
# regex 패턴 추가 → storescope-* 서브도메인 모두 허용 (CORS 차단 자동 회피).
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_origin_regex=r"https://storescope-(api|app)(-[a-z0-9]+)?\.onrender\.com",
    allow_methods=["GET", "POST"],
    allow_headers=["X-API-Key", "Content-Type", "Accept"],
)

app.include_router(billing_router)
app.include_router(admin_router)


class ApiResponse(BaseModel):
    success: bool
    data: object
    error: Optional[str] = None

# FIX: SQL ORDER BY 절을 화이트리스트 dict로 관리.
# Pydantic pattern 검증이 있어도 f-string SQL은 나쁜 패턴 — 향후 검증 우회 시 SQL injection 취약점으로 전환.
# D+20 V2: rising 정렬을 momentum_score (Storm Score V2) 로 교체.
# 기존 (week_delta * 3 + store_count) 의 결함 6개 fix — pipeline/load_to_db._storm_score 참조.
_SORT_CLAUSES: dict[str, str] = {
    "rising":  "COALESCE(ts.momentum_score, 0) DESC, ts.week_delta DESC NULLS LAST",
    "popular": "c.store_count DESC, ts.week_delta DESC NULLS LAST",
}

# FIX: domain 형식 검증 정규식 — 파라미터화 쿼리라 SQL injection은 없으나
# 비정상 입력(경로 순회 시도 등)이 DB 로그에 기록되는 것을 방지.
import re as _re
_DOMAIN_RE = _re.compile(r"^[a-z0-9][a-z0-9\-]{0,61}[a-z0-9]?\.myshopify\.com$")


# ── /trending 모듈-레벨 TTL 캐시 ─────────────────────────────────
# OPTIMIZE: 데이터는 주간 갱신 (run_pipeline 토요일 02:55 cron).
# 5분 TTL이면 데이터 신선도 영향 0, DB 부하 90%+ 절감.
# Render free = 1 worker = process-local dict 충분 (별도 Redis 불필요).
# 키 = (limit, min_stores, sort). 캐시 사이즈 자연 한계 ~50 (limit 1-100 × min_stores 2-N × sort 2).
import threading as _threading
import time as _time
_TRENDING_CACHE: dict[tuple, tuple[float, list]] = {}
_TRENDING_TTL_SEC = 300  # 5분
_TRENDING_LOCK = _threading.Lock()


def _trending_cache_get(key: tuple) -> Optional[list]:
    now = _time.time()
    entry = _TRENDING_CACHE.get(key)
    if not entry:
        return None
    ts, val = entry
    if now - ts > _TRENDING_TTL_SEC:
        # 만료. 명시 제거로 메모리 누적 방지.
        with _TRENDING_LOCK:
            _TRENDING_CACHE.pop(key, None)
        return None
    return val


def _trending_cache_set(key: tuple, val: list) -> None:
    with _TRENDING_LOCK:
        # 캐시 무한 증가 방지: > 100 항목이면 절반 제거 (가장 오래된 것부터)
        if len(_TRENDING_CACHE) > 100:
            sorted_keys = sorted(_TRENDING_CACHE.items(), key=lambda kv: kv[1][0])
            for k, _ in sorted_keys[:50]:
                _TRENDING_CACHE.pop(k, None)
        _TRENDING_CACHE[key] = (_time.time(), val)


# ── 헬스체크 ────────────────────────────────────────────────
@app.get("/health")
def health():
    # OPTIMIZE: 경량 헬스 — UptimeRobot 5분 핑(일 288회)이 DB 안 거치도록.
    # 프로세스 살아있음 + middleware 동작 = 충분. Render의 healthCheckPath 도 동일 의미.
    return {"status": "ok"}


@app.get("/health/db")
def health_db():
    # 깊은 헬스 — 수동 검사 + 진단용. UptimeRobot이 호출 안 함.
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
        return {"status": "ok", "db": "reachable"}
    except Exception:
        logger.error("DB health check failed", exc_info=True)
        raise HTTPException(status_code=503, detail="DB 연결 실패")


FRESHNESS_WARNING_HOURS = 24
FRESHNESS_STALE_HOURS = 72


# /health/freshness 호출 최적화: 랜딩이 매 페이지뷰마다 호출 → 30초 캐시.
# 30초 stale은 dead-man 정확도에 무관 (임계값 24h/72h 기준).
_FRESHNESS_CACHE: dict = {"ts": 0.0, "value": None}
_FRESHNESS_TTL_SEC = 30


@app.get("/health/freshness")
@limiter.limit(RATE_LIMIT_FRESHNESS)  # 랜딩 페이지뷰 DDoS-like 패턴 방어
def health_freshness(request: Request):
    """Pipeline 데이터 신선도 + Dead-man switch 상태.

    mode:
      - "live"    : < 24h  (정상)
      - "warning" : 24~72h (경고 배너, 결제 허용)
      - "stale"   : 72h+   (신규 결제 차단)

    payments_blocked=True 이면 클라이언트는 Paddle Checkout 차단.
    DB 에러 시 fail-safe 로 payments_blocked=True 반환.
    30s 캐시: 랜딩 페이지뷰 DDoS-like 패턴 대응 (DB 부담 99% 감소).
    """
    now = _time.time()
    if _FRESHNESS_CACHE["value"] is not None and now - _FRESHNESS_CACHE["ts"] < _FRESHNESS_TTL_SEC:
        return _FRESHNESS_CACHE["value"]

    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT MAX(snapshot_at) FROM trend_snapshots")
                row = cur.fetchone()
                last_snap = row[0] if row else None
    except Exception as exc:
        logger.error("Freshness check DB error", exc_info=True)
        # 에러는 캐싱 안 함 — 다음 호출에서 즉시 재시도.
        return {
            "status": "error",
            "mode": "unknown",
            "payments_blocked": True,
            "error": str(exc)[:120],
        }

    if last_snap is None:
        result = {
            "status": "no_data",
            "mode": "stale",
            "updated_at": None,
            "hours_since": None,
            "days_since": None,
            "payments_blocked": True,
        }
        _FRESHNESS_CACHE["ts"] = now
        _FRESHNESS_CACHE["value"] = result
        return result

    if last_snap.tzinfo is None:
        last_snap = last_snap.replace(tzinfo=timezone.utc)
    hours = (datetime.now(timezone.utc) - last_snap).total_seconds() / 3600

    if hours < FRESHNESS_WARNING_HOURS:
        mode, blocked = "live", False
    elif hours < FRESHNESS_STALE_HOURS:
        mode, blocked = "warning", False
    else:
        mode, blocked = "stale", True

    result = {
        "status": "ok",
        "mode": mode,
        "updated_at": last_snap.isoformat(),
        "hours_since": round(hours, 1),
        "days_since": round(hours / 24, 1),
        "payments_blocked": blocked,
    }
    _FRESHNESS_CACHE["ts"] = now
    _FRESHNESS_CACHE["value"] = result
    return result


# ── GET /trending ───────────────────────────────────────────
@app.get("/trending", response_model=ApiResponse)
@limiter.limit(RATE_LIMIT_TRENDING)  # API 키 일일 한도와 별개 — IP당 burst 제한
def get_trending(
    request: Request,
    limit: int = Query(default=20, ge=1, le=100),
    min_stores: int = Query(default=2, ge=2),
    sort: str = Query(default="rising", pattern="^(rising|popular)$"),
    _auth: dict = Depends(require_api_key),
):
    """
    sort=rising (기본): week_delta 가중 복합 점수 — 빠르게 퍼지고 있으나
    아직 store_count가 낮은 블루오션 신호 우선.
    sort=popular: 순수 store_count 기준 (포화 시장 확인용).
    FIX: store_count DESC 단일 정렬은 고포화 제품을 trending으로 오인하게 만듦,
    week_delta 가중 점수로 교체하여 실질 수요 신호 제공 → 사용자 리텐션 향상
    """
    # OPTIMIZE: 5분 TTL 캐시. 데이터 주간 갱신이라 5분 stale 영향 0, DB 호출 90%+ 절감.
    cache_key = (limit, min_stores, sort)
    cached = _trending_cache_get(cache_key)
    if cached is not None:
        return {"success": True, "data": cached, "cached": True}

    # SEC-ALERT: f-string SQL 제거 — _SORT_CLAUSES 화이트리스트 dict로 교체.
    # Pydantic pattern 검증이 있어도 f-string SQL은 코드 변경 시 injection 취약점으로 전환될 위험.
    order_clause = _SORT_CLAUSES[sort]
    try:
        with get_conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(f"""
                    SELECT
                        c.id           AS cluster_id,
                        c.cluster_hash,
                        c.store_count,
                        c.product_count,
                        COALESCE(ts.week_delta, 0) AS week_delta,
                        p.title        AS representative_title,
                        p.price_min    AS representative_price,
                        p.image_url    AS representative_image
                    FROM clusters c
                    LEFT JOIN LATERAL (
                        SELECT week_delta FROM trend_snapshots
                        WHERE cluster_id = c.id
                        ORDER BY snapshot_at DESC LIMIT 1
                    ) ts ON true
                    LEFT JOIN LATERAL (
                        SELECT p2.title, p2.price_min, p2.image_url
                        FROM product_clusters pc2
                        JOIN products p2 ON p2.id = pc2.product_id
                        WHERE pc2.cluster_id = c.id
                        ORDER BY p2.price_min ASC NULLS LAST
                        LIMIT 1
                    ) p ON true
                    WHERE c.store_count >= %s
                    ORDER BY {order_clause}
                    LIMIT %s
                """, (min_stores, limit))
                rows = cur.fetchall()
        result = [dict(r) for r in rows]
        _trending_cache_set(cache_key, result)
        return {"success": True, "data": result}
    except Exception:
        logger.error("GET /trending 실패", exc_info=True)
        raise HTTPException(status_code=500, detail="내부 서버 오류가 발생했습니다.")


# ── GET /store/{domain} ─────────────────────────────────────
@app.get("/store/{domain}", response_model=ApiResponse)
def get_store(domain: str, _auth: dict = Depends(require_api_key)):
    """스토어 정보 + 해당 스토어의 상품 목록"""
    if not domain.endswith(".myshopify.com"):
        domain = domain + ".myshopify.com"
    # FIX: 도메인 형식 검증 — 파라미터화 쿼리라 SQL injection은 없으나
    # 경로 순회(../), 내부 호스트명 등 비정상 입력이 DB 로그에 기록되는 것을 방지.
    if not _DOMAIN_RE.match(domain.lower()):
        raise HTTPException(status_code=400, detail="유효하지 않은 도메인 형식입니다.")

    try:
        with get_conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    "SELECT * FROM stores WHERE domain = %s", (domain,)
                )
                store = cur.fetchone()
                if not store:
                    raise HTTPException(status_code=404, detail=f"스토어 '{domain}' 없음")

                cur.execute("""
                    SELECT shopify_id, title, price_min, price_max, image_url, image_hash
                    FROM products
                    WHERE store_id = %s
                    ORDER BY price_min ASC NULLS LAST
                """, (store["id"],))
                products = cur.fetchall()

        return {
            "success": True,
            "data": {
                "store": dict(store),
                "products": [dict(p) for p in products],
            }
        }
    except HTTPException:
        raise
    except Exception:
        logger.error("GET /store 실패", exc_info=True)
        raise HTTPException(status_code=500, detail="내부 서버 오류가 발생했습니다.")


# ── GET /cluster/{cluster_id} ───────────────────────────────
@app.get("/cluster/{cluster_id}", response_model=ApiResponse)
def get_cluster(cluster_id: int, _auth: dict = Depends(require_api_key)):
    """클러스터 상세: 동일 상품을 파는 모든 스토어 + 가격 분포"""
    try:
        with get_conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    "SELECT * FROM clusters WHERE id = %s", (cluster_id,)
                )
                cluster = cur.fetchone()
                if not cluster:
                    raise HTTPException(status_code=404, detail=f"클러스터 {cluster_id} 없음")

                cur.execute("""
                    SELECT
                        s.domain,
                        p.title,
                        p.price_min,
                        p.price_max,
                        p.image_url
                    FROM product_clusters pc
                    JOIN products p ON p.id = pc.product_id
                    JOIN stores s ON s.id = p.store_id
                    WHERE pc.cluster_id = %s
                    ORDER BY p.price_min ASC NULLS LAST
                """, (cluster_id,))
                products = cur.fetchall()

                # FIX: 플랜별 history depth 차등 적용 — Pro 업그레이드 실질 인센티브 생성,
                # Starter→Pro 전환율 목표 15%p 향상
                history_limit = 30 if _auth.get("plan") == "pro" else 7
                cur.execute("""
                    SELECT store_count, week_delta, snapshot_at
                    FROM trend_snapshots
                    WHERE cluster_id = %s
                    ORDER BY snapshot_at DESC
                    LIMIT %s
                """, (cluster_id, history_limit))
                history = cur.fetchall()

        prices = [p["price_min"] for p in products if p["price_min"]]
        return {
            "success": True,
            "data": {
                "cluster": dict(cluster),
                "products": [dict(p) for p in products],
                "price_range": {
                    "min": min(prices) if prices else None,
                    "max": max(prices) if prices else None,
                    "avg": round(sum(prices) / len(prices), 2) if prices else None,
                },
                "trend_history": [dict(h) for h in history],
            }
        }
    except HTTPException:
        raise
    except Exception:
        logger.error("GET /cluster 실패", exc_info=True)
        raise HTTPException(status_code=500, detail="내부 서버 오류가 발생했습니다.")


# ── GET /search ─────────────────────────────────────────────
@app.get("/search", response_model=ApiResponse)
def search_products(
    q: str = Query(..., min_length=2),
    limit: int = Query(default=20, ge=1, le=50),
    _auth: dict = Depends(require_api_key),
):
    """상품명 검색 (부분 일치)"""
    try:
        with get_conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("""
                    SELECT p.shopify_id, p.title, p.price_min, p.price_max,
                           p.image_url, s.domain
                    FROM products p
                    JOIN stores s ON s.id = p.store_id
                    WHERE p.title ILIKE %s
                    ORDER BY p.price_min ASC NULLS LAST
                    LIMIT %s
                """, (f"%{q}%", limit))
                rows = cur.fetchall()
        return {"success": True, "data": [dict(r) for r in rows]}
    except Exception:
        logger.error("GET /search 실패", exc_info=True)
        raise HTTPException(status_code=500, detail="내부 서버 오류가 발생했습니다.")


# ── GET /export/trending (Pro only) ─────────────────────────
@app.get("/export/trending")
def export_trending_csv(
    min_stores: int = Query(default=2, ge=2),
    _auth: dict = Depends(require_api_key),
):
    # FIX: CSV export를 Pro 전용으로 gate — 광고한 기능을 실제 구현하여
    # Pro $49 플랜의 실질 가치 확보, 허위광고 리스크 제거
    if _auth.get("plan") != "pro":
        raise HTTPException(
            status_code=403,
            detail="CSV export는 Pro 플랜 전용입니다. /billing/plans 에서 업그레이드하세요.",
        )
    try:
        with get_conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("""
                    SELECT
                        c.id AS cluster_id,
                        c.store_count,
                        c.product_count,
                        COALESCE(ts.week_delta, 0) AS week_delta,
                        p.title AS representative_title,
                        p.price_min AS representative_price
                    FROM clusters c
                    LEFT JOIN LATERAL (
                        SELECT week_delta FROM trend_snapshots
                        WHERE cluster_id = c.id
                        ORDER BY snapshot_at DESC LIMIT 1
                    ) ts ON true
                    LEFT JOIN LATERAL (
                        SELECT p2.title, p2.price_min
                        FROM product_clusters pc2
                        JOIN products p2 ON p2.id = pc2.product_id
                        WHERE pc2.cluster_id = c.id
                        ORDER BY p2.price_min ASC NULLS LAST LIMIT 1
                    ) p ON true
                    WHERE c.store_count >= %s
                    ORDER BY c.store_count DESC
                    LIMIT 1000
                """, (min_stores,))
                rows = cur.fetchall()

        buf = io.StringIO()
        writer = csv.DictWriter(buf, fieldnames=[
            "cluster_id", "store_count", "product_count",
            "week_delta", "representative_title", "representative_price",
        ])
        writer.writeheader()
        writer.writerows([dict(r) for r in rows])
        buf.seek(0)
        return StreamingResponse(
            iter([buf.getvalue()]),
            media_type="text/csv",
            headers={"Content-Disposition": "attachment; filename=storescope_trending.csv"},
        )
    except HTTPException:
        raise
    except Exception:
        logger.error("GET /export/trending 실패", exc_info=True)
        raise HTTPException(status_code=500, detail="내부 서버 오류가 발생했습니다.")


# DELETED 2026-06-04: /webhook/subscribe + WebhookSubscribeRequest + _assert_safe_webhook_url
# 7일간 구독자 0명 (psql SELECT COUNT 검증). Pro 활성 키 2개도 호출 흔적 0.
# 마스터플랜 Path A 22% 확률 + Pro tier 미실증 → 추측성 기능. YAGNI 원칙.
# 첫 Pro 고객 명시 요청 시 복구 (git revert <SHA>).


# ── POST /leads ─────────────────────────────────────────────
class LeadRequest(BaseModel):
    email: str
    domain: Optional[str] = None
    source: str = "xray"


@app.post("/leads")
@limiter.limit(RATE_LIMIT_LEADS)  # 봇 스팸 차단 — IP당 분당 N건
def capture_lead(request: Request, req: LeadRequest, background: BackgroundTasks):
    # FIX: 무료 툴 이메일 리드 저장 — 전환 퍼널 복구,
    # 이메일 리드는 Paddle checkout 유도 또는 직접 API 키 발급의 선행 조건
    import re
    if not re.match(r"^[^@]+@[^@]+\.[^@]+$", req.email):
        raise HTTPException(status_code=400, detail="유효하지 않은 이메일 형식입니다.")
    if len(req.email) > 254:
        raise HTTPException(status_code=400, detail="이메일이 너무 깁니다.")
    # SEC: domain DoS 방어 — Shopify 최대 도메인 ~80자, 여유 256 cap.
    if req.domain and len(req.domain) > 256:
        raise HTTPException(status_code=400, detail="도메인이 너무 깁니다.")
    # SEC: paddle_routes와 일관성 — disposable 이메일 차단으로 리드 테이블 오염 방지.
    # 200 fake-success 반환 (스팸봇이 "차단됨" 신호로 우회 시도하는 것 차단).
    from api.auth import is_disposable_email
    normalized_email = req.email.lower().strip()
    if is_disposable_email(normalized_email):
        logger.info("Disposable email rejected at /leads: %s", normalized_email)
        return {"captured": True}  # 의도적 silent reject
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO email_leads (email, source, domain)
                    VALUES (%s, %s, %s)
                    ON CONFLICT (email) DO NOTHING
                """, (normalized_email, req.source, req.domain))
        # FIX 2026-06-06: X-Ray 리드 즉시 보고서 자동 발송 (랜딩 모달 약속 이행).
        # source='xray'인 경우만 — capture form 다른 소스는 향후 별도 핸들러.
        # BackgroundTask = response 즉시 반환 + SMTP 호출은 별도 thread.
        # SMTP 미설정 시 console stub (개발 환경 안전).
        if req.source == "xray":
            from services.xray_report import send_xray_report
            background.add_task(send_xray_report, normalized_email, req.domain)
        return {"captured": True}
    except Exception:
        logger.error("POST /leads 실패", exc_info=True)
        raise HTTPException(status_code=500, detail="내부 서버 오류가 발생했습니다.")


# ── POST /optout ─────────────────────────────────────────────
@app.post("/optout")
@limiter.limit(RATE_LIMIT_OPTOUT)  # 경쟁자 공격 차단 + typo retry UX 균형 (기본 10/min)
def optout(request: Request, domain: str = Query(..., description="myshopify.com 도메인")):
    """
    머천트 옵트아웃 — 해당 스토어 데이터를 DB에서 삭제하고 재크롤링 차단.
    법적 방어: GDPR Article 17 (삭제 요청권) 및 선의 크롤링 정책 준수 증거.
    인증 불필요 — 머천트 본인 확인은 도메인 소유 확인으로 대체.
    FIX: nginx.conf의 api_general zone(60r/m)으로 IP 레벨 레이트 리밋 적용.
    대량 opt-out 공격(경쟁자가 타 스토어를 제거하는 시도)은 nginx에서 차단됨.
    """
    if not domain.endswith(".myshopify.com"):
        domain = domain + ".myshopify.com"
    # FIX: 도메인 형식 검증 — 유효한 myshopify.com 도메인만 허용
    if not _DOMAIN_RE.match(domain.lower()):
        raise HTTPException(status_code=400, detail="유효하지 않은 도메인 형식입니다.")
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE stores SET is_active = FALSE WHERE domain = %s RETURNING id",
                    (domain,),
                )
                row = cur.fetchone()
        if row:
            return {"success": True, "message": f"{domain} 데이터가 삭제 요청 처리되었습니다. 48시간 내 반영됩니다."}
        return {"success": False, "message": "해당 도메인을 찾을 수 없습니다."}
    except Exception:
        logger.error("POST /optout 실패", exc_info=True)
        raise HTTPException(status_code=500, detail="내부 서버 오류가 발생했습니다.")
