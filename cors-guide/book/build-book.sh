#!/usr/bin/env bash
#
# Build cors-guide.pdf from the fragments in src/.
#
# The book is a fixed-layout HTML document -- each <section class="page"> is
# exactly one Letter page -- rendered by headless Chrome, which is needed for
# the inline SVG diagrams to come out crisp and correctly laid out.
#
#   ./build-book.sh           assemble, paginate, render, stamp metadata
#   ./build-book.sh --check   also report pages whose content overflows
#
set -euo pipefail
cd "$(dirname "$0")"

OUT="cors-guide.pdf"
HTML="cors-guide.html"

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
  echo "Could not find Chrome or Chromium. Set CHROME_BIN=/path/to/chrome and re-run." >&2
  exit 1
fi

# 1. Assemble the fragments in filename order.
cat src/*.html > "$HTML"
printf '\n</body>\n</html>\n' >> "$HTML"

# 2. Generate the code listing from the real lab source, then derive every page
#    number (footers and contents) from the document itself.
python3 build-labs.py
python3 renumber.py "$HTML"
pages=$(grep -c '<section class="page' "$HTML")
sed -i.bak "s/__PAGES__/$pages/" "$HTML" && rm -f "$HTML.bak"

# 3. Render.
echo "  rendering with: $(basename "$CHROME")"
"$CHROME" --headless --disable-gpu --no-pdf-header-footer --no-margins \
  --hide-scrollbars --run-all-compositor-stages-before-draw \
  --virtual-time-budget=10000 --print-to-pdf="$OUT" "file://$PWD/$HTML" 2>/dev/null || true
[ -s "$OUT" ] || { echo "  PDF was not produced" >&2; exit 1; }

# 4. Author, subject and keywords for readers, retailers and library software.
python3 set-metadata.py

rendered=$(pdfinfo "$OUT" 2>/dev/null | awk '/^Pages:/ {print $2}' || true)
printf "  %s  %s pages rendered (%s in source), %s KB\n" \
  "$OUT" "${rendered:-?}" "$pages" "$(( $(wc -c < "$OUT") / 1024 ))"
if [ -n "$rendered" ] && [ "$rendered" != "$pages" ]; then
  echo "  WARNING: page count does not match the source; a page is overflowing." >&2
fi

if [ "${1:-}" = "--check" ]; then
  python3 check-overflow.py "$HTML" --report
fi
