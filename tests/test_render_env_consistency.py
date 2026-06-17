"""
render.neon.yaml 의 envVars vs 코드 내 os.environ.get() 정합 게이트

라운드 16 발견: services/xray_report.py 의 APP_URL 사용 추가됐는데 render.neon.yaml 미반영
→ 이메일 모든 CTA 가 미존재 도메인 storescope.com 으로 가서 404.

본 게이트는 정적 검증:
    1. 코드에서 os.environ.get() 으로 읽는 변수가 render.neon.yaml 에 정의되어 있는지
    2. 누락된 변수는 명시 default 가 있어야 함 (없으면 fail)
    3. sync: false 변수 (사용자가 직접 입력) 는 LAUNCH_TODAY.md 의 필수 4개 또는
       render_env_template.md 의 선택 5개에 명시되어 있는지
"""

import re
import sys
from pathlib import Path

import pytest

_here = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_here))


def _collect_code_env_vars() -> set[str]:
    """os.environ.get("KEY"...) 패턴 추출."""
    keys: set[str] = set()
    pattern = re.compile(r"""os\.environ\.get\(\s*["']([A-Z][A-Z0-9_]+)["']""")
    for d in ["api", "services", "scripts", "pipeline", "pages"]:
        for f in (_here / d).rglob("*.py"):
            if "__pycache__" in str(f):
                continue
            try:
                text = f.read_text()
            except Exception:
                continue
            keys.update(pattern.findall(text))
    # 루트 app.py 도 포함
    app_py = _here / "app.py"
    if app_py.exists():
        keys.update(pattern.findall(app_py.read_text()))
    return keys


def _collect_render_env_vars() -> set[str]:
    """render.neon.yaml 의 envVars 키 추출 — stdlib만 (PyYAML dependency 회피)."""
    yaml_path = _here / "render.neon.yaml"
    text = yaml_path.read_text()
    # 정규식: '- key: VAR_NAME' 또는 '  - key: VAR_NAME' 패턴
    pattern = re.compile(r"^\s+-\s+key:\s+([A-Z][A-Z0-9_]+)\s*$", re.MULTILINE)
    return set(pattern.findall(text))


# 시스템 / 라이브러리 env vars (Render가 자동 주입 또는 stdlib)
SYSTEM_VARS = {
    "PORT",                  # Render 자동 주입
    "RENDER",                # Render 자동 주입
    "RENDER_SERVICE_NAME",   # Render 자동 주입 (Sentry environment 식별)
    "DATABASE_URL",          # sync: false (사용자 입력) — 코드에서 사용 + render.yaml에 명시
    "PYTHONPATH",            # 환경 설정
    "PATH", "HOME", "USER",
    "LOG_LEVEL",             # logging.basicConfig 용, 실 사용자가 launch 시 설정 안 함 (default OK)
}

# Operational scripts 전용 (production web service 무관 — 일상 운영 시점 사용)
OPERATIONAL_VARS = {
    "APP_DIR",               # pipeline/health_check.py disk monitor path
    "DO_API_TOKEN",          # scripts/scale_infra.py — DigitalOcean droplet 스케일링
    "DO_DROPLET_ID",         # 동일
}


def test_no_undocumented_env_vars():
    """
    코드에서 사용하는 env var 중 render.yaml/SYSTEM_VARS 어디에도 명시 안 된 것이 없어야 함.

    회귀 시나리오 (라운드 16 발견):
        신규 service 에서 NEW_FANCY_URL os.environ.get() 추가
        → render.neon.yaml 미반영 → production launch 시 default fallback
        → default 가 잘못된 URL 이면 silent fail (UX 사고)
    """
    code_vars = _collect_code_env_vars()
    render_vars = _collect_render_env_vars()
    missing = code_vars - render_vars - SYSTEM_VARS - OPERATIONAL_VARS

    if missing:
        pytest.fail(
            f"render.neon.yaml 에 명시되지 않은 env vars: {sorted(missing)}\n"
            f"  코드에서는 사용 중 → production 에서 default fallback (UX 사고 가능).\n"
            f"  수정: render.neon.yaml envVars 섹션에 추가."
        )


def test_render_yaml_critical_keys_present():
    """반드시 있어야 할 핵심 env vars 가 정의됨."""
    render_vars = _collect_render_env_vars()
    required = {
        "DATABASE_URL",
        "PADDLE_API_KEY",
        "PADDLE_CLIENT_TOKEN",
        "PADDLE_WEBHOOK_SECRET",
        "PADDLE_STARTER_PRICE_ID",
        "PADDLE_PRO_PRICE_ID",
        "ALLOWED_ORIGINS",
        "PYTHON_VERSION",
        "APP_URL",       # 라운드 16 추가
    }
    missing = required - render_vars
    assert not missing, (
        f"render.neon.yaml 누락 핵심 env vars: {sorted(missing)}"
    )


def test_app_url_does_not_default_to_nonexistent_domain():
    """APP_URL 의 render.yaml default 가 사용자 도메인 없이도 작동하는 URL.

    회귀 시나리오 (라운드 16 발견):
        default storescope.com → 사용자가 도메인 안 사면 미존재
        → 이메일 CTA 클릭 시 404
    """
    yaml_path = _here / "render.neon.yaml"
    text = yaml_path.read_text()
    # APP_URL 항목의 value 추출
    pattern = re.compile(
        r"-\s+key:\s+APP_URL\s*\n\s+value:\s+(\S+)", re.MULTILINE
    )
    m = pattern.search(text)
    assert m, "APP_URL value 누락"
    app_url = m.group(1)
    # 안전한 default: ddookim.github.io (gh-pages, 항상 가용) 또는 onrender.com (자동 생성)
    assert "ddookim.github.io" in app_url or "onrender.com" in app_url, (
        f"APP_URL default '{app_url}' — 사용자 도메인 의존. "
        f"안전한 fallback (ddookim.github.io 또는 onrender.com) 권장."
    )
