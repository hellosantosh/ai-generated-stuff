#!/usr/bin/env python3
"""
Generate the code-listing pages of the book from the real lab source files.

The listings are generated rather than pasted so the code printed in the book
is, by construction, the code that was tested. Edit lab/*.js, run this, and
the book follows.

It replaces whatever sits between the LAB_LISTINGS markers in
src/dev-guide.html, so it is safe to run repeatedly.
"""
import html as htmllib
import math
import pathlib
import re
import sys

HERE = pathlib.Path(__file__).parent
LAB = HERE.parent / "lab"
SRC = HERE / "src" / "dev-guide.html"

# Measured against the fixed page box: a continuation page holds 59 code lines,
# and the first page of each file gives ~5 of them up to its intro blurb.
FIRST_PAGE_LINES = 53
CONT_PAGE_LINES = 59

FILES = [
    ("crypto-lab.js", "Listing A", "PKCE and JWT primitives",
     "Everything cryptographic the labs need, on Node core modules. Read "
     "<code>verifyPkce</code> and <code>verifyJwt</code> and you have read the "
     "two checks the rest of the protocol rests on."),
    ("authz-server.js", "Listing B", "The authorization server and IdP",
     "The whole of Chapter 4 as running code: <code>/authorize</code> with its "
     "six validations, login, consent, and a <code>/token</code> endpoint "
     "handling three grants. The <code>policy</code> object near the top is what "
     "Lab 4 switches off."),
    ("api-server.js", "Listing C", "The resource server",
     "The four gates of Chapter 12, in order, in one function. Note that steps "
     "1&ndash;3 never contact the authorization server: the signature is checked "
     "offline against cached public keys."),
    ("client.js", "Listing D", "The client, and all fifteen labs",
     "The client walks the front channel with a cookie jar instead of a browser, "
     "so every lab is one command whose result is printed rather than clicked. "
     "Each <code>LABS.*</code> entry is one exercise."),
]


# --------------------------------------------------------------------------
# A small JavaScript scanner. Only enough to know whether we are inside a
# string, a comment or a regex, so highlighting can never mis-tokenise --
# e.g. "//" inside a string, or the regex literal /^[a-z\-._~]{43}$/.
# --------------------------------------------------------------------------
REGEX_OK_AFTER = set("(,=:[!&|?{};+-*%<>~^")
KEYWORDS_BEFORE_REGEX = {"return", "typeof", "instanceof", "in", "of", "new",
                         "delete", "void", "case", "do", "else", "yield", "await"}


def tokenize(src):
    """Yield (kind, text) where kind is 'code' | 'str' | 'com' | 'rex'."""
    i, n = 0, len(src)
    out = []
    buf = []
    last_sig = ""          # last significant (non-space) character of code
    last_word = ""

    def flush():
        if buf:
            out.append(("code", "".join(buf)))
            buf.clear()

    while i < n:
        c = src[i]
        two = src[i:i + 2]

        if two == "//":
            flush()
            j = src.find("\n", i)
            j = n if j == -1 else j
            out.append(("com", src[i:j]))
            i = j
            continue

        if two == "/*":
            flush()
            j = src.find("*/", i + 2)
            j = n if j == -1 else j + 2
            out.append(("com", src[i:j]))
            i = j
            continue

        if c in "'\"`":
            flush()
            quote = c
            j = i + 1
            while j < n:
                if src[j] == "\\":
                    j += 2
                    continue
                if src[j] == quote:
                    j += 1
                    break
                if src[j] == "\n" and quote != "`":
                    break
                j += 1
            out.append(("str", src[i:j]))
            last_sig = quote
            i = j
            continue

        if c == "/":
            # regex, or division? decide from what came before.
            starts_regex = (last_sig == "" or last_sig in REGEX_OK_AFTER
                            or last_word in KEYWORDS_BEFORE_REGEX)
            if starts_regex:
                j, in_class, closed = i + 1, False, False
                while j < n:
                    ch = src[j]
                    if ch == "\\":
                        j += 2
                        continue
                    if ch == "\n":
                        break
                    if ch == "[":
                        in_class = True
                    elif ch == "]":
                        in_class = False
                    elif ch == "/" and not in_class:
                        j += 1
                        closed = True
                        break
                    j += 1
                if closed:
                    while j < n and src[j].isalpha():   # flags
                        j += 1
                    flush()
                    out.append(("rex", src[i:j]))
                    last_sig = "/"
                    last_word = ""
                    i = j
                    continue

        buf.append(c)
        if not c.isspace():
            last_sig = c
            last_word = last_word + c if (c.isalnum() or c == "_") else ""
        i += 1

    flush()
    return out


CSS_CLASS = {"str": "s", "com": "c", "rex": "x", "code": None}


def highlight(src):
    """Return the source with highlight spans, split back into lines."""
    pieces = []
    for kind, text in tokenize(src):
        cls = CSS_CLASS[kind]
        esc = htmllib.escape(text)
        if cls is None:
            pieces.append(esc)
        else:
            # a span must not straddle a newline, or the line split breaks it
            parts = esc.split("\n")
            pieces.append("\n".join(
                f'<span class="{cls}">{p}</span>' if p else "" for p in parts))
    return "".join(pieces).split("\n")


def page(file_name, label, title, blurb, lines, start_no, part, total_parts):
    """One listing page."""
    body = []
    for offset, code in enumerate(lines):
        no = start_no + offset
        body.append(f'<span class="ln">{no:>3}</span>{code}')
    cont = "" if total_parts == 1 else (
        f' <span class="lst-part">part {part} of {total_parts}</span>')
    intro = f'<p class="lst-blurb">{blurb}</p>' if part == 1 else ""
    end = start_no + len(lines) - 1
    return f'''
<section class="page listing-page">
  <div class="chnum">{label} &middot; lab/{file_name}{cont}</div>
  <h1 class="lst-title">{title}</h1>
  {intro}
  <div class="lst-meta">lines {start_no}&ndash;{end} of {"{total}"}</div>
<pre class="listing">{chr(10).join(body)}</pre>
  <div class="pfoot"><span class="ch">{label} &middot; lab/{file_name}</span><span class="n">0</span></div>
</section>'''


def split_pages(lines):
    """Spread lines over the fewest pages that fit, then balance them so the
    last page of a file is never left nearly empty."""
    total = len(lines)
    pages = 1
    while FIRST_PAGE_LINES + (pages - 1) * CONT_PAGE_LINES < total:
        pages += 1

    even = min(math.ceil(total / pages), CONT_PAGE_LINES)
    counts = [min(FIRST_PAGE_LINES, even)]
    remaining, rest = total - counts[0], pages - 1
    if rest:
        base, extra = divmod(remaining, rest)
        counts += [base + 1] * extra + [base] * (rest - extra)
    assert sum(counts) == total and all(c <= CONT_PAGE_LINES for c in counts)

    out, at = [], 0
    for c in counts:
        out.append(lines[at:at + c])
        at += c
    return out


def main():
    sections = []
    for file_name, label, title, blurb in FILES:
        path = LAB / file_name
        if not path.exists():
            sys.exit(f"missing {path}")
        src = path.read_text().rstrip("\n")
        total = src.count("\n") + 1
        chunks = split_pages(highlight(src))
        start = 1
        for idx, chunk in enumerate(chunks):
            sections.append(
                page(file_name, label, title, blurb, chunk,
                     start, idx + 1, len(chunks))
                .replace("{total}", str(total)))
            start += len(chunk)

    generated = ("\n<!-- LAB_LISTINGS:START - generated by build-labs.py, "
                 "do not edit by hand -->" + "".join(sections)
                 + "\n<!-- LAB_LISTINGS:END -->\n")

    doc = SRC.read_text()
    pattern = re.compile(
        r"\n?<!-- LAB_LISTINGS:START.*?<!-- LAB_LISTINGS:END -->\n?", re.S)
    if pattern.search(doc):
        # A lambda replacement, because the generated listings contain JS regex
        # literals and re.sub would try to interpret \s, \+ etc. as escapes.
        doc = pattern.sub(lambda _m: generated, doc)
    elif "<!-- LAB_LISTINGS -->" in doc:
        doc = doc.replace("<!-- LAB_LISTINGS -->", generated)
    else:
        sys.exit("no LAB_LISTINGS marker found in src/dev-guide.html")
    SRC.write_text(doc)

    # rstrip first, or a trailing newline counts as an extra line per file
    total_lines = sum(len((LAB / f[0]).read_text().rstrip("\n").split("\n"))
                      for f in FILES)
    print(f"  generated {len(sections)} listing pages from {len(FILES)} files "
          f"({total_lines} lines)")


if __name__ == "__main__":
    main()
