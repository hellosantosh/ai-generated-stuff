#!/usr/bin/env python3
"""
Derive page numbers from the document itself and write them into the page
footers and the table of contents.

Hand-maintained page numbers rot the moment a page is inserted, so the build
computes them instead. Run by build-guide.sh before rendering.
"""
import re, sys, pathlib

SRC = pathlib.Path(__file__).parent / "src" / "dev-guide.html"
html = SRC.read_text()

sections = list(re.finditer(r'<section class="page[^"]*">(.*?)</section>', html, re.S))
if not sections:
    sys.exit("no pages found")

# ---- work out what lives on each page -------------------------------------
chapter_start = {}      # "05" -> page number of its first page
ref_pages = {}          # "Glossary" -> page number
for i, sec in enumerate(sections, start=1):
    body = sec.group(1)
    chnum = re.search(r'<div class="chnum">(.*?)</div>', body, re.S)
    label = re.sub(r'<[^>]+>', '', chnum.group(1)).strip() if chnum else ""
    m = re.match(r'Chapter\s+(\d+)', label)
    if m and "continued" not in label:
        chapter_start.setdefault(m.group(1), i)
    title = re.search(r'<h1 class="title">(.*?)</h1>', body, re.S)
    if label == "Reference" and title:
        ref_pages[re.sub(r'<[^>]+>', '', title.group(1)).strip()] = i
    if label == "Contents":
        ref_pages["Contents"] = i

# ---- rewrite each page's footer number ------------------------------------
out, cursor, page = [], 0, 0
for sec in sections:
    page += 1
    seg = html[cursor:sec.end()]
    new_seg, n = re.subn(
        r'(<div class="pfoot">.*?<span class="n">)\d+(</span>)',
        lambda mm: f"{mm.group(1)}{page}{mm.group(2)}", seg, count=1, flags=re.S)
    if n == 0 and page > 1:
        print(f"  warning: page {page} has no footer")
    out.append(new_seg)
    cursor = sec.end()
out.append(html[cursor:])
html = "".join(out)

# ---- rewrite the table of contents ---------------------------------------
def toc_fix(m):
    num, rest = m.group(1), m.group(2)
    key = num.strip()
    if key.isdigit():
        target = chapter_start.get(key.zfill(2))
    else:
        title = re.search(r'<span class="t">(.*?)</span>', rest)
        target = ref_pages.get(title.group(1).strip()) if title else None
    if target is None:
        return m.group(0)
    return re.sub(r'(<span class="pg">)\d+(</span>)',
                  lambda p: f"{p.group(1)}{target}{p.group(2)}", m.group(0))

html = re.sub(r'<li><span class="num">(.*?)</span>(.*?)</li>', toc_fix, html, flags=re.S)

SRC.write_text(html)
print(f"  paginated {len(sections)} pages")
print("  chapters:", ", ".join(f"{k}->p{v}" for k, v in sorted(chapter_start.items())))
print("  reference:", ", ".join(f"{k}->p{v}" for k, v in ref_pages.items()))
