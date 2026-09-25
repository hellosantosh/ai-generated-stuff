#!/usr/bin/env python3
"""
Render the square store thumbnail (thumbnail.png, 1000 x 1000).

Storefronts such as Gumroad show a square thumbnail alongside the full cover.
A tall cover cropped to a square loses the title, so the thumbnail is its own
small layout that uses the cover's colors, emblem and title.

  ./export-thumbnail.py
"""
import os
import pathlib
import shutil
import subprocess
import sys

HERE = pathlib.Path(__file__).parent
OUT = HERE / "thumbnail.png"

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

PAGE = """<!doctype html><html lang="en-US"><head><meta charset="utf-8"><style>
html, body { margin: 0; width: 1000px; height: 1000px; overflow: hidden; }
body { font-family: "Helvetica Neue", Helvetica, Arial, sans-serif; color: #f2f5ff;
  background:
    radial-gradient(760px 560px at 50% 58%, rgba(91,140,255,.30), transparent 62%),
    radial-gradient(520px 420px at 88% 96%, rgba(224,168,74,.16), transparent 66%),
    linear-gradient(178deg, #0c1224 0%, #0a0f1e 50%, #06090f 100%);
  position: relative; }
.grid { position: absolute; inset: 0;
  background-image: linear-gradient(to right, rgba(255,255,255,.03) 1px, transparent 1px),
                    linear-gradient(to bottom, rgba(255,255,255,.03) 1px, transparent 1px);
  background-size: 40px 40px; }
.spine { position: absolute; left: 0; top: 0; bottom: 0; width: 18px;
  background: linear-gradient(180deg, #4ad3a6 0%, #5b8cff 55%, #e0a84a 100%); }
.inner { position: absolute; inset: 0; padding: 70px 80px 60px 96px; }
.kicker { font-family: "SF Mono", Menlo, monospace; font-size: 21px; font-weight: 700;
  letter-spacing: .16em; text-transform: uppercase; color: #7ea6ff; }
h1 { margin: 14px 0 0; font-size: 170px; line-height: .92; letter-spacing: -0.035em; }
.sub { font-size: 50px; line-height: 1.08; color: #9db4ff; margin-top: 10px; letter-spacing: -0.02em; }
.rule { width: 200px; height: 7px; border-radius: 4px; margin: 30px 0 0;
  background: linear-gradient(90deg, #4ad3a6, #5b8cff); }
.art { position: absolute; left: 96px; right: 80px; bottom: 150px; }
.foot { position: absolute; left: 96px; right: 80px; bottom: 60px; display: flex;
  justify-content: space-between; align-items: baseline;
  border-top: 1px solid rgba(255,255,255,.16); padding-top: 22px; }
.author { font-size: 34px; font-weight: 700; }
.badge { font-family: "SF Mono", Menlo, monospace; font-size: 19px; letter-spacing: .1em;
  text-transform: uppercase; color: #b9c4e4; }
</style></head><body>
<div class="grid"></div><div class="spine"></div>
<div class="inner">
  <div class="kicker">The Ultimate Developer's Guide to</div>
  <h1>CORS</h1>
  <div class="sub">Border Control for the Web</div>
  <div class="rule"></div>
</div>
<div class="art">
  <svg viewBox="0 0 560 220" xmlns="http://www.w3.org/2000/svg" width="100%">
    <g transform="translate(70,110)">
      <circle r="56" fill="#0e1630" stroke="#4ad3a6" stroke-width="3"/>
      <rect x="-28" y="-19" width="56" height="38" rx="5" fill="none" stroke="#4ad3a6" stroke-width="2.4"/>
      <line x1="-28" y1="-9" x2="28" y2="-9" stroke="#4ad3a6" stroke-width="2"/>
    </g>
    <g transform="translate(490,110)">
      <circle r="56" fill="#0e1630" stroke="#e0a84a" stroke-width="3"/>
      <g fill="none" stroke="#e0a84a" stroke-width="2.4">
        <ellipse cx="0" cy="-15" rx="21" ry="7"/>
        <path d="M-21,-15 v29 a21,7 0 0 0 42,0 v-29"/>
      </g>
    </g>
    <path d="M130,92 L222,92" stroke="#4ad3a6" stroke-width="3" stroke-dasharray="9 7"/>
    <path d="M338,92 L428,92" stroke="#5b8cff" stroke-width="3" stroke-dasharray="9 7"/>
    <path d="M428,128 L338,128" stroke="#e0a84a" stroke-width="3"/>
    <path d="M222,128 L130,128" stroke="#4ad3a6" stroke-width="3"/>
    <g transform="translate(280,110)">
      <path d="M0,-82 L60,-47 L60,47 L0,82 L-60,47 L-60,-47 Z" fill="#0d1428" stroke="#7ea6ff" stroke-width="3"/>
      <rect x="-4.5" y="-32" width="9" height="58" rx="2" fill="#c7d6ff"/>
      <rect x="-36" y="-38" width="72" height="10" rx="5" fill="#7ea6ff"/>
    </g>
  </svg>
</div>
<div class="foot">
  <div class="author">Smiroh Dev</div>
  <div class="badge">8 hands-on labs &#183; 61 pages</div>
</div>
</body></html>"""

tmp = HERE / "_thumbnail.html"
tmp.write_text(PAGE)
subprocess.run([chrome, "--headless", "--disable-gpu", "--no-first-run", "--hide-scrollbars",
                "--force-device-scale-factor=1", "--window-size=1000,1000",
                f"--screenshot={OUT}", f"file://{tmp.resolve()}"], capture_output=True)
tmp.unlink(missing_ok=True)

if not OUT.exists() or OUT.stat().st_size == 0:
    sys.exit("thumbnail.png was not produced")
head = OUT.read_bytes()[16:24]
print(f"  {OUT.name}  {int.from_bytes(head[:4], 'big')} x {int.from_bytes(head[4:], 'big')} px"
      f"  ({OUT.stat().st_size // 1024} KB)")
