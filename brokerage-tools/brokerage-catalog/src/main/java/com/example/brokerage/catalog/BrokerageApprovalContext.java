package com.example.brokerage.catalog;

import java.math.BigDecimal;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

import com.example.brokerage.domain.Accounts;
import com.example.brokerage.domain.Brokerage;
import com.example.brokerage.domain.Money;
import com.example.brokerage.domain.Orders;
import com.example.brokerage.domain.RiskEngine;
import com.example.brokerage.tooling.contract.ApprovalContext;
import com.example.brokerage.tooling.contract.Invocation;

import org.springframework.stereotype.Component;

/**
 * What the customer actually sees before they approve a trade.
 *
 * <p>Everything here is computed at the moment of the confirmation, not carried forward from the
 * preview the agent ran. That is the important choice. The preview may have been a minute ago, the
 * agent may have had two turns of conversation since, and the customer is agreeing <em>now</em>.
 * Re-pricing costs one read-only call and removes a whole class of complaint that begins "but it
 * said it would cost".
 *
 * <p>If the order can no longer be placed at all, the dialog says so instead of quoting a cost.
 * A confirmation prompt for an order that would be refused is worse than useless: the customer
 * says yes, the tool refuses, and they have learned that the approval step is theatre.
 */
@Component
public class BrokerageApprovalContext implements ApprovalContext {

    private final Brokerage brokerage;

    public BrokerageApprovalContext(Brokerage brokerage) {
        this.brokerage = brokerage;
    }

    @Override
    public Map<String, String> enrich(Invocation invocation) {
        Map<String, String> values = new LinkedHashMap<>();
        String accountId = invocation.string("accountId");
        Accounts.Account account = accountId == null ? null
                : brokerage.accounts().find(accountId).orElse(null);
        if (account == null) {
            return values;
        }
        values.put("accountNickname", account.nickname() + " (" + account.type().label() + ")");
        values.put("currency", account.currency());

        return switch (invocation.descriptor().name()) {
            case "brokerage_order_place" -> placing(invocation, account, values);
            case "brokerage_order_replace" -> replacing(invocation, account, values);
            case "brokerage_order_cancel" -> cancelling(invocation, values);
            default -> values;
        };
    }

    private Map<String, String> placing(Invocation invocation, Accounts.Account account,
                                        Map<String, String> values) {
        Orders.OrderType type = Orders.OrderType.valueOf(invocation.string("orderType"));
        BigDecimal limit = decimal(invocation, "limitPrice");
        BigDecimal stop = decimal(invocation, "stopPrice");
        Orders.Ticket ticket = new Orders.Ticket(account.accountId(), invocation.string("symbol"),
                Orders.Side.valueOf(invocation.string("side")), invocation.integer("quantity"),
                type, limit, stop,
                Orders.TimeInForce.valueOf(invocation.string("timeInForce", "DAY")),
                invocation.flag("extendedHours", false));
        values.put("orderTypeDescription", describe(type, limit, stop));
        values.put("timeInForce", ticket.timeInForce().name());
        price(ticket, values);
        return values;
    }

    private Map<String, String> replacing(Invocation invocation, Accounts.Account account,
                                          Map<String, String> values) {
        String orderId = invocation.string("orderId");
        Orders.Order order = brokerage.orders().find(orderId).orElse(null);
        if (order == null) {
            return values;
        }
        BigDecimal limit = decimal(invocation, "limitPrice");
        BigDecimal stop = decimal(invocation, "stopPrice");
        values.put("symbol", order.symbol());
        values.put("side", order.side().name());
        values.put("previousTerms", order.quantity() + " shares, "
                + describe(order.orderType(), order.limitPrice(), order.stopPrice()));
        values.put("newTerms", invocation.integer("quantity") + " shares, "
                + describe(order.orderType(), limit, stop));
        price(new Orders.Ticket(account.accountId(), order.symbol(), order.side(),
                invocation.integer("quantity"), order.orderType(), limit, stop,
                order.timeInForce(), false), values);
        return values;
    }

    private Map<String, String> cancelling(Invocation invocation, Map<String, String> values) {
        Orders.Order order = brokerage.orders().find(invocation.string("orderId")).orElse(null);
        if (order == null) {
            return values;
        }
        values.put("symbol", order.symbol());
        values.put("side", order.side().name());
        values.put("quantity", String.valueOf(order.quantity()));
        values.put("filledQuantity", String.valueOf(order.filledQuantity()));
        values.put("orderTypeDescription",
                describe(order.orderType(), order.limitPrice(), order.stopPrice()));
        return values;
    }

    /** Re-price the order now, and say plainly if it would no longer be accepted. */
    private void price(Orders.Ticket ticket, Map<String, String> values) {
        RiskEngine.Preview preview = brokerage.risk().preview(ticket);
        values.put("estimatedCost", Money.string(preview.estimate().estimatedCost()));
        values.put("buyingPowerAfter", Money.string(preview.estimate().buyingPowerAfter()));
        if (!preview.canPlace()) {
            values.put("warnings", "This order would now be refused: "
                    + preview.blockers().getFirst().message());
            return;
        }
        List<String> warnings = preview.warnings();
        values.put("warnings", warnings.isEmpty() ? "" : "Note: " + String.join(" ", warnings));
    }

    private static String describe(Orders.OrderType type, BigDecimal limit, BigDecimal stop) {
        return switch (type) {
            case MARKET -> "at market";
            case LIMIT -> "limit " + Money.price(limit);
            case STOP -> "stop " + Money.price(stop);
            case STOP_LIMIT -> "stop " + Money.price(stop) + " limit " + Money.price(limit);
        };
    }

    private static BigDecimal decimal(Invocation invocation, String field) {
        String raw = invocation.string(field);
        return raw == null ? null : Money.of(raw);
    }
}
