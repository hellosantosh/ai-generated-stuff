#!/usr/bin/env bash
#
# Build Enterprise-Tool-Specification-Standard.pdf from the fragments in pdf-src/.
#
# Fragments are concatenated in filename order, so the numeric prefixes control
# document order. Rendering is done by headless Chrome, the only engine on hand
# that supports the CSS paged-media features the stylesheet relies on (@page,
# break-inside, running gradients on the cover).
#
# Usage:  tools/build-pdf.sh [output.pdf]

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SRC="$ROOT/pdf-src"
OUT="${1:-$ROOT/Enterprise-Tool-Specification-Standard.pdf}"
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
  echo "The HTML renders in any browser: open the assembled file and print to PDF." >&2
  exit 1
fi

shopt -s nullglob
fragments=("$SRC"/*.html)
if (( ${#fragments[@]} == 0 )); then
  echo "error: no fragments in $SRC" >&2
  exit 1
fi

echo "Linting the reference catalogue..."
python3 "$ROOT/tools/lint-descriptors.py" "$ROOT/catalog"

echo "Assembling ${#fragments[@]} fragments..."
cat "${fragments[@]}" > "$WORK/raw.html"

# Catalogue markers are expanded from catalog/*.json, so the descriptors printed
# in the book are by construction the descriptors that pass the linter.
echo "Embedding the catalogue..."
python3 "$ROOT/tools/embed-catalog.py" < "$WORK/raw.html" > "$WORK/standard.html"

echo "Rendering with $(basename "$CHROME")..."
"$CHROME" \
  --headless \
  --disable-gpu \
  --no-sandbox \
  --no-pdf-header-footer \
  --print-to-pdf="$OUT" \
  --run-all-compositor-stages-before-draw \
  --virtual-time-budget=10000 \
  "file://$WORK/standard.html" 2>/dev/null

# Keep the assembled HTML next to the PDF: it makes diffs reviewable and is what
# the intranet copy serves.
cp "$WORK/standard.html" "$ROOT/Enterprise-Tool-Specification-Standard.html"

echo "Wrote $OUT ($(du -h "$OUT" | cut -f1))"
