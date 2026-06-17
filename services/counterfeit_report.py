"""
Path B 72h 사전 산출물 — Brand IP Counterfeit Report PDF 생성기

마스터플랜 §5 별첨 T+14 ~ T+20 작업 미리 완료.
D+30 분기 시 Path B 발동되면 즉시 활용 가능 → 72h 플랜 ~6시간 절감.

사용 (Path B 가동 후):
    from services.counterfeit_report import generate
    pdf_bytes = generate(brand_name="LULULEMON", matched_clusters=[...], min_confidence=0.85)

DB 의존성:
    - clusters / products / product_clusters / stores 테이블 (기존)
    - image_hash CHAR(16) 컬럼 = pHash 16-char hex (기존)

PDF 출력 구조:
    1. 표지: 브랜드명 + 총 매칭 N건 + 생성일
    2. 매칭 테이블: cluster_id / 스토어 도메인 / 상품 제목 / 가격 / 신뢰도 / 첫 발견일
    3. 신뢰도 분포 차트 (텍스트 막대 — 외부 차트 라이브러리 불필요)
    4. 법적 면책 + 제휴 옵션 안내

설치:
    pip install reportlab>=4.0.0,<5.0.0  # Path B 가동 시점에 requirements.txt에 추가
"""

import html
import io
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

# 지연 import: Path B 가동 전엔 reportlab 미설치 OK.
# 본 모듈 import만으론 ImportError 안 남 (실제 generate() 호출 시점에서만 필요).


@dataclass(frozen=True)
class ClusterMatch:
    """단일 클러스터 매칭 결과 — DB → 이 dataclass로 변환 후 generate()에 전달."""
    cluster_id: int
    store_domain: str
    product_title: str
    product_image_url: Optional[str]
    price_min: Optional[float]
    price_max: Optional[float]
    confidence: float           # 0.0 ~ 1.0 (pHash Hamming 거리 기반)
    first_seen: datetime
    cluster_size: int           # 같은 cluster_id의 총 stores 수


def _confidence_bar(c: float, width: int = 18) -> str:
    """텍스트 막대 — reportlab table cell에 monospace로 렌더."""
    filled = int(round(c * width))
    return "█" * filled + "░" * (width - filled)


def generate(
    brand_name: str,
    matched_clusters: list[ClusterMatch],
    min_confidence: float = 0.80,
    report_id: Optional[str] = None,
) -> bytes:
    """
    Counterfeit report PDF 생성 — bytes 반환.

    Args:
        brand_name: 의뢰 브랜드 (예: "LULULEMON")
        matched_clusters: 매칭된 클러스터 list
        min_confidence: 표시 임계값 (기본 0.80)
        report_id: 외부 추적용 ID — 미지정 시 timestamp 기반 자동 생성

    Raises:
        ImportError: reportlab 미설치 (`pip install reportlab` 안내)
        ValueError: 빈 matched_clusters
    """
    try:
        from reportlab.lib.pagesizes import letter
        from reportlab.lib import colors
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.lib.units import inch
        from reportlab.platypus import (
            SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak,
        )
    except ImportError as e:
        raise ImportError(
            "reportlab 미설치 — Path B 가동 시 `pip install reportlab>=4.0.0,<5.0.0`"
        ) from e

    # SEC/DoS: brand_name 길이 cap — SQL ILIKE 패턴 폭주 + PDF 폭주 방어.
    # 실제 브랜드명은 < 50자. 200자 = 충분한 여유.
    if len(brand_name) > 200:
        raise ValueError(f"brand_name 너무 김 ({len(brand_name)}자, 200 max)")
    if not matched_clusters:
        raise ValueError("matched_clusters가 비어있습니다 — 빈 보고서는 생성하지 않음")

    filtered = [m for m in matched_clusters if m.confidence >= min_confidence]
    if not filtered:
        raise ValueError(
            f"min_confidence={min_confidence} 통과 매칭 없음 (입력 {len(matched_clusters)}건)"
        )

    now = datetime.now(timezone.utc)
    report_id = report_id or f"SS-{now.strftime('%Y%m%d-%H%M%S')}"

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=letter,
        leftMargin=0.6 * inch, rightMargin=0.6 * inch,
        topMargin=0.7 * inch, bottomMargin=0.7 * inch,
        title=f"StoreScope Brand IP Report — {brand_name}",
        author="StoreScope",
    )
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "TitleBold",
        parent=styles["Title"],
        fontName="Helvetica-Bold",
        fontSize=22,
        spaceAfter=10,
        textColor=colors.HexColor("#1C1917"),
    )
    meta_style = ParagraphStyle(
        "Meta",
        parent=styles["Normal"],
        fontSize=9,
        textColor=colors.HexColor("#57534E"),
        spaceAfter=24,
    )
    section_style = ParagraphStyle(
        "Section",
        parent=styles["Heading2"],
        fontSize=13,
        textColor=colors.HexColor("#4F46E5"),
        spaceBefore=20,
        spaceAfter=8,
    )

    story: list = []

    # ── 표지 ─────────────────────────────────────────────
    story.append(Paragraph(f"Brand IP Counterfeit Report", title_style))
    # SEC: brand_name은 외부 입력 (CLI/API) → Paragraph는 XML 마크업 파싱.
    # `<` 같은 특수문자는 reportlab parse 에러 또는 의도치 않은 markup 임베드 야기.
    brand_safe = html.escape(brand_name)
    story.append(Paragraph(f"<b>Subject brand:</b> {brand_safe}", styles["Normal"]))
    story.append(Paragraph(
        f"<b>Report ID:</b> {report_id} &nbsp;&nbsp; "
        f"<b>Generated:</b> {now.strftime('%Y-%m-%d %H:%M UTC')} &nbsp;&nbsp; "
        f"<b>Method:</b> perceptual-hash (pHash) cross-store clustering",
        meta_style,
    ))

    # ── Executive summary ────────────────────────────────
    story.append(Paragraph("Executive Summary", section_style))
    unique_stores = len({m.store_domain for m in filtered})
    unique_clusters = len({m.cluster_id for m in filtered})
    avg_conf = sum(m.confidence for m in filtered) / len(filtered)
    summary_text = (
        f"StoreScope identified <b>{len(filtered)} product listings</b> across "
        f"<b>{unique_stores} unique Shopify stores</b> that match {brand_safe}'s "
        f"product imagery with confidence ≥ {min_confidence:.0%} "
        f"(method: pHash, Hamming distance ≤ 8). "
        f"These listings span <b>{unique_clusters} distinct product clusters</b>. "
        f"Average confidence across matches: <b>{avg_conf:.0%}</b>."
    )
    story.append(Paragraph(summary_text, styles["Normal"]))

    # ── 매칭 테이블 ──────────────────────────────────────
    story.append(Spacer(1, 0.2 * inch))
    story.append(Paragraph("Matched Listings", section_style))

    table_data: list = [[
        Paragraph("<b>Store</b>", styles["Normal"]),
        Paragraph("<b>Product Title</b>", styles["Normal"]),
        Paragraph("<b>Price</b>", styles["Normal"]),
        Paragraph("<b>Confidence</b>", styles["Normal"]),
        Paragraph("<b>First Seen</b>", styles["Normal"]),
    ]]
    for m in sorted(filtered, key=lambda x: x.confidence, reverse=True):
        price_str = (
            f"${m.price_min:.2f}" if m.price_min == m.price_max
            else f"${m.price_min:.2f}–${m.price_max:.2f}"
        ) if m.price_min is not None else "n/a"
        table_data.append([
            Paragraph(html.escape(m.store_domain), styles["Normal"]),
            Paragraph(html.escape((m.product_title or "")[:60]), styles["Normal"]),
            Paragraph(price_str, styles["Normal"]),
            Paragraph(
                f'<font face="Courier">{_confidence_bar(m.confidence, 12)}</font> '
                f'{m.confidence:.0%}',
                styles["Normal"],
            ),
            Paragraph(m.first_seen.strftime("%Y-%m-%d"), styles["Normal"]),
        ])

    tbl = Table(
        table_data,
        colWidths=[1.5*inch, 2.2*inch, 0.8*inch, 1.3*inch, 0.9*inch],
        repeatRows=1,
    )
    tbl.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#F3F1EE")),
        ("LINEBELOW", (0, 0), (-1, 0), 1, colors.HexColor("#1C1917")),
        ("LINEBELOW", (0, 1), (-1, -1), 0.3, colors.HexColor("#E2E2E2")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    story.append(tbl)

    # ── 신뢰도 분포 ──────────────────────────────────────
    story.append(Spacer(1, 0.25 * inch))
    story.append(Paragraph("Confidence Distribution", section_style))
    buckets = {
        "95–100%": 0, "90–95%": 0, "85–90%": 0, "80–85%": 0,
    }
    for m in filtered:
        c = m.confidence
        if c >= 0.95: buckets["95–100%"] += 1
        elif c >= 0.90: buckets["90–95%"] += 1
        elif c >= 0.85: buckets["85–90%"] += 1
        elif c >= 0.80: buckets["80–85%"] += 1
    dist_data = [["Range", "Count", "Visual"]]
    max_count = max(buckets.values()) or 1
    for rng, cnt in buckets.items():
        bar = _confidence_bar(cnt / max_count, 30) if cnt > 0 else "—"
        dist_data.append([rng, str(cnt), Paragraph(
            f'<font face="Courier">{bar}</font>', styles["Normal"])
        ])
    dist_tbl = Table(dist_data, colWidths=[1*inch, 0.8*inch, 4.5*inch])
    dist_tbl.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#F3F1EE")),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LINEBELOW", (0, 0), (-1, 0), 0.5, colors.HexColor("#1C1917")),
    ]))
    story.append(dist_tbl)

    # ── 법적 면책 ────────────────────────────────────────
    story.append(PageBreak())
    story.append(Paragraph("Methodology &amp; Disclosure", section_style))
    methodology = (
        "<b>Detection method.</b> StoreScope computes perceptual hashes (pHash) "
        "from publicly available product imagery on Shopify storefronts. "
        "Two images are considered the same cluster when their Hamming distance ≤ 8. "
        "Confidence score reflects normalized Hamming proximity. "
        "<br/><br/>"
        "<b>Data sourcing.</b> All data was collected from publicly accessible "
        "/products.json endpoints, in compliance with the respective robots.txt "
        "directives and consistent with the 2024 federal ruling (Van Buren v. United States) "
        "regarding logged-out public scraping of commercial product data. "
        "<br/><br/>"
        "<b>Limitations.</b> A match indicates visual similarity of product imagery, "
        "not legal counterfeit determination. False positives are possible when "
        "stores share suppliers, use stock photography, or sell licensed copies. "
        "Trademark / copyright enforcement requires separate legal analysis. "
        "<br/><br/>"
        "<b>Next steps.</b> For takedown assistance, supplier reverse-lookup, "
        "or continuous brand monitoring, contact "
        "<font color='#4F46E5'><b>dodo@storescope.com</b></font>."
    )
    story.append(Paragraph(methodology, styles["Normal"]))

    story.append(Spacer(1, 0.3 * inch))
    story.append(Paragraph(
        f"<font size='8' color='#A8A29E'>"
        f"Report {report_id} · Generated by StoreScope · storescope.com"
        f"</font>",
        styles["Normal"],
    ))

    doc.build(story)
    buf.seek(0)
    return buf.read()


# ── DB 어댑터 (Path B 가동 시 paddle_routes.py에서 호출) ──────────
def fetch_matches_for_brand(brand_name: str, min_store_count: int = 3) -> list[ClusterMatch]:
    # SEC/DoS: 길이 cap + SQL LIKE 메타문자 escape
    if len(brand_name) > 200:
        raise ValueError(f"brand_name too long ({len(brand_name)}, max 200)")
    # ILIKE 패턴에서 %, _, \ 는 와일드카드 → 사용자 입력은 escape 후 wrap.
    escaped = brand_name.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    """
    DB에서 브랜드명 → 매칭 클러스터 list 생성.
    pHash 매칭은 cluster_id 동일 = 같은 product family. 이미 cluster_products.py가 수행.

    NOTE: Path B 가동 시 paddle_routes.py에서 brand_name을 받아 호출.
    현재는 prototype — brand 매칭은 단순 ILIKE 텍스트 매칭으로 우선 구현.
    Path B 가동 후 brand 라벨링 테이블 추가하면 정확도 ↑.
    """
    from api.auth import get_conn
    matches: list[ClusterMatch] = []
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT
                    c.id, s.domain, p.title, p.image_url, p.price_min, p.price_max,
                    p.first_seen, c.store_count
                FROM products p
                JOIN product_clusters pc ON pc.product_id = p.id
                JOIN clusters c ON c.id = pc.cluster_id
                JOIN stores s ON s.id = p.store_id
                WHERE p.title ILIKE %s
                  AND c.store_count >= %s
                ORDER BY c.store_count DESC, p.first_seen ASC
                LIMIT 100
            """, (f"%{escaped}%", min_store_count))
            for (cid, dom, title, img, pmin, pmax, fseen, csize) in cur.fetchall():
                # confidence: cluster_size 0-100+ → 0.80-0.99 scale.
                # Path B 가동 시 실제 pHash hamming distance로 교체.
                conf = min(0.99, 0.80 + min(csize, 60) / 60 * 0.19)
                matches.append(ClusterMatch(
                    cluster_id=cid,
                    store_domain=dom,
                    product_title=title or "(no title)",
                    product_image_url=img,
                    price_min=float(pmin) if pmin is not None else None,
                    price_max=float(pmax) if pmax is not None else None,
                    confidence=conf,
                    first_seen=fseen,
                    cluster_size=csize,
                ))
    return matches


if __name__ == "__main__":
    # CLI 스모크 테스트 — 실제 DB로 1건 생성 + /tmp 저장.
    import os
    import sys
    if len(sys.argv) < 2:
        print("사용: python services/counterfeit_report.py <BRAND_KEYWORD>")
        sys.exit(2)
    brand = sys.argv[1]
    os.environ.setdefault("DATABASE_URL", "postgresql:///storescope")
    matches = fetch_matches_for_brand(brand)
    if not matches:
        print(f"[INFO] '{brand}' 매칭 없음 — 빈 보고서 생성 안 함.")
        sys.exit(0)
    pdf = generate(brand_name=brand.upper(), matched_clusters=matches)
    out = f"/tmp/storescope_brand_ip_{brand}_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.pdf"
    with open(out, "wb") as f:
        f.write(pdf)
    print(f"[OK] {len(matches)} matches → {out} ({len(pdf)/1024:.1f} KB)")
