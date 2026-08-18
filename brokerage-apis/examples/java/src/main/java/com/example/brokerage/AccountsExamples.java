package com.example.brokerage;

import com.example.brokerage.BrokerageClient.ApiException;
import com.example.brokerage.BrokerageClient.Options;
import com.example.brokerage.BrokerageClient.Response;
import com.fasterxml.jackson.databind.JsonNode;

import java.math.BigDecimal;
import java.time.Duration;
import java.time.Instant;
import java.time.LocalDate;
import java.util.ArrayList;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.stream.Collectors;
import java.util.stream.Stream;

import static com.example.brokerage.BrokerageClient.can;
import static com.example.brokerage.BrokerageClient.decimal;
import static com.example.brokerage.BrokerageClient.link;

/**
 * Accounts API examples: list, balances, positions, order status, transactions.
 *
 * <pre>{@code mvn -q compile exec:java -Dexec.mainClass=com.example.brokerage.AccountsExamples}</pre>
 */
public final class AccountsExamples {

    private AccountsExamples() {}

    // -----------------------------------------------------------------------
    // GET /accounts
    // -----------------------------------------------------------------------

    /**
     * Every account reachable with the current grant.
     *
     * <p>{@code accountId} is the opaque handle used everywhere else in the API.
     * {@code accountNumber} is the customer-facing value and arrives masked unless
     * the grant carries {@code accounts:read:full} — do not use it as a key.
     */
    public static List<JsonNode> listAccounts(BrokerageClient client) {
        JsonNode response = client.get("/accounts");
        List<JsonNode> accounts = new ArrayList<>();
        response.path("items").forEach(accounts::add);

        for (JsonNode account : accounts) {
            System.out.printf(
                    "%s  %10s  %-7s %-16s %-10s options=%s%n",
                    account.path("accountId").asText(),
                    account.path("accountNumber").asText(),
                    account.path("type").asText(),
                    account.path("registration").asText(),
                    account.path("status").asText(),
                    account.path("optionsLevel").asText());

            // Affordances are state-dependent: a RESTRICTED account carries no
            // place-order link, so a UI can disable the trade button without
            // knowing anything about compliance rules.
            if (!can(account, "place-order")) {
                System.out.println("    (not trade-enabled with this grant)");
            }
        }

        return accounts;
    }

    // -----------------------------------------------------------------------
    // GET /accounts/{accountId}/balances
    // -----------------------------------------------------------------------

    /**
     * Cash, buying power, and margin. Reached by following the {@code balances}
     * link rather than building the URL, so the client survives a path change.
     */
    public static JsonNode showBalances(BrokerageClient client, JsonNode account) {
        JsonNode balances = client.follow(account, "balances");

        System.out.printf("%nBalances as of %s (%s)%n",
                balances.path("asOf").asText(), balances.path("currency").asText());
        System.out.printf("  Net liquidation : %,14.2f%n", decimal(balances, "netLiquidation"));
        System.out.printf("  Cash (settled)  : %,14.2f%n", decimal(balances, "cash", "settled"));
        System.out.printf("  Cash (total)    : %,14.2f%n", decimal(balances, "cash", "total"));
        System.out.printf("  Withdrawable    : %,14.2f%n", decimal(balances, "cash", "withdrawable"));

        // Stock and option buying power differ materially in a margin account —
        // sizing an option order off stock buying power gets it rejected.
        System.out.println("  Buying power");
        System.out.printf("    stock         : %,14.2f%n", decimal(balances, "buyingPower", "stock"));
        System.out.printf("    option        : %,14.2f%n", decimal(balances, "buyingPower", "option"));
        if (balances.path("buyingPower").has("dayTrading")) {
            System.out.printf("    day trading   : %,14.2f%n", decimal(balances, "buyingPower", "dayTrading"));
        }

        JsonNode margin = balances.path("margin");
        if (margin.isObject()) {
            System.out.printf("  Excess liquidity: %,14.2f%n", decimal(margin, "excessLiquidity"));
            JsonNode call = margin.path("marginCall");
            if (call.isObject()) {
                // A margin call is the one balance field worth alerting on.
                System.out.printf("  ** %s: %,.2f due %s **%n",
                        call.path("type").asText(),
                        decimal(call, "amount"),
                        call.path("dueDate").asText());
            }
        }

        if (balances.path("patternDayTrader").asBoolean()) {
            System.out.printf("  Day trades left : %d%n", balances.path("dayTradesRemaining").asInt());
        }

        return balances;
    }

    // -----------------------------------------------------------------------
    // GET /accounts/{accountId}/positions
    // -----------------------------------------------------------------------

    public static List<JsonNode> showPositions(BrokerageClient client, JsonNode account) {
        List<JsonNode> positions = client.collect(client.linkOrThrow(account, "positions"), Options.none());

        if (positions.isEmpty()) {
            System.out.println("\nNo open positions.");
            return positions;
        }

        System.out.printf("%n%-24s %-6s %8s %11s %14s %13s%n",
                "Symbol", "Side", "Qty", "Avg cost", "Market value", "Unrealized");
        System.out.println("-".repeat(80));

        BigDecimal totalUnrealized = BigDecimal.ZERO;
        for (JsonNode position : positions) {
            JsonNode instrument = position.path("instrument");

            // Option positions carry the 21-character OSI symbol, which is
            // unreadable in a list. Build something human-facing from the
            // option detail instead.
            String label;
            if ("OPTION".equals(instrument.path("type").asText())) {
                JsonNode option = instrument.path("option");
                label = "%s %s %s%s".formatted(
                        option.path("underlyingSymbol").asText(),
                        option.path("expiration").asText().substring(2),
                        decimal(option, "strike").stripTrailingZeros().toPlainString(),
                        option.path("optionType").asText().substring(0, 1));
            } else {
                label = instrument.path("symbol").asText();
            }

            BigDecimal unrealized = decimal(position, "unrealizedPnl");
            totalUnrealized = totalUnrealized.add(unrealized);

            System.out.printf("%-24s %-6s %8s %11.4f %,14.2f %,+13.2f%n",
                    label,
                    position.path("side").asText(),
                    decimal(position, "quantity").stripTrailingZeros().toPlainString(),
                    decimal(position, "averageCost"),
                    decimal(position, "marketValue"),
                    unrealized);
        }

        System.out.println("-".repeat(80));
        System.out.printf("%-57s%,+13.2f%n", "Total unrealized", totalUnrealized);
        return positions;
    }

    // -----------------------------------------------------------------------
    // GET /accounts/{accountId}/orders
    // -----------------------------------------------------------------------

    /**
     * Working orders, then a wider historical view.
     *
     * <p>With no {@code status} filter the endpoint returns working orders only —
     * the set a trading screen needs on load. Pass {@code status=ALL} to widen it.
     */
    public static List<JsonNode> showOrderStatus(BrokerageClient client, JsonNode account) {
        String ordersUrl = client.linkOrThrow(account, "orders");

        List<JsonNode> working = new ArrayList<>();
        client.get(ordersUrl).path("items").forEach(working::add);
        System.out.printf("%nWorking orders: %d%n", working.size());

        for (JsonNode order : working) {
            String symbol = order.path("instrument").has("symbol")
                    ? order.path("instrument").path("symbol").asText()
                    : order.path("strategy").asText("?");

            System.out.printf("  %s  %-17s %-12s %-10s %s/%s @ %s%n",
                    order.path("orderId").asText(),
                    order.path("status").asText(),
                    order.path("side").asText(""),
                    symbol,
                    decimal(order, "filledQuantity").stripTrailingZeros().toPlainString(),
                    decimal(order, "quantity").stripTrailingZeros().toPlainString(),
                    order.path("limitPrice").asText("MKT"));

            // Presence of the link is the authority on what you may do next.
            String actions = Stream.of("cancel", "replace")
                    .filter(rel -> can(order, rel))
                    .collect(Collectors.joining(", "));
            System.out.println("      available: " + (actions.isEmpty() ? "none (terminal)" : actions));
        }

        // Everything from the last 7 days, terminal states included.
        Options options = Options.none()
                .param("status", List.of("ALL"))
                .param("from", Instant.now().minus(Duration.ofDays(7)).toString());

        List<JsonNode> recent = client.collect(ordersUrl, options);
        System.out.printf("%nOrders in the last 7 days: %d%n", recent.size());

        Map<String, Integer> byStatus = new LinkedHashMap<>();
        for (JsonNode order : recent) {
            byStatus.merge(order.path("status").asText(), 1, Integer::sum);
        }
        byStatus.entrySet().stream()
                .sorted(Map.Entry.<String, Integer>comparingByValue().reversed())
                .forEach(e -> System.out.printf("  %-18s %d%n", e.getKey(), e.getValue()));

        return working;
    }

    /**
     * Poll one order to a terminal state using conditional requests.
     *
     * <p>{@code If-None-Match} makes an unchanged poll a 304 with no body, which is
     * what keeps a 1-second cadence inside the rate limit. For anything
     * latency-sensitive use the SSE stream — this exists for batch reconciliation.
     */
    public static JsonNode pollOrderUntilTerminal(BrokerageClient client, JsonNode order, Duration timeout) {
        Set<String> terminal = Set.of("FILLED", "CANCELLED", "REJECTED", "EXPIRED", "REPLACED");
        String url = client.linkOrThrow(order, "self");
        Instant deadline = Instant.now().plus(timeout);

        JsonNode current = order;
        String etag = null;

        while (Instant.now().isBefore(deadline)) {
            Options options = Options.none();
            if (etag != null) {
                options.header("If-None-Match", etag);
            }

            Response response = client.getWithHeaders(url, options);
            if (!response.notModified() && response.body() != null) {
                current = response.body();
                etag = response.headers().etag();
                System.out.printf("  -> %s filled=%s/%s%n",
                        current.path("status").asText(),
                        current.path("filledQuantity").asText(),
                        current.path("quantity").asText());
                if (terminal.contains(current.path("status").asText())) {
                    return current;
                }
            }

            try {
                Thread.sleep(1000);
            } catch (InterruptedException e) {
                Thread.currentThread().interrupt();
                break;
            }
        }

        System.out.printf("  timed out; order still %s%n", current.path("status").asText());
        return current;
    }

    // -----------------------------------------------------------------------
    // GET /accounts/{accountId}/transactions
    // -----------------------------------------------------------------------

    /**
     * Transaction history over a date window.
     *
     * <p>Settlement can back-date entries, so a transaction may appear in a page you
     * already read. Reconcile on {@code transactionId}, which is permanent and
     * unique — hence the dedupe below rather than a blind sum.
     */
    public static List<JsonNode> showTransactions(BrokerageClient client, JsonNode account, int days) {
        LocalDate today = LocalDate.now();
        Options options = Options.none()
                .param("from", today.minusDays(days).toString())
                .param("to", today.toString());

        Set<String> seen = new HashSet<>();
        List<JsonNode> transactions = new ArrayList<>();
        BigDecimal netCash = BigDecimal.ZERO;

        for (JsonNode txn : client.collect(client.linkOrThrow(account, "transactions"), options)) {
            if (!seen.add(txn.path("transactionId").asText())) {
                continue;
            }
            transactions.add(txn);
            netCash = netCash.add(decimal(txn, "netAmount"));
        }

        System.out.printf("%nTransactions, last %d days: %d%n", days, transactions.size());
        System.out.printf("%-12s %-16s %-44s %13s%n", "Date", "Type", "Description", "Net");
        System.out.println("-".repeat(88));

        transactions.stream().limit(20).forEach(txn -> {
            String description = txn.path("description").asText("");
            System.out.printf("%-12s %-16s %-44s %,+13.2f%n",
                    txn.path("tradeDate").asText(),
                    txn.path("type").asText(),
                    description.substring(0, Math.min(44, description.length())),
                    decimal(txn, "netAmount"));
        });
        if (transactions.size() > 20) {
            System.out.printf("... and %d more%n", transactions.size() - 20);
        }

        System.out.println("-".repeat(88));
        System.out.printf("%-73s%,+13.2f%n", "Net cash movement", netCash);

        // Fee totals are a common reconciliation need and are not exposed as a
        // summary field, so roll them up from the fee arrays.
        Map<String, BigDecimal> feesByType = new LinkedHashMap<>();
        for (JsonNode txn : transactions) {
            for (JsonNode fee : txn.path("fees")) {
                feesByType.merge(fee.path("type").asText(), decimal(fee, "amount"), BigDecimal::add);
            }
        }
        if (!feesByType.isEmpty()) {
            System.out.println("\nFees by type:");
            feesByType.entrySet().stream()
                    .sorted(Map.Entry.comparingByKey())
                    .forEach(e -> System.out.printf("  %-14s %,10.2f%n", e.getKey(), e.getValue()));
        }

        return transactions;
    }

    // -----------------------------------------------------------------------

    public static void main(String[] args) {
        BrokerageClient client = new BrokerageClient();

        try {
            List<JsonNode> accounts = listAccounts(client);
            if (accounts.isEmpty()) {
                System.out.println("No accounts available under this grant.");
                return;
            }

            JsonNode account = accounts.get(0);
            System.out.println("\n" + "=".repeat(88));
            System.out.printf("Account %s (%s)%n",
                    account.path("accountId").asText(),
                    account.path("nickname").asText(account.path("type").asText()));
            System.out.println("=".repeat(88));

            showBalances(client, account);
            showPositions(client, account);
            showOrderStatus(client, account);
            showTransactions(client, account, 30);

        } catch (ApiException e) {
            System.err.println("\nAPI error: " + e.getMessage());
            if (e.type().endsWith("/insufficient-scope")) {
                System.err.println("  Re-authorize with the "
                        + e.problem().path("requiredScope").asText() + " scope.");
            }
            e.fieldErrors().forEach(err -> System.err.println(
                    "  " + err.path("pointer").asText() + ": " + err.path("detail").asText()));
            System.exit(1);
        }
    }
}
