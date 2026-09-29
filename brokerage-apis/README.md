# Brokerage APIs

A hypermedia (HATEOAS) REST API for a retail brokerage, built with Spring Boot 4 on Java 27,
plus an OAuth 2.1 authorization server, a Java client that navigates the API by following
links, and an AI agent built with Spring AI that trades only with a person's confirmation.

This is the sample application of the e-book **REST API Standards with HATEOAS**
([`../e-books/rest-api-hateoas/`](../e-books/rest-api-hateoas/)), which explains every design
decision in it and prints all of its source in Appendix E.

## What this project delivers

| Requirement | How it is met |
| --- | --- |
| Java and Spring Boot, latest versions | Java 27 and Spring Boot 4.1.1 (Spring Framework 7.0, Spring Security 7.1, Spring HATEOAS 3.1, Jackson 3.1, JUnit 6) |
| Strict REST, including HATEOAS | HAL (`application/hal+json`) and HAL-FORMS (`application/prs.hal-forms+json`); one root URL, links that depend on state and scopes, templates for every state change |
| Brokerage domain | Accounts, balances, positions, transaction history, instruments, quotes, option chains, and stock and option orders (market, limit, stop, stop-limit) |
| API standards | RFC 9457 problem details, `Idempotency-Key`, ETags with `If-Match`/`If-None-Match`, JSON merge patch, `202 Accepted` cancels, cursor pagination, server-sent events with replay, header-based versioning with `Deprecation`/`Sunset`, `RateLimit` headers, request IDs |
| Authentication and authorization | OAuth 2.1: client credentials for scripts and agents; authorization code + PKCE for a web backend, with rotated refresh tokens; scopes, audience restriction, RFC 9728 protected resource metadata |
| OpenAPI | An OpenAPI **3.2.1** contract in [`openapi/brokerage-api.yaml`](openapi/brokerage-api.yaml), served by the API at `/openapi.yaml` and validated against real responses by the tests |
| Agentic AI | A Spring AI 2.0 agent using Claude (`claude-opus-5`) with tools over the hypermedia client, human-in-the-loop confirmation, and an API-enforced trading limit |
| Reference API | The design is compared, operation by operation, with the public Webull OpenAPI in Chapter 24 of the book |

## Modules

| Module | Port | What it is |
| --- | --- | --- |
| [`auth-server`](auth-server) | 9000 | OAuth 2.1 authorization server (Spring Security's authorization server), development only |
| [`brokerage-api`](brokerage-api) | 8080 | The REST API, with an in-memory ledger and a simulated market |
| [`brokerage-client`](brokerage-client) | | A hypermedia client library and a guided demo |
| [`brokerage-agent`](brokerage-agent) | | A console AI assistant (Spring AI) that uses the client as its tools |
| [`openapi`](openapi) | | The OpenAPI 3.2 contract, packaged into the API's jar |

## Prerequisites

- **JDK 27.** Check with `java -version`. Install it with your package manager (for example
  `brew install openjdk`) or a version manager such as SDKMAN.
- **Maven** is not required: the included Maven Wrapper (`./mvnw`, or `mvnw.cmd` on Windows)
  downloads Maven 3.9.16 on first use.
- **curl** (and optionally `jq`) to call the API by hand.
- **An Anthropic API key** only if you want to chat with the agent.

IDE support for a new Java release can lag behind the command-line build. If your editor
reports errors that `./mvnw verify` does not, update the IDE's Java tooling.

## Install and build

```bash
cd brokerage-apis
./mvnw verify                     # compile, run all 42 tests, package the jars
./mvnw -q -DskipTests package     # just the jars
```

## Run

Start the authorization server first, then the API, each in its own terminal:

```bash
java -jar auth-server/target/auth-server-1.0.0.jar       # http://localhost:9000
java -jar brokerage-api/target/brokerage-api-1.0.0.jar   # http://localhost:8080
```

The API seeds two demo customers with accounts, positions and a year of history, and runs a
market simulation that ticks every second. All state is in memory; restart to reset.

### Take the guided tour

With both servers running, the demo lists accounts, reads balances and positions, fetches a
quote, previews and places a limit order, then cancels it, all by following links:

```bash
./mvnw -q -pl brokerage-client compile exec:java -Dexec.mainClass=com.example.brokerage.client.Demo
```

### Call the API with curl

```bash
# A token for the customer's own script (client credentials)
TOKEN=$(curl -s -u brokerage-cli:cli-secret http://localhost:9000/oauth2/token \
  -d grant_type=client_credentials \
  -d scope="accounts:read market-data:read orders:read orders:write" | jq -r .access_token)

# The API root: the only URL a client needs to know (no token required)
curl -s http://localhost:8080/ | jq

# Follow the links: accounts, then one account's balances
curl -s -H "Authorization: Bearer $TOKEN" -H "API-Version: 2" http://localhost:8080/accounts | jq
curl -s -H "Authorization: Bearer $TOKEN" -H "API-Version: 2" \
  http://localhost:8080/accounts/ACC-1001/balances | jq

# Preview, then place an order. Every new order needs a unique Idempotency-Key.
ORDER='{"instrumentId":"EQ-MSFT","side":"BUY","type":"LIMIT","quantity":"10","limitPrice":"450.00"}'
curl -s -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d "$ORDER" http://localhost:8080/accounts/ACC-1001/order-previews | jq
curl -si -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -H "Idempotency-Key: $(uuidgen)" -d "$ORDER" http://localhost:8080/accounts/ACC-1001/orders

# Change it, sending the ETag you received, then cancel it. Take the order's URL and ETag from
# the Location and ETag headers above (on a fresh server: ORD-100002 and "ORD-100002-v1").
curl -si -X PATCH -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/merge-patch+json" \
  -H 'If-Match: "ORD-100002-v1"' -d '{"limitPrice":"455.00"}' \
  http://localhost:8080/accounts/ACC-1001/orders/ORD-100002
curl -si -X POST -H "Authorization: Bearer $TOKEN" \
  http://localhost:8080/accounts/ACC-1001/orders/ORD-100002/cancellation

# Watch order events as they happen (server-sent events)
curl -N -H "Authorization: Bearer $TOKEN" http://localhost:8080/accounts/ACC-1001/order-events
```

Ask for `Accept: application/prs.hal-forms+json` to receive HAL-FORMS templates describing
the actions available on a resource.

### Run the AI agent

```bash
export ANTHROPIC_API_KEY=...
java -jar brokerage-agent/target/brokerage-agent-1.0.0.jar
```

Ask about balances, positions and quotes, or ask for a trade. The agent proposes orders with a
confirmation code; nothing is placed until you type `/confirm CODE`. Other commands:
`/discard CODE`, `/status` (checks the API and the agent's token without calling the model), and
`/quit`. Without an API key the console still starts, and `/status` works.

The agent uses the model `claude-opus-5`. Its `RefusalFallbacks` component opts every Messages
API request into Anthropic's server-side refusal fallbacks (a beta feature): if a safety
classifier declines a request, the API re-runs it on Anthropic's recommended fallback model
instead of returning a refusal. Delete that component to opt out.

## Clients and demo data

| Client | Secret | Grant | Notes |
| --- | --- | --- | --- |
| `brokerage-cli` | `cli-secret` | client_credentials | 15-minute tokens for customer `cust-1001` |
| `brokerage-agent` | `agent-secret` | client_credentials | 5-minute tokens for `cust-1001`, `trading_limit` 5000 USD |
| `brokerage-web` | `web-secret` | authorization_code + PKCE, refresh_token | A web backend (BFF); user `alice` / `wonderland`; redirect `http://127.0.0.1:3000/callback` |

Scopes: `accounts:read`, `market-data:read`, `orders:read`, `orders:write` (and `openid` for
`brokerage-web`). Accounts `ACC-1001` (margin) and `ACC-1002` (Roth IRA) belong to `cust-1001`;
`ACC-2001` belongs to another customer and is invisible to these tokens. These credentials are
for local development only.

## Endpoints

| Method | Path | Scope |
| --- | --- | --- |
| GET | `/`, `/openapi.yaml`, `/.well-known/oauth-protected-resource` | none |
| GET | `/accounts`, `/accounts/{id}`, `…/balances`, `…/positions[/{instrumentId}]`, `…/transactions` | `accounts:read` |
| GET | `/accounts/{id}/orders`, `…/orders/{orderId}`, `…/order-events` | `orders:read` |
| POST | `/accounts/{id}/order-previews` | `orders:read` |
| POST | `/accounts/{id}/orders` (requires `Idempotency-Key`) | `orders:write` |
| PATCH | `/accounts/{id}/orders/{orderId}` (requires `If-Match`) | `orders:write` |
| POST | `/accounts/{id}/orders/{orderId}/cancellation` | `orders:write` |
| GET | `/instruments`, `/instruments/{id}`, `…/quote`, `…/option-chain` | `market-data:read` |

Clients should reach all of these by following links from `/`; the paths are for reference.

## Tests

`./mvnw verify` runs 42 tests: hypermedia and security (`HypermediaTest`), the order lifecycle
(`OrderLifecycleTest`), protocol details such as cursors, versions and caching (`ProtocolTest`),
rate limiting (`RateLimitTest`), contract tests that validate the OpenAPI document and real
responses against it (`ContractTest`), the hypermedia client (`BrokerageClientTest`), and the
agent's tools and guardrails (`BrokerageToolsTest`, `GuardrailsTest`). No test needs a network
connection or an API key.

## Configuration

| Property | Default | Meaning |
| --- | --- | --- |
| `server.port` | 8080 / 9000 | Ports of the API / authorization server |
| `brokerage.simulator.enabled` | `true` | Moves prices and matches orders every tick |
| `brokerage.simulator.tick-millis` | `1000` | Tick interval |
| `brokerage.rate-limit.requests-per-minute` | `120` | Default quota per client |
| `brokerage.rate-limit.order-changes-per-minute` | `30` | Quota for placing, changing and canceling orders |
| `brokerage.api-root` (agent) | `http://localhost:8080/` | Where the agent starts navigating |

Pass any of them on the command line, for example
`java -jar brokerage-api/target/brokerage-api-1.0.0.jar --brokerage.rate-limit.requests-per-minute=10`.

## Troubleshooting

| Symptom | Fix |
| --- | --- |
| `UnsupportedClassVersionError` (class file version 71) | Run with JDK 27 |
| `Port 8080 was already in use` | Stop the other process, or pass `--server.port=8081` |
| Every call returns 401 | The token expired, or the authorization server restarted (its signing key is generated at startup): get a new token |
| A fresh token still gets 401 | The authorization server must be running on port 9000: the API fetches its keys from there |

## History

This folder previously held a documentation-only version (a PDF, a developer portal, and
examples in Java, Node and Python). It was replaced by this Spring Boot implementation; the
earlier version remains in the repository history, for example at commit `da800b3`.
[`specs.md`](specs.md) is the original brief for that earlier version.
