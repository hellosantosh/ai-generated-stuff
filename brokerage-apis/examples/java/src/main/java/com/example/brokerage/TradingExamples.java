package com.example.brokerage;

import com.example.brokerage.BrokerageClient.ApiException;
import com.example.brokerage.BrokerageClient.Options;
import com.example.brokerage.BrokerageClient.Response;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.node.ArrayNode;
import com.fasterxml.jackson.databind.node.ObjectNode;

import java.math.BigDecimal;
import java.math.RoundingMode;
import java.util.ArrayList;
import java.util.List;
import java.util.Set;

import static com.example.brokerage.BrokerageClient.MAPPER;
import static com.example.brokerage.BrokerageClient.can;
import static com.example.brokerage.BrokerageClient.decimal;

/**
 * Trading API examples: stock orders and options orders.
 *
 * <p>Point this at the sandbox. {@link BrokerageClient} defaults to the sandbox
 * base URL; switching to production is a deliberate edit, not an environment
 * accident.
 *
 * <pre>{@code mvn -q compile exec:java -Dexec.mainClass=com.example.brokerage.TradingExamples}</pre>
 */
public final class TradingExamples {

    private TradingExamples() {}

    // -----------------------------------------------------------------------
    // Instrument resolution — you need an instrumentId before you can trade
    // -----------------------------------------------------------------------

    public static JsonNode resolveEquity(BrokerageClient client, String symbol) {
        JsonNode response = client.get("/instruments",
                Options.none().param("query", symbol).param("type", "EQUITY"));

        for (JsonNode instrument : response.path("items")) {
            if (instrument.path("symbol").asText().equalsIgnoreCase(symbol)) {
                return instrument;
            }
        }
        throw new IllegalArgumentException("No tradable equity matching '" + symbol + "'.");
    }

    /**
     * Find one option contract in a chain.
     *
     * <p>Always narrow by expiration. An unfiltered chain on a liquid underlying is
     * thousands of contracts and will be slow for both sides.
     */
    public static JsonNode resolveOption(
            BrokerageClient client, String underlying, String expiration, String strike, String optionType) {

        JsonNode chain = client.get("/instruments/" + underlying + "/option-chain",
                Options.none().param("expiration", expiration).param("optionType", optionType));

        BigDecimal target = new BigDecimal(strike);
        List<String> available = new ArrayList<>();

        for (JsonNode contract : chain.path("contracts")) {
            BigDecimal contractStrike = decimal(contract.path("option"), "strike");
            // compareTo, not equals: BigDecimal.equals("250.0") != "250.00".
            if (contractStrike.compareTo(target) == 0) {
                return contract;
            }
            available.add(contractStrike.toPlainString());
        }

        throw new IllegalArgumentException("No %s %s %s%s. Strikes: %s".formatted(
                underlying, expiration, strike, optionType.charAt(0),
                available.stream().distinct().limit(12).toList()));
    }

    // -----------------------------------------------------------------------
    // Preview — run this before every order you show a human
    // -----------------------------------------------------------------------

    /**
     * Cost, fees, buying-power impact, and warnings, without routing anything.
     *
     * <p>{@code valid: false} means at least one ERROR-severity warning; submitting
     * anyway will be rejected. Surface the warnings instead of retrying.
     */
    public static JsonNode preview(BrokerageClient client, JsonNode account, ObjectNode order) {
        JsonNode result = client.post(
                client.linkOrThrow(account, "self") + "/orders/preview", order, Options.none());

        System.out.println("  Preview");
        if (result.hasNonNull("estimatedCost")) {
            System.out.printf("    est. cost        %,12.2f%n", decimal(result, "estimatedCost"));
        }
        if (result.hasNonNull("estimatedProceeds")) {
            System.out.printf("    est. proceeds    %,12.2f%n", decimal(result, "estimatedProceeds"));
        }
        System.out.printf("    commission       %,12.2f%n", decimal(result, "commission"));
        for (JsonNode fee : result.path("fees")) {
            System.out.printf("    %-16s %,12.2f%n",
                    fee.path("type").asText().toLowerCase(), decimal(fee, "amount"));
        }
        if (result.hasNonNull("buyingPowerAfter")) {
            System.out.printf("    BP after         %,12.2f%n", decimal(result, "buyingPowerAfter"));
        }
        if (result.hasNonNull("maxLoss")) {
            System.out.printf("    max loss         %,12.2f%n", decimal(result, "maxLoss"));
        }

        for (JsonNode warning : result.path("warnings")) {
            System.out.printf("    [%s] %s%n",
                    warning.path("severity").asText(), warning.path("message").asText());
        }

        return result;
    }

    // -----------------------------------------------------------------------
    // Placing orders
    // -----------------------------------------------------------------------

    /**
     * Preview, then place.
     *
     * <p>The idempotency key is generated once, outside the client's retry loop, and
     * reused on every attempt. That is the whole point: a socket timeout on a market
     * order is indistinguishable from a rejection, and the natural retry would
     * otherwise double the position.
     */
    public static JsonNode place(
            BrokerageClient client, JsonNode account, ObjectNode order, boolean dryRun) {

        JsonNode result = preview(client, account, order);
        if (!result.path("valid").asBoolean()) {
            System.out.println("  Preview invalid; not submitting.");
            return null;
        }
        if (dryRun) {
            System.out.println("  [dry run] not submitting.");
            return null;
        }

        String idempotencyKey = BrokerageClient.newIdempotencyKey();
        JsonNode placed = client.post(
                client.linkOrThrow(account, "self") + "/orders",
                order,
                Options.none().idempotencyKey(idempotencyKey));

        System.out.printf("  Placed %s  status=%s%n",
                placed.path("orderId").asText(), placed.path("status").asText());
        // A 201 acknowledges routing, not a fill. Terminal state comes from the
        // event stream or a poll.
        return placed;
    }

    // --- stock -------------------------------------------------------------

    /** Buy 300 MSFT, limit 412.50, day. */
    public static JsonNode stockLimitBuy(BrokerageClient client, JsonNode account, boolean dryRun) {
        System.out.println("\nStock: limit buy");
        JsonNode instrument = resolveEquity(client, "MSFT");

        ObjectNode order = MAPPER.createObjectNode();
        order.put("assetClass", "EQUITY");
        order.put("clientOrderId", "java-example-limit-buy");
        order.put("side", "BUY");
        order.put("instrumentId", instrument.path("instrumentId").asText());
        order.put("quantity", "300");
        order.put("orderType", "LIMIT");
        order.put("limitPrice", "412.50");
        order.put("timeInForce", "DAY");
        order.put("extendedHours", false);

        return place(client, account, order, dryRun);
    }

    /**
     * Flatten an existing position at market.
     *
     * <p>Quantity comes from the position rather than a constant — selling more than
     * you hold turns a close into an unintended short.
     */
    public static JsonNode stockMarketSell(BrokerageClient client, JsonNode account, boolean dryRun) {
        System.out.println("\nStock: market sell to flatten");
        List<JsonNode> positions = client.collect(
                client.linkOrThrow(account, "positions"),
                Options.none().param("instrumentType", "EQUITY"));

        if (positions.isEmpty()) {
            System.out.println("  No equity positions to close.");
            return null;
        }

        JsonNode position = positions.get(0);
        String positionId = position.path("positionId").asText();

        ObjectNode order = MAPPER.createObjectNode();
        order.put("assetClass", "EQUITY");
        order.put("clientOrderId", "java-flatten-" + positionId.substring(positionId.length() - 8));
        order.put("side", "LONG".equals(position.path("side").asText()) ? "SELL" : "BUY_TO_COVER");
        order.put("instrumentId", position.path("instrument").path("instrumentId").asText());
        order.put("quantity", decimal(position, "quantity").abs().toPlainString());
        order.put("orderType", "MARKET");
        order.put("timeInForce", "DAY");

        return place(client, account, order, dryRun);
    }

    /**
     * Entry with an attached profit target and stop.
     *
     * <p>The two exits are released only when the entry fills and are OCO: whichever
     * triggers first cancels the other, so a filled target cannot leave a live stop
     * behind.
     */
    public static JsonNode stockBracket(BrokerageClient client, JsonNode account, boolean dryRun) {
        System.out.println("\nStock: bracket order");
        JsonNode instrument = resolveEquity(client, "MSFT");

        ObjectNode order = MAPPER.createObjectNode();
        order.put("assetClass", "EQUITY");
        order.put("clientOrderId", "java-example-bracket");
        order.put("side", "BUY");
        order.put("instrumentId", instrument.path("instrumentId").asText());
        order.put("quantity", "100");
        order.put("orderType", "LIMIT");
        order.put("limitPrice", "410.00");
        order.put("timeInForce", "DAY");

        ObjectNode bracket = order.putObject("bracket");
        ObjectNode takeProfit = bracket.putObject("takeProfit");
        takeProfit.put("orderType", "LIMIT");
        takeProfit.put("limitPrice", "430.00");
        ObjectNode stopLoss = bracket.putObject("stopLoss");
        stopLoss.put("orderType", "STOP");
        stopLoss.put("stopPrice", "398.00");

        return place(client, account, order, dryRun);
    }

    /** Trailing stop 5% below the high-water mark, good till cancelled. */
    public static JsonNode stockTrailingStop(BrokerageClient client, JsonNode account, boolean dryRun) {
        System.out.println("\nStock: trailing stop");
        JsonNode instrument = resolveEquity(client, "NVDA");

        ObjectNode order = MAPPER.createObjectNode();
        order.put("assetClass", "EQUITY");
        order.put("clientOrderId", "java-example-trail");
        order.put("side", "SELL");
        order.put("instrumentId", instrument.path("instrumentId").asText());
        order.put("quantity", "50");
        order.put("orderType", "TRAILING_STOP");
        order.put("trailingPercent", "5.0");
        order.put("timeInForce", "GTC");

        return place(client, account, order, dryRun);
    }

    // --- options -----------------------------------------------------------

    private static ObjectNode leg(String instrumentId, String side, int ratio) {
        ObjectNode node = MAPPER.createObjectNode();
        node.put("instrumentId", instrumentId);
        node.put("side", side);
        node.put("ratio", ratio);
        return node;
    }

    /**
     * Buy 5 AAPL Dec-26 250 calls to open, limit 12.40 per contract.
     *
     * <p>{@code limitPrice} is per contract; the cash outlay is price × quantity ×
     * contractMultiplier — 12.40 × 5 × 100 = 6,200. Requires LEVEL_2 approval.
     */
    public static JsonNode optionSingleLeg(BrokerageClient client, JsonNode account, boolean dryRun) {
        System.out.println("\nOption: single-leg long call");
        JsonNode contract = resolveOption(client, "AAPL", "2026-12-18", "250.00", "CALL");

        ObjectNode order = MAPPER.createObjectNode();
        order.put("assetClass", "OPTION");
        order.put("strategy", "SINGLE");
        order.put("clientOrderId", "java-example-long-call");
        ArrayNode legs = order.putArray("legs");
        legs.add(leg(contract.path("instrumentId").asText(), "BUY_TO_OPEN", 1));
        order.put("quantity", "5");
        order.put("orderType", "LIMIT");
        order.put("limitPrice", "12.40");
        order.put("priceEffect", "DEBIT");
        order.put("timeInForce", "DAY");

        return place(client, account, order, dryRun);
    }

    /**
     * 10 AAPL Dec-26 250/260 call debit spreads at a net 3.15.
     *
     * <p>Two points that trip people up:
     *
     * <ul>
     *   <li>{@code quantity} is strategy units, not contracts. 10 here means 10
     *       spreads, so 20 contracts total.
     *   <li>Declaring {@code strategy: VERTICAL} is what gets spread margin applied.
     *       The same legs sent as CUSTOM are margined leg by leg, and the short call
     *       alone would demand far more buying power — often the difference between
     *       accepted and rejected.
     * </ul>
     */
    public static JsonNode optionVerticalSpread(BrokerageClient client, JsonNode account, boolean dryRun) {
        System.out.println("\nOption: vertical debit spread");
        JsonNode longLeg = resolveOption(client, "AAPL", "2026-12-18", "250.00", "CALL");
        JsonNode shortLeg = resolveOption(client, "AAPL", "2026-12-18", "260.00", "CALL");

        ObjectNode order = MAPPER.createObjectNode();
        order.put("assetClass", "OPTION");
        order.put("strategy", "VERTICAL");
        order.put("clientOrderId", "java-example-vertical");
        ArrayNode legs = order.putArray("legs");
        legs.add(leg(longLeg.path("instrumentId").asText(), "BUY_TO_OPEN", 1));
        legs.add(leg(shortLeg.path("instrumentId").asText(), "SELL_TO_OPEN", 1));
        order.put("quantity", "10");
        order.put("orderType", "NET_DEBIT");
        order.put("netPrice", "3.15");   // always positive; direction is in priceEffect
        order.put("priceEffect", "DEBIT");
        order.put("timeInForce", "DAY");

        return place(client, account, order, dryRun);
    }

    /**
     * Four-leg iron condor for a net credit. Requires LEVEL_3 approval.
     *
     * <p>Leg order does not matter to the matching engine, but keeping them in strike
     * order makes the intent legible to whoever reads this next.
     */
    public static JsonNode optionIronCondor(BrokerageClient client, JsonNode account, boolean dryRun) {
        System.out.println("\nOption: iron condor");

        String[][] spec = {
            {"520.00", "PUT", "BUY_TO_OPEN"},
            {"540.00", "PUT", "SELL_TO_OPEN"},
            {"600.00", "CALL", "SELL_TO_OPEN"},
            {"620.00", "CALL", "BUY_TO_OPEN"},
        };

        ObjectNode order = MAPPER.createObjectNode();
        order.put("assetClass", "OPTION");
        order.put("strategy", "IRON_CONDOR");
        order.put("clientOrderId", "java-example-condor");
        ArrayNode legs = order.putArray("legs");

        for (String[] row : spec) {
            JsonNode contract = resolveOption(client, "SPY", "2026-11-20", row[0], row[1]);
            legs.add(leg(contract.path("instrumentId").asText(), row[2], 1));
        }

        order.put("quantity", "2");
        order.put("orderType", "NET_CREDIT");
        order.put("netPrice", "1.85");
        order.put("priceEffect", "CREDIT");
        order.put("timeInForce", "DAY");

        return place(client, account, order, dryRun);
    }

    /**
     * Sell calls against stock you already own. LEVEL_1, the lowest tier.
     *
     * <p>The covered-call check is on shares held, not on the order: 100 shares per
     * contract. Selling more contracts than your share count covers makes the excess
     * naked, which requires LEVEL_4 and far more margin.
     */
    public static JsonNode optionCoveredCall(BrokerageClient client, JsonNode account, boolean dryRun) {
        System.out.println("\nOption: covered call");
        List<JsonNode> positions = client.collect(
                client.linkOrThrow(account, "positions"),
                Options.none().param("symbol", "AAPL").param("instrumentType", "EQUITY"));

        if (positions.isEmpty()) {
            System.out.println("  No AAPL shares held; nothing to cover.");
            return null;
        }

        BigDecimal shares = decimal(positions.get(0), "quantity").abs();
        int contracts = shares.divide(BigDecimal.valueOf(100), 0, RoundingMode.DOWN).intValue();
        if (contracts < 1) {
            System.out.printf("  Only %s shares; need 100 per contract.%n", shares.toPlainString());
            return null;
        }

        JsonNode contract = resolveOption(client, "AAPL", "2026-12-18", "250.00", "CALL");

        ObjectNode order = MAPPER.createObjectNode();
        order.put("assetClass", "OPTION");
        order.put("strategy", "COVERED_CALL");
        order.put("clientOrderId", "java-example-covered-call");
        ArrayNode legs = order.putArray("legs");
        legs.add(leg(contract.path("instrumentId").asText(), "SELL_TO_OPEN", 1));
        order.put("quantity", String.valueOf(contracts));
        order.put("orderType", "LIMIT");
        order.put("limitPrice", "9.85");
        order.put("priceEffect", "CREDIT");
        order.put("timeInForce", "DAY");

        return place(client, account, order, dryRun);
    }

    // -----------------------------------------------------------------------
    // Amending and cancelling
    // -----------------------------------------------------------------------

    /**
     * Reprice a working order using optimistic concurrency.
     *
     * <p>The read/decide/write cycle is racing the market. {@code If-Match} is what
     * turns "the order filled while I was deciding" from a silent mis-amendment into
     * a 412 you can actually handle.
     */
    public static JsonNode amendOrder(BrokerageClient client, JsonNode order, String newPrice) {
        String selfUrl = client.linkOrThrow(order, "self");
        Response current = client.getWithHeaders(selfUrl, Options.none());
        JsonNode fresh = current.body();

        if (!can(fresh, "replace")) {
            System.out.printf("  %s is %s; not amendable.%n",
                    fresh.path("orderId").asText(), fresh.path("status").asText());
            return fresh;
        }

        ObjectNode patch = MAPPER.createObjectNode();
        patch.put("limitPrice", newPrice);

        try {
            JsonNode amended = client.patch(selfUrl, patch,
                    Options.none().ifMatch(current.headers().etag()));
            System.out.printf("  Repriced to %s; status=%s%n", newPrice, amended.path("status").asText());
            return amended;
        } catch (ApiException e) {
            if (e.status() == 412) {
                // Re-read and decide again — do not blindly retry with a new
                // ETag, or you will amend an order whose state you never
                // evaluated.
                JsonNode now = client.get(selfUrl);
                System.out.printf("  Stale ETag; order is now %s. Re-evaluate before retrying.%n",
                        now.path("status").asText());
                return now;
            }
            throw e;
        }
    }

    /**
     * Request cancellation.
     *
     * <p>Cancellation is a request, not a guarantee. The order moves to
     * PENDING_CANCEL and reaches CANCELLED only once the venue acknowledges; a fill
     * already in flight can still land. Confirm terminal state separately.
     */
    public static JsonNode cancelOrder(BrokerageClient client, JsonNode order) {
        String selfUrl = client.linkOrThrow(order, "self");
        Response current = client.getWithHeaders(selfUrl, Options.none());
        JsonNode fresh = current.body();

        if (!can(fresh, "cancel")) {
            System.out.printf("  %s is %s; nothing to cancel.%n",
                    fresh.path("orderId").asText(), fresh.path("status").asText());
            return fresh;
        }

        JsonNode cancelled = client.delete(selfUrl, Options.none().ifMatch(current.headers().etag()));
        System.out.printf("  Cancel requested; status=%s%n", cancelled.path("status").asText());
        return cancelled;
    }

    /** Flatten the working order book. A useful panic button. */
    public static void cancelAllWorking(BrokerageClient client, JsonNode account) {
        List<JsonNode> working = new ArrayList<>();
        client.get(client.linkOrThrow(account, "orders")).path("items").forEach(working::add);

        System.out.printf("%nCancelling %d working order(s)%n", working.size());
        for (JsonNode order : working) {
            try {
                cancelOrder(client, order);
            } catch (ApiException e) {
                // 409 here just means it reached a terminal state first — benign.
                System.out.printf("  %s: %s%n", order.path("orderId").asText(), e.title());
            }
        }
    }

    // -----------------------------------------------------------------------

    public static void main(String[] args) {
        BrokerageClient client = new BrokerageClient();
        JsonNode account = client.get("/accounts").path("items").get(0);

        String optionsLevel = account.path("optionsLevel").asText();
        System.out.printf("Account %s  options=%s%n", account.path("accountId").asText(), optionsLevel);

        if (!"ACTIVE".equals(account.path("status").asText())) {
            System.out.printf("Account is %s; trading operations will be rejected.%n",
                    account.path("status").asText());
            return;
        }

        // dryRun previews without routing. Flip it deliberately, sandbox only.
        boolean dryRun = true;

        try {
            stockLimitBuy(client, account, dryRun);
            stockBracket(client, account, dryRun);
            stockTrailingStop(client, account, dryRun);

            if (Set.of("LEVEL_2", "LEVEL_3", "LEVEL_4").contains(optionsLevel)) {
                optionSingleLeg(client, account, dryRun);
            }
            if (Set.of("LEVEL_3", "LEVEL_4").contains(optionsLevel)) {
                optionVerticalSpread(client, account, dryRun);
                optionIronCondor(client, account, dryRun);
            }
            optionCoveredCall(client, account, dryRun);

        } catch (ApiException e) {
            System.err.println("\nAPI error: " + e.getMessage());
            if (e.type().endsWith("/insufficient-buying-power")) {
                System.err.printf("  needs %s %s, has %s%n",
                        e.problem().path("required").path("amount").asText(),
                        e.problem().path("required").path("currency").asText(),
                        e.problem().path("available").path("amount").asText());
            } else if (e.type().endsWith("/options-level-insufficient")) {
                System.err.printf("  needs %s, account is %s%n",
                        e.problem().path("requiredLevel").asText(),
                        e.problem().path("currentLevel").asText());
            }
            System.exit(1);
        } catch (IllegalArgumentException e) {
            System.err.println("\nInstrument lookup failed: " + e.getMessage());
            System.exit(1);
        }
    }
}
