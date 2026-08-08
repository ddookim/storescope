#!/usr/bin/env python3
# visible English residue sweep — 영구 룰 (D+24 R4)
# 사용: python3 deploy/sweep_residue.py <html_file>
# 출력: stdout = 잔재 카운트 (0이면 PASS)
# stderr = 잔재 텍스트 목록 (debug)
import re
import sys

if len(sys.argv) < 2:
    print("usage: sweep_residue.py <html_file>", file=sys.stderr)
    sys.exit(2)

with open(sys.argv[1]) as f:
    html = f.read()

# script/style/주석 제거 (visible text만)
clean = re.sub(r'<script[\s\S]*?</script>', '', html)
clean = re.sub(r'<style[\s\S]*?</style>', '', clean)
clean = re.sub(r'<!--[\s\S]*?-->', '', clean)

# 텍스트 노드: ">TEXT<" 또는 "</svg>TEXT<" (SVG 자식 다음 텍스트)
pattern = re.compile(
    r"(?:</svg>|>)([A-Z][a-zA-Z][a-zA-Z0-9 ,.'\-:&;]{2,150}?)\s*<"
)

# 제외: 제품명, 도메인, 브랜드, 약어 (번역 X)
EXCLUDE = [
    'Projector', 'LED Strip', 'Car Phone Mount', 'Car Mount', 'Silicone',
    'Wafer', 'Drilling', 'Reusable Bags', 'BZP', 'PH-',
    'myshopify', 'storescope.com', 'StoreScope', '.com', '.co', '.store',
    '/storescope', '$', 'minimalist-home', 'gadgetnest', 'trendpicks',
    'SS', '@', 'app.storescope', 'http', 'href',
    'GDPR', 'VAT', 'MoR', 'Powered by', 'Hetzner', 'PRO', 'NEW', 'BETA',
    'Log in',  # data-i18n="nav_login" 적용됨, mailto link 너무 길어 sweep window 못 잡음
    'Leave empty',  # Netlify Forms honeypot label — display:none via parent style, invisible to users
]

seen = set()
residue = []
for m in pattern.finditer(clean):
    text = m.group(1).strip()
    if text in seen:
        continue
    if any(ord(c) > 127 for c in text):
        continue
    if any(kw in text for kw in EXCLUDE):
        continue
    if len(text) > 200 or len(text) < 4:
        continue
    if text.count(' ') < 1:
        continue
    start = m.start()
    surrounding = clean[max(0, start - 250):start + 100]
    if 'data-i18n' in surrounding[-200:]:
        continue
    seen.add(text)
    residue.append(text)

if residue:
    print(f"잔재 {len(residue)}건:", file=sys.stderr)
    for txt in residue:
        print(f"  - {txt}", file=sys.stderr)

print(len(residue))
