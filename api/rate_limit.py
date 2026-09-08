"""
StoreScope — 공용 rate limiter (slowapi).

Main app + admin router 등 여러 파일에서 동일 limiter 인스턴스 공유.
이전엔 api/main.py 에만 있어 admin_routes.py 에서 순환 임포트 불가 = /admin/* unrate-limited (2026-07-29 D+58 backend red team CRIT).

사용:
    from api.rate_limit import limiter

    @router.get("/admin/foo")
    @limiter.limit("5/minute")
    def foo(request: Request): ...
"""

from slowapi import Limiter
from slowapi.util import get_remote_address as _slowapi_get_remote_address


def get_remote_address(request):
    """X-Forwarded-For 대응 remote address helper.
    Render / Cloudflare 뒤에서 request.client.host = LB IP → key 오염.

    D+40 fix (2026-09-08): 이전엔 XFF first hop 사용 → 클라이언트 조작 가능 →
    rate limit 완전 우회 가능 (매 요청 XFF 다른 값 전송 시 매번 새 키).
    XFF 스펙: 마지막 hop = 신뢰 가능한 proxy 가 추가한 값. First hop = 클라이언트 자체.
    Render 는 request.client.host 에 실 클라이언트 IP 채움 (LB 가 XFF 마지막에 append 후 전달).
    표준 방식: XFF 있으면 마지막 hop 사용, 없으면 slowapi default (request.client.host).
    """
    xff = request.headers.get("x-forwarded-for", "")
    if xff:
        # 마지막 hop = 신뢰 가능. First hop 은 클라이언트 조작 가능 (rate limit bypass).
        parts = [p.strip() for p in xff.split(",") if p.strip()]
        if parts:
            return parts[-1]
    return _slowapi_get_remote_address(request)


# Shared limiter instance — main.py + admin_routes.py + billing_routes.py 공유
limiter = Limiter(key_func=get_remote_address, default_limits=["200/minute"])
