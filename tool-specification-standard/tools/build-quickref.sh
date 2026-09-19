#!/usr/bin/env bash
#
# Build Tool-Specification-Standard-Quick-Reference.pdf from quickref-src/.
#
# Same pipeline as the standard itself, with two extra expansions: the rule
# index is generated from the standard's own source fragments, and the brokerage
# tool card is generated from catalog/. Neither can drift from what it describes.
#
# Usage:  tools/build-quickref.sh [output.pdf]

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SRC="$ROOT/quickref-src"
OUT="${1:-$ROOT/Tool-Specification-Standard-Quick-Reference.pdf}"
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

echo "Checking that every cited rule is defined..."
python3 "$ROOT/tools/extract-rules.py" --check

echo "Linting the reference catalog..."
python3 "$ROOT/tools/lint-descriptors.py" "$ROOT/catalog"

shopt -s nullglob
fragments=("$SRC"/*.html)
if (( ${#fragments[@]} == 0 )); then
  echo "error: no fragments in $SRC" >&2
  exit 1
fi

echo "Assembling ${#fragments[@]} fragments..."
cat "${fragments[@]}" > "$WORK/raw.html"

# The rule index is generated from the standard's fragments, so a rule added to
# the book appears here without anyone remembering to copy it.
echo "Generating the rule index..."
python3 - "$WORK/raw.html" "$WORK/indexed.html" "$ROOT" <<'PY'
import subprocess, sys
raw, out, root = sys.argv[1], sys.argv[2], sys.argv[3]
text = open(raw).read()
if "<!--RULEINDEX-->" in text:
    table = subprocess.run([sys.executable, f"{root}/tools/extract-rules.py", "--html"],
                           capture_output=True, text=True, check=True).stdout
    text = text.replace("<!--RULEINDEX-->", table)
open(out, "w").write(text)
PY

echo "Embedding the catalog..."
python3 "$ROOT/tools/embed-catalog.py" < "$WORK/indexed.html" > "$WORK/quickref.html"

echo "Rendering with $(basename "$CHROME")..."
"$CHROME" \
  --headless \
  --disable-gpu \
  --no-sandbox \
  --no-pdf-header-footer \
  --print-to-pdf="$OUT" \
  --run-all-compositor-stages-before-draw \
  --virtual-time-budget=10000 \
  "file://$WORK/quickref.html" 2>/dev/null

cp "$WORK/quickref.html" "$ROOT/Tool-Specification-Standard-Quick-Reference.html"

echo "Wrote $OUT ($(du -h "$OUT" | cut -f1))"
