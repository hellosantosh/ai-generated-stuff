#!/usr/bin/env bash
#
# Build dev-guide-pkce.pdf from src/dev-guide-pkce.html.
#
# Companion volume to dev-guide.pdf, built by the same pipeline: a fixed-layout
# HTML source where each <section class="page"> is exactly one Letter page,
# rendered by headless Chrome because the diagrams are inline SVG and need a
# real browser engine.
#
#   ./build-pkce-guide.sh          build the PDF
#   ./build-pkce-guide.sh --check  only verify that no page overflows
#
set -euo pipefail
cd "$(dirname "$0")"

SRC="src/dev-guide-pkce.html"
OUT="dev-guide-pkce.pdf"

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

# Page numbers are derived from the document rather than hand-maintained, so
# inserting a page never leaves a stale folio behind.
if command -v python3 > /dev/null; then
  python3 renumber.py "$SRC"
fi

# A .page clips what does not fit, so an overflowing page loses text silently
# and the page count still looks right. This is the check that actually catches it.
if command -v python3 > /dev/null; then
  python3 check-overflow.py "$SRC" || exit 1
fi

# --check renders to a scratch file only: it is for catching an overflowing
# page while writing, without disturbing the committed PDF.
if [ "${1:-}" = "--check" ]; then
  OUT="$(mktemp -t pkce-check).pdf"
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

if [ "${1:-}" != "--check" ] && command -v python3 > /dev/null; then
  python3 set-metadata.py "$OUT"
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
  exit 1
fi

[ "${1:-}" = "--check" ] && rm -f "$OUT"
exit 0
