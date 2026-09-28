#!/usr/bin/env python3
"""
Export the front cover as a standalone high-resolution PNG.

Retailers ask for the cover as an image, separately from the book file, for
store listings and thumbnails. This renders page 1 of the source at ~216 DPI
using the same Chrome that builds the PDF, so the image and the book's own
cover can never drift apart.

  ./export-cover.py            -> cover.png  (1836 x 2376)
  ./export-cover.py 3.0        -> a larger scale factor
"""
import pathlib, re, shutil, subprocess, sys, os

HERE = pathlib.Path(__file__).parent
SRC = HERE / "cors-guide.html"
OUT = HERE / "cover.png"
SCALE = sys.argv[1] if len(sys.argv) > 1 else "2.25"

CANDIDATES = [
    os.environ.get("CHROME_BIN", ""),
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    shutil.which("google-chrome") or "",
    shutil.which("google-chrome-stable") or "",
    shutil.which("chromium") or "",
    shutil.which("chromium-browser") or "",
]
chrome = next((c for c in CANDIDATES if c and os.access(c, os.X_OK)), None)
if not chrome:
    sys.exit("Could not find Chrome or Chromium. Set CHROME_BIN and re-run.")

html = SRC.read_text()
style = re.search(r"<style>.*?</style>", html, re.S).group(0)
cover = re.search(r'<section class="page bookcover[^"]*">.*?</section>', html, re.S)
if not cover:
    sys.exit("could not find the cover section in the source")

tmp = HERE / "_cover_export.html"
tmp.write_text(f'<!doctype html><html><head><meta charset="utf-8">{style}</head>'
               f'<body style="margin:0">{cover.group(0)}</body></html>')

subprocess.run([chrome, "--headless", "--disable-gpu", "--hide-scrollbars",
                f"--force-device-scale-factor={SCALE}", "--window-size=816,1056",
                f"--screenshot={OUT}", f"file://{tmp}"],
               capture_output=True)

tmp.unlink(missing_ok=True)
if not OUT.exists() or OUT.stat().st_size == 0:
    sys.exit("cover.png was not produced")

# read the dimensions straight out of the PNG header
head = OUT.read_bytes()[16:24]
w = int.from_bytes(head[:4], "big")
h = int.from_bytes(head[4:], "big")
print(f"  {OUT.name}  {w} x {h} px  ({OUT.stat().st_size // 1024} KB, ~{round(w / 8.5)} DPI)")
