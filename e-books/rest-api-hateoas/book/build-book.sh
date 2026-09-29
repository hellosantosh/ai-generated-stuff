#!/usr/bin/env bash
#
# Build rest-api-hateoas.pdf from the fragments in src/.
#
#   ./build-book.sh            assemble, expand the code and capture directives, paginate, render
#   ./build-book.sh --html     assemble the HTML only, for reviewing in a browser
#   ./build-book.sh --assets   also export cover.png and thumbnail.png for store listings
#
# The HTTP exchanges printed in the book live in captures/. To record them again from the
# running application, build the jars in ../../../brokerage-apis and run tools/capture.py.
#
set -euo pipefail
cd "$(dirname "$0")"

if [ "${1:-}" = "--html" ]; then
  exec python3 tools/build.py --html
fi

python3 tools/build.py

if [ "${1:-}" = "--assets" ]; then
  python3 tools/export-cover.py
  python3 tools/export-thumbnail.py
fi
