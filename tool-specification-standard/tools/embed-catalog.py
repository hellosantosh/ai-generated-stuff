#!/usr/bin/env python3
"""
Expand catalog markers in the PDF source fragments.

The book must print the descriptors that actually ship, not a retyped copy that
drifts. Fragments therefore carry markers, and this script replaces each one
with a syntax-highlighted rendering of the corresponding file in catalog/.

    <!--CATALOG:brokerage_positions_list:full-->        the whole descriptor
    <!--CATALOG:brokerage_positions_list:description--> just the prose contract
    <!--CATALOG:brokerage_positions_list:input-->       just inputSchema
    <!--CATALOG:brokerage_positions_list:output-->      just outputSchema
    <!--CATALOG:brokerage_positions_list:governance-->  just the governance block
    <!--JSONFILE:schemas/tool-descriptor.schema.json-->  any JSON file in the repository
    <!--TOOLCARDS-->                                     a summary row per catalog descriptor

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
CARDS_MARKER = re.compile(r"<!--TOOLCARDS-->")

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
        raise SystemExit(f"unknown catalog section '{section}'")

    return f'<pre class="long json"><code>{highlight(payload)}</code></pre>'


TIER_CLASS = {0: "r0", 1: "r1", 2: "r2", 3: "r3"}


def tool_cards() -> str:
    """One row per catalog descriptor, read from the descriptors themselves."""
    rows = []
    docs = []
    for path in sorted(CATALOG.glob("*.json")):
        d = json.loads(path.read_text())
        gov = next(v for k, v in d["_meta"].items() if k.endswith("/governance"))
        docs.append((d, gov))
    # Reads before writes, then by tier.
    docs.sort(key=lambda t: (t[1]["riskTier"], not t[0]["annotations"]["readOnlyHint"]))
    for d, gov in docs:
        ann = d["annotations"]
        pills = '<span class="pill ro">read-only</span>' if ann["readOnlyHint"] else \
                '<span class="pill no">writes</span>'
        if ann["destructiveHint"]:
            pills += '<span class="pill no">destructive</span>'
        if ann["idempotentHint"]:
            pills += '<span class="pill">idempotent</span>'
        if ann["openWorldHint"]:
            pills += '<span class="pill">open world</span>'
        scopes = "".join(f'<span class="pill sc">{html.escape(e)}</span>'
                         for e in gov["entitlements"])
        first = d["description"].split(". ")[0] + "."
        approval = gov["approval"]["mode"]
        rows.append(
            "<tr>"
            f'<td><code>{d["name"]}</code><br><span class="small">{html.escape(d["title"])}</span></td>'
            f'<td><span class="risk {TIER_CLASS[gov["riskTier"]]}">T{gov["riskTier"]}</span><br>'
            f'<span class="small">{html.escape(approval)}</span></td>'
            f'<td>{pills}<br>{scopes}</td>'
            f'<td>{html.escape(first)}</td>'
            "</tr>")
    return ('<table class="long">'
            "<thead><tr><th style=\"width:26%\">Tool</th><th style=\"width:9%\">Tier</th>"
            "<th style=\"width:22%\">Contract</th><th>Answers</th></tr></thead>"
            "<tbody>" + "".join(rows) + "</tbody></table>")


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

    out = CARDS_MARKER.sub(lambda _: tool_cards(),
                           FILE_MARKER.sub(sub_file, MARKER.sub(sub, text)))
    if missing:
        print(f"embed-catalog: no descriptor for {sorted(set(missing))}", file=sys.stderr)
        return 1
    sys.stdout.write(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
