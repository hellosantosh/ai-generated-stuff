# Tool Specification Standard — EA-STD-TOOL-001

A normative enterprise standard for defining, describing, schema-ing, classifying, securing,
and governing **model-invocable tools**, with a complete reference catalogue in the brokerage
domain: accounts, positions, balances, and order status.

Written for an Architecture Review Board. Transport-neutral by design — it specifies a
contract, not a wire protocol, so a conformant descriptor publishes over any tool-calling
runtime through an adapter that renames nothing.

## Deliverables

| Artifact | Path | Audience |
| --- | --- | --- |
| **The standard (PDF)** | [`Enterprise-Tool-Specification-Standard.pdf`](Enterprise-Tool-Specification-Standard.pdf) | Architecture Review Board, tech leads, developers, risk and audit |
| Assembled single-page HTML | [`Enterprise-Tool-Specification-Standard.html`](Enterprise-Tool-Specification-Standard.html) | Intranet copy; build byproduct, reviewable in diffs |
| Normative descriptor schema | [`schemas/tool-descriptor.schema.json`](schemas/tool-descriptor.schema.json) | CI, tooling, codegen |
| Shared type library | [`schemas/common-types.schema.json`](schemas/common-types.schema.json) | Anyone writing a descriptor |
| Reference catalogue | [`catalog/`](catalog/) | Seven conformant brokerage descriptors |
| Conformance linter | [`tools/lint-descriptors.py`](tools/lint-descriptors.py) | CI gate; reports findings by rule identifier |

## Layout

```
tool-specification-standard/
├── Enterprise-Tool-Specification-Standard.pdf   158-page standard  ← primary deliverable
├── Enterprise-Tool-Specification-Standard.html  assembled HTML (build byproduct)
├── pdf-src/                 PDF source fragments, concatenated in filename order
├── schemas/
│   ├── tool-descriptor.schema.json   Appendix A — the shape of a descriptor
│   └── common-types.schema.json      Appendix B — Money, identifiers, completeness, errors
├── catalog/                 the Part IV reference catalogue, one descriptor per file
│   ├── brokerage_accounts_list.json
│   ├── brokerage_positions_list.json
│   ├── brokerage_balances_get.json
│   ├── brokerage_orders_list.json
│   ├── brokerage_order_get.json
│   ├── brokerage_order_preview.json
│   └── brokerage_order_place.json
└── tools/
    ├── build-pdf.sh         pdf-src/ → PDF (headless Chrome)
    ├── lint-descriptors.py  conformance rules, by rule identifier
    └── embed-catalog.py     expands catalogue markers in the fragments
```

## Regenerating

```bash
./tools/build-pdf.sh                  # lints the catalogue, then renders the PDF
CHROME=/path/to/chrome ./tools/build-pdf.sh    # if Chrome is not auto-detected
```

The build lints `catalog/` first and fails if any descriptor is non-conformant, then expands
`<!--CATALOG:name:section-->` and `<!--JSONFILE:path-->` markers in the fragments. **The JSON
printed in the book is by construction the JSON that passes the linter** — there is no
retyped copy to drift.

## Checking conformance

```bash
python3 tools/lint-descriptors.py catalog/        # a directory or a list of files
python3 tools/lint-descriptors.py catalog/brokerage_balances_get.json
```

Standard library only. Exit status 1 on any **MUST** violation; **SHOULD** findings are
warnings. Every finding names the rule identifier (`TS-INP-09`, `TS-ANN-04`, …) that the PDF
defines, which is what makes the Appendix E checklist mechanically enforceable rather than a
matter of taste.

Current state: **7 descriptors · 0 errors · 0 warnings.**

## What the standard covers

| Part | Contents |
| --- | --- |
| **I — Foundations** | Conformance levels L0–L3, what a tool is and why it is not an endpoint, the trust boundary, the five-component reference architecture |
| **II — The descriptor** | The eight fields · naming grammar and the operation vocabulary · the seven-part description template · the JSON Schema profile · the result envelope · behavioural annotations and risk tiers 0–3 · governance metadata · the fifteen-code error taxonomy |
| **III — Tool sets** | Granularity and catalogue design · payload economics and token budgets · confirmation, preview-then-execute, idempotency, long-running jobs · security and trust · observability and audit · versioning and deprecation |
| **IV — Brokerage catalogue** | Seven worked descriptors with schemas, results, errors, and the reasoning behind each design decision; a composition walkthrough; a twelve-entry anti-pattern catalogue |
| **V — Governance** | The review gate, testing and certification including agent evaluation suites, catalogue requirements, and a first-year adoption roadmap |
| **Appendices** | A normative schema · B type library · C error code registry · D reserved enumerations · **E the one-page review checklist** · F glossary |

## Design decisions

| Concern | Decision |
| --- | --- |
| Protocol | None named. The contract maps one-to-one onto current tool-calling interfaces; §17.4 covers runtime-specific constraints |
| Wire names | `^[a-z][a-z0-9_]{2,63}$`, `<domain>_<entity>_<operation>` — the intersection of what every runtime accepts. Dots are reserved for the catalogue identifier and never cross the wire |
| Operations | A closed verb vocabulary, so annotations and risk tier can be checked against the name |
| Money | Decimal **strings** plus ISO 4217 — never IEEE-754 floats |
| Pagination | Opaque forward cursors; offset and page numbers prohibited against a moving book |
| Errors | A fifteen-code closed taxonomy returned *inside* a successful response, with imperative remediation |
| Risk | Four tiers driving approval mode, evidence, and monitoring — never the self-asserted annotations |
| State change | Read-only `preview` issuing a confirmation token, plus a tier-3 `place` requiring it, in preference to any `dryRun` flag |

## Adapting it for your firm

Three substitutions and it is yours: replace `com.example.tooling/` with your reverse-DNS
metadata namespace, `schemas.example.com` with your schema host, and the entitlement scopes in
§10.4 with your own. The brokerage catalogue is a worked example — keep it as the reference
appendix, or swap in your own domain and keep the structure.
