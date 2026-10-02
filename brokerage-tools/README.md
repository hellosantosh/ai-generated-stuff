# Brokerage Tools

The reference implementation for the book **[Tool Specifications and Design for the Brokerage
Domain](../e-books/brokerage-tools/)** — fourteen model-invocable tools, the registry that
publishes them, the runtime that makes them safe to point a frontier model at, a Spring AI agent
over Claude, an Angular console, and an agentic test harness that runs offline.

Nothing here is a mock of a tool system. The descriptors are the files the model is shown, the
linter is the gate that fails the build, the approval gate is the one the agent actually hits, and
the evaluation suites replay through the real pipeline.

```bash
./mvnw verify                    # build and test everything: 149 tests, no network, no API key
./harness.sh                     # the agentic evaluation suites, offline
cd console && npm install && npm run build
export ANTHROPIC_API_KEY=sk-ant-...
./mvnw -pl agent-service spring-boot:run
open http://localhost:8080/
```

Requires JDK 25 and Node 22 or later. The API key is needed only for the chat page and for
`./harness.sh --live`; everything else works without one, which is deliberate — a tool catalog you
can only exercise through a language model is a catalog you cannot debug.

## The modules

| Module | What it is |
| --- | --- |
| [`tool-contract`](tool-contract/) | The descriptor, the ontology, the result envelope, the fifteen-code error taxonomy, the JSON Schema profile, and the conformance linter. No Spring, no domain, no framework. |
| [`brokerage-domain`](brokerage-domain/) | A self-contained brokerage: accounts, instruments, deterministic quotes, positions, tax lots, a risk engine, an order book and single-use confirmation tokens. |
| [`brokerage-catalog`](brokerage-catalog/) | The fourteen descriptors, the ontology, the conduct prompt, and the handler behind each tool. |
| [`tool-runtime`](tool-runtime/) | The registry, the entitlement-aware selection view, the ten-step invocation pipeline, the approval gate, the audit log, and the Spring AI binding. |
| [`agent-service`](agent-service/) | The agent over Claude, the HTTP surface the console talks to, and the console itself once built. |
| [`test-harness`](test-harness/) | Thirty scenarios in three suites, cassette replay, and the four scores. |
| [`desktop-bridge`](desktop-bridge/) | A local connector that presents the catalog to Claude Desktop, and prints the tools array for the Claude Developer Platform. |
| [`console/`](console/) | The Angular front end: catalog, ontology, agent and governance. |

## The fourteen tools

| Tool | Tier | Approval | What it does |
| --- | --- | --- | --- |
| `brokerage_accounts_list` | 0 | — | The entry point. The only tool that needs nothing to run. |
| `brokerage_instruments_search` | 0 | — | Resolves a company name to candidate tickers, and refuses to choose. |
| `brokerage_quotes_get` | 0 | — | Up to ten symbols in one call. |
| `brokerage_price_history_get` | 0 | — | Daily bars, with the period summary already computed. |
| `brokerage_balances_get` | 0 | — | Four different numbers, because a customer means a different one each time. |
| `brokerage_positions_list` | 0 | — | Holdings, largest first, with portfolio weight. |
| `brokerage_tax_lots_list` | 0 | — | Lots, holding periods and wash-sale exposure. |
| `brokerage_orders_list` | 0 | — | Working orders by default, which is the question that gets asked. |
| `brokerage_order_get` | 0 | — | One order, with every fill. |
| `brokerage_executions_list` | 0 | — | Fills and settlement dates. |
| `brokerage_order_preview` | 1 | — | Prices an order, changes nothing, and issues the only confirmation token there is. |
| `brokerage_order_place` | 3 | confirm | Sends a real order. Needs a token and a human. |
| `brokerage_order_replace` | 3 | confirm | Amends a working order in place, keeping its identifier. |
| `brokerage_order_cancel` | 2 | confirm | Requests a cancellation and reports what actually happened. |

## The five properties worth copying

**The descriptor is data, not an annotation.** There is no `@Tool` anywhere in this project. Each
tool is a JSON file that a linter checks, a reviewer reads in a pull request, a desktop client is
handed directly, and the Angular console displays verbatim. The entire Spring AI binding is one
class, [`DescriptorToolCallback`](tool-runtime/src/main/java/com/example/brokerage/tooling/runtime/DescriptorToolCallback.java),
and publishing to a different runtime means writing another one about that long.

**The ontology derives what would otherwise be chosen.** The
[ontology](brokerage-catalog/src/main/resources/ontology/brokerage.ontology.json) declares entities,
a closed verb vocabulary, and one capability row per tool. From it the build derives each tool's
name, its risk tier, its approval mode, its entitlement floor, and the *producer graph* — which
tool hands out each identifier. `python3`-free, mechanical, and checked on every commit by
`TS-ONT-*` rules in the linter.

**Preview-before-place is structural, not advisory.**
`brokerage_order_place` requires a `ConfirmationToken`. Exactly one capability in the catalog
produces one, and it is the read-only preview. The token carries a fingerprint of the exact ticket,
is bound to the session, is single-use and expires in two minutes. An agent cannot trade without
pricing the trade, cannot trade terms the customer did not see, and cannot double-book on a retry.

**The system prompt is generated.** The conduct half is
[a file beside the descriptors](brokerage-catalog/src/main/resources/prompts/conduct.md) that
compliance can read. The toolset half is generated from the ontology on every turn by
[`ToolsetBriefing`](tool-runtime/src/main/java/com/example/brokerage/tooling/runtime/ToolsetBriefing.java),
so nothing in it can disagree with the catalog. Per-tool advice is deliberately absent; that
belongs in the tool's own description, where it is reviewed and versioned with the tool.

**The evaluation suite runs offline.** Thirty scenarios across selection, trajectory and refusal.
Each one replays a recorded set of model choices *through the real pipeline* — real schemas, real
entitlements, real approval gate, real handlers — so narrowing a pattern, renaming an output field
or raising a risk tier still fails the suite. `./harness.sh --live` runs the same scenarios against
Claude when a description has changed.

## Checking conformance

```bash
./mvnw -q test -pl brokerage-catalog                 # the gate, as a test
./mvnw -q -pl brokerage-catalog exec:java \
  -Dexec.mainClass=com.example.brokerage.catalog.CatalogLint    # the same rules, as a command
```

Current state: **14 descriptors · 0 errors · 0 warnings · 14 capabilities**. The registry refuses
to start on any error, so a non-conformant descriptor cannot reach a model.

## The test harness

```bash
./harness.sh                     # every suite, offline, from the cassettes
./harness.sh --live              # every suite against Claude
./harness.sh --record            # against Claude, writing fresh cassettes
./harness.sh --suite trading     # one suite
```

Four scores, reported separately because they fail independently:

| Score | What it measures |
| --- | --- |
| **Selection accuracy** | Of the scenarios expecting a tool, how many got it. The one everybody measures. |
| **Refusal accuracy** | Of the scenarios where declining was correct, how many declined. The one that decides whether it ships. |
| **Argument accuracy** | The right tool with the wrong account is not a near miss. |
| **Tool calls per scenario** | The number that shows up on the invoice. |

The cassettes committed here are marked `AUTHORED` rather than `RECORDED`: they are the
trajectories the catalog's authors intend, written by hand, and they exercise the whole runtime.
They are **not** evidence about what Claude chose. Run `./harness.sh --record` with a key to
replace them with real recordings.

## Claude Desktop and the Claude Developer Platform

```bash
java -jar desktop-bridge/target/desktop-bridge-1.0.0.jar --manifest
```

prints the catalog as the `tools` array a Messages request takes — name, description and
`input_schema`, nothing translated and nothing renamed. Paste it into the workbench and Claude has
this catalog.

Without `--manifest` the same jar serves on standard input and output, one JSON object per line:
`list_tools`, `call_tool`, `pending_approvals`, `approve`, `deny`. Add `--trading` to publish the
four tools that change something; without it the connector is read-only. The transport is an
adapter concern and lives in one class,
[`BridgeLoop`](desktop-bridge/src/main/java/com/example/brokerage/bridge/BridgeLoop.java) — the
descriptors, the handlers, the ontology and the linter are untouched by it.

## Adapting it for your firm

Four substitutions. Replace `com.example.tooling/` with your reverse-DNS metadata namespace;
replace the entitlement scopes with your own; replace
[`brokerage-domain`](brokerage-domain/) with calls to your real systems; and rewrite the ontology
for your domain, keeping the shape. The linter, the pipeline, the harness and the console are
domain-neutral and need no changes at all.
