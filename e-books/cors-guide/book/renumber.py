#!/usr/bin/env python3
"""
Derive page numbers from the document itself and write them into the page
footers and the table of contents.

Hand-maintained page numbers rot the moment a page is inserted, so the build
computes them instead. Run by build-guide.sh before rendering.
"""
import re, sys, pathlib

# The file to paginate: the assembled book, unless a path is given.
SRC = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else (
    pathlib.Path(__file__).parent / "cors-guide.html")
html = SRC.read_text()

sections = list(re.finditer(r'<section class="(page[^"]*)">(.*?)</section>', html, re.S))
if not sections:
    sys.exit("no pages found")

# ---- work out what lives on each page -------------------------------------
chapter_start = {}      # "05" -> page number of its first page
ref_pages = {}          # page title -> page number (first occurrence wins), so
                        # the contents can point at anything, not just chapters
for i, sec in enumerate(sections, start=1):
    body = sec.group(2)
    chnum = re.search(r'<div class="chnum">(.*?)</div>', body, re.S)
    label = re.sub(r'<[^>]+>', '', chnum.group(1)).strip() if chnum else ""
    m = re.match(r'Chapter\s+(\d+)', label)
    if m and "continued" not in label:
        chapter_start.setdefault(m.group(1), i)
    # any page heading, so a contents row can resolve by the text it shows
    title = re.search(r'<h1 class="(?:title|lst-title)"[^>]*>(.*?)</h1>', body, re.S)
    if title:
        text = re.sub(r'<[^>]+>', '', title.group(1))
        text = re.sub(r'\s+', ' ', text.replace('&ndash;', '-')).strip()
        ref_pages.setdefault(text, i)
    if label == "Contents":
        ref_pages.setdefault("Contents", i)

# ---- rewrite each page's footer number ------------------------------------
out, cursor, page = [], 0, 0
for sec in sections:
    page += 1
    seg = html[cursor:sec.end()]
    new_seg, n = re.subn(
        r'(<div class="pfoot">.*?<span class="n">)\d+(</span>)',
        lambda mm: f"{mm.group(1)}{page}{mm.group(2)}", seg, count=1, flags=re.S)
    # Front matter (cover, copyright) carries no folio by convention.
    if n == 0 and "front" not in sections[page - 1].group(1):
        print(f"  warning: page {page} has no footer")
    out.append(new_seg)
    cursor = sec.end()
out.append(html[cursor:])
html = "".join(out)

# ---- rewrite the table of contents ---------------------------------------
def toc_fix(m):
    whole, num, rest = m.group(0), m.group(1), m.group(2)
    # An explicit data-ref wins, so a row's visible label can be short even when
    # the page it points at has a long heading.
    ref = re.search(r'data-ref="([^"]+)"', whole)
    if ref:
        target = ref_pages.get(ref.group(1).strip())
        if target is None:
            print(f"  warning: contents row has no matching page: {ref.group(1)}")
            return whole
        return re.sub(r'(<span class="pg">)\d+(</span>)',
                      lambda p: f"{p.group(1)}{target}{p.group(2)}", whole)
    key = num.strip()
    if key.isdigit():
        target = chapter_start.get(key.zfill(2))
    else:
        t = re.search(r'<span class="t">(.*?)</span>', rest)
        if t:
            want = re.sub(r'<[^>]+>', '', t.group(1))
            want = re.sub(r'\s+', ' ', want.replace('&ndash;', '-')).strip()
            target = ref_pages.get(want)
        else:
            target = None
    if target is None:
        return m.group(0)
    return re.sub(r'(<span class="pg">)\d+(</span>)',
                  lambda p: f"{p.group(1)}{target}{p.group(2)}", m.group(0))

html = re.sub(r'<li[^>]*><span class="num">(.*?)</span>(.*?)</li>',
              toc_fix, html, flags=re.S)

SRC.write_text(html)
print(f"  paginated {len(sections)} pages")
print("  chapters:", ", ".join(f"{k}->p{v}" for k, v in sorted(chapter_start.items())))
print("  reference:", ", ".join(f"{k}->p{v}" for k, v in ref_pages.items()))
