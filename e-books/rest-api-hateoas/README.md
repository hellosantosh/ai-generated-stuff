# REST API Standards with HATEOAS

An e-book by Smiroh Dev (Smiroh Publishers) on designing, building and consuming hypermedia
REST APIs, built around a complete brokerage API in Spring Boot 4 and Java 27. It runs from
first principles to idempotent trading, OAuth 2.1, OpenAPI 3.2 contracts and an AI agent built
with Spring AI. The sample application it describes lives in
[`../../brokerage-apis/`](../../brokerage-apis/), and the book prints all of its source.

| Artifact | Path |
| --- | --- |
| **The book (PDF)** | [`book/rest-api-hateoas.pdf`](book/rest-api-hateoas.pdf) |
| Cover image for store listings (1836 x 2376) | [`book/cover.png`](book/cover.png) |
| Square thumbnail for store listings (1000 x 1000) | [`book/thumbnail.png`](book/thumbnail.png) |
| Book source, one fragment per part | [`book/src/`](book/src/) |
| HTTP exchanges recorded from the running application | [`book/captures/`](book/captures/) |
| Build, capture and export tools | [`book/tools/`](book/tools/) |
| Assembled HTML (a build byproduct; it also reads well in a browser) | [`book/rest-api-hateoas.html`](book/rest-api-hateoas.html) |

## What the book covers

The chapters build from the basics to advanced topics:

| Part | Chapters |
| --- | --- |
| I · Foundations | Why API standards matter · REST in one chapter (constraints, the Richardson maturity model) · HTTP, the parts that matter · your workbench: build, run and call the API |
| II · Designing Resources | Modeling a brokerage as resources · representations: money as strings, time in UTC · accounts, balances, positions and history · instruments, quotes and option chains |
| III · Hypermedia | HATEOAS and the order state machine · HAL, link relations and CURIEs · Spring HATEOAS · affordances with HAL-FORMS |
| IV · Trading Safely | Placing orders and layered validation · idempotency keys · ETags, merge patch and `202 Accepted` cancels · RFC 9457 problem details |
| V · Operating at Scale | Cursor pagination, filtering and caching · server-sent events with replay · versioning, deprecation and rate limits · OAuth 2.1, PKCE and RFC 9728 |
| VI · Contracts, Clients and Agents | The OpenAPI 3.2 contract · testing, including contract tests · a hypermedia client in Java · RPC or resources: a comparison with the Webull OpenAPI · an AI trading assistant with Spring AI and a human in the loop · the whole standard as a one-page checklist |
| Appendices | Quick reference · status codes and problem types · glossary · further reading · the complete source code |

## How the book is built

Two rules keep the book honest:

1. **No code is typed by hand.** Directives in the source pull excerpts from
   `brokerage-apis` at build time, selected by patterns rather than line numbers, and
   Appendix E prints every source file with line numbers. When the code changes, the book
   follows on the next build.
2. **No HTTP exchange is invented.** [`tools/capture.py`](book/tools/capture.py) starts the
   authorization server and the API from their jars, runs a scripted session (tokens, reads,
   the order lifecycle, errors, versions, the PKCE sign-in, server-sent events, and a second API
   instance with a tiny quota to show a 429), and saves each exchange to `captures/`.

The layout is a flowing HTML document rendered by headless Chrome's paged-media engine: `@page`
margins with running footers, one named page per chapter so each footer carries its chapter's
title, and full-bleed named pages for the cover and part openers. Page numbers in the contents
and in the Appendix E file index are found rather than typed: a first render plants invisible
markers, `pdftotext` reports where they landed, and a second render prints the numbers.

## Build the book

```bash
cd book
./build-book.sh            # src/*.html -> rest-api-hateoas.html -> rest-api-hateoas.pdf
./build-book.sh --html     # assemble the HTML only, for reviewing in a browser
./build-book.sh --assets   # also export cover.png and thumbnail.png
```

The build needs Chrome or Chromium (set `CHROME_BIN` if it is not in a standard location),
Python 3, and poppler's `pdftotext`, `pdfinfo` and `pdftoppm`. It stops with a warning if an
excerpt pattern no longer matches its source file, or if a source line is too wide for the page.

## Record the HTTP exchanges again

After changing the sample application, rebuild its jars and re-record:

```bash
cd ../../brokerage-apis && ./mvnw -q -DskipTests package && cd -
cd book
JAVA_HOME=/path/to/jdk-27 python3 tools/capture.py
./build-book.sh
```

Ports 8080, 8081 and 9000 must be free. Prices and timestamps differ from run to run, because
the sample market is simulated; the shapes of the exchanges do not.
