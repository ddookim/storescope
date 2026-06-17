"""
StoreScope Admin Dashboard — 솔로 파운더 모니터링 페이지

마스터플랜 §4 STEP 1 추적 KPI 5개 자동 표시:
    1. X-Ray DAU (Plausible 추정 또는 /leads 카운트)
    2. 이메일 캡처 수 (lead 누적)
    3. 유료 가입자 수 (api_keys is_active)
    4. 환불 요청 수 (paddle webhook 로그 — 향후 카운트 컬럼 추가 시)
    5. 주간 CS 시간 (수동 입력)

접근:
    Streamlit URL ?secret=... 쿼리 또는 ADMIN_SECRET 환경변수 매칭.
    main app.py와 별도 인증 (multi-page 전환 시 동일 세션).

D+30 자동 분기 의사결정에 직접 사용 — 마스터플랜 STEP 2 "측정값만 따른다" 정합.
"""

import os
import hmac
import streamlit as st
import psycopg2.extras
import pandas as pd
from datetime import datetime, timezone
from contextlib import contextmanager
import psycopg2.pool


st.set_page_config(
    page_title="StoreScope Admin",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# ── 인증 (상수 시간 비교) ──────────────────────────────────────
_ADMIN_SECRET = os.environ.get("ADMIN_SECRET", "")
if not _ADMIN_SECRET:
    st.error("ADMIN_SECRET 환경변수가 설정되지 않았습니다.")
    st.stop()

# 쿼리 또는 입력 인증
qp = st.query_params if hasattr(st, "query_params") else {}
provided = qp.get("secret") if hasattr(qp, "get") else (qp.get("secret", [None])[0] if qp else None)
if isinstance(provided, list):
    provided = provided[0] if provided else None
if not provided:
    provided = st.text_input("Admin secret", type="password")
    if not provided:
        st.stop()
if not hmac.compare_digest(provided.encode("utf-8"), _ADMIN_SECRET.encode("utf-8")):
    st.error("Invalid admin secret")
    st.stop()


# ── DB 풀 (싱글톤) ─────────────────────────────────────────────
@st.cache_resource
def _get_pool():
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        return None
    try:
        return psycopg2.pool.ThreadedConnectionPool(1, 5, db_url)
    except Exception:
        return None


@contextmanager
def _conn():
    p = _get_pool()
    if p is None or p.closed:
        _get_pool.clear()
        yield None
        return
    c = p.getconn()
    c.autocommit = True
    try:
        yield c
    finally:
        p.putconn(c)


def _query(sql, params=()):
    with _conn() as c:
        if not c:
            return None
        try:
            with c.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(sql, params)
                return cur.fetchall()
        except Exception as e:
            st.warning(f"Query failed: {e}")
            return None


# ── 헤더 ──────────────────────────────────────────────────────
st.title("StoreScope Admin")
now = datetime.now(timezone.utc)
st.caption(f"Generated {now.strftime('%Y-%m-%d %H:%M UTC')} · auto-refresh on rerun")

# ── 마스터플랜 STEP 1 KPI 5개 ───────────────────────────────────
st.divider()
st.subheader("Master Plan STEP 1 KPI (D+30 auto-branch input)")

col1, col2, col3, col4, col5 = st.columns(5)

# KPI 1: X-Ray DAU (지난 7일 lead 평균 / 7)
xray_recent = _query("""
    SELECT COUNT(*) AS n
    FROM email_leads
    WHERE source = 'xray' AND created_at >= NOW() - INTERVAL '7 days'
""") or [{"n": 0}]
xray_dau = xray_recent[0]["n"] / 7
col1.metric("X-Ray DAU (7d avg)", f"{xray_dau:.1f}", help="email_leads source='xray' / 7")

# KPI 2: Email captures (누적)
email_total = _query("SELECT COUNT(*) AS n FROM email_leads") or [{"n": 0}]
col2.metric("Total email captures", email_total[0]["n"], help="email_leads 누적 row")

# KPI 3: Paid subs (active)
paid = _query("""
    SELECT COUNT(*) AS n
    FROM api_keys
    WHERE is_active = TRUE AND plan IN ('starter', 'pro')
""") or [{"n": 0}]
col3.metric("Active paid subs", paid[0]["n"], help="api_keys is_active AND plan in (starter,pro)")

# KPI 4: 신규 paid (7일)
new_paid = _query("""
    SELECT COUNT(*) AS n
    FROM api_keys
    WHERE is_active = TRUE
      AND plan IN ('starter', 'pro')
      AND created_at >= NOW() - INTERVAL '7 days'
""") or [{"n": 0}]
col4.metric("New paid (7d)", new_paid[0]["n"], help="last 7d 활성화")

# KPI 5: today's API usage
today_usage = _query("""
    SELECT COALESCE(SUM(request_count), 0) AS n
    FROM api_usage
    WHERE used_date = CURRENT_DATE
""") or [{"n": 0}]
col5.metric("API requests today", today_usage[0]["n"])


# ── D+30 분기 자동 판정 (현재 데이터 기준) ─────────────────────
st.divider()
st.subheader("D+30 auto-branch decision (current state)")

# 마스터플랜 Path 조건:
# Path A · CONTINUE: 유료 ≥ 3 AND 이메일 ≥ 200
# Path B · PIVOT (Brand IP): 유료 0-2 AND DAU ≥ 20
# Path C · ARCHIVE: 유료 0-2 AND DAU < 20
# Path D · EMERGENCY: Paddle 동결 OR 채널 밴 (수동 판정)
paid_n = paid[0]["n"]
email_n = email_total[0]["n"]
dau = xray_dau

if paid_n >= 3 and email_n >= 200:
    path = "A · CONTINUE"
    color = "green"
    note = "현 모델 유지. D+90까지 마케팅 확장."
elif paid_n <= 2 and dau >= 20:
    path = "B · PIVOT to Brand IP $499"
    color = "blue"
    note = "72h 플랜 즉시 실행 (services/counterfeit_report.py 준비 완료)."
elif paid_n <= 2 and dau < 20:
    path = "C · ARCHIVE to dataset"
    color = "orange"
    note = "Hugging Face 업로드, 학업 집중."
else:
    path = "(edge case — manual review)"
    color = "gray"
    note = "기준 미충족 — 수동 검토"

st.markdown(f"### Predicted path: **:{color}[{path}]**")
st.caption(note)
st.caption(f"Inputs: paid={paid_n} · 7d-DAU={dau:.1f} · email={email_n}")


# ── 최근 paid 가입자 ─────────────────────────────────────────
st.divider()
st.subheader("Recent paid customers")
recent_keys = _query("""
    SELECT
        ak.id,
        ak.email,
        ak.plan,
        ak.is_active,
        ak.daily_limit,
        ak.created_at,
        COALESCE(au.request_count, 0) AS today_requests
    FROM api_keys ak
    LEFT JOIN api_usage au
        ON ak.id = au.key_id AND au.used_date = CURRENT_DATE
    WHERE ak.plan IN ('starter', 'pro')
    ORDER BY ak.created_at DESC
    LIMIT 50
""")
if recent_keys:
    df = pd.DataFrame(recent_keys)
    df["created_at"] = pd.to_datetime(df["created_at"]).dt.strftime("%Y-%m-%d %H:%M")
    st.dataframe(df, use_container_width=True, hide_index=True)
else:
    st.info("No paid customers yet.")


# ── 최근 lead ───────────────────────────────────────────────
st.divider()
st.subheader("Recent email leads (last 50)")
recent_leads = _query("""
    SELECT email, source, domain, created_at
    FROM email_leads
    ORDER BY created_at DESC
    LIMIT 50
""")
if recent_leads:
    df = pd.DataFrame(recent_leads)
    df["created_at"] = pd.to_datetime(df["created_at"]).dt.strftime("%Y-%m-%d %H:%M")
    st.dataframe(df, use_container_width=True, hide_index=True)
else:
    st.info("No leads yet.")


# ── 데이터 신선도 ────────────────────────────────────────────
st.divider()
st.subheader("Pipeline freshness")
fresh = _query("SELECT MAX(snapshot_at) AS last FROM trend_snapshots")
if fresh and fresh[0]["last"]:
    last_snap = fresh[0]["last"]
    if last_snap.tzinfo is None:
        last_snap = last_snap.replace(tzinfo=timezone.utc)
    age_hr = (now - last_snap).total_seconds() / 3600
    if age_hr < 24:
        mode_color = "green"; label = "LIVE"
    elif age_hr < 72:
        mode_color = "orange"; label = "WARNING"
    else:
        mode_color = "red"; label = "STALE — payments blocked"
    st.markdown(f"### Status: :{mode_color}[{label}] · {age_hr:.1f}h since last snapshot")
else:
    st.error("No snapshots yet.")


# ── 푸터 ──────────────────────────────────────────────────────
st.divider()
st.caption("To force-rerun: F5 or `streamlit run app.py` (main app)")
