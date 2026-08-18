# Brokerage REST API — code examples

Runnable examples in Python, Node.js, and Java. The brokerage publishes no SDK, so
these build directly on each language's standard HTTP client. That is deliberate: it
keeps the wire protocol visible, and nothing here goes stale when a vendor library does.

Each language implements the same four files:

| File | Contents |
| --- | --- |
| PKCE auth | OAuth 2.1 authorization code flow, token store, refresh with rotation, revocation |
| Client | Auth, retry with jitter, idempotency, `_links` following, cursor pagination |
| Accounts examples | Account list, balances, positions, order status, transaction history |
| Trading examples | Stock and option orders, preview, amend, cancel |

## Safe defaults

All three clients default to the **sandbox** base URL, and the trading examples default
to `dryRun = true` — they preview orders without routing them. Reaching production, and
actually placing an order, each require a deliberate edit. Keep it that way while you are
learning the API.

## Setup

```bash
export BROKERAGE_CLIENT_ID="your-client-id"
export BROKERAGE_REDIRECT_URI="http://127.0.0.1:8723/callback"
# optional; defaults to the public sandbox authorization server
export BROKERAGE_AUTH_BASE="https://auth.brokerage.example.com"
```

The redirect URI must **exactly** match one registered for your client — OAuth 2.1
requires string equality, with no wildcards or prefix matching.

The first run opens a browser for authorization and writes `.brokerage-tokens.json`
(mode `0600`) in the working directory. Subsequent runs reuse and refresh it.

## Python

Standard library only. Requires Python 3.9+.

```bash
cd python
python3 pkce_auth.py           # one-time interactive authorization
python3 accounts_examples.py
python3 trading_examples.py    # dry run by default
```

## Node.js

Built-ins only — global `fetch`, `node:crypto`, `node:http`. Requires Node 18+.

```bash
cd node
node pkce-auth.mjs             # one-time interactive authorization
node accounts-examples.mjs
node trading-examples.mjs      # dry run by default
```

`brokerage-client.mjs` also includes a complete SSE consumer (`streamOrderEvents`) that
reconnects, resumes from `Last-Event-ID`, and reassembles multi-line `data:` frames.

## Java

Requires Java 17+. Jackson is the only third-party dependency.

```bash
cd java
mvn -q compile

mvn -q exec:java -Dexec.mainClass=com.example.brokerage.PkceAuth
mvn -q exec:java -Dexec.mainClass=com.example.brokerage.AccountsExamples
mvn -q exec:java -Dexec.mainClass=com.example.brokerage.TradingExamples
```

## What these examples are actually demonstrating

The code is commented where a choice is non-obvious. The recurring themes:

**Money is never a float.** Prices and quantities arrive as decimal strings and are parsed
into `Decimal`, `BigDecimal`, or exact `BigInt` arithmetic. `Decimal(0.1)` and
`BigDecimal.valueOf(0.1)` both route through a binary float and inherit its error before
the decimal type ever sees the value — only the string constructors are safe.

**One idempotency key per order intent.** Generated before the first attempt and reused on
every retry. A socket timeout on a market order is indistinguishable from a rejection, so
retrying without a key is how a customer ends up with two positions.

**Follow links, do not build URLs.** The clients navigate by `_links` relations, and branch
on whether a link is *present* rather than on a status string. A `FILLED` order simply has
no `cancel` link.

**`If-Match` on every order mutation.** Closes the race where an order fills while you are
deciding to reprice it. On `412` the examples re-read and re-decide rather than blindly
retrying with the newer `ETag`.

**Refresh tokens rotate.** The new token is persisted atomically *before* the new access
token is used, so a crash between the two leaves you holding the one that still works.

## Related

- Full specification: [`../developer-specs.pdf`](../developer-specs.pdf)
- Developer portal: [`../dev-portal/index.html`](../dev-portal/index.html)
- OpenAPI 3.2.0: [`../openapi/brokerage-api.yaml`](../openapi/brokerage-api.yaml)
