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

try:
    from starlette.requests import Request
except ImportError:  # pragma: no cover
    Request = None  # type: ignore


def get_remote_address(request):
    """X-Forwarded-For 대응 remote address helper.
    Render / Cloudflare 뒤에서 request.client.host = LB IP → key 오염.
    XFF 존재 시 first hop 사용."""
    # Backward-compat: 기존 main.py 로직 유지
    xff = request.headers.get("x-forwarded-for", "")
    if xff:
        return xff.split(",")[0].strip()
    return _slowapi_get_remote_address(request)


# Shared limiter instance — main.py + admin_routes.py + billing_routes.py 공유
limiter = Limiter(key_func=get_remote_address, default_limits=["200/minute"])
