#!/usr/bin/env bash
#
# Render diagrams/*.puml to SVG and PNG.
#
# The .puml source is the deliverable: it is what a reviewer edits and what the
# VS Code PlantUML extension previews. The exports are committed so the diagram
# is viewable without a PlantUML install, and regenerated here so they cannot
# drift from the source.
#
#   brew install plantuml graphviz      # or apt-get install plantuml graphviz
#   tools/build-diagrams.sh

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DIR="$ROOT/diagrams"

if ! command -v plantuml > /dev/null; then
  echo "error: plantuml not found. brew install plantuml graphviz" >&2
  exit 1
fi

shopt -s nullglob
sources=("$DIR"/*.puml)
if (( ${#sources[@]} == 0 )); then
  echo "error: no .puml files in $DIR" >&2
  exit 1
fi

echo "Checking the invocation diagram against section 3.2..."
python3 "$ROOT/tools/check-diagram-sync.py"

echo "Checking syntax..."
plantuml -checkonly "${sources[@]}"

echo "Rendering ${#sources[@]} diagram(s)..."
plantuml -tsvg -o "$DIR" "${sources[@]}"
plantuml -tpng -Sdpi=144 -o "$DIR" "${sources[@]}"

for f in "$DIR"/*.svg "$DIR"/*.png; do
  echo "  $(basename "$f")  ($(du -h "$f" | cut -f1))"
done
