package com.example.brokerage.catalog.handlers;

import java.math.BigDecimal;

import com.example.brokerage.domain.Brokerage;
import com.example.brokerage.domain.Money;
import com.example.brokerage.domain.Orders;
import com.example.brokerage.tooling.contract.Invocation;
import com.example.brokerage.tooling.contract.ToolFailure;

import org.springframework.stereotype.Component;

/**
 * Builds an order ticket from validated arguments, and applies the rules a JSON Schema cannot.
 *
 * <p>This class is where the schema profile's ban on {@code if}/{@code then} is paid for, so it is
 * worth being explicit about the trade. "A limit price is required when the order type is LIMIT"
 * is expressible in JSON Schema, as a conditional. It is not reliably expressible to a model,
 * because most runtimes drop the conditional on the way through and the model is then shown a
 * schema in which {@code limitPrice} is simply optional. So the rule is stated twice, in the two
 * places that actually work: in the descriptor's prose, which the model does read, and here, where
 * a violation becomes an error the model can act on.
 *
 * <p>Writing a rule twice is a cost. The alternative — a conditional the model never sees and the
 * handler therefore has to enforce anyway — is the same cost plus a schema that lies.
 */
@Component
public class Tickets {

    private final Brokerage brokerage;

    public Tickets(Brokerage brokerage) {
        this.brokerage = brokerage;
    }

    public Orders.Ticket from(Invocation invocation, String accountId) {
        String symbol = invocation.string("symbol");
        if (brokerage.instruments().find(symbol).isEmpty()) {
            throw ToolFailure.notFound(symbol,
                    "Call brokerage_instruments_search with what the customer said, confirm the "
                            + "ticker with them, and try again.");
        }
        Orders.OrderType type = Orders.OrderType.valueOf(invocation.string("orderType"));
        BigDecimal limit = decimal(invocation, "limitPrice");
        BigDecimal stop = decimal(invocation, "stopPrice");

        requirePrice(type, limit, "limitPrice", Orders.OrderType.LIMIT, Orders.OrderType.STOP_LIMIT);
        requirePrice(type, stop, "stopPrice", Orders.OrderType.STOP, Orders.OrderType.STOP_LIMIT);
        rejectPrice(type, limit, "limitPrice", Orders.OrderType.MARKET, Orders.OrderType.STOP);
        rejectPrice(type, stop, "stopPrice", Orders.OrderType.MARKET, Orders.OrderType.LIMIT);

        Orders.Side side = Orders.Side.valueOf(invocation.string("side"));
        long quantity = invocation.integer("quantity");
        Orders.TimeInForce tif = Orders.TimeInForce.valueOf(invocation.string("timeInForce", "DAY"));
        boolean extended = invocation.flag("extendedHours", false);
        return new Orders.Ticket(accountId, symbol, side, quantity, type, limit, stop, tif,
                extended);
    }

    /** A decimal argument, parsed strictly. The schema already pinned the shape; this pins the value. */
    public static BigDecimal decimal(Invocation invocation, String field) {
        String raw = invocation.string(field);
        if (raw == null) {
            return null;
        }
        BigDecimal value = Money.of(raw);
        if (value.signum() <= 0) {
            throw ToolFailure.invalidArgument(field + " must be greater than zero.",
                    "Re-send with a positive price, or omit it if this order type does not take one.");
        }
        return value;
    }

    private static void requirePrice(Orders.OrderType type, BigDecimal value, String field,
                                     Orders.OrderType... needsIt) {
        if (value == null && contains(needsIt, type)) {
            throw ToolFailure.invalidArgument(
                    "A " + type + " order requires " + field + ".",
                    "Ask the customer what price they want and re-send with " + field + " set.");
        }
    }

    private static void rejectPrice(Orders.OrderType type, BigDecimal value, String field,
                                    Orders.OrderType... forbidsIt) {
        if (value != null && contains(forbidsIt, type)) {
            throw ToolFailure.invalidArgument(
                    "A " + type + " order must not carry " + field + ".",
                    "Re-send without " + field + ", or change orderType if the customer wants a "
                            + "price condition.");
        }
    }

    private static boolean contains(Orders.OrderType[] types, Orders.OrderType type) {
        for (Orders.OrderType candidate : types) {
            if (candidate == type) {
                return true;
            }
        }
        return false;
    }
}
