#!/usr/bin/env python3
"""
Extract the normative rules from the standard's source fragments.

The quick reference carries a complete rule index. Retyping it would guarantee
drift, so it is generated from pdf-src/*.html: every rule block and every row of
a rules table, in document order, tagged with the section it was defined in.

    tools/extract-rules.py --html     HTML table for the quick reference
    tools/extract-rules.py --check    verify every cited rule is also defined
    tools/extract-rules.py            plain text, one rule per line

Standard library only.
"""

from __future__ import annotations

import html
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "pdf-src"

AREA_ORDER = ["GEN", "NAM", "DSC", "INP", "OUT", "ERR", "ANN", "GOV", "SEC", "OBS", "VER", "CAT"]
AREA_NAME = {
    "GEN": "General and descriptor structure", "NAM": "Naming and identity",
    "DSC": "Descriptions", "INP": "Input schemas", "OUT": "Output and envelopes",
    "ERR": "Errors", "ANN": "Annotations and risk tiers", "GOV": "Governance metadata",
    "SEC": "Security and trust", "OBS": "Observability and audit",
    "VER": "Versioning and lifecycle", "CAT": "Catalog and registration",
}

# A rule block, a rules-table row, or a section number, whichever comes next.
TOKEN = re.compile(
    r'<span class="num">(?P<sec>[\d.]+)</span>'
    r'|<span class="rid">(?P<rid>TS-[A-Z]+-\d+)</span>(?P<rbody>.*?)</p>'
    r'|<td><code>(?P<tid>TS-[A-Z]+-\d+)</code></td>\s*'
    r'<td><span class="lvl lvl-(?P<tlvl>\w+)">[^<]*</span></td>\s*'
    r'<td>(?P<tbody>.*?)</td>',
    re.S)

LEVEL = re.compile(r'<span class="lvl lvl-(\w+)">')

# Two rules are stated in normative prose rather than in a rule block, because
# their natural home is a table cell. Their text is pinned here.
PROSE_RULES = {
    "TS-INP-15": ("must", "7.6",
                  "Where a tool needs one of the reserved parameter concepts, it MUST use the "
                  "standard name and shape rather than inventing an equivalent."),
    "TS-OUT-11": ("must", "8.6",
                  "Balances, buying power, and anything that gates a trading decision MUST NOT "
                  "be returned partially computed; the tool fails instead."),
}


def clean(fragment: str) -> str:
    """Tags out, entities decoded, whitespace collapsed, level markers dropped."""
    text = re.sub(r'<span class="lvl[^>]*>[^<]*</span>', "", fragment)
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def collect() -> list[dict]:
    rules: dict[str, dict] = {}
    for path in sorted(SRC.glob("*.html")):
        section = ""
        for m in TOKEN.finditer(path.read_text()):
            if m.group("sec"):
                section = m.group("sec")
                continue
            if m.group("rid"):
                rid, body = m.group("rid"), m.group("rbody")
                lvl = LEVEL.search(body)
                level = lvl.group(1) if lvl else "must"
            else:
                rid, body, level = m.group("tid"), m.group("tbody"), m.group("tlvl")
            rules.setdefault(rid, {"id": rid, "level": level,
                                   "section": section, "text": clean(body)})
    for rid, (level, section, text) in PROSE_RULES.items():
        rules.setdefault(rid, {"id": rid, "level": level, "section": section, "text": text})

    def key(r):
        area, num = r["id"].split("-")[1], int(r["id"].split("-")[2])
        return (AREA_ORDER.index(area) if area in AREA_ORDER else 99, num)

    return sorted(rules.values(), key=key)


def cited() -> set[str]:
    seen = set()
    for path in SRC.glob("*.html"):
        seen |= set(re.findall(r"\bTS-[A-Z]+-\d+\b", path.read_text()))
    return seen


def shorten(text: str, limit: int = 250) -> str:
    """First sentence or two, trimmed to the index's column width."""
    text = re.sub(r"^(MUST NOT|MUST|SHOULD NOT|SHOULD|MAY|NEVER)\s+", "", text)
    if len(text) <= limit:
        return text
    cut = text[:limit]
    stop = max(cut.rfind(". "), cut.rfind("; "))
    return (cut[:stop + 1] if stop > limit * 0.5 else cut.rsplit(" ", 1)[0] + "…")


def as_html(rules: list[dict]) -> str:
    out = ['<table class="long ruleindex">',
           "<thead><tr><th>Rule</th><th>Level</th><th>§</th><th>Requirement</th></tr></thead>",
           "<tbody>"]
    current = None
    for r in rules:
        area = r["id"].split("-")[1]
        if area != current:
            current = area
            out.append(f'<tr class="grp"><td colspan="4">TS-{area} &nbsp;·&nbsp; '
                       f'{AREA_NAME.get(area, area)}</td></tr>')
        lvl = {"must": "MUST", "should": "SHOULD", "may": "MAY", "never": "NEVER"}[r["level"]]
        out.append(f'<tr><td><code>{r["id"]}</code></td>'
                   f'<td><span class="lvl lvl-{r["level"]}">{lvl}</span></td>'
                   f'<td class="sec">{r["section"]}</td>'
                   f'<td>{html.escape(shorten(r["text"]))}</td></tr>')
    out.append("</tbody></table>")
    return "\n".join(out)


def main(argv: list[str]) -> int:
    rules = collect()
    if "--check" in argv:
        defined = {r["id"] for r in rules}
        missing = sorted(cited() - defined)
        for rid in missing:
            print(f"cited but not defined: {rid}")
        print(f"{len(defined)} rules defined · {len(missing)} dangling citation(s)")
        return 1 if missing else 0
    if "--html" in argv:
        print(as_html(rules))
        return 0
    for r in rules:
        print(f'{r["id"]:12s} {r["level"]:7s} §{r["section"]:6s} {shorten(r["text"], 100)}')
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
