#!/usr/bin/env bash
#
# Build dev-guide.pdf from src/dev-guide.html.
#
# The guide is a fixed-layout HTML document -- each <section class="page"> is
# exactly one Letter page -- rendered by headless Chrome. Chrome is used
# because the diagrams are inline SVG and need a real browser engine to come
# out crisp and correctly laid out.
#
#   ./build-guide.sh          build the PDF
#   ./build-guide.sh --check  only verify that no page overflows
#
set -euo pipefail
cd "$(dirname "$0")"

SRC="src/dev-guide.html"
OUT="dev-guide.pdf"

# Find a Chrome or Chromium binary.
CHROME=""
for candidate in \
  "${CHROME_BIN:-}" \
  "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" \
  "/Applications/Chromium.app/Contents/MacOS/Chromium" \
  "$(command -v google-chrome || true)" \
  "$(command -v google-chrome-stable || true)" \
  "$(command -v chromium || true)" \
  "$(command -v chromium-browser || true)"
do
  [ -n "$candidate" ] && [ -x "$candidate" ] && { CHROME="$candidate"; break; }
done

if [ -z "$CHROME" ]; then
  echo "Could not find Chrome or Chromium." >&2
  echo "Install one, or set CHROME_BIN=/path/to/chrome and re-run." >&2
  echo "The HTML source renders fine in any browser: open $SRC and print to PDF." >&2
  exit 1
fi

# Page numbers are derived from the document, never hand-maintained.
if command -v python3 > /dev/null; then
  python3 renumber.py
fi

echo "  rendering with: $(basename "$CHROME")"
"$CHROME" \
  --headless \
  --disable-gpu \
  --no-pdf-header-footer \
  --no-margins \
  --hide-scrollbars \
  --run-all-compositor-stages-before-draw \
  --virtual-time-budget=10000 \
  --print-to-pdf="$OUT" \
  "file://$PWD/$SRC" 2>&1 | grep -viE "cvdisplay|allocator|task_policy|fontconfig|GPU|Fallback" || true

if [ ! -s "$OUT" ]; then
  echo "  PDF was not produced" >&2
  exit 1
fi

pages="unknown"
if command -v python3 > /dev/null; then
  pages=$(python3 - "$OUT" <<'PY'
import re, sys
data = open(sys.argv[1], 'rb').read()
counts = [int(m.group(1)) for m in re.finditer(rb'/Count\s+(\d+)', data)]
print(max(counts) if counts else len(re.findall(rb'/Type\s*/Page[^s]', data)))
PY
)
fi

expected=$(grep -c '<section class="page' "$SRC")
printf "  %s  %s pages (%s sections in source), %s KB\n" \
  "$OUT" "$pages" "$expected" "$(( $(wc -c < "$OUT") / 1024 ))"

if [ "$pages" != "unknown" ] && [ "$pages" != "$expected" ]; then
  echo "  WARNING: page count does not match the source; a page is probably overflowing." >&2
fi
