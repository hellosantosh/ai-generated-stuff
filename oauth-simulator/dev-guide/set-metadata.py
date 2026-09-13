#!/usr/bin/env python3
"""
Write proper document metadata into the built PDF.

Headless Chrome sets /Title (from the <title> tag) but leaves /Author,
/Subject and /Keywords empty and reports itself as the /Creator. Retailers,
library software and PDF readers all surface those fields, so a book being
sold needs them filled in.

This is done as a PDF *incremental update*: the original bytes are left
untouched and a new version of the document-information object is appended,
followed by a fresh cross-reference section whose /Prev points at the old
one. That is the mechanism the PDF specification provides for exactly this,
and it cannot corrupt the existing objects.
"""
import re, sys, pathlib

PDF = pathlib.Path(__file__).parent / "dev-guide.pdf"

META = {
    "Title":    "The Ultimate Developer's Guide to OAUTH 2.1 and PKCE",
    "Author":   "Smiroh Dev",
    "Subject":  ("OAuth 2.1 and PKCE explained from first principles: the authorization "
                 "code flow, proof key for code exchange, tokens and scopes, refresh "
                 "rotation, and the attack each rule prevents."),
    "Keywords": ("OAuth 2.1, PKCE, OAuth, authorization, delegated authorization, "
                 "OpenID Connect, JWT, access token, refresh token, RFC 7636, RFC 9700, "
                 "API security, web security, developer guide, ebook"),
    "Creator":  "Smiroh Publishers",
    "Producer": "Smiroh Publishers (rendered with Chromium/Skia)",
}


def pdf_string(value):
    """Escape a value for a PDF literal string."""
    out = value.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")
    return f"({out})"


def main():
    data = PDF.read_bytes()

    # Where does the current cross-reference section start?
    tail = data[-2048:]
    m = list(re.finditer(rb'startxref\s+(\d+)', tail))
    if not m:
        sys.exit("no startxref found; is this a PDF?")
    prev_xref = int(m[-1].group(1))

    # The trailer that follows it tells us /Size, /Root and /Info.
    trailer = data[prev_xref:]
    def grab(pattern, what):
        found = re.search(pattern, trailer)
        if not found:
            sys.exit(f"could not read {what} from the trailer")
        return found.group(1).decode()
    size = int(grab(rb'/Size\s+(\d+)', "/Size"))
    root = grab(rb'/Root\s+(\d+\s+\d+\s+R)', "/Root")
    info_num = int(grab(rb'/Info\s+(\d+)\s+\d+\s+R', "/Info"))

    # Preserve the timestamps the renderer recorded.
    dates = ""
    old_info = re.search(rb'\n%d 0 obj(.{0,900}?)endobj' % info_num, data, re.S)
    if old_info:
        for key in (b"CreationDate", b"ModDate"):
            d = re.search(rb'/%s\s*(\([^)]*\))' % key, old_info.group(1))
            if d:
                dates += f"\n/{key.decode()} {d.group(1).decode('latin-1')}"

    body = "".join(f"\n/{k} {pdf_string(v)}" for k, v in META.items()) + dates
    new_info = f"{info_num} 0 obj\n<<{body}\n>>\nendobj\n".encode("latin-1")

    # Append: the replacement object, then an xref section naming only it.
    if not data.endswith(b"\n"):
        data += b"\n"
    info_offset = len(data)
    out = data + new_info

    xref_offset = len(out)
    xref = (
        b"xref\n"
        b"0 1\n"
        b"0000000000 65535 f \n"
        + f"{info_num} 1\n".encode()
        + f"{info_offset:010d} 00000 n \n".encode()
        + b"trailer\n"
        + f"<</Size {size}\n/Root {root}\n/Info {info_num} 0 R\n/Prev {prev_xref}>>\n".encode()
        + b"startxref\n"
        + f"{xref_offset}\n".encode()
        + b"%%EOF\n"
    )
    PDF.write_bytes(out + xref)

    print(f"  metadata written (incremental update, +{len(new_info) + len(xref)} bytes)")
    for k in ("Title", "Author", "Subject"):
        v = META[k]
        print(f"    /{k:9} {v[:64]}{'...' if len(v) > 64 else ''}")


if __name__ == "__main__":
    main()
