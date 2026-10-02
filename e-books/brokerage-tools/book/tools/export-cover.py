#!/usr/bin/env python3
"""
Export the front cover as a standalone high-resolution PNG, for store listings.

The image is rasterized from page 1 of the built PDF itself, so the cover image and the
book's own cover can never drift apart. Build the book first.

  tools/export-cover.py          -> cover.png (1836 x 2376, 216 DPI)
  tools/export-cover.py 300      -> a different resolution, in DPI
"""
import pathlib
import shutil
import subprocess
import sys

BOOK = pathlib.Path(__file__).resolve().parent.parent
PDF = BOOK / "brokerage-tools.pdf"
OUT = BOOK / "cover.png"
DPI = sys.argv[1] if len(sys.argv) > 1 else "216"

if not PDF.exists():
    sys.exit(f"{PDF.name} not found; run build-book.sh first")
if not shutil.which("pdftoppm"):
    sys.exit("pdftoppm (poppler) is required")

stem = BOOK / "_cover"
subprocess.run(["pdftoppm", "-r", DPI, "-f", "1", "-l", "1", "-singlefile", "-png", str(PDF), str(stem)],
               check=True)
produced = stem.with_suffix(".png")
produced.replace(OUT)

head = OUT.read_bytes()[16:24]
w, h = int.from_bytes(head[:4], "big"), int.from_bytes(head[4:], "big")
print(f"  {OUT.name}  {w} x {h} px  ({OUT.stat().st_size // 1024} KB, {DPI} DPI)")
