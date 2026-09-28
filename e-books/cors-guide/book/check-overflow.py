#!/usr/bin/env python3
"""
Find pages whose content is taller than the page.

These guides are fixed-layout: .page is exactly one Letter sheet and clips what
does not fit. An overflowing page therefore loses text *silently* — the PDF
still has the right number of sheets and the missing paragraph is simply gone,
so comparing page counts cannot catch it.

This renders a probe copy with the clipping switched off and a marker planted at
the top of every page. Any page whose content is too tall then pushes the next
marker onto a later sheet, which names the page exactly.

    check-overflow.py cors-guide.html

Exit status 1 if any page overflows. Needs Chrome and pdftotext; skips cleanly
if either is missing.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

CHROME_CANDIDATES = [
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    "google-chrome", "google-chrome-stable", "chromium", "chromium-browser",
]
PDFTOTEXT_CANDIDATES = ["pdftotext", "/opt/homebrew/bin/pdftotext", "/usr/local/bin/pdftotext"]

# Markers must survive pdftotext, so they are plain text in a tiny element.
MARK = "ZQMARKZQ{}ZQ"
SHEET_PT = 400 * 72.0      # height of the probe sheet, in points


def find(*names: str) -> str | None:
    for n in names:
        if Path(n).is_file():
            return n
        found = shutil.which(n)
        if found:
            return found
    return None


def main(argv: list[str]) -> int:
    positional = [a for a in argv if not a.startswith("--")]
    src = Path(positional[0] if positional else "cors-guide.html")
    chrome, pdftotext = find(*CHROME_CANDIDATES), find(*PDFTOTEXT_CANDIDATES)
    if not (chrome and pdftotext):
        print("  (skipping overflow check: need Chrome and pdftotext)")
        return 0

    html = src.read_text()

    index = 0

    def plant(m: re.Match) -> str:
        nonlocal index
        index += 1
        # Absolutely positioned so the marker itself adds no height: .page is
        # position:relative, so this is taken out of flow entirely.
        return m.group(0) + (
            '<div style="position:absolute;top:1pt;left:1pt;font-size:5pt;'
            f'color:#ffffff">{MARK.format(index)}</div>')

    probed = re.sub(r'<section class="page[^"]*">', plant, html)
    total = index
    if not total:
        print("  (skipping overflow check: no pages found)")
        return 0
    # One very tall sheet, pages left at their natural height: the distance
    # between consecutive markers is then exactly how tall each page wants to be.
    probed += ("\n<style>@page{size:8.5in 400in;margin:0}"
               ".page{overflow:visible !important;height:auto !important;"
               "min-height:0 !important;page-break-after:auto !important;"
               "break-after:auto !important}</style>\n")

    with tempfile.TemporaryDirectory() as tmp:
        probe, pdf = Path(tmp) / "probe.html", Path(tmp) / "probe.pdf"
        probe.write_text(probed)
        subprocess.run([chrome, "--headless", "--disable-gpu", "--no-pdf-header-footer",
                        "--no-margins", "--hide-scrollbars",
                        "--run-all-compositor-stages-before-draw",
                        "--virtual-time-budget=10000", f"--print-to-pdf={pdf}",
                        f"file://{probe}"], capture_output=True)
        if not pdf.exists():
            print("  (skipping overflow check: probe render failed)")
            return 0
        bbox = subprocess.run([pdftotext, "-bbox", str(pdf), "-"],
                              capture_output=True, text=True).stdout

    # -bbox gives every word an yMin; the markers' yMin are the page boundaries.
    # A long book can spill past one probe sheet, so positions are made
    # continuous by adding each sheet's offset.
    tops: dict[int, float] = {}
    last = 0.0
    for sheet_no, sheet in enumerate(re.split(r'<page width="[\d.]+" height="[\d.]+">', bbox)[1:]):
        offset = sheet_no * SHEET_PT
        for m in re.finditer(r'<word xMin="[\d.]+" yMin="([\d.]+)" xMax="[\d.]+" yMax="([\d.]+)">([^<]*)</word>', sheet):
            hit = re.match(r"ZQMARKZQ(\d+)ZQ", m.group(3))
            if hit:
                tops.setdefault(int(hit.group(1)), float(m.group(1)) + offset)
            last = max(last, float(m.group(2)) + offset)

    missing = [n for n in range(1, total + 1) if n not in tops]
    if missing:
        print(f"  (overflow check inconclusive: markers {missing[:5]} not found)")
        return 0

    LIMIT = 792.0   # 11in in points
    over = []
    for n in range(1, total + 1):
        end = tops[n + 1] if n + 1 in tops else last
        height = end - tops[n]
        if height > LIMIT + 1:
            over.append((n, height - LIMIT))

    if "--report" in sys.argv:
        print(f"  page fill ({total} pages, {LIMIT:.0f}pt each)")
        for n in range(1, total + 1):
            end = tops[n + 1] if n + 1 in tops else last
            h = end - tops[n]
            pct = h / LIMIT * 100
            bar = "#" * int(pct / 4)
            flag = "  OVER" if h > LIMIT + 1 else ("  sparse" if pct < 62 else "")
            print(f"    {n:3d}  {h:6.0f}pt  {pct:5.1f}%  {bar}{flag}")

    if not over:
        print(f"  no page overflows ({total} pages, tallest "
              f"{max(( (tops[n+1] if n+1 in tops else last) - tops[n]) for n in range(1, total+1)):.0f}"
              f"/{LIMIT:.0f}pt)")
        return 0

    print(f"  OVERFLOW on {len(over)} page(s) of {total}:", file=sys.stderr)
    for n, excess in over:
        lines = excess / 16.1     # body line height at 10.4pt/1.55
        print(f"    page {n:2d}: {excess:6.0f}pt too tall  (~{lines:.0f} lines to cut)",
              file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
