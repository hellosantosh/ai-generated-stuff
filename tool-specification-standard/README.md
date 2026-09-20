# Tool Specification Standard — EA-STD-TOOL-001

A normative enterprise standard for defining, describing, schema-ing, classifying, securing,
and governing **model-invocable tools**, with a complete reference catalog in the brokerage
domain: accounts, positions, balances, and order status.

Written for an Architecture Review Board. Transport-neutral by design — it specifies a
contract, not a wire protocol, so a conformant descriptor publishes over any tool-calling
runtime through an adapter that renames nothing.

Written in US English throughout. The one exception is the `CANCELLED` enumeration member,
which keeps its systems-of-record spelling; Appendix D explains why.

## Deliverables

| Artifact | Path | Audience |
| --- | --- | --- |
| **The standard (PDF)** | [`Enterprise-Tool-Specification-Standard.pdf`](Enterprise-Tool-Specification-Standard.pdf) | Architecture Review Board, tech leads, developers, risk and audit — **read this first** |
| **Quick reference (PDF)** | [`Tool-Specification-Standard-Quick-Reference.pdf`](Tool-Specification-Standard-Quick-Reference.pdf) | Anyone who has read the standard and is now writing or reviewing a descriptor |
| Assembled single-page HTML | [`Enterprise-Tool-Specification-Standard.html`](Enterprise-Tool-Specification-Standard.html) | Intranet copy; build byproduct, reviewable in diffs |
| Normative descriptor schema | [`schemas/tool-descriptor.schema.json`](schemas/tool-descriptor.schema.json) | CI, tooling, codegen |
| Shared type library | [`schemas/common-types.schema.json`](schemas/common-types.schema.json) | Anyone writing a descriptor |
| Reference catalog | [`catalog/`](catalog/) | Seven conformant brokerage descriptors |
| Invocation sequence diagram | [`diagrams/anatomy-of-an-invocation.puml`](diagrams/anatomy-of-an-invocation.puml) | §3.2 as a PlantUML sequence diagram, editable and previewable in VS Code |
| Conformance linter | [`tools/lint-descriptors.py`](tools/lint-descriptors.py) | CI gate; reports findings by rule identifier |

## Layout

```
tool-specification-standard/
├── Enterprise-Tool-Specification-Standard.pdf        160-page standard  ← primary deliverable
├── Tool-Specification-Standard-Quick-Reference.pdf    23-page reference card
├── *.html                   assembled HTML for each (build byproducts)
├── pdf-src/                 standard: source fragments, concatenated in filename order
├── quickref-src/            quick reference: same pipeline, denser stylesheet
├── diagrams/
│   ├── anatomy-of-an-invocation.puml   §3.2, the source of truth
│   ├── anatomy-of-an-invocation.svg    generated, for viewing without PlantUML
│   └── anatomy-of-an-invocation.png    generated, 144 dpi
├── schemas/
│   ├── tool-descriptor.schema.json   Appendix A — the shape of a descriptor
│   └── common-types.schema.json      Appendix B — Money, identifiers, completeness, errors
├── catalog/                 the Part IV reference catalog, one descriptor per file
│   ├── brokerage_accounts_list.json
│   ├── brokerage_positions_list.json
│   ├── brokerage_balances_get.json
│   ├── brokerage_orders_list.json
│   ├── brokerage_order_get.json
│   ├── brokerage_order_preview.json
│   └── brokerage_order_place.json
└── tools/
    ├── build-pdf.sh         pdf-src/ → the standard (headless Chrome)
    ├── build-quickref.sh    quickref-src/ → the quick reference
    ├── build-diagrams.sh    diagrams/*.puml → SVG and PNG
    ├── check-diagram-sync.py  fails if the diagram drifts from §3.2
    ├── lint-descriptors.py  conformance rules, by rule identifier
    ├── extract-rules.py     pdf-src/ → the quick reference's rule index
    └── embed-catalog.py     expands catalog, file, and tool-card markers
```

## Regenerating

```bash
./tools/build-pdf.sh                  # the standard
./tools/build-quickref.sh             # the quick reference
CHROME=/path/to/chrome ./tools/build-pdf.sh    # if Chrome is not auto-detected
```

Both builds lint `catalog/` first and fail if any descriptor is non-conformant, then expand
markers in the fragments:

| Marker | Expands to |
| --- | --- |
| `<!--CATALOG:name:section-->` | A descriptor, or one section of it, syntax-highlighted |
| `<!--JSONFILE:path-->` | Any JSON file in the repository |
| `<!--TOOLCARDS-->` | A summary row per catalog descriptor, read from the descriptors |
| `<!--RULEINDEX-->` | All 113 rules, extracted from `pdf-src/` |

**Nothing in either document is retyped.** The JSON printed in the book is the JSON that passes
the linter; the quick reference's rule index is generated from the book's own source, so a rule
added to the standard appears in the card without anyone remembering to copy it; the brokerage
tool card is read from the descriptors themselves.

## Diagrams

[`diagrams/anatomy-of-an-invocation.puml`](diagrams/anatomy-of-an-invocation.puml) draws §3.2 —
the eleven steps of a single invocation across the five components of the reference
architecture, with the trust boundary, the validation and entitlement branches, the tier-3
approval alternative, and the audit write. Steps marked ◆ are the ones where a descriptor
defect cannot be recovered downstream.

**Viewing in VS Code.** Install the **PlantUML** extension (`jebbs.plantuml`), open the
`.puml` file and press **Alt+D** (**Option+D** on macOS). Local rendering needs Java and
Graphviz:

```bash
brew install plantuml graphviz        # or: apt-get install plantuml graphviz
```

The extension falls back to a remote PlantUML server if it cannot render locally; for a
document describing internal architecture, install the local renderer instead. Markdown
Preview Enhanced also renders `.puml` files.

**Regenerating the exports:**

```bash
./tools/build-diagrams.sh             # checks §3.2 sync, then renders SVG and PNG
python3 tools/check-diagram-sync.py   # just the drift check
```

The drift check compares the diagram's eleven step dividers against the table in
`pdf-src/20-foundations.html` and fails if a step is renamed, added, or removed on either
side — the same "generated, never retyped" discipline as the rule index and the tool card.

## Checking conformance

```bash
python3 tools/lint-descriptors.py catalog/        # a directory or a list of files
python3 tools/lint-descriptors.py catalog/brokerage_balances_get.json
```

Standard library only. Exit status 1 on any **MUST** violation; **SHOULD** findings are
warnings. Every finding names the rule identifier (`TS-INP-09`, `TS-ANN-04`, …) that the PDF
defines, which is what makes the Appendix E checklist mechanically enforceable rather than a
matter of taste.

Current state: **7 descriptors · 0 errors · 0 warnings · 113 rules · 0 dangling citations.**

```bash
python3 tools/extract-rules.py --check    # every cited rule is also defined
python3 tools/extract-rules.py            # the rule index as plain text
```

## What the standard covers

| Part | Contents |
| --- | --- |
| **I — Foundations** | Conformance levels L0–L3, what a tool is and why it is not an endpoint, the trust boundary, the five-component reference architecture |
| **II — The descriptor** | The eight fields · naming grammar and the operation vocabulary · the seven-part description template · the JSON Schema profile · the result envelope · behavioral annotations and risk tiers 0–3 · governance metadata · the fifteen-code error taxonomy |
| **III — Tool sets** | Granularity and catalog design · payload economics and token budgets · confirmation, preview-then-execute, idempotency, long-running jobs · security and trust · observability and audit · versioning and deprecation |
| **IV — Brokerage catalog** | Seven worked descriptors with schemas, results, errors, and the reasoning behind each design decision; a composition walkthrough; a twelve-entry anti-pattern catalog |
| **V — Governance** | The review gate, testing and certification including agent evaluation suites, catalog requirements, and a first-year adoption roadmap |
| **Appendices** | A normative schema · B type library · C error code registry · D reserved enumerations · **E the one-page review checklist** · F glossary |

## Design decisions

| Concern | Decision |
| --- | --- |
| Protocol | None named. The contract maps one-to-one onto current tool-calling interfaces; §17.4 covers runtime-specific constraints |
| Wire names | `^[a-z][a-z0-9_]{2,63}$`, `<domain>_<entity>_<operation>` — the intersection of what every runtime accepts. Dots are reserved for the catalog identifier and never cross the wire |
| Operations | A closed verb vocabulary, so annotations and risk tier can be checked against the name |
| Money | Decimal **strings** plus ISO 4217 — never IEEE-754 floats |
| Pagination | Opaque forward cursors; offset and page numbers prohibited against a moving book |
| Errors | A fifteen-code closed taxonomy returned *inside* a successful response, with imperative remediation |
| Risk | Four tiers driving approval mode, evidence, and monitoring — never the self-asserted annotations |
| State change | Read-only `preview` issuing a confirmation token, plus a tier-3 `place` requiring it, in preference to any `dryRun` flag |

## The quick reference

Twenty-three pages, meant to be kept open while working rather than read start to finish:

| Section | Contents |
| --- | --- |
| 1–3 | The descriptor skeleton annotated with rules · naming grammar and the operation verb vocabulary · the seven-part description template and its budgets |
| 4–5 | Permitted and prohibited schema keywords · reserved parameter names · prohibited input patterns · the result envelope · number, empty, partial, and truncation rules |
| 6–7 | Annotations, risk tiers, approval modes, what a confirmation must show · the governance block |
| 8–9 | The fifteen error codes with retry and next action · upstream mapping · the decisions you make repeatedly (one tool or two, idempotency, long-running, polling) |
| 10–12 | Security non-negotiables · the audit record · metrics · breaking vs compatible change · the brokerage catalog card and domain conventions · thirteen anti-patterns |
| **13** | **The complete index of all 113 rules**, with level and section |
| 14 | The review checklist, and six questions to ask out loud |

## Adapting it for your firm

Three substitutions and it is yours: replace `com.example.tooling/` with your reverse-DNS
metadata namespace, `schemas.example.com` with your schema host, and the entitlement scopes in
§10.4 with your own. The brokerage catalog is a worked example — keep it as the reference
appendix, or swap in your own domain and keep the structure.
