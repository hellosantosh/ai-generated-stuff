#!/usr/bin/env python3
"""
Build brokerage-tools.pdf from the fragments in src/.

The book is a flowing HTML document laid out by Chrome's paged-media engine: @page
margins, one named page per chapter (so each footer names its chapter), and full-bleed
named pages for the cover and part openers. Nothing is typeset by hand, so long code
listings simply continue on the next page.

Code in the book is never pasted. Three directives pull it in at build time:

  <pre data-src="tool-runtime/.../InvocationPipeline.java" data-from="REGEX" data-to="REGEX"></pre>
      an excerpt of a source file under brokerage-tools/, highlighted. Optional:
      data-after (search for data-from only after this line), data-skip="A..B"
      (elide the lines strictly between A and B), data-to-nth="3" (end at the third line
      matching data-to, to take several methods), data-doc="no" (do not pull in the Javadoc
      and annotations above data-from), data-cap (a caption).
  <pre data-file="accounts-list.json"></pre>
      output captured from the real system by tools/capture.py: tool envelopes, the generated
      briefing, the linter's report, the harness run. Nothing printed in this book is retyped.

<!-- SOURCE-LISTINGS --> becomes Appendix F: every source file, with line numbers.

Page numbers in the contents are found, not typed: the first render plants an invisible
marker at each chapter, pdftotext reports which page each marker landed on, and the
second render prints those numbers.

  tools/build.py            build the PDF
  tools/build.py --html     assemble the HTML only (fast, for reviewing in a browser)
"""
import html
import os
import re
import shutil
import subprocess
import sys
import tempfile
import zlib
from pathlib import Path

import highlight

HERE = Path(__file__).resolve().parent
BOOK = HERE.parent
CODE = BOOK.parent.parent.parent / "brokerage-tools"
SRC = BOOK / "src"
CAPTURES = BOOK / "captures"
NAME = "brokerage-tools"
HTML_OUT = BOOK / f"{NAME}.html"
PDF_OUT = BOOK / f"{NAME}.pdf"

# Widest line, in characters, that fits a code block at its font size. Captured HTTP may wrap
# (long URLs do); source excerpts must not, because the source is formatted to this width.
EXCERPT_WIDTH = 110
LISTING_WIDTH = 112

warnings = []


def warn(message):
    warnings.append(message)


# ----------------------------------------------------------------------------- directives

def attributes(tag):
    attrs = {}
    for m in re.finditer(r'([\w-]+)=(?:"([^"]*)"|\'([^\']*)\')', tag):
        attrs[m.group(1)] = html.unescape(m.group(2) if m.group(2) is not None else m.group(3))
    return attrs


def short_path(path):
    """tool-runtime/src/main/java/com/example/brokerage/tooling/runtime/X.java -> runtime/X.java"""
    m = re.match(r"[\w-]+/src/(main|test)/java/com/example/brokerage/(?:tooling/)?(.*)$", path)
    if m:
        return ("test: " if m.group(1) == "test" else "") + m.group(2)
    m = re.match(r"[\w-]+/src/(?:main|test)/resources/(.*)$", path)
    if m:
        return m.group(1)
    m = re.match(r"console/src/(.*)$", path)
    if m:
        return "console/" + m.group(1)
    return path


def excerpt(attrs):
    rel = attrs["data-src"]
    path = CODE / rel
    if not path.exists():
        warn(f"missing source {rel}")
        return [f"missing: {rel}"], rel
    lines = path.read_text().rstrip("\n").split("\n")
    start = 0
    if "data-after" in attrs:
        hit = _find(lines, attrs["data-after"], 0, rel)
        start = hit + 1 if hit is not None else 0
    first = _find(lines, attrs["data-from"], start, rel) if "data-from" in attrs else 0
    if first is None:
        return [f"pattern not found: {attrs['data-from']}"], rel
    last = len(lines) - 1
    if "data-to" in attrs:                  # data-to-nth: stop at the Nth match, to take several methods
        last = first - 1
        for _ in range(int(attrs.get("data-to-nth", "1"))):
            last = _find(lines, attrs["data-to"], last + 1, rel)
            if last is None:
                break
    if last is None:
        return [f"pattern not found: {attrs['data-to']}"], rel
    if rel.endswith(".java") and attrs.get("data-doc", "yes") != "no" and "data-from" in attrs:
        while first > 0 and re.match(r"\s*(@|/\*\*|\*)", lines[first - 1]):
            first -= 1
    chunk = lines[first:last + 1]
    for spec in filter(None, attrs.get("data-skip", "").split(";;")):
        a, _, b = spec.partition("..")
        i = _find(chunk, a, 0, rel)
        j = _find(chunk, b, (i or 0) + 1, rel) if i is not None else None
        if i is not None and j is not None and j > i + 1:
            indent = re.match(r"\s*", chunk[i + 1]).group(0)
            chunk[i + 1:j] = [indent + _ellipsis(rel)]
    return dedent(chunk), rel


def _ellipsis(rel):
    if rel.endswith((".yaml", ".yml")):
        return "# ..."
    if rel.endswith(".xml"):
        return "<!-- ... -->"
    return "// ..."


def _find(lines, pattern, start, rel):
    rx = re.compile(pattern)
    for k in range(start, len(lines)):
        if rx.search(lines[k]):
            return k
    warn(f"{rel}: no line matches /{pattern}/ after line {start + 1}")
    return None


def dedent(lines):
    widths = [len(l) - len(l.lstrip()) for l in lines if l.strip()]
    cut = min(widths) if widths else 0
    return [l[cut:] for l in lines]


def code_figure(kind, label, lines_html, raw_lines, cap=None, width=EXCERPT_WIDTH, extra_class=""):
    too_wide = [l for l in raw_lines if len(l) > width]
    if too_wide:
        warn(f"{label}: {len(too_wide)} line(s) wider than {width} characters, e.g. {too_wide[0][:60]!r}")
    keep = " keep" if len(raw_lines) <= 26 else ""
    caption = f'<span class="path">{html.escape(label)}</span>'
    if cap:
        caption += f'<span class="note">{cap}</span>'
    body = "\n".join(lines_html)
    return (f'<figure class="code {kind}{keep}{extra_class}"><figcaption>{caption}</figcaption>'
            f'<pre>{body}</pre></figure>')


def expand_src(m):
    attrs = attributes(m.group(1))
    lines, rel = excerpt(attrs)
    fn = highlight.for_path(rel)
    # Data soft-wraps; code is formatted to the column and must not.
    data = rel.endswith((".json", ".md"))
    return code_figure("src", short_path(rel), fn("\n".join(lines)), lines, attrs.get("data-cap"),
                       width=10_000 if data else EXCERPT_WIDTH,
                       extra_class=" wrap" if data else "")


def expand_file(m):
    """
    A file captured from the running system by tools/capture.py.

    Everything printed in this book that claims to be output is one of these. A tool envelope, the
    generated briefing, the linter's report and the harness run are all read from disk at build
    time, so a figure that disagrees with the code is a build failure rather than a reader's
    discovery.
    """
    attrs = attributes(m.group(1))
    name = attrs["data-file"]
    path = CAPTURES / name
    if not path.exists():
        warn(f"missing capture {name}")
        return f"<p><b>missing capture {name}</b></p>"
    text = path.read_text().rstrip("\n")
    if "data-from" in attrs:
        lines = text.split("\n")
        first = _find(lines, attrs["data-from"], 0, name) or 0
        last = _find(lines, attrs["data-to"], first + 1, name) if "data-to" in attrs else None
        text = "\n".join(lines[first:(last + 1) if last is not None else len(lines)])
    fn = highlight.by_name(attrs["data-lang"]) if "data-lang" in attrs else highlight.for_path(name)
    return code_figure("file", attrs.get("data-label", name), fn(text), text.split("\n"),
                       attrs.get("data-cap"), width=10_000,
                       extra_class=" wrap" if attrs.get("data-wrap") != "no" else "")


def expand_svg(m):
    """
    Inline an SVG that tools/capture.py drew from the system itself.

    The producer graph was hand-drawn first and was wrong about two tools within a week, which is
    the argument for this whole mechanism in miniature: a figure that claims to show what the code
    does should be produced by the code.
    """
    path = CAPTURES / m.group(1)
    if not path.exists():
        warn(f"missing figure {m.group(1)}")
        return f"<p><b>missing figure {m.group(1)}</b></p>"
    return path.read_text()


def expand_directives(doc):
    # A tag's attributes: anything but a quote or an angle bracket, or a quoted run that may
    # contain either. Both halves earn their keep. Without the quoted run, a data-from pattern
    # containing a Java generic ends the match at the generic's '>' and the directive is silently
    # left in the page. Without the exclusion, a non-greedy .*? starts at an earlier <pre and
    # swallows every chapter between it and the next directive, which is a far worse bug because
    # the output is still valid HTML.
    attrs = r'(?:[^>"]|"[^"]*")*'
    doc = re.sub(rf"<pre\s+({attrs}data-src={attrs})></pre>", expand_src, doc, flags=re.S)
    doc = re.sub(rf"<pre\s+({attrs}data-file={attrs})></pre>", expand_file, doc, flags=re.S)
    doc = re.sub(r"<!--SVG:([\w.-]+)-->", expand_svg, doc)
    return doc


# ----------------------------------------------------------------------------- Appendix F

MODULES = [
    ("F.1", "The build", "The parent POM and each module's POM, and the two commands that run "
     "everything.", [
        "pom.xml", "tool-contract/pom.xml", "brokerage-domain/pom.xml",
        "brokerage-catalog/pom.xml", "tool-runtime/pom.xml", "agent-service/pom.xml",
        "test-harness/pom.xml", "desktop-bridge/pom.xml", "harness.sh"]),
    ("F.2", "The contract", "tool-contract: the descriptor, the ontology, the envelope, the "
     "error taxonomy, the schema profile and the conformance linter. No Spring, no domain.", None),
    ("F.3", "The ontology and the catalog", "brokerage-catalog/src/main/resources: the ontology, "
     "the fourteen descriptors, and the conduct half of the system prompt.", None),
    ("F.4", "The domain", "brokerage-domain: a self-contained brokerage with deterministic "
     "market data, a risk engine, an order book and single-use confirmation tokens.", None),
    ("F.5", "The handlers", "brokerage-catalog: the code behind each of the fourteen tools, and "
     "the runtime wiring the three front ends share.", None),
    ("F.6", "The runtime", "tool-runtime: the registry, the invocation pipeline, the approval "
     "gate, the audit log, the generated briefing and the Spring AI binding.", None),
    ("F.7", "The agent service", "agent-service: the agent over Claude and the HTTP surface the "
     "console talks to.", None),
    ("F.8", "The test harness", "test-harness: the scenario model, the cassettes, the runner, "
     "the expectations and the report.", None),
    ("F.9", "The evaluation suites", "test-harness/src/main/resources/evals: thirty scenarios "
     "across selection, trajectory and refusal.", [
        "test-harness/src/main/resources/evals/brokerage-reads.json",
        "test-harness/src/main/resources/evals/brokerage-trading.json",
        "test-harness/src/main/resources/evals/brokerage-refusals.json"]),
    ("F.10", "The desktop connector", "desktop-bridge: the protocol, and the transport it is "
     "deliberately separate from.", None),
    ("F.11", "The console", "console/src: the Angular front end, four pages.", None),
    ("F.12", "The tests", "Every test in the project: conformance, handler contracts, trading "
     "trajectories, the pipeline, the HTTP surface and the evaluation suites.", None),
]

CATALOG_ORDER = ["brokerage_accounts_list", "brokerage_instruments_search", "brokerage_quotes_get",
                 "brokerage_price_history_get", "brokerage_balances_get",
                 "brokerage_positions_list", "brokerage_tax_lots_list", "brokerage_orders_list",
                 "brokerage_order_get", "brokerage_executions_list", "brokerage_order_preview",
                 "brokerage_order_place", "brokerage_order_replace", "brokerage_order_cancel"]


def module_files(code):
    def under(base, *suffixes):
        root = CODE / base
        if not root.exists():
            return []
        found = []
        for suffix in suffixes or (".java",):
            found += [str(p.relative_to(CODE)) for p in root.rglob("*" + suffix)]
        return sorted(found)

    catalog_json = ["brokerage-catalog/src/main/resources/ontology/brokerage.ontology.json"]
    catalog_json += ["brokerage-catalog/src/main/resources/catalog/%s.json" % name
                     for name in CATALOG_ORDER]
    catalog_json += ["brokerage-catalog/src/main/resources/prompts/conduct.md"]

    console = [p for p in under("console/src", ".ts", ".css", ".html")
               if "/node_modules/" not in p]

    files = {
        "F.2": under("tool-contract/src/main/java"),
        "F.3": catalog_json,
        "F.4": under("brokerage-domain/src/main/java"),
        "F.5": under("brokerage-catalog/src/main/java"),
        "F.6": under("tool-runtime/src/main/java"),
        "F.7": under("agent-service/src/main/java")
               + ["agent-service/src/main/resources/application.yaml"],
        "F.8": under("test-harness/src/main/java"),
        "F.10": under("desktop-bridge/src/main/java")
                + ["desktop-bridge/src/main/resources/application.yaml"],
        "F.11": sorted(console) + ["console/proxy.config.json"],
        "F.12": (under("tool-contract/src/test/java") + under("brokerage-domain/src/test/java")
                 + under("tool-runtime/src/test/java") + under("brokerage-catalog/src/test/java")
                 + under("agent-service/src/test/java") + under("desktop-bridge/src/test/java")
                 + under("test-harness/src/test/java")),
    }
    return {code: files.get(code) for code in [m[0] for m in MODULES]}


def file_id(rel):
    return "f-" + re.sub(r"[^a-z0-9]+", "-", rel.lower()).strip("-")


def source_listings():
    generated = module_files(None)
    out, index, total_lines, total_files = [], [], 0, 0
    for code, title, blurb, files in MODULES:
        files = files or generated[code]
        out.append(f'<h2 class="lst-module" id="mod-{code.replace(".", "-")}">'
                   f'<span class="mnum">{code}</span> {html.escape(title)}</h2>')
        out.append(f'<p class="lst-blurb">{blurb}</p>')
        index.append(f'<li class="grp">{code} &nbsp;{html.escape(title)}</li>')
        for rel in files:
            src = (CODE / rel).read_text().rstrip("\n")
            lines = src.split("\n")
            total_lines += len(lines)
            total_files += 1
            # Code is formatted to the listing width and a long line is an author's mistake.
            # Data is not: a tool description is one JSON string and cannot be wrapped without
            # changing what the model is shown, so those listings soft-wrap instead.
            data = rel.endswith((".json", ".md"))
            wide = [l for l in lines if len(l) > LISTING_WIDTH]
            if wide and not data:
                warn(f"{rel}: {len(wide)} line(s) too wide for the listing page")
            # A wrapping listing needs each line in its own block, so that a continuation hangs
            # under the code rather than under the line number. Without it the numbers interleave
            # with the text and a long JSON description becomes unreadable.
            row = '<span class="lrow"><span class="ln">{:>4}</span>{}</span>' if data \
                else '<span class="ln">{:>4}</span>{}'
            numbered = [row.format(n, line)
                        for n, line in enumerate(highlight.for_path(rel)(src), start=1)]
            fid = file_id(rel)
            out.append(f'<h3 class="lst-file" id="{fid}">{html.escape(rel)}'
                       f'<span class="lines">{len(lines)} lines</span></h3>')
            # Blocks already break; joining them with a newline would double the leading.
            joined = "".join(numbered) if data else chr(10).join(numbered)
            out.append(f'<pre class="listing{" wrap" if data else ""}">{joined}</pre>')
            index.append(f'<li><a href="#{fid}"><span class="t">{html.escape(short_path(rel))}</span>'
                         f'<span class="pg" data-ref="{fid}">000</span></a></li>')
    head = (f'<div class="lst-index"><h2 class="lst-module" style="margin-top:0">Files in this appendix'
            f'</h2><ol class="toc files">{"".join(index)}</ol></div>')
    return head + "\n".join(out), total_files, total_lines


# ----------------------------------------------------------------------------- chapters and pages

def page_rules(doc):
    """One named page per chapter, so each chapter's footer carries its own title."""
    rules = []
    for m in re.finditer(r'<section class="([^"]*\bchapter\b[^"]*)" id="([\w-]+)"[^>]*data-foot="([^"]*)"', doc):
        classes, sid, foot = m.groups()
        label = html.unescape(foot).replace('"', '\\"')
        margins = " margin-left: 0.6in; margin-right: 0.6in;" if "listings" in classes else ""
        rules.append(f'#{sid} {{ page: {sid}; }}\n'
                     f'@page {sid} {{{margins} @bottom-left {{ content: "{label}"; }} }}')
    return "<style>\n/* generated by tools/build.py: one named page per chapter */\n" + \
        "\n".join(rules) + "\n</style>\n"


def plant_markers(doc):
    """An invisible marker at the start of every section and listed file, found later in the PDF."""
    def mark(m):
        return m.group(0) + f'<span class="pgmark">ZQ{m.group(2)}ZQ</span>'
    doc = re.sub(r'(<section class="[^"]*" id="([\w-]+)"[^>]*>)', mark, doc)
    doc = re.sub(r'(<h3 class="lst-file" id="([\w-]+)">)', mark, doc)
    return doc


def assemble():
    fragments = sorted(SRC.glob("*.html"))
    doc = "".join(f.read_text() for f in fragments) + "\n</body>\n</html>\n"
    listings, files, lines = source_listings()
    doc = doc.replace("<!-- SOURCE-LISTINGS -->", listings)
    doc = doc.replace("__LISTED_FILES__", str(files)).replace("__LISTED_LINES__", f"{lines:,}")
    doc = expand_directives(doc)
    doc = doc.replace("</head>", page_rules(doc) + "</head>", 1)
    return plant_markers(doc)


# ----------------------------------------------------------------------------- rendering

def chrome():
    for candidate in [os.environ.get("CHROME_BIN", ""),
                      "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
                      "/Applications/Chromium.app/Contents/MacOS/Chromium",
                      shutil.which("google-chrome") or "", shutil.which("chromium") or ""]:
        if candidate and os.access(candidate, os.X_OK):
            return candidate
    sys.exit("Could not find Chrome or Chromium. Set CHROME_BIN and re-run.")


def render(html_path, pdf_path):
    if pdf_path.exists():
        pdf_path.unlink()
    subprocess.run(["perl", "-e", "alarm 240; exec @ARGV", chrome(), "--headless", "--disable-gpu",
                    "--no-first-run", "--no-pdf-header-footer", "--run-all-compositor-stages-before-draw",
                    "--virtual-time-budget=20000", "--generate-pdf-document-outline",
                    f"--print-to-pdf={pdf_path}", f"file://{html_path}"], capture_output=True)
    if not pdf_path.exists() or pdf_path.stat().st_size == 0:
        sys.exit("Chrome did not produce a PDF")


def check_full_bleed(pdf_path):
    """
    Catch the one layout bug that ruins a whole book silently.

    Chrome's --print-to-pdf shrinks the entire document to fit when any single element is wider
    than the page. One over-long table cell with white-space: nowrap, five hundred pages in, and
    every page comes out at two thirds scale with a white band down two sides. Nothing warns; the
    PDF is valid; it just looks wrong in a way that is easy to put down to the renderer.

    The cover is a full-bleed dark page, so if its ink does not reach the paper's edge, the whole
    book has been scaled. Rendering it at twelve pixels to the inch is enough to tell, and the PNG
    is decoded here rather than with an image library so that the build keeps working on a machine
    that has only poppler.
    """
    if not shutil.which("pdftoppm"):
        return
    with tempfile.TemporaryDirectory() as tmp:
        stem = Path(tmp) / "p1"
        subprocess.run(["pdftoppm", "-r", "12", "-f", "1", "-l", "1", "-singlefile", "-gray",
                        "-png", str(pdf_path), str(stem)], capture_output=True)
        png = stem.with_suffix(".png")
        if not png.exists():
            return
        size, pixels = _gray_png(png.read_bytes())
    if not pixels:
        return
    width, height = size
    row = pixels[(height // 2) * width:(height // 2 + 1) * width]
    inked = [x for x, value in enumerate(row) if value < 200]
    if not inked:
        return
    reach = (max(inked) + 1) / width
    if reach < 0.97:
        warn(f"the cover's ink stops at {reach:.0%} of the page width, which means Chrome scaled "
             f"the whole book to fit something too wide. Look for a td.k, a pre with "
             f"white-space:pre, or an svg with a fixed width larger than the text column")


def _gray_png(data):
    """Decode an 8-bit grayscale, non-interlaced PNG. Enough for the check above, and no more."""
    width = height = 0
    idat = b""
    at = 8
    while at < len(data):
        length = int.from_bytes(data[at:at + 4], "big")
        kind = data[at + 4:at + 8]
        body = data[at + 8:at + 8 + length]
        if kind == b"IHDR":
            width = int.from_bytes(body[0:4], "big")
            height = int.from_bytes(body[4:8], "big")
            if body[8] != 8 or body[9] != 0 or body[12] != 0:
                return (0, 0), []
        elif kind == b"IDAT":
            idat += body
        elif kind == b"IEND":
            break
        at += length + 12
    if not width:
        return (0, 0), []
    raw = zlib.decompress(idat)
    out, previous = [], bytearray(width)
    at = 0
    for _ in range(height):
        filt = raw[at]
        line = bytearray(raw[at + 1:at + 1 + width])
        at += width + 1
        for x in range(width):                      # grayscale, so bpp is 1
            left = line[x - 1] if x else 0
            up = previous[x]
            upleft = previous[x - 1] if x else 0
            if filt == 1:
                line[x] = (line[x] + left) & 0xFF
            elif filt == 2:
                line[x] = (line[x] + up) & 0xFF
            elif filt == 3:
                line[x] = (line[x] + (left + up) // 2) & 0xFF
            elif filt == 4:
                p = left + up - upleft
                pa, pb, pc = abs(p - left), abs(p - up), abs(p - upleft)
                best = left if (pa <= pb and pa <= pc) else (up if pb <= pc else upleft)
                line[x] = (line[x] + best) & 0xFF
        out.extend(line)
        previous = line
    return (width, height), out


def page_texts(pdf_path):
    text = subprocess.run(["pdftotext", "-layout", str(pdf_path), "-"], capture_output=True,
                          text=True).stdout
    return text.split("\f")[:-1] if text.endswith("\f") else text.split("\f")


def find_pages(pdf_path):
    pages = {}
    for number, text in enumerate(page_texts(pdf_path), start=1):
        for ref in re.findall(r"ZQ([\w-]+?)ZQ", text):
            pages.setdefault(ref, number)
    return pages


def fill_numbers(doc, pages, total):
    def number(m):
        ref = m.group(1)
        if ref not in pages:
            warn(f"no page found for {ref}")
            return m.group(0)
        return f'<span class="pg" data-ref="{ref}">{pages[ref]}</span>'
    doc = re.sub(r'<span class="pg" data-ref="([\w-]+)">[^<]*</span>', number, doc)
    doc = doc.replace("__PAGES__", str(total))
    return re.sub(r'<span class="pgmark">ZQ[\w-]+ZQ</span>', "", doc)


# ----------------------------------------------------------------------------- metadata

META = {
    "Title": "REST API Standards with HATEOAS",
    "Author": "Smiroh Dev",
    "Subject": ("Designing, building and consuming hypermedia REST APIs, with a complete brokerage API "
                "in Spring Boot 4 and Java 27: HAL and HAL-FORMS, RFC 9457 problem details, idempotency, "
                "ETags, cursor pagination, server-sent events, versioning, OAuth 2.1, OpenAPI 3.2, "
                "contract tests, and an AI agent built with Spring AI."),
    "Keywords": ("REST, HATEOAS, hypermedia, HAL, HAL-FORMS, Spring Boot, Spring HATEOAS, Java, "
                 "OpenAPI 3.2, RFC 9457, idempotency, ETag, OAuth 2.1, PKCE, brokerage API, trading API, "
                 "Spring AI, AI agents, API design, API standards, ebook"),
    "Creator": "Smiroh Publishers",
    "Producer": "Smiroh Publishers (rendered with Chromium/Skia)",
}


def stamp_metadata(pdf_path):
    """Write /Author, /Subject and /Keywords as a PDF incremental update (see the CORS book)."""
    data = pdf_path.read_bytes()
    prev_xref = int(list(re.finditer(rb"startxref\s+(\d+)", data[-2048:]))[-1].group(1))
    trailer = data[prev_xref:]
    size = int(re.search(rb"/Size\s+(\d+)", trailer).group(1))
    root = re.search(rb"/Root\s+(\d+\s+\d+\s+R)", trailer).group(1).decode()
    info_num = int(re.search(rb"/Info\s+(\d+)\s+\d+\s+R", trailer).group(1))
    dates = ""
    old = re.search(rb"\n%d 0 obj(.{0,900}?)endobj" % info_num, data, re.S)
    if old:
        for key in (b"CreationDate", b"ModDate"):
            d = re.search(rb"/%s\s*(\([^)]*\))" % key, old.group(1))
            if d:
                dates += f"\n/{key.decode()} {d.group(1).decode('latin-1')}"

    def pdf_string(value):
        return "(" + value.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)") + ")"

    body = "".join(f"\n/{k} {pdf_string(v)}" for k, v in META.items()) + dates
    new_info = f"{info_num} 0 obj\n<<{body}\n>>\nendobj\n".encode("latin-1")
    if not data.endswith(b"\n"):
        data += b"\n"
    info_offset = len(data)
    out = data + new_info
    xref_offset = len(out)
    xref = (b"xref\n0 1\n0000000000 65535 f \n" + f"{info_num} 1\n".encode()
            + f"{info_offset:010d} 00000 n \n".encode() + b"trailer\n"
            + f"<</Size {size}\n/Root {root}\n/Info {info_num} 0 R\n/Prev {prev_xref}>>\n".encode()
            + b"startxref\n" + f"{xref_offset}\n".encode() + b"%%EOF\n")
    pdf_path.write_bytes(out + xref)


# ----------------------------------------------------------------------------- main

def main():
    doc = assemble()
    if "--html" in sys.argv:
        HTML_OUT.write_text(re.sub(r'<span class="pgmark">ZQ[\w-]+ZQ</span>', "", doc))
        print(f"  {HTML_OUT.name} assembled")
        report()
        return
    with tempfile.TemporaryDirectory() as tmp:
        probe_html, probe_pdf = Path(tmp) / f"{NAME}.html", Path(tmp) / "probe.pdf"
        probe_html.write_text(doc)
        render(probe_html, probe_pdf)
        pages = find_pages(probe_pdf)
        total = len(page_texts(probe_pdf))
    final = fill_numbers(doc, pages, total)
    HTML_OUT.write_text(final)
    render(HTML_OUT, PDF_OUT)
    rendered = len(page_texts(PDF_OUT))
    if rendered != total:
        warn(f"page count changed between passes ({total} -> {rendered}); rebuild")
    check_full_bleed(PDF_OUT)
    stamp_metadata(PDF_OUT)
    print(f"  {PDF_OUT.name}: {rendered} pages, {PDF_OUT.stat().st_size // 1024} KB")
    report()


def report():
    for message in warnings:
        print(f"  WARNING: {message}")
    if warnings:
        sys.exit(1)


if __name__ == "__main__":
    main()
