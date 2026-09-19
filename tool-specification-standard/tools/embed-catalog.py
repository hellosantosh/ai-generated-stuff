#!/usr/bin/env python3
"""
Expand catalogue markers in the PDF source fragments.

The book must print the descriptors that actually ship, not a retyped copy that
drifts. Fragments therefore carry markers, and this script replaces each one
with a syntax-highlighted rendering of the corresponding file in catalog/.

    <!--CATALOG:brokerage_positions_list:full-->        the whole descriptor
    <!--CATALOG:brokerage_positions_list:description--> just the prose contract
    <!--CATALOG:brokerage_positions_list:input-->       just inputSchema
    <!--CATALOG:brokerage_positions_list:output-->      just outputSchema
    <!--CATALOG:brokerage_positions_list:governance-->  just the governance block
    <!--JSONFILE:schemas/tool-descriptor.schema.json-->  any JSON file in the repository

Usage:  embed-catalog.py < assembled.html > expanded.html
"""

from __future__ import annotations

import html
import json
import re
import sys
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CATALOG = ROOT / "catalog"

MARKER = re.compile(r"<!--CATALOG:([a-z0-9_]+):([a-z]+)-->")
FILE_MARKER = re.compile(r"<!--JSONFILE:([A-Za-z0-9_./-]+)-->")

TOKEN = re.compile(
    r'"(?:[^"\\]|\\.)*"(?=\s*:)'      # key
    r'|"(?:[^"\\]|\\.)*"'             # string
    r'|\b-?\d+(?:\.\d+)?\b'           # number
    r'|\b(?:true|false|null)\b'       # literal
)


def highlight(text: str) -> str:
    """Escape for HTML and wrap JSON tokens in the stylesheet's tint classes."""
    out, last = [], 0
    for m in TOKEN.finditer(text):
        out.append(html.escape(text[last:m.start()]))
        tok = m.group(0)
        following = text[m.end():m.end() + 4].lstrip()[:1]
        if tok[0] == '"' and following == ":":
            cls = "c-key"
        elif tok[0] == '"':
            cls = "c-str"
        elif tok in ("true", "false", "null"):
            cls = "c-key"
        else:
            cls = "c-num"
        out.append(f'<span class="{cls}">{html.escape(tok)}</span>')
        last = m.end()
    out.append(html.escape(text[last:]))
    return "".join(out)


def render(name: str, section: str) -> str:
    doc = json.loads((CATALOG / f"{name}.json").read_text())

    if section == "full":
        payload = json.dumps(doc, indent=2, ensure_ascii=False)
    elif section == "description":
        # Wrapped so a 1,100-character paragraph is readable in a code block.
        body = "\n".join(textwrap.wrap(doc["description"], 92))
        return ('<pre class="long"><code>'
                + html.escape(body)
                + "</code></pre>")
    elif section == "input":
        payload = json.dumps({"inputSchema": doc["inputSchema"]}, indent=2, ensure_ascii=False)
    elif section == "output":
        payload = json.dumps({"outputSchema": doc["outputSchema"]}, indent=2, ensure_ascii=False)
    elif section == "annotations":
        payload = json.dumps({"annotations": doc["annotations"]}, indent=2, ensure_ascii=False)
    elif section == "governance":
        payload = json.dumps(doc["_meta"], indent=2, ensure_ascii=False)
    else:
        raise SystemExit(f"unknown catalogue section '{section}'")

    return f'<pre class="long json"><code>{highlight(payload)}</code></pre>'


def main() -> int:
    text = sys.stdin.read()
    missing = []

    def sub(m: re.Match) -> str:
        name, section = m.group(1), m.group(2)
        if not (CATALOG / f"{name}.json").exists():
            missing.append(name)
            return m.group(0)
        return render(name, section)

    def sub_file(m: re.Match) -> str:
        target = ROOT / m.group(1)
        if not target.exists():
            missing.append(m.group(1))
            return m.group(0)
        payload = json.dumps(json.loads(target.read_text()), indent=2, ensure_ascii=False)
        return f'<pre class="long json"><code>{highlight(payload)}</code></pre>'

    out = FILE_MARKER.sub(sub_file, MARKER.sub(sub, text))
    if missing:
        print(f"embed-catalog: no descriptor for {sorted(set(missing))}", file=sys.stderr)
        return 1
    sys.stdout.write(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
