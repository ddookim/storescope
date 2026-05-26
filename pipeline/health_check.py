"""
StoreScope — 무음 일일 헬스체크
==================================
cron: 0 9 * * * python -m pipeline.health_check

원칙: 정상 시 완전 무음. 알림이 없으면 시스템 정상 운영 중.
이상 감지 시만 Telegram ERROR 알림 발송.

임계값 (마스터플랜 5-1 기준):
    min_products_daily   50,000  — 일 최소 수집 상품 수
    max_failure_rate      0.15   — 최대 허용 실패율
    max_stale_hours         30   — 데이터 최신성 임계값 (시간)
    min_cluster_count      50    — 최소 유효 클러스터 수

실행:
    python -m pipeline.health_check
"""

import os
import sys
import time
from datetime import datetime, timezone

import psycopg2
import psycopg2.extras

from pipeline.alerting import send_alert

# FIX: 디스크·메모리 임계값 추가 — 파이프라인 실패 전 선제 경고
DISK_WARN_PCT  = 80   # 80% 초과 시 WARNING
DISK_CRIT_PCT  = 90   # 90% 초과 시 CRITICAL (파이프라인 실패 직전)
MEM_WARN_PCT   = 85   # 메모리 85% 초과 시 경고

DB_URL = os.environ.get("DATABASE_URL", "postgresql://localhost/storescope")

THRESHOLDS = {
    "min_products_daily": 50_000,
    "max_failure_rate":   0.15,
    "max_stale_hours":    30,
    "min_cluster_count":  50,
}


def _get_conn():
    return psycopg2.connect(DB_URL)


def _check_system_resources(issues: list[str]) -> None:
    """
    FIX: 디스크·메모리 리소스 점검 — shutil + /proc/meminfo 사용,
    외부 의존성 0, 단일 VPS 환경에서 즉시 적용 가능한 가장 가벼운 모니터링.
    """
    import shutil

    # ── 디스크 사용률 ─────────────────────────────────────────
    try:
        monitor_path = os.environ.get("APP_DIR", "/opt/storescope")
        if not os.path.exists(monitor_path):
            monitor_path = "/"
        disk = shutil.disk_usage(monitor_path)
        disk_pct = disk.used / disk.total * 100
        free_gb = (disk.total - disk.used) / 1024**3

        if disk_pct >= DISK_CRIT_PCT:
            issues.append(
                f"디스크 CRITICAL: {disk_pct:.0f}% 사용 중 (여유 {free_gb:.1f}GB) — "
                f"파이프라인 다음 실행 전 정리 필요"
            )
        elif disk_pct >= DISK_WARN_PCT:
            issues.append(
                f"디스크 경고: {disk_pct:.0f}% 사용 중 (여유 {free_gb:.1f}GB) — "
                f"오래된 JSON 파일 정리 권고"
            )
    except Exception as exc:
        issues.append(f"디스크 확인 실패: {exc}")

    # ── 메모리 사용률 (/proc/meminfo — Linux only) ────────────
    try:
        with open("/proc/meminfo") as f:
            info = {}
            for line in f:
                if ":" in line:
                    k, v = line.split(":", 1)
                    info[k.strip()] = int(v.split()[0])  # kB

        total_kb     = info.get("MemTotal", 0)
        available_kb = info.get("MemAvailable", 0)
        if total_kb > 0:
            mem_used_pct = (1 - available_kb / total_kb) * 100
            if mem_used_pct >= MEM_WARN_PCT:
                avail_mb = available_kb / 1024
                issues.append(
                    f"메모리 경고: {mem_used_pct:.0f}% 사용 중 (여유 {avail_mb:.0f}MB) — "
                    f"pHash 클러스터링 OOM 위험"
                )
    except FileNotFoundError:
        pass  # macOS 개발 환경 — /proc/meminfo 없음, 무시
    except Exception as exc:
        issues.append(f"메모리 확인 실패: {exc}")


def run_health_check() -> list[str]:
    """
    DB 상태 + 시스템 리소스 점검 후 발견된 이슈 목록 반환.
    이슈 없으면 빈 리스트 반환 → 호출자가 완전 무음 처리.
    """
    issues: list[str] = []

    # 시스템 리소스 먼저 점검 (DB 연결 실패와 독립적)
    _check_system_resources(issues)

    try:
        conn = _get_conn()
    except Exception as exc:
        issues.append(f"DB 연결 실패: {exc}")
        return issues

    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:

            # ── 1. 데이터 최신성 확인 ─────────────────────────────
            cur.execute("SELECT MAX(last_seen) AS last_seen FROM products")
            row = cur.fetchone()
            last_seen = row["last_seen"] if row else None

            if last_seen is None:
                issues.append("상품 데이터 없음 — 파이프라인 한 번도 실행 안 됨")
            else:
                if last_seen.tzinfo is None:
                    last_seen = last_seen.replace(tzinfo=timezone.utc)
                stale_hours = (datetime.now(timezone.utc) - last_seen).total_seconds() / 3600
                if stale_hours > THRESHOLDS["max_stale_hours"]:
                    issues.append(
                        f"데이터 최신성 초과: 마지막 갱신 {stale_hours:.1f}시간 전 "
                        f"(임계값 {THRESHOLDS['max_stale_hours']}h)"
                    )

            # ── 2. 일별 수집량 확인 ───────────────────────────────
            cur.execute("""
                SELECT COUNT(*) AS cnt
                FROM products
                WHERE last_seen > NOW() - INTERVAL '24 hours'
            """)
            daily_count = cur.fetchone()["cnt"]
            if daily_count < THRESHOLDS["min_products_daily"]:
                issues.append(
                    f"일일 수집량 부족: {daily_count:,}개 "
                    f"(최소 {THRESHOLDS['min_products_daily']:,}개)"
                )

            # ── 3. 클러스터 수 확인 ───────────────────────────────
            cur.execute("SELECT COUNT(*) AS cnt FROM clusters WHERE store_count >= 2")
            cluster_count = cur.fetchone()["cnt"]
            if cluster_count < THRESHOLDS["min_cluster_count"]:
                issues.append(
                    f"유효 클러스터 부족: {cluster_count}개 "
                    f"(최소 {THRESHOLDS['min_cluster_count']}개)"
                )

            # ── 4. 스토어 수 확인 ─────────────────────────────────
            cur.execute("SELECT COUNT(*) AS cnt FROM stores WHERE is_active = TRUE")
            store_count = cur.fetchone()["cnt"]
            if store_count == 0:
                issues.append("활성 스토어 없음 — discover_stores 재실행 필요")

            # ── 5. 트렌드 스냅샷 최신성 확인 ────────────────────
            cur.execute("SELECT MAX(snapshot_at) AS last_snap FROM trend_snapshots")
            row = cur.fetchone()
            last_snap = row["last_snap"] if row else None
            if last_snap is None:
                issues.append("트렌드 스냅샷 없음 — load_to_db 실행 필요")
            else:
                if last_snap.tzinfo is None:
                    last_snap = last_snap.replace(tzinfo=timezone.utc)
                snap_age_days = (datetime.now(timezone.utc) - last_snap).days
                if snap_age_days > 10:
                    issues.append(
                        f"트렌드 스냅샷 {snap_age_days}일 전 데이터 — 파이프라인 점검 필요"
                    )

    except Exception as exc:
        issues.append(f"헬스체크 쿼리 오류: {exc}")
    finally:
        conn.close()

    return issues


def main() -> None:
    t = time.monotonic()
    issues = run_health_check()
    elapsed = time.monotonic() - t

    if not issues:
        # 정상 — 완전 무음 (No news is good news)
        # 로컬 실행 시에만 확인용 출력
        if sys.stdout.isatty():
            print(f"[{datetime.now().strftime('%H:%M')}] 헬스체크 PASS ({elapsed:.1f}초)")
        return

    # 이상 감지 → Telegram ERROR 발송
    body = "\n".join(f"• {issue}" for issue in issues)
    send_alert(
        f"헬스체크 실패 ({len(issues)}건)\n\n{body}\n\n경과: {elapsed:.1f}초",
        level="ERROR",
    )
    sys.exit(1)


if __name__ == "__main__":
    main()
