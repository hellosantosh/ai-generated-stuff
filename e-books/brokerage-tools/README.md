# Tool Specifications and Design for the Brokerage Domain

A 398-page book on designing and building model-invocable tools, with a complete working
implementation: fourteen conformant descriptors, a domain ontology, a registry, a ten-step
invocation pipeline, a Spring AI agent over Claude, an Angular console, a desktop connector and an
agentic test harness that runs offline.

| Artifact | Path | Audience |
| --- | --- | --- |
| **The book (PDF)** | [`book/brokerage-tools.pdf`](book/brokerage-tools.pdf) | Anyone building or reviewing tools for a frontier model — **start here** |
| Assembled HTML | [`book/brokerage-tools.html`](book/brokerage-tools.html) | Intranet copy; a build byproduct, reviewable in a diff |
| **The code** | [`../../brokerage-tools/`](../../brokerage-tools/) | `./mvnw verify` — 149 tests, no API key, no network |
| Cover and thumbnail | `book/cover.png`, `book/thumbnail.png` | Store listings |
| **Unbranded copy** | [`book/brokerage-tools-unbranded.pdf`](book/brokerage-tools-unbranded.pdf) | The same book with no imprint and no copyright page — 397 pages |

## What it covers

| Part | Contents |
| --- | --- |
| **I — Foundations** | Why a tool is not an endpoint, the five components, where the trust boundary runs, conformance levels L0–L3 |
| **II — The descriptor** | The eight fields · the naming grammar and a closed verb vocabulary · the seven-part description · the JSON Schema profile and what it bans · the result envelope and a fifteen-code taxonomy · annotations, risk tiers and approval · governance metadata |
| **III — The ontology** | Why a catalog needs one · entities, operations and capabilities · the producer graph · nine rules that turn a design document into a build step |
| **IV — Building the catalog** | One tool end to end in nine steps · nine more reads and the ideas behind them · preview and place · replace and cancel · the rules a schema cannot express |
| **V — The registry and runtime** | The registry and the selection view · the ten-step pipeline, with the order argued for · approval, rate limits, kill switches · the audit record · publishing to Spring AI in one class |
| **VI — The agent** | What Claude is reading when it chooses · a generated system prompt · five conversations tool by tool · the Angular console · Claude Desktop and the Claude Developer Platform |
| **VII — Testing** | The five-layer pyramid · conformance and contract tests · the agentic harness · cassettes and offline evaluation · four scores and what to gate on |
| **VIII — Governance** | The review gate · versioning and deprecation · six incidents and the control that would have caught each · a first-year adoption plan |
| **Appendices** | A the fourteen tools · B the error registry · C the rule index · D the one-page review checklist · E glossary · **F the complete source, 130 files and 14,436 lines** |

## The five ideas

**The descriptor is data, not an annotation.** There is no `@Tool` anywhere in the companion
repository. Each tool is a JSON file that a linter checks, a reviewer reads in a pull request, a
desktop client is handed directly and the console displays verbatim. The whole Spring AI binding is
one class.

**The ontology derives what would otherwise be chosen.** One capability row per tool, and from it
the build derives the name, the risk tier, the approval mode, the entitlement floor and the
*producer graph* — which tool hands out each identifier. Nine linter rules hold the catalog to it.

**Preview-before-place is structural.** `brokerage_order_place` requires a `ConfirmationToken`, and
exactly one capability produces one: the read-only preview. The agent cannot trade without pricing
the trade, cannot trade terms the customer did not see, and cannot double-book on a retry.

**The system prompt is generated.** The conduct half is a file compliance can read; the toolset
half is written from the ontology on every turn, so nothing in it can disagree with the catalog.

**The evaluation suite runs offline.** Thirty scenarios across selection, trajectory and refusal,
replaying recorded model choices *through the real pipeline* — real schemas, real entitlements,
real approval gate, real handlers.

## Nothing in the book is retyped

Every code figure is an excerpt pulled from the repository at build time. Every figure that shows
output — an envelope, an error, the confirmation dialog, the generated prompt, the conformance
report, a harness run, the producer graph — is recorded from the running system by
`tools/capture.py`. A figure that disagrees with the code is a build failure.

## Building it

```bash
cd book
./build-book.sh              # expand directives, paginate, render
./build-book.sh --html       # assemble the HTML only, for a browser
./build-book.sh --capture    # re-record every figure from the running system first
./build-book.sh --assets     # also export cover.png and thumbnail.png

tools/build-unbranded.py --assets    # the unbranded copy, and its own cover and thumbnail
```

`build-unbranded.py` builds a copy of the book with no imprint, no byline and no copyright page.
It works by editing a scratch copy of `src/`, so the original fragments, PDF, HTML and store
assets are untouched — and it checksums all of them before and after to prove it rather than
assume it.

Needs Python 3, headless Chrome, and poppler (`pdftotext`, `pdftoppm`). `--capture` additionally
needs a JDK 27 and builds the companion repository; it needs no API key, because every captured
figure is model-free.

```
book/
├── brokerage-tools.pdf    398 pages          ← the book
├── brokerage-tools.html   assembled HTML     (build byproduct)
├── src/                   fragments, concatenated in filename order
├── captures/              39 figures recorded from the running system
└── tools/
    ├── build.py           src/ + captures/ → the PDF
    ├── capture.py         the companion repository → captures/
    ├── highlight.py       Java, TypeScript, JSON, YAML, XML, Markdown, shell
    ├── export-cover.py    page 1 of the PDF → cover.png
    └── export-thumbnail.py  the square store thumbnail
```

Page numbers in the contents are found, not typed: the book is rendered once to see where each
chapter landed, then rendered again with the numbers filled in. The build also checks that the
cover's ink reaches the paper's edge — if it does not, something on a later page is wider than the
column and Chrome has silently scaled the whole book.

## Scope

The book specifies a **contract**, not a wire protocol. A conformant descriptor is a JSON document;
publishing it to a particular tool-calling runtime is an adapter, and the two in the repository are
one class each. Nothing in the specification names a transport and nothing depends on one.

The brokerage is a simulation: deterministic market data, imaginary money, and no order that
reaches a venue. The domain rules it enforces — settled cash, margin eligibility, short-sale
permissions, pattern day trading, wash sales, T+1 settlement — are modeled closely enough to make
the design decisions real. Nothing in the book is regulatory guidance or investment advice.
