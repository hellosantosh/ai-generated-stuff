#!/usr/bin/env python3
"""
Render the square store thumbnail (thumbnail.png, 1000 x 1000).

Storefronts show a square thumbnail beside the full cover. A tall cover cropped to a square
loses its title, so the thumbnail is its own small layout using the cover's colors, title
and link graph. The page count is read from the built PDF.

  tools/export-thumbnail.py
"""
import os
import pathlib
import re
import shutil
import subprocess
import sys

BOOK = pathlib.Path(__file__).resolve().parent.parent
OUT = BOOK / "thumbnail.png"
PDF = BOOK / "rest-api-hateoas.pdf"

CANDIDATES = [
    os.environ.get("CHROME_BIN", ""),
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    shutil.which("google-chrome") or "",
    shutil.which("chromium") or "",
]
chrome = next((c for c in CANDIDATES if c and os.access(c, os.X_OK)), None)
if not chrome:
    sys.exit("Could not find Chrome or Chromium. Set CHROME_BIN and re-run.")

pages = "256"
if PDF.exists() and shutil.which("pdfinfo"):
    info = subprocess.run(["pdfinfo", str(PDF)], capture_output=True, text=True).stdout
    found = re.search(r"^Pages:\s+(\d+)", info, re.M)
    pages = found.group(1) if found else pages

PAGE = """<!doctype html><html lang="en-US"><head><meta charset="utf-8"><style>
html, body { margin: 0; width: 1000px; height: 1000px; overflow: hidden; }
body { font-family: "Helvetica Neue", Helvetica, Arial, sans-serif; color: #f2f5ff; position: relative;
  background:
    radial-gradient(760px 560px at 50% 62%, rgba(74,211,166,.22), transparent 62%),
    radial-gradient(520px 420px at 88% 96%, rgba(91,140,255,.24), transparent 66%),
    linear-gradient(178deg, #0c1224 0%, #0a0f1e 50%, #06090f 100%); }
.grid { position: absolute; inset: 0;
  background-image: linear-gradient(to right, rgba(255,255,255,.03) 1px, transparent 1px),
                    linear-gradient(to bottom, rgba(255,255,255,.03) 1px, transparent 1px);
  background-size: 40px 40px; }
.spine { position: absolute; left: 0; top: 0; bottom: 0; width: 18px;
  background: linear-gradient(180deg, #4ad3a6 0%, #5b8cff 55%, #a06bff 100%); }
.inner { position: absolute; inset: 0; padding: 66px 80px 60px 96px; }
.kicker { font-family: Menlo, monospace; font-size: 22px; font-weight: 700; letter-spacing: .16em;
  text-transform: uppercase; color: #6fe0bb; }
h1 { margin: 16px 0 0; font-size: 104px; line-height: .98; letter-spacing: -0.035em; }
.sub { font-size: 64px; line-height: 1.05; color: #9db4ff; margin-top: 8px; letter-spacing: -0.02em; }
.rule { width: 200px; height: 7px; border-radius: 4px; margin: 30px 0 0;
  background: linear-gradient(90deg, #4ad3a6, #5b8cff); }
.art { position: absolute; left: 96px; right: 80px; bottom: 150px; }
.foot { position: absolute; left: 96px; right: 80px; bottom: 60px; display: flex;
  justify-content: space-between; align-items: baseline;
  border-top: 1px solid rgba(255,255,255,.16); padding-top: 22px; }
.author { font-size: 34px; font-weight: 700; }
.badge { font-family: Menlo, monospace; font-size: 19px; letter-spacing: .1em; text-transform: uppercase;
  color: #b9c4e4; }
</style></head><body>
<div class="grid"></div><div class="spine"></div>
<div class="inner">
  <div class="kicker">A practical guide to</div>
  <h1>REST API Standards</h1>
  <div class="sub">with HATEOAS</div>
  <div class="rule"></div>
</div>
<div class="art">
  <svg viewBox="0 0 560 250" xmlns="http://www.w3.org/2000/svg" width="100%" font-family="Menlo,monospace">
    <g fill="none" stroke-width="3">
      <path d="M262,52 C220,70 190,88 176,108" stroke="#7ea6ff"/>
      <path d="M298,52 C340,70 370,88 384,108" stroke="#7ea6ff"/>
      <path d="M146,146 C120,170 100,188 88,204" stroke="#4ad3a6"/>
      <path d="M160,154 C158,180 156,198 156,214" stroke="#4ad3a6"/>
      <path d="M176,146 C200,168 216,186 226,202" stroke="#4ad3a6"/>
      <path d="M392,154 C384,178 374,196 368,208" stroke="#7ea6ff"/>
      <path d="M414,146 C434,168 448,186 456,202" stroke="#7ea6ff"/>
    </g>
    <path d="M280,10 L306,25 L306,55 L280,70 L254,55 L254,25 Z" fill="#0d1428" stroke="#c7d6ff" stroke-width="3"/>
    <text x="280" y="47" text-anchor="middle" font-size="22" font-weight="700" fill="#fff">/</text>
    <g fill="#0e1630" stroke-width="3">
      <circle cx="160" cy="130" r="24" stroke="#4ad3a6"/><circle cx="400" cy="130" r="24" stroke="#7ea6ff"/>
      <circle cx="80" cy="220" r="17" stroke="#4ad3a6"/><circle cx="156" cy="232" r="17" stroke="#4ad3a6"/>
      <circle cx="236" cy="220" r="17" stroke="#e0a84a"/><circle cx="362" cy="226" r="17" stroke="#7ea6ff"/>
      <circle cx="464" cy="220" r="17" stroke="#7ea6ff"/>
    </g>
  </svg>
</div>
<div class="foot">
  <div class="author">Smiroh Dev</div>
  <div class="badge">Spring Boot 4 &#183; Java 27 &#183; __PAGES__ pages</div>
</div>
</body></html>"""

tmp = BOOK / "_thumbnail.html"
tmp.write_text(PAGE.replace("__PAGES__", pages))
subprocess.run(["perl", "-e", "alarm 60; exec @ARGV", chrome, "--headless", "--disable-gpu", "--no-first-run",
                "--hide-scrollbars", "--force-device-scale-factor=1", "--window-size=1000,1000",
                f"--screenshot={OUT}", f"file://{tmp.resolve()}"], capture_output=True)
tmp.unlink(missing_ok=True)

if not OUT.exists() or OUT.stat().st_size == 0:
    sys.exit("thumbnail.png was not produced")
head = OUT.read_bytes()[16:24]
print(f"  {OUT.name}  {int.from_bytes(head[:4], 'big')} x {int.from_bytes(head[4:], 'big')} px"
      f"  ({OUT.stat().st_size // 1024} KB)")
