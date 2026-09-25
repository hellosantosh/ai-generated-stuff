# The Ultimate Developer's Guide to CORS: Border Control for the Web

A 61-page illustrated ebook on Cross-Origin Resource Sharing, by Smiroh Dev (Smiroh
Publishers), plus the zero-dependency lab server its hands-on exercises use. It is a companion
to the OAuth 2.1 book in [`../oauth-simulator/dev-guide/`](../oauth-simulator/dev-guide/).

| Artifact | Path |
| --- | --- |
| **The book (PDF)** | [`book/cors-guide.pdf`](book/cors-guide.pdf) |
| Cover image for store listings | [`book/cover.png`](book/cover.png) |
| Square thumbnail for store listings | [`book/thumbnail.png`](book/thumbnail.png) |
| Lab server | [`lab/server.js`](lab/server.js) |
| Book source (one fragment per part) | [`book/src/`](book/src/) |
| Assembled HTML | [`book/cors-guide.html`](book/cors-guide.html), a build byproduct |

## What the book covers

The book follows a fictional fintech team, Harbor Pay, from a launch night broken by a CORS
error to a company-wide security review. It starts with basic concepts and ends with advanced
ones:

| Part | Chapters |
| --- | --- |
| I · A Web Without Walls | The launch that went dark · why browsers built walls · origins · what the same-origin policy blocks · life before CORS |
| II · How CORS Works | The protocol · simple requests · the preflight · credentials and cookies · reading responses · debugging |
| III · When the Wall Has Holes | Why CORS is a security priority · six configuration mistakes and their fixes · seven attack patterns, drawn, with warning signs, defenses and an incident checklist · real-world case studies · why CORS is not CSRF protection |
| IV · Advanced Territory | Caches and `Vary: Origin` · the local network and Local Network Access · CORP, COEP, COOP and Fetch Metadata · secure policy design · epilogue |
| V · The Labs | Eight DevTools labs, plus the complete lab server source |

The security material is written for defenders. Chapter 14 draws each attack pattern as a
diagram showing how it flows, where a defense breaks the chain, and the warning signs to watch
for, then ends with a detection and response checklist. Real incidents are summarized at the
level of public reporting. The book contains no exploit code.

## Run the labs

```bash
cd lab
node server.js          # app on http://localhost:8080, API on http://localhost:8081
node server.js check    # print the headers the CORS policy produces
```

Node.js 18 or later. No `npm install`. Each lab in Part V is a one-line change to the `POLICY`
object at the top of `server.js`.

## Build the book

```bash
cd book
./build-book.sh           # src/*.html -> cors-guide.html -> cors-guide.pdf
./build-book.sh --check   # also report any page whose content overflows
./export-cover.py         # cover.png, 1836 x 2376
./export-thumbnail.py     # thumbnail.png, 1000 x 1000
```

The build needs Chrome or Chromium, Python 3 and `pdfinfo`/`pdftotext` (poppler). It works like
the OAuth book's pipeline:

1. The fragments in `src/` are joined in filename order.
2. Listing A is generated from the real `lab/server.js`.
3. Page numbers in footers and the contents are derived from the document.
4. Headless Chrome renders the PDF, and `set-metadata.py` fills in the author, subject and keywords.
