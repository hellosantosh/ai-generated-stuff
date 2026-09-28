#!/usr/bin/env python3
"""
Build rest-api-hateoas.pdf from the fragments in src/.

The book is a flowing HTML document laid out by Chrome's paged-media engine: @page
margins, one named page per chapter (so each footer names its chapter), and full-bleed
named pages for the cover and part openers. Nothing is typeset by hand, so long code
listings simply continue on the next page.

Code in the book is never pasted. Three directives pull it in at build time:

  <pre data-src="brokerage-api/.../OrderService.java" data-from="REGEX" data-to="REGEX"></pre>
      an excerpt of a source file under brokerage-apis/, highlighted. Optional:
      data-after (search for data-from only after this line), data-skip="A..B"
      (elide the lines strictly between A and B), data-doc="no" (do not pull in the
      Javadoc and annotations above data-from), data-cap (a caption).
  <pre data-http="place"></pre>
      an HTTP exchange recorded from the running application by tools/capture.py.
  <pre data-file="claims-cli.json"></pre>
      any other captured file.

<!-- SOURCE-LISTINGS --> becomes Appendix E: every source file, with line numbers.

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
from pathlib import Path

import highlight

HERE = Path(__file__).resolve().parent
BOOK = HERE.parent
CODE = BOOK.parent.parent.parent / "brokerage-apis"
SRC = BOOK / "src"
CAPTURES = BOOK / "captures"
NAME = "rest-api-hateoas"
HTML_OUT = BOOK / f"{NAME}.html"
PDF_OUT = BOOK / f"{NAME}.pdf"

# Widest line, in characters, that fits a code block at its font size.
EXCERPT_WIDTH = 104
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
    """brokerage-api/src/main/java/com/example/brokerage/api/orders/X.java -> api/orders/X.java"""
    m = re.match(r"[\w-]+/src/(main|test)/java/com/example/brokerage/(.*)$", path)
    if m:
        return ("test: " if m.group(1) == "test" else "") + m.group(2)
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
    last = _find(lines, attrs["data-to"], first, rel) if "data-to" in attrs else len(lines) - 1
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
    return code_figure("src", short_path(rel), fn("\n".join(lines)), lines, attrs.get("data-cap"))


def expand_http(m):
    attrs = attributes(m.group(1))
    name = attrs["data-http"]
    path = CAPTURES / f"{name}.http"
    if not path.exists():
        warn(f"missing capture {name}")
        return f"<p><b>missing capture {name}</b></p>"
    text = path.read_text().rstrip("\n")
    if attrs.get("data-show") == "response":
        text = text[re.search(r"^HTTP/1\.1 \d{3}", text, re.M).start():]
    elif attrs.get("data-show") == "request":
        text = text[:re.search(r"^HTTP/1\.1 \d{3}", text, re.M).start()].rstrip()
    if "data-cut" in attrs:        # drop response body lines matching a pattern (e.g. curies)
        text = cut_json(text, attrs["data-cut"])
    lines = text.split("\n")
    label = attrs.get("data-label", "HTTP")
    return code_figure("http", label, highlight.http(text), lines, attrs.get("data-cap"))


def cut_json(text, what):
    """Remove the HAL "curies" array, which every response repeats, from printed JSON."""
    if what == "curies":
        text = re.sub(r',\n(\s*)"curies": \[\n.*?\n\1\]', "", text, flags=re.S)
    return text


def expand_file(m):
    attrs = attributes(m.group(1))
    name = attrs["data-file"]
    path = CAPTURES / name
    if not path.exists():
        warn(f"missing capture {name}")
        return f"<p><b>missing capture {name}</b></p>"
    text = path.read_text().rstrip("\n")
    fn = highlight.by_name(attrs["data-lang"]) if "data-lang" in attrs else highlight.for_path(name)
    return code_figure("file", attrs.get("data-label", name), fn(text), text.split("\n"),
                       attrs.get("data-cap"))


def expand_directives(doc):
    doc = re.sub(r"<pre\s+(data-src=[^>]*)></pre>", expand_src, doc)
    doc = re.sub(r"<pre\s+(data-http=[^>]*)></pre>", expand_http, doc)
    doc = re.sub(r"<pre\s+(data-file=[^>]*)></pre>", expand_file, doc)
    return doc


# ----------------------------------------------------------------------------- Appendix E

MODULES = [
    ("E.1", "The build", "The parent POM and each module's POM.", [
        "pom.xml", "auth-server/pom.xml", "brokerage-api/pom.xml", "brokerage-client/pom.xml",
        "brokerage-agent/pom.xml"]),
    ("E.2", "The authorization server", "auth-server: Spring Authorization Server, configured for "
     "three clients and one demo customer.", [
        "auth-server/src/main/java/com/example/brokerage/auth/AuthServerApplication.java",
        "auth-server/src/main/resources/application.yaml"]),
    ("E.3", "The API", "brokerage-api: the hypermedia REST API, package by package.", None),
    ("E.4", "The API's tests", "brokerage-api/src/test: protocol, hypermedia, lifecycle, rate limit "
     "and contract tests.", None),
    ("E.5", "The hypermedia client", "brokerage-client: a Java client that follows links.", None),
    ("E.6", "The agent", "brokerage-agent: the Spring AI assistant, its tools and its guardrails.", None),
    ("E.7", "The OpenAPI contract", "openapi/brokerage-api.yaml: the API's OpenAPI 3.2 description.",
     ["openapi/brokerage-api.yaml"]),
]

API_ORDER = ["BrokerageApiApplication.java", "RootController.java", "platform/", "market/", "accounts/",
             "orders/"]


def module_files(code):
    def java_under(base):
        return sorted(str(p.relative_to(CODE)) for p in (CODE / base).rglob("*.java"))

    api_main = java_under("brokerage-api/src/main/java")

    def api_key(p):
        tail = p.split("com/example/brokerage/api/")[1]
        for i, prefix in enumerate(API_ORDER):
            if tail == prefix or tail.startswith(prefix):
                return (i, tail)
        return (99, tail)

    files = {
        "E.3": sorted(api_main, key=api_key) + ["brokerage-api/src/main/resources/application.yaml"],
        "E.4": java_under("brokerage-api/src/test/java"),
        "E.5": java_under("brokerage-client/src/main/java") + java_under("brokerage-client/src/test/java"),
        "E.6": java_under("brokerage-agent/src/main/java")
               + ["brokerage-agent/src/main/resources/application.yaml"]
               + java_under("brokerage-agent/src/test/java"),
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
            wide = [l for l in lines if len(l) > LISTING_WIDTH]
            if wide:
                warn(f"{rel}: {len(wide)} line(s) too wide for the listing page")
            numbered = [f'<span class="ln">{n:>4}</span>{line}'
                        for n, line in enumerate(highlight.for_path(rel)(src), start=1)]
            fid = file_id(rel)
            out.append(f'<h3 class="lst-file" id="{fid}">{html.escape(rel)}'
                       f'<span class="lines">{len(lines)} lines</span></h3>')
            out.append(f'<pre class="listing">{chr(10).join(numbered)}</pre>')
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
