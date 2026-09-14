#!/usr/bin/env python3
# EN/KO/JA STORESCOPE_I18N dict key counts — verify_landing.sh check #28 helper.
# 사용: python3 deploy/i18n_key_counts.py <html_file>
# 출력: "EN_COUNT KO_COUNT JA_COUNT" (공백 구분, stdout)
import re
import sys

if len(sys.argv) < 2:
    print("usage: i18n_key_counts.py <html_file>", file=sys.stderr)
    sys.exit(2)

with open(sys.argv[1], encoding="utf-8") as f:
    html = f.read()

m = re.search(r"window\.STORESCOPE_I18N\s*=\s*\{(.*?)\n\s*\};", html, re.S)
block = m.group(1) if m else ""

starts = [(mm.start(), mm.group(1)) for mm in re.finditer(r"\n\s{6}(en|ko|ja):\s*\{", block)]
starts.append((len(block), None))

counts = {}
for i in range(len(starts) - 1):
    start, lang = starts[i]
    end = starts[i + 1][0]
    counts[lang] = len(re.findall(r'\n\s+[a-zA-Z0-9_]+:\s*["\']', block[start:end]))

print(counts.get("en", 0), counts.get("ko", 0), counts.get("ja", 0))
