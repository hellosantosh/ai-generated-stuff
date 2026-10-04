#!/usr/bin/env python3
"""
Record every piece of output the book prints, from the real system.

Nothing in this book that claims to be output is typed by hand. A tool envelope, the generated
system briefing, the conformance report, the desktop manifest and the harness run are all produced
here, written into captures/, and pulled into the text at build time by a data-file directive. A
figure that disagrees with the code is therefore a build failure rather than a reader's discovery.

    tools/capture.py            build the project, then record everything

It needs a JDK 27 on JAVA_HOME or at the usual Homebrew location. It needs no API key and no
network: every capture is model-free, which is the same property the project itself is built on.
"""
import json
import os
import pathlib
import shutil
import subprocess
import sys

BOOK = pathlib.Path(__file__).resolve().parent.parent
CAPTURES = BOOK / "captures"
CODE = BOOK.parent.parent.parent / "brokerage-tools"

MARGIN = "acct_4RZ61FZAD0RMHNMM"
CASH = "acct_2GDR417WM0WF031F"
ROTH = "acct_TF7KNH2A136X695X"
FROZEN = "acct_V5WR2E9BFKVE7J8F"
MSFT_ORDER = "ord_CZ9R91DVFPMN5BX0"
NVDA_ORDER = "ord_M07KB16SEA01HW70"

READ_SCOPES = ["brokerage:reference:read", "brokerage:market-data:read",
               "brokerage:accounts:read", "brokerage:balances:read", "brokerage:positions:read",
               "brokerage:orders:read"]
TRADE_SCOPES = READ_SCOPES + ["brokerage:orders:write"]

# name -> (role, tool, arguments). Each becomes captures/<name>.json, an envelope exactly as the
# model would receive it.
CALLS = {
    "accounts-list": ("read", "brokerage_accounts_list", {}),
    "balances-margin": ("read", "brokerage_balances_get", {"accountId": MARGIN}),
    "balances-cash": ("read", "brokerage_balances_get", {"accountId": CASH}),
    "positions": ("read", "brokerage_positions_list", {"accountId": MARGIN}),
    "quotes": ("read", "brokerage_quotes_get", {"symbols": ["AAPL", "MSFT", "NVDA", "BRK.B"]}),
    "quotes-unresolved": ("read", "brokerage_quotes_get", {"symbols": ["AAPL", "ZZZZ"]}),
    "search-apple": ("read", "brokerage_instruments_search", {"query": "Apple"}),
    "search-exact": ("read", "brokerage_instruments_search", {"query": "MSFT"}),
    "history": ("read", "brokerage_price_history_get", {"symbol": "NVDA", "days": 30}),
    "tax-lots": ("read", "brokerage_tax_lots_list", {"accountId": MARGIN, "symbol": "AAPL"}),
    "tax-lots-roth": ("read", "brokerage_tax_lots_list", {"accountId": ROTH, "symbol": "VOO"}),
    "orders-working": ("read", "brokerage_orders_list", {"accountId": MARGIN}),
    "order-get": ("read", "brokerage_order_get", {"accountId": MARGIN, "orderId": NVDA_ORDER}),
    "executions": ("read", "brokerage_executions_list", {"accountId": MARGIN}),
    "error-not-found": ("read", "brokerage_balances_get",
                        {"accountId": "acct_ZZZZZZZZZZZZZZZZ"}),
    "error-bad-argument": ("read", "brokerage_quotes_get", {"symbols": ["apple inc"]}),
    "error-unknown-tool": ("read", "brokerage_order_place", {}),
    "error-cursor": ("read", "brokerage_orders_list", {"accountId": MARGIN, "cursor": "page2"}),
    "preview-ok": ("trade", "brokerage_order_preview",
                   {"accountId": MARGIN, "symbol": "AAPL", "side": "BUY", "quantity": 50,
                    "orderType": "MARKET"}),
    "preview-blocked": ("trade", "brokerage_order_preview",
                        {"accountId": CASH, "symbol": "MSFT", "side": "BUY", "quantity": 4000,
                         "orderType": "MARKET"}),
    "preview-oversell": ("trade", "brokerage_order_preview",
                         {"accountId": MARGIN, "symbol": "MSFT", "side": "SELL", "quantity": 500,
                          "orderType": "MARKET"}),
    "preview-short-ira": ("trade", "brokerage_order_preview",
                          {"accountId": ROTH, "symbol": "TSLA", "side": "SELL_SHORT",
                           "quantity": 100, "orderType": "MARKET"}),
    "preview-frozen": ("trade", "brokerage_order_preview",
                       {"accountId": FROZEN, "symbol": "KO", "side": "BUY", "quantity": 10,
                        "orderType": "MARKET"}),
    "preview-limit-no-price": ("trade", "brokerage_order_preview",
                               {"accountId": MARGIN, "symbol": "MSFT", "side": "BUY",
                                "quantity": 25, "orderType": "LIMIT"}),
    "cancel-filled": ("trade+approve", "brokerage_order_cancel",
                      {"accountId": MARGIN, "orderId": NVDA_ORDER}),
    "cancel-working": ("trade+approve", "brokerage_order_cancel",
                       {"accountId": MARGIN, "orderId": MSFT_ORDER}),
}

RECORDER = r"""
import com.example.brokerage.harness.HarnessContext;
import com.example.brokerage.tooling.contract.DescriptorCodec;
import com.example.brokerage.tooling.contract.Principal;
import com.example.brokerage.tooling.runtime.InvocationPipeline;
import com.example.brokerage.tooling.runtime.ToolsetBriefing;
import java.nio.file.*;
import java.util.*;

/** Written by tools/capture.py, run once, thrown away. */
public class Capture {

    public static void main(String[] args) throws Exception {
        Path out = Path.of(args[0]);
        Map<String, Object> plan = DescriptorCodec.mapper().readValue(Files.readString(
                Path.of(args[1])), Map.class);

        for (var entry : ((Map<String, List<Object>>) plan.get("calls")).entrySet()) {
            List<Object> call = entry.getValue();
            String role = (String) call.get(0);
            HarnessContext context = new HarnessContext();     // a fresh world per capture
            Principal principal = principal(context, role);
            InvocationPipeline pipeline = context.publisher().pipeline();
            if (role.endsWith("+approve")) {
                context.autoApprovingGate().standInForTheHuman("book");
            }
            String json = pipeline.invoke((String) call.get(1),
                    DescriptorCodec.json(call.get(2)), principal).json();
            Files.writeString(out.resolve(entry.getKey() + ".json"), pretty(json) + "\n");
        }

        HarnessContext context = new HarnessContext();
        Files.writeString(out.resolve("briefing-trading.md"),
                ToolsetBriefing.forPrincipal(context.registry(), principal(context, "trade")));
        Files.writeString(out.resolve("briefing-research.md"),
                ToolsetBriefing.forPrincipal(context.registry(), principal(context, "research")));
        Files.writeString(out.resolve("conduct.md"), context.catalog().conduct());

        // The preview, then the place that spends its token: the only capture that needs two calls.
        Principal trader = principal(context, "trade+approve");
        context.autoApprovingGate().standInForTheHuman("book");
        InvocationPipeline pipeline = context.publisher().pipeline();
        String preview = pipeline.invoke("brokerage_order_preview",
                ticket(args[2], null), trader).json();
        String token = DescriptorCodec.tree(preview).path("data").path("confirmationToken")
                .asString();
        String placed = pipeline.invoke("brokerage_order_place",
                ticket(args[2], token), trader).json();
        Files.writeString(out.resolve("place-filled.json"), pretty(placed) + "\n");
        String again = pipeline.invoke("brokerage_order_place",
                ticket(args[2], token), trader).json();
        Files.writeString(out.resolve("place-duplicate.json"), pretty(again) + "\n");

        // The approval dialog, as the customer is shown it.
        HarnessContext gated = new HarnessContext();
        Principal waiting = principal(gated, "trade");
        String p2 = gated.publisher().pipeline().invoke("brokerage_order_preview",
                ticket(args[2], null), waiting).json();
        String token2 = DescriptorCodec.tree(p2).path("data").path("confirmationToken").asString();
        var outcome = gated.publisher().pipeline().invoke("brokerage_order_place",
                ticket(args[2], token2), waiting);
        Files.writeString(out.resolve("approval-required.json"), pretty(outcome.json()) + "\n");
        Files.writeString(out.resolve("approval-dialog.txt"),
                gated.autoApprovingGate().peek(outcome.approvalReference()).orElseThrow()
                        .summary() + "\n");

        // The audit trail those two calls left behind.
        StringBuilder audit = new StringBuilder();
        audit.append(String.format("%-26s %-26s %-5s %-18s %-10s %s%n",
                "requestId", "tool", "tier", "outcome", "retention", "argument fields"));
        for (var record : gated.audit().forSession(waiting.sessionId())) {
            audit.append(String.format("%-26s %-26s %-5s %-18s %-10s %s%n", record.requestId(),
                    record.tool(), "T" + record.riskTier(), record.outcome(), record.retention(),
                    String.join(", ", record.argumentFields())));
        }
        Files.writeString(out.resolve("audit-trail.txt"), audit.toString());
    }

    /** The one ticket this book places, as JSON, with or without the token that authorizes it. */
    static String ticket(String accountId, String token) {
        String json = "{\"accountId\":\"" + accountId + "\",\"symbol\":\"AAPL\","
                + "\"side\":\"BUY\",\"quantity\":50,\"orderType\":\"MARKET\"";
        return token == null ? json + "}" : json + ",\"confirmationToken\":\"" + token + "\"}";
    }

    static Principal principal(HarnessContext context, String role) {
        Set<String> scopes = new LinkedHashSet<>(List.of("brokerage:reference:read",
                "brokerage:market-data:read", "brokerage:accounts:read", "brokerage:balances:read",
                "brokerage:positions:read", "brokerage:orders:read"));
        if (role.startsWith("trade")) {
            scopes.add("brokerage:orders:write");
        }
        if (role.equals("research")) {
            scopes = new LinkedHashSet<>(List.of("brokerage:reference:read",
                    "brokerage:market-data:read"));
        }
        return new Principal("book:capture", com.example.brokerage.domain.Accounts.DEMO_CUSTOMER,
                scopes, "book-session");
    }

    static String pretty(String json) {
        return DescriptorCodec.mapper().writerWithDefaultPrettyPrinter()
                .writeValueAsString(DescriptorCodec.tree(json));
    }
}
"""


def java_home():
    """
    A JDK 27, which is what the project targets. JAVA_HOME is checked but not trusted: a machine
    with an older default JDK will compile the capture program happily and then fail to load a
    class file it cannot read, which is a confusing way to find out.
    """
    for candidate in [os.environ.get("JAVA_HOME", ""), "/opt/homebrew/opt/openjdk@27",
                      "/usr/libexec/java_home"]:
        home = pathlib.Path(candidate)
        if not candidate or not (home / "bin" / "javac").exists():
            continue
        release = home / "release"
        if release.exists() and 'JAVA_VERSION="27' not in release.read_text():
            continue
        return candidate
    sys.exit("A JDK 27 is required. Set JAVA_HOME to one, or install openjdk@27.")


def run(command, **kwargs):
    result = subprocess.run(command, capture_output=True, text=True, **kwargs)
    if result.returncode != 0:
        print(result.stdout[-4000:])
        print(result.stderr[-4000:], file=sys.stderr)
        sys.exit(f"failed: {' '.join(str(c) for c in command)}")
    return result.stdout


# ----------------------------------------------------------------- the producer graph

GRAPH_COLUMNS = ["needs nothing", "needs an account", "needs a symbol",
                 "needs an order or a confirmation"]
NODE_W, NODE_H, ROW, COL = 120, 17, 22, 134


def draw_producer_graph(classpath, home, env):
    """
    Draw the producer graph from the ontology itself, so the figure in the book cannot disagree
    with the briefing the model is given. Hand-drawing it was tried first and was wrong about two
    tools within a week.
    """
    dump = run([f"{home}/bin/java", "-cp", classpath, "-e"] if False else
               [f"{home}/bin/java", "-cp", classpath, "GraphDump"], env=env, cwd=CODE)
    graph = json.loads(dump)
    prerequisites = {tool: [n for n in needs if n != tool]
                     for tool, needs in graph["prerequisites"].items()}
    tiers = graph["tiers"]

    depth, columns = {}, {}

    def depth_of(tool, seen):
        if tool in depth:
            return depth[tool]
        if tool in seen:
            return 0
        seen.add(tool)
        needs = prerequisites.get(tool, [])
        value = min(3, 1 + max((depth_of(other, seen) for other in needs), default=-1)) if needs \
            else 0
        depth[tool] = value
        return value

    nodes = {}
    for tool in graph["order"]:
        column = depth_of(tool, set())
        row = columns.get(column, 0)
        columns[column] = row + 1
        nodes[tool] = {"x": 6 + column * COL, "y": 26 + row * ROW, "column": column,
                       "tier": tiers[tool], "label": tool.replace("brokerage_", "")}

    height = max(node["y"] for node in nodes.values()) + NODE_H + 14
    fill = {0: "#e6f6ee", 1: "#eaeeff", 2: "#fdf1e0", 3: "#fdeaee"}
    line = {0: "#0d7a52", 1: "#2f4fd0", 2: "#a8620a", 3: "#c0203f"}

    out = [f'<svg viewBox="0 0 548 {height}" width="100%" role="img" aria-label="Which tool '
           f'produces the identifiers each other tool needs; three state-changing tools sit '
           f'behind one read-only preview">',
           '<defs><marker id="pgx" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="5" '
           'markerHeight="5" orient="auto-start-reverse">'
           '<path d="M0 0 L10 5 L0 10 z" fill="#b9c2da"/></marker></defs>']
    for index, label in enumerate(GRAPH_COLUMNS):
        if index in columns:
            out.append(f'<text x="{6 + index * COL}" y="14" font-family="monospace" '
                       f'font-size="6.4" fill="#8d95ab">{label}</text>')
    for tool, needs in prerequisites.items():
        target = nodes.get(tool)
        for need in needs:
            source = nodes.get(need)
            if not source or not target or source["column"] >= target["column"]:
                continue
            x1, y1 = source["x"] + NODE_W, source["y"] + NODE_H / 2
            x2, y2 = target["x"] - 2, target["y"] + NODE_H / 2
            bend = (x2 - x1) / 2
            out.append(f'<path d="M {x1} {y1} C {x1 + bend} {y1}, {x2 - bend} {y2}, {x2} {y2}" '
                       f'fill="none" stroke="#b9c2da" stroke-width="0.6" opacity="0.85" '
                       f'marker-end="url(#pgx)"/>')
    for tool, node in nodes.items():
        tier = node["tier"]
        out.append(f'<rect x="{node["x"]}" y="{node["y"]}" width="{NODE_W}" height="{NODE_H}" '
                   f'rx="3" fill="{fill[tier]}" stroke="{line[tier]}" stroke-width="0.6"/>')
        out.append(f'<text x="{node["x"] + 5} " y="{node["y"] + 12}" font-family="monospace" '
                   f'font-size="6.6" fill="{line[tier]}">{node["label"]}</text>')
    out.append('</svg>')
    (CAPTURES / "producer-graph.svg").write_text("\n".join(out) + "\n")


GRAPH_DUMP = r"""
import com.example.brokerage.catalog.BrokerageCatalog;
import com.example.brokerage.tooling.contract.DescriptorCodec;
import java.util.*;

/** Written by tools/capture.py: the ontology's own view of the graph, as JSON. */
public class GraphDump {
    public static void main(String[] args) {
        BrokerageCatalog catalog = new BrokerageCatalog();
        Map<String, Object> out = new LinkedHashMap<>();
        Map<String, Object> prerequisites = new LinkedHashMap<>();
        Map<String, Object> tiers = new LinkedHashMap<>();
        for (String name : BrokerageCatalog.NAMES) {
            prerequisites.put(name, catalog.ontology().prerequisitesOf(name));
            tiers.put(name, catalog.ontology()
                    .riskTierFor(catalog.ontology().capabilityOf(name)));
        }
        out.put("order", BrokerageCatalog.NAMES);
        out.put("prerequisites", prerequisites);
        out.put("tiers", tiers);
        System.out.println(DescriptorCodec.json(out));
    }
}
"""


def main():
    home = java_home()
    env = dict(os.environ, JAVA_HOME=home, PATH=f"{home}/bin:" + os.environ["PATH"])
    CAPTURES.mkdir(exist_ok=True)

    print("  building the project")
    run([str(CODE / "mvnw"), "-q", "install", "-DskipTests"], cwd=CODE, env=env)
    run([str(CODE / "mvnw"), "-q", "-pl", "test-harness", "dependency:build-classpath",
         "-Dmdep.outputFile=target/cp.txt"], cwd=CODE, env=env)
    classpath = ":".join([
        str(CODE / "test-harness/target/classes"),
        (CODE / "test-harness/target/cp.txt").read_text().strip(),
    ])

    print("  recording the conformance report")
    lint = subprocess.run([f"{home}/bin/java", "-cp", classpath,
                           "com.example.brokerage.catalog.CatalogLint"],
                          capture_output=True, text=True, cwd=CODE)
    (CAPTURES / "conformance.txt").write_text(lint.stdout.lstrip("\n"))

    print("  recording the desktop manifest")
    manifest = run([f"{home}/bin/java", "-jar",
                    str(CODE / "desktop-bridge/target/desktop-bridge-1.0.0.jar"), "--manifest"],
                   cwd=CODE, env=env)
    tools = json.loads(manifest)
    (CAPTURES / "desktop-manifest.json").write_text(
        json.dumps(tools[:2], indent=2) + "\n")
    (CAPTURES / "desktop-manifest-names.txt").write_text(
        "".join(f"{tool['name']}\n" for tool in tools))

    print("  recording the harness run")
    harness = subprocess.run([f"{home}/bin/java", "-cp", classpath,
                              "com.example.brokerage.harness.HarnessMain"],
                             capture_output=True, text=True, cwd=CODE)
    (CAPTURES / "harness-run.txt").write_text(harness.stdout.lstrip("\n"))

    work0 = CAPTURES / ".graph"
    work0.mkdir(exist_ok=True)
    (work0 / "GraphDump.java").write_text(GRAPH_DUMP)
    run([f"{home}/bin/javac", "-cp", classpath, "-d", str(work0), str(work0 / "GraphDump.java")],
        env=env)
    classpath = f"{work0}:{classpath}"

    print("  recording tool envelopes")
    plan = {"calls": {name: [role, tool, args] for name, (role, tool, args) in CALLS.items()}}
    work = CAPTURES / ".build"
    work.mkdir(exist_ok=True)
    (work / "plan.json").write_text(json.dumps(plan))
    (work / "Capture.java").write_text(RECORDER)
    run([f"{home}/bin/javac", "-cp", classpath, "-d", str(work), str(work / "Capture.java")],
        env=env)
    run([f"{home}/bin/java", "-cp", f"{work}:{classpath}", "Capture", str(CAPTURES),
         str(work / "plan.json"), MARGIN], cwd=CODE, env=env)
    shutil.rmtree(work)

    print("  drawing the producer graph")
    draw_producer_graph(classpath, home, env)
    shutil.rmtree(work0)          # the graph dumper's scratch; it is on the classpath until now

    recorded = sorted(p.name for p in CAPTURES.iterdir() if p.is_file())
    print(f"\n  {len(recorded)} captures in {CAPTURES.relative_to(BOOK.parent)}/")


if __name__ == "__main__":
    main()
