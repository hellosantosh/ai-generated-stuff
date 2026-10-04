#!/usr/bin/env python3
"""
Build an unbranded copy of the book, for a reader who should not see the imprint.

    tools/build-unbranded.py            brokerage-tools-unbranded.pdf
    tools/build-unbranded.py --html     the assembled HTML only
    tools/build-unbranded.py --assets   also export the unbranded cover and thumbnail

Three differences from the book proper, and nothing else:

  1. The copyright page is dropped. It is page 2 of the original, so every page after it moves up
     by one -- which costs nothing, because the contents' page numbers are found by rendering and
     not typed.
  2. The author and the publisher are removed from the cover, from the HTML head and from the
     PDF's own metadata. The cover keeps its edition line so the byline rule still has something
     to sit under.
  3. Nothing else. The text, the figures, the captures and the source appendix are the same file
     for file.

It works by copying src/ to a scratch directory, editing the copies, and running the ordinary
build against those. The original fragments are opened read-only and the original PDF, HTML and
store assets are never written to -- which is the point, and which the final check asserts rather
than assumes.
"""
import hashlib
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile

HERE = pathlib.Path(__file__).resolve().parent
BOOK = HERE.parent
sys.path.insert(0, str(HERE))

import build                                                        # noqa: E402

NAME = "brokerage-tools-unbranded"

# Everything the original says about who made it. Each is removed rather than replaced, except
# the byline, which becomes the edition line on its own so the rule above it still has a purpose.
IMPRINT = re.compile(r'\s*<div class="imprint">.*?</div>\n', re.S)
COPYRIGHT = re.compile(r'<!-- =*? copyright -->\n<section class="copyright">.*?</section>\n', re.S)
BYLINE = re.compile(r'    <div class="byline">.*?\n    </div>\n', re.S)
AUTHOR_META = re.compile(r'<meta name="author" content="[^"]*">\n')

BYLINE_REPLACEMENT = """    <div class="byline">
      <div class="author-lbl">First edition &middot; 2026</div>
    </div>
"""

# The PDF's Info dictionary. The three fields that name a person or a house are dropped and the
# two Chrome fills in are left neutral.
#
# Title, Subject and Keywords are written out here rather than inherited from build.META, which
# still carries the values this book's build script was ported with: a reader opening the original
# brokerage-tools.pdf sees "REST API Standards with HATEOAS" in the title bar. That is a defect in
# the original and it is deliberately not fixed from here, because this script's one promise is to
# leave the original alone.
META = {
    "Title": "Tool Specifications and Design for the Brokerage Domain",
    "Subject": ("Designing and building model-invocable tools a frontier model can use safely: the "
                "descriptor, the naming grammar, the JSON Schema profile, the result envelope and "
                "error taxonomy, risk tiers and approval, a domain ontology and producer graph, a "
                "tool registry and invocation pipeline, a Spring AI agent over Claude, an Angular "
                "console, a desktop connector, and an offline agentic test harness - with a "
                "complete fourteen-tool brokerage catalog in Java 27 and Spring Boot 4."),
    "Keywords": ("tool specification, tool design, model-invocable tools, tool descriptor, tool "
                 "registry, tool ontology, JSON Schema, tool calling, agent evaluation, selection "
                 "accuracy, risk tier, approval gate, idempotency, confirmation token, audit, "
                 "brokerage, trading tools, order preview, Spring AI, Spring Boot, Java, Angular, "
                 "Claude, AI agents, agentic testing, ebook"),
    "Creator": "Chromium",
    "Producer": "Chromium/Skia",
}


def fingerprint(paths):
    """A digest of files this script must not change, taken before and after the build."""
    digest = hashlib.sha256()
    for path in sorted(paths):
        digest.update(path.name.encode())
        digest.update(path.read_bytes() if path.exists() else b"absent")
    return digest.hexdigest()


def strip(text, pattern, what, substitute=""):
    edited, count = pattern.subn(substitute, text)
    if not count:
        sys.exit(f"nothing matched {what}; the sources have moved and this script is stale")
    return edited


def main():
    protected = [BOOK / "brokerage-tools.pdf", BOOK / "brokerage-tools.html",
                 BOOK / "cover.png", BOOK / "thumbnail.png"]
    protected += sorted((BOOK / "src").glob("*.html"))
    before = fingerprint(protected)

    with tempfile.TemporaryDirectory() as tmp:
        scratch = pathlib.Path(tmp) / "src"
        shutil.copytree(BOOK / "src", scratch)

        head = scratch / "00-head.html"
        head.write_text(strip(head.read_text(), AUTHOR_META, "the author meta tag"))

        cover = scratch / "10-cover.html"
        text = cover.read_text()
        text = strip(text, COPYRIGHT, "the copyright page")
        text = strip(text, IMPRINT, "the cover imprint")
        text = strip(text, BYLINE, "the cover byline", BYLINE_REPLACEMENT)
        cover.write_text(text)

        for leftover in ("Smiroh",):
            found = [p.name for p in scratch.glob("*.html") if leftover in p.read_text()]
            if found:
                sys.exit(f"'{leftover}' still appears in {found}")

        build.SRC = scratch
        build.NAME = NAME
        build.HTML_OUT = BOOK / f"{NAME}.html"
        build.PDF_OUT = BOOK / f"{NAME}.pdf"
        build.META = META
        build.main()

    if fingerprint(protected) != before:
        sys.exit("this script changed a file it was supposed to leave alone")

    if "--assets" in sys.argv:
        assets()
    print("  the original book and its assets are byte for byte unchanged")


def assets():
    """The cover, rasterized from page 1 of the copy, and a thumbnail with the byline removed."""
    if not shutil.which("pdftoppm"):
        print("  pdftoppm not found; skipping the assets")
        return
    stem = BOOK / "_unbranded_cover"
    subprocess.run(["pdftoppm", "-r", "216", "-f", "1", "-l", "1", "-singlefile", "-png",
                    str(BOOK / f"{NAME}.pdf"), str(stem)], check=True)
    out = BOOK / f"cover-{NAME.split('-')[-1]}.png"
    stem.with_suffix(".png").replace(out)
    head = out.read_bytes()[16:24]
    print(f"  {out.name}  {int.from_bytes(head[:4], 'big')} x {int.from_bytes(head[4:], 'big')} px"
          f"  ({out.stat().st_size // 1024} KB)")

    source = (HERE / "export-thumbnail.py").read_text()
    source = source.replace('OUT = BOOK / "thumbnail.png"',
                            'OUT = BOOK / "thumbnail-unbranded.png"')
    source = source.replace('PDF = BOOK / "brokerage-tools.pdf"',
                            f'PDF = BOOK / "{NAME}.pdf"')
    source = source.replace('<div class="author">Smiroh Dev</div>',
                            '<div class="badge">First edition &#183; 2026</div>')
    scratch = HERE / "_export-thumbnail-unbranded.py"
    scratch.write_text(source)
    try:
        subprocess.run([sys.executable, str(scratch)], check=True)
    finally:
        scratch.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
