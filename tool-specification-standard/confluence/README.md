# Confluence page set — EA-STD-TOOL-001

**Generated. Do not edit these files, and do not edit the pages in Confluence.**
Everything here is produced from the standard's sources by
`tools/build-confluence.py` and overwritten on the next import. A rule that
exists only in Confluence is not a rule anyone's CI enforces (TS-GEN-03).

46 pages · 19 attachments · Confluence storage format.

## Importing

### 1. The importer (recommended)

```bash
export CONFLUENCE_BASE_URL=https://example.atlassian.net/wiki   # DC: https://wiki.example.com
export CONFLUENCE_SPACE=EATOOLS
export CONFLUENCE_USER=you@example.com     # Cloud: your email
export CONFLUENCE_TOKEN=...                # Cloud: an API token · DC: a PAT, omit USER

python3 tools/import-confluence.py         # dry run, prints the plan
python3 tools/import-confluence.py --apply
```

Creates what is missing, updates what exists (matched on title, version bumped),
uploads each page's attachments. Safe to re-run. Nothing is ever deleted; pages
in the space that are no longer in the manifest are reported, not removed.

### 2. Paste one page at a time

For a space where API tokens are not available. In Confluence Cloud open the
page editor, then **⋯ → Advanced → Source editor**; in Data Center, **⋯ → Source
editor**. Paste the contents of the matching file in `pages/`. Create the page
tree by hand first, following the order below, and attach the files listed in
`manifest.json` for that page.

### 3. Land the PDFs only

If the space is meant purely as a distribution point, create one page, attach
`Enterprise-Tool-Specification-Standard.pdf` and
`Tool-Specification-Standard-Quick-Reference.pdf`, and skip the rest. You lose
search, linking, and the tickable review checklist.

## Space setup

| Setting | Recommendation |
| --- | --- |
| Space key | `EATOOLS`, or your architecture space with these pages under one parent |
| Read access | All engineering. This is a standard; hiding it defeats it |
| Edit access | **The platform team only.** Pages are generated; a wiki edit is lost on the next import |
| Labels | `ea-standard`, `tooling`, `agents` on every page, for cross-space search |
| Space home | Point it at *Tool Specification Standard* |

## After any change to the standard

```bash
./tools/build-pdf.sh && ./tools/build-quickref.sh   # the PDFs
./tools/build-diagrams.sh                           # the sequence diagram
python3 tools/build-confluence.py                   # this page set
python3 tools/check-confluence.py                   # validate before importing
python3 tools/import-confluence.py --apply          # publish
```

`check-confluence.py` is the gate: it parses every page as XML in the storage
namespaces, fails on print-only markup that survived conversion, on named HTML
entities (which break Confluence's parser), and on links or attachments that do
not resolve.

## Macros used

All built-in — no marketplace add-ons, so this imports into a vanilla space:
`info`, `note`, `warning`, `tip`, `panel`, `code`, `status`, `excerpt`,
`children`, `attachments`, and native task lists for the review checklist.

## Page tree

```
- Tool Specification Standard
  - How to use this standard
  - 0. Document control
  - Part I — Foundations
    - 1. Scope, audience, and conformance
    - 2. What a tool is
    - 3. Reference architecture
  - Part II — The tool descriptor
    - 4. Descriptor anatomy
    - 5. Identity and naming
    - 6. The description: the model-facing contract
    - 7. Input schema
    - 8. Output schema and the result envelope
    - 9. Behavioral annotations and risk tiers
    - 10. Governance metadata
    - 11. Errors
  - Part III — Designing a tool set
    - 12. Granularity and catalog design
    - 13. Payload economics
    - 14. Interaction patterns
    - 15. Security and trust
    - 16. Observability, audit, and supervision
    - 17. Versioning and lifecycle
  - Part IV — Brokerage reference catalog
    - 18. Reference domain and shared types
    - 19. Accounts
    - 20. Positions
    - 21. Balances
    - 22. Order status
    - 23. A state-changing tool, for contrast
    - 24. Composition: five questions, one catalog
    - 25. The anti-pattern catalog
  - Part V — Governance and adoption
    - 26. Conformance and review
    - 27. Testing and certification
    - 28. The catalog and the onboarding process
    - 29. Adoption roadmap
  - Appendices
    - A. Normative descriptor schema
    - B. Shared type library
    - C. Error code registry
    - D. Reserved enumerations
  - Review checklist
    - F. Glossary
  - Rule index
  - Machine-readable artifacts
```

## Attachments

- `Enterprise-Tool-Specification-Standard.pdf`
- `Tool-Specification-Standard-Quick-Reference.pdf`
- `anatomy-of-an-invocation.png`
- `anatomy-of-an-invocation.puml`
- `anatomy-of-an-invocation.svg`
- `brokerage_accounts_list.json`
- `brokerage_balances_get.json`
- `brokerage_order_get.json`
- `brokerage_order_place.json`
- `brokerage_order_preview.json`
- `brokerage_orders_list.json`
- `brokerage_positions_list.json`
- `common-types.schema.json`
- `figure-2-1.png`
- `figure-2-1.svg`
- `figure-22-1.png`
- `figure-22-1.svg`
- `lint-descriptors.py`
- `tool-descriptor.schema.json`
