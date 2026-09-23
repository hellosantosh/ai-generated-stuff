#!/usr/bin/env bash
#
# Build Growth-Stock-Screener-Guide.pdf from Growth-Stock-Screener-Guide.html
# with headless Chrome.
#
# Usage:  tools/build-pdf.sh [output.pdf]

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SRC="$ROOT/Growth-Stock-Screener-Guide.html"
OUT="${1:-$ROOT/Growth-Stock-Screener-Guide.pdf}"

CHROME="${CHROME:-}"
if [[ -z "$CHROME" ]]; then
  for candidate in \
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" \
    "/Applications/Chromium.app/Contents/MacOS/Chromium" \
    "$(command -v google-chrome || true)" \
    "$(command -v chromium || true)" \
    "$(command -v chromium-browser || true)"
  do
    if [[ -n "$candidate" && -x "$candidate" ]]; then CHROME="$candidate"; break; fi
  done
fi

if [[ -z "$CHROME" ]]; then
  echo "error: no Chrome or Chromium found. Set CHROME=/path/to/binary." >&2
  echo "The HTML renders in any browser: open it and print to PDF." >&2
  exit 1
fi

echo "Rendering with $(basename "$CHROME")..."
"$CHROME" \
  --headless \
  --disable-gpu \
  --no-sandbox \
  --no-pdf-header-footer \
  --print-to-pdf="$OUT" \
  --run-all-compositor-stages-before-draw \
  --virtual-time-budget=10000 \
  "file://$SRC" 2>/dev/null

echo "Wrote $OUT ($(du -h "$OUT" | cut -f1))"
