#!/usr/bin/env bash
#
# Build brokerage-tools.pdf from the fragments in src/.
#
#   ./build-book.sh            expand the directives, paginate, render
#   ./build-book.sh --html     assemble the HTML only, for reviewing in a browser
#   ./build-book.sh --capture  re-record every figure from the running system first
#   ./build-book.sh --assets   also export cover.png and thumbnail.png for store listings
#
# Everything the book prints as output lives in captures/ and is recorded by tools/capture.py
# from the companion repository at ../../../brokerage-tools. Nothing in the book is retyped.
#
set -euo pipefail
cd "$(dirname "$0")"

if [ "${1:-}" = "--capture" ]; then
  python3 tools/capture.py
  shift || true
fi

if [ "${1:-}" = "--html" ]; then
  exec python3 tools/build.py --html
fi

python3 tools/build.py

if [ "${1:-}" = "--assets" ]; then
  python3 tools/export-cover.py
  python3 tools/export-thumbnail.py
fi
