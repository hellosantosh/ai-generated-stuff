#!/usr/bin/env python3
"""
Render the square store thumbnail (thumbnail.png, 1000 x 1000).

Storefronts show a square thumbnail beside the full cover. A tall cover cropped to a square
loses its title, so the thumbnail is its own small layout using the cover's colors, title and
diagram. The page count is read from the built PDF; the tool count is read from the catalog.

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
PDF = BOOK / "brokerage-tools.pdf"

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

pages = "390"
if PDF.exists() and shutil.which("pdfinfo"):
    info = subprocess.run(["pdfinfo", str(PDF)], capture_output=True, text=True).stdout
    found = re.search(r"^Pages:\s+(\d+)", info, re.M)
    pages = found.group(1) if found else pages

CATALOG = BOOK.parent.parent.parent / "brokerage-tools/brokerage-catalog/src/main/resources/catalog"
tools_count = len(list(CATALOG.glob("*.json"))) if CATALOG.exists() else 14

PAGE = """<!doctype html><html lang="en-US"><head><meta charset="utf-8"><style>
html, body { margin: 0; width: 1000px; height: 1000px; overflow: hidden; }
body { font-family: "Helvetica Neue", Helvetica, Arial, sans-serif; color: #f2f5ff; position: relative;
  background:
    radial-gradient(760px 560px at 50% 62%, rgba(255,186,84,.17), transparent 62%),
    radial-gradient(520px 420px at 88% 96%, rgba(91,140,255,.24), transparent 66%),
    linear-gradient(178deg, #0c1224 0%, #0a0f1e 50%, #06090f 100%); }
.grid { position: absolute; inset: 0;
  background-image: linear-gradient(to right, rgba(255,255,255,.03) 1px, transparent 1px),
                    linear-gradient(to bottom, rgba(255,255,255,.03) 1px, transparent 1px);
  background-size: 40px 40px; }
.spine { position: absolute; left: 0; top: 0; bottom: 0; width: 18px;
  background: linear-gradient(180deg, #ffba54 0%, #5b8cff 55%, #a06bff 100%); }
.inner { position: absolute; inset: 0; padding: 66px 80px 60px 96px; }
.kicker { font-family: Menlo, monospace; font-size: 22px; font-weight: 700; letter-spacing: .16em;
  text-transform: uppercase; color: #ffcb7a; }
h1 { margin: 16px 0 0; font-size: 104px; line-height: .98; letter-spacing: -0.035em; }
.sub { font-size: 64px; line-height: 1.05; color: #9db4ff; margin-top: 8px; letter-spacing: -0.02em; }
.rule { width: 200px; height: 7px; border-radius: 4px; margin: 30px 0 0;
  background: linear-gradient(90deg, #ffba54, #5b8cff); }
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
  <div class="kicker">A working specification</div>
  <h1>Tool Specs<br>&amp; Design</h1>
  <div class="sub">for the brokerage domain</div>
  <div class="rule"></div>
</div>
<div class="art">
  <svg viewBox="0 0 560 230" xmlns="http://www.w3.org/2000/svg" width="100%"
       font-family="Menlo,monospace">
    <defs>
      <marker id="t-ar" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="5" markerHeight="5"
              orient="auto-start-reverse">
        <path d="M0 0 L10 5 L0 10 z" fill="#8fa2d4"/>
      </marker>
    </defs>
    <g stroke="#8fa2d4" stroke-width="2.4" fill="none" marker-end="url(#t-ar)" opacity="0.8">
      <path d="M176 38 C206 38, 206 92, 230 92"/>
      <path d="M176 92 L230 92"/>
      <path d="M176 146 C206 146, 206 100, 230 100"/>
      <path d="M404 96 L434 96"/>
    </g>
    <g stroke-width="3" fill="#0e1630">
      <rect x="16" y="20" width="160" height="36" rx="8" stroke="#6fe0bb"/>
      <rect x="16" y="74" width="160" height="36" rx="8" stroke="#6fe0bb"/>
      <rect x="16" y="128" width="160" height="36" rx="8" stroke="#6fe0bb"/>
      <rect x="236" y="34" width="168" height="124" rx="10" stroke="#ffba54"/>
      <rect x="440" y="78" width="108" height="36" rx="8" stroke="#7ea6ff"/>
    </g>
    <g font-size="17" fill="#cfdaf7">
      <text x="34" y="44">descriptor</text>
      <text x="34" y="98">ontology</text>
      <text x="34" y="152">registry</text>
      <text x="256" y="60" fill="#ffcb7a" font-size="17">the pipeline</text>
      <text x="256" y="88" font-size="15" fill="#9aa8cc">validate</text>
      <text x="256" y="112" font-size="15" fill="#9aa8cc">entitle</text>
      <text x="256" y="136" font-size="15" fill="#9aa8cc">approve</text>
      <text x="458" y="101" fill="#9db4ff">Claude</text>
    </g>
    <g font-size="15" fill="#7f8db8">
      <text x="16" y="200">__TOOLS__ tools &#183; one descriptor each</text>
      <text x="16" y="222">nothing it can talk its way past</text>
    </g>
  </svg>
</div>
<div class="foot">
  <div class="author">Smiroh Dev</div>
  <div class="badge">Spring AI 2 &#183; Angular 22 &#183; __PAGES__ pages</div>
</div>
</body></html>"""

tmp = BOOK / "_thumbnail.html"
tmp.write_text(PAGE.replace("__PAGES__", pages).replace("__TOOLS__", str(tools_count)))
subprocess.run(["perl", "-e", "alarm 60; exec @ARGV", chrome, "--headless", "--disable-gpu", "--no-first-run",
                "--hide-scrollbars", "--force-device-scale-factor=1", "--window-size=1000,1000",
                f"--screenshot={OUT}", f"file://{tmp.resolve()}"], capture_output=True)
tmp.unlink(missing_ok=True)

if not OUT.exists() or OUT.stat().st_size == 0:
    sys.exit("thumbnail.png was not produced")
head = OUT.read_bytes()[16:24]
print(f"  {OUT.name}  {int.from_bytes(head[:4], 'big')} x {int.from_bytes(head[4:], 'big')} px"
      f"  ({OUT.stat().st_size // 1024} KB)")
