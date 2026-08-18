# Brokerage REST API — developer documentation

Documentation for a REST API in the brokerage domain: accounts, positions, order status,
transaction history, and stock and options trading, over an OAuth 2.1 protected,
hypermedia-driven surface.

Built to the brief in [`specs.md`](specs.md).

## Deliverables

| Artifact | Path | Audience |
| --- | --- | --- |
| **Developer specification (PDF)** | [`developer-specs.pdf`](developer-specs.pdf) | Internal development team |
| **Developer portal (HTML)** | [`dev-portal/index.html`](dev-portal/index.html) | External developers |
| **OpenAPI 3.2.0 spec** | [`openapi/brokerage-api.yaml`](openapi/brokerage-api.yaml) | Tooling, codegen, contract tests |
| **Code examples** | [`examples/`](examples/) | Java, Node.js, Python |

## Layout

```
brokerage-apis/
├── specs.md                   the original brief
├── developer-specs.pdf        56-page specification  ← primary deliverable
├── developer-specs.html       assembled single-page HTML (build byproduct)
├── openapi/
│   ├── brokerage-api.yaml     source of truth, hand-maintained and commented
│   └── brokerage-api.json     generated JSON rendering
├── dev-portal/                static site, no CDN and no build step
│   ├── index.html             overview and quickstart
│   ├── authentication.html    OAuth 2.1 + PKCE
│   ├── accounts.html          accounts guide
│   ├── trading.html           trading guide
│   ├── examples.html          code examples, with language tabs
│   ├── reference.html         generated from the OpenAPI spec
│   ├── errors.html            error catalogue and retry guidance
│   ├── assets/                portal.css, portal.js
│   └── openapi/               spec copy served by the portal
├── examples/
│   ├── python/                standard library only, 3.9+
│   ├── node/                  built-ins only, 18+
│   └── java/                  Java 17+, Jackson only
├── pdf-src/                   PDF source fragments, concatenated in filename order
└── tools/
    ├── build-pdf.sh           pdf-src/ → developer-specs.pdf (headless Chrome)
    └── build-reference.py     openapi/ → dev-portal/reference.html
```

## Regenerating

```bash
# PDF — concatenates pdf-src/*.html in order and prints via headless Chrome.
# Override the browser with CHROME=/path/to/chrome if it is not auto-detected.
./tools/build-pdf.sh

# Portal endpoint reference — derived from the spec so it cannot drift.
python3 tools/build-reference.py

# JSON rendering of the spec.
python3 -c "import yaml,json; json.dump(yaml.safe_load(open('openapi/brokerage-api.yaml')), open('openapi/brokerage-api.json','w'), indent=2)"
```

## Validating the OpenAPI spec

The spec validates clean against the **official OpenAPI 3.2 meta-schema**, and every
embedded example validates against its own schema. Both checks are worth keeping in CI —
a spec can be structurally valid while carrying examples that contradict their schemas,
which is worse than no examples because integrators copy them.

```bash
curl -sL -o /tmp/oas32.json https://spec.openapis.org/oas/3.2/schema/2025-09-17
python3 - <<'PY'
import json, yaml
from jsonschema import Draft202012Validator
meta = json.load(open('/tmp/oas32.json'))
doc  = yaml.safe_load(open('openapi/brokerage-api.yaml'))
errors = list(Draft202012Validator(meta).iter_errors(doc))
print('VALID' if not errors else f'{len(errors)} error(s)')
for e in errors[:10]:
    print(' /' + '/'.join(map(str, e.absolute_path)), '::', e.message[:200])
PY
```

Current state: **17 paths · 21 operations · 48 schemas · 1 webhook**, meta-schema valid,
32/32 embedded examples valid.

## OpenAPI 3.2.0 features used

The brief asked for 3.2.0 specifically. Five additions earn their place, each because it
lets the description state something that previously needed a vendor extension or a prose
footnote:

- **`$self`** — stable document identity that also serves as the reference-resolution base.
- **Hierarchical tags** (`summary`, `parent`, `kind`) — the description carries its own
  navigation tree instead of delegating to `x-tagGroups`.
- **The `query` method** — HTTP QUERY for the structured instrument screener: safe and
  idempotent, but with a request body too large for a query string. `POST` would
  misrepresent the semantics.
- **`itemSchema`** — describes each item of the SSE order-event stream independently,
  which is what a streaming consumer actually processes.
- **`oauth2MetadataUrl`** and the **`deviceAuthorization`** flow — RFC 8414 discovery
  instead of hard-coded endpoints, and the device grant is declarable for the first time.

> **A 3.1/3.2 gotcha worth knowing:** `nullable: true` is an OpenAPI 3.0-ism and is **not**
> a JSON Schema 2020-12 keyword. Left in a 3.1+ document it is silently ignored, so a field
> you believe is nullable validates as non-nullable and generated clients throw on a
> legitimate `null`. The correct spelling is `type: [string, "null"]`, or
> `oneOf: [{$ref: …}, {type: "null"}]` behind a `$ref`. Worth grepping any 3.1+ document
> you inherit.

## Scope

Covers stock and listed equity options for a single authenticated customer's accounts.

Deliberately **excluded**, per the brief: the institutional application process, futures
trading, and crypto trading. A minimal *instruments* surface is included because option
orders reference contracts by `instrumentId` and would otherwise be unusable.

## Design decisions

| Concern | Decision |
| --- | --- |
| Style | Resource-oriented REST, JSON representations |
| Hypermedia | `_links` on every representation; clients bookmark only the API root |
| Authorization | OAuth 2.1 authorization code grant with mandatory PKCE (S256), optionally DPoP-bound |
| Errors | RFC 9457 `application/problem+json`, stable `type` URIs |
| Money | Decimal **strings** plus ISO 4217 currency — never IEEE-754 floats |
| Pagination | Opaque forward cursors surfaced as a `next` link |
| Safe retries | `Idempotency-Key` required on order placement |
| Concurrency | Strong `ETag` plus mandatory `If-Match` on order mutation |

Both reference APIs named in the brief informed the design. Webull authenticates with an
app key and HMAC request signing; Interactive Brokers uses a stateful session kept alive by
periodic `/tickle` calls plus OAuth 1.0a for third parties. This design uses OAuth 2.1 with
stateless bearer tokens instead — the brief called for it explicitly, and §2.1 of the PDF
makes the case: a scoped, expiring, revocable token lets a customer grant read-only access
to one application and full trading access to another, and take either back without anyone
rotating a shared secret.
