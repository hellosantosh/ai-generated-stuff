#!/usr/bin/env python3
"""
Verify that diagrams/anatomy-of-an-invocation.puml still draws the steps the
standard defines in section 3.2.

The diagram is a second telling of the same eleven steps, so it can drift from
the book silently. This compares the step numbers and titles and fails loudly
if they diverge. Run by tools/build-diagrams.sh.
"""

from __future__ import annotations

import html
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIAMOND = "◆"


def clean(fragment: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", fragment))).strip()


def steps_in_standard() -> list[tuple[str, str]]:
    book = (ROOT / "pdf-src" / "20-foundations.html").read_text()
    section = book[book.index('id="anatomy"'):book.index('id="binds"')]
    rows = re.findall(r"<tr><td>(.*?)</td><td>(.*?)</td><td>(.*?)</td></tr>", section, re.S)
    return [(clean(num).replace(DIAMOND, "").strip(), clean(title)) for num, title, _ in rows]


def steps_in_diagram() -> list[tuple[str, str]]:
    puml = (ROOT / "diagrams" / "anatomy-of-an-invocation.puml").read_text()
    pattern = r"^== (\d+) [" + DIAMOND + r"·] (.+?) ==$"
    return [(m.group(1), m.group(2).strip()) for m in re.finditer(pattern, puml, re.M)]


def main() -> int:
    book, drawn = steps_in_standard(), steps_in_diagram()
    if len(book) != len(drawn):
        print(f"drift: the standard has {len(book)} steps, the diagram draws {len(drawn)}",
              file=sys.stderr)
        return 1
    for (bn, bt), (dn, dt) in zip(book, drawn):
        if bn != dn or bt.lower() != dt.lower():
            print(f"drift: step {bn} is '{bt}' in the standard but '{dt}' in the diagram",
                  file=sys.stderr)
            return 1
    print(f"  {len(book)} steps, all matching")
    return 0


if __name__ == "__main__":
    sys.exit(main())
