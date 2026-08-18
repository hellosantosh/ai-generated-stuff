#!/usr/bin/env bash
#
# Build brokerage-apis/developer-specs.pdf from the fragments in pdf-src/.
#
# Fragments are concatenated in filename order, so the numeric prefixes control
# document order. Rendering is done by headless Chrome, which is the only
# engine on hand that supports the CSS paged-media features the stylesheet
# relies on (`@page`, `break-inside`, running gradients on the cover).
#
# Usage:  tools/build-pdf.sh [output.pdf]

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SRC="$ROOT/pdf-src"
OUT="${1:-$ROOT/developer-specs.pdf}"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

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
  exit 1
fi

shopt -s nullglob
fragments=("$SRC"/*.html)
if (( ${#fragments[@]} == 0 )); then
  echo "error: no fragments in $SRC" >&2
  exit 1
fi

echo "Assembling ${#fragments[@]} fragments..."
cat "${fragments[@]}" > "$WORK/developer-specs.html"

echo "Rendering with $(basename "$CHROME")..."
"$CHROME" \
  --headless \
  --disable-gpu \
  --no-sandbox \
  --no-pdf-header-footer \
  --print-to-pdf="$OUT" \
  --virtual-time-budget=10000 \
  "file://$WORK/developer-specs.html" 2>/dev/null

# Keep the assembled HTML next to the PDF; it is what the dev portal's
# "single-page spec" link serves, and it makes diffs reviewable.
cp "$WORK/developer-specs.html" "$ROOT/developer-specs.html"

echo "Wrote $OUT ($(du -h "$OUT" | cut -f1))"
