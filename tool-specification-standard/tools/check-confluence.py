#!/usr/bin/env python3
"""
Validate the generated Confluence page set before anyone imports it.

Confluence rejects malformed storage format outright, and a page that imports
but renders print-only markup is worse than one that fails loudly. This checks
that every page is well-formed XML in the storage namespaces, that no print
vocabulary survived the conversion, that only XML entities are used, and that
every internal link resolves to a page in the manifest.

    tools/check-confluence.py

Exit status 1 on any finding. Standard library only.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from xml.etree import ElementTree

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "confluence"

NS = ('xmlns:ac="http://atlassian.com/content" '
      'xmlns:ri="http://atlassian.com/resource/identifier" '
      'xmlns:at="http://atlassian.com/schema"')

# Classes that only mean something to the print stylesheet.
PRINT_ONLY = ["rule", "note", "tip", "warn", "danger", "dd", "tool", "lede",
              "check", "pill", "risk", "lvl", "part", "cover", "toc", "num",
              "rid", "lbl", "c-key", "c-str", "c-num", "c-com", "tnum", "pnum"]

ALLOWED_ENTITIES = {"amp", "lt", "gt", "quot", "apos"}


def main() -> int:
    manifest = json.loads((OUT / "manifest.json").read_text())
    titles = {p["title"] for p in manifest["pages"]}
    findings: list[str] = []
    macros: dict[str, int] = {}

    for page in manifest["pages"]:
        path = OUT / page["file"]
        name = page["title"]
        body = path.read_text()

        # 1. Well-formed in the storage namespaces.
        try:
            ElementTree.fromstring(f"<root {NS}>{body}</root>")
        except ElementTree.ParseError as exc:
            findings.append(f"{name}: not well-formed XML — {exc}")
            continue

        # 2. No print-only vocabulary survived.
        for cls in PRINT_ONLY:
            if re.search(rf'class="[^"]*\b{re.escape(cls)}\b', body):
                findings.append(f"{name}: unconverted print class '{cls}'")
        for tag in ("<div", "<span", "<figure", "<svg", "<pre", "<section"):
            if tag in body:
                findings.append(f"{name}: unconverted element '{tag}>'")

        # 3. Only XML entities.
        for ent in set(re.findall(r"&([a-zA-Z][a-zA-Z0-9]*);", body)):
            if ent not in ALLOWED_ENTITIES:
                findings.append(f"{name}: named HTML entity '&{ent};' breaks the parser")

        # 4. Internal links resolve.
        for target in re.findall(r'<ri:page ri:content-title="([^"]+)"/>', body):
            import html as _html
            if _html.unescape(target) not in titles:
                findings.append(f"{name}: link to unknown page '{target}'")

        # 5. Attachments referenced are attached.
        for fn in re.findall(r'<ri:attachment ri:filename="([^"]+)"/>', body):
            if fn not in page["attachments"]:
                findings.append(f"{name}: references attachment '{fn}' not in its manifest entry")
            elif not (OUT / "attachments" / fn).exists():
                findings.append(f"{name}: attachment '{fn}' missing from confluence/attachments")

        # 6. Empty pages are a conversion bug, not a page.
        if len(re.sub(r"<[^>]+>", "", body).strip()) < 40:
            findings.append(f"{name}: page body is essentially empty")

        for m in re.findall(r'<ac:structured-macro ac:name="([^"]+)"', body):
            macros[m] = macros.get(m, 0) + 1

    # 7. Every parent exists, and the tree has one root.
    for page in manifest["pages"]:
        if page["parent"] and page["parent"] not in titles:
            findings.append(f"{page['title']}: parent '{page['parent']}' is not a page")
    seen: set[str] = set()
    for page in manifest["pages"]:
        if page["parent"] and page["parent"] not in seen:
            findings.append(f"{page['title']}: appears before its parent '{page['parent']}'")
        seen.add(page["title"])
    roots = [p["title"] for p in manifest["pages"] if not p["parent"]]
    if roots != [manifest["space_home"]]:
        findings.append(f"expected one root page, found {roots}")

    for f in findings:
        print(f"FAIL  {f}")
    print(f"\n{len(manifest['pages'])} pages · {len(findings)} finding(s)")
    print("macros used: " + ", ".join(f"{k}×{v}" for k, v in sorted(macros.items())))
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
