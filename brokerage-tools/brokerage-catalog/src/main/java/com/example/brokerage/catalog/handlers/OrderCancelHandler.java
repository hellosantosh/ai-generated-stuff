package com.example.brokerage.catalog.handlers;

import java.time.Instant;

import com.example.brokerage.domain.Accounts;
import com.example.brokerage.domain.Brokerage;
import com.example.brokerage.domain.Orders;
import com.example.brokerage.tooling.contract.Invocation;
import com.example.brokerage.tooling.contract.ToolFailure;
import com.example.brokerage.tooling.contract.ToolHandler;

import org.springframework.stereotype.Component;

/**
 * {@code brokerage_order_cancel}.
 *
 * <p>Tier 2 and no confirmation token, which is the one asymmetry in this catalog and the one most
 * worth defending. Cancelling reduces exposure. Requiring a priced preview before a customer can
 * stop an order would mean that in the one situation where speed matters — a price running away
 * from them — the system is slowest. A human still approves the call; what it does not need is a
 * round trip through the risk engine to find out what stopping something would cost.
 *
 * <p>The handler's real job is honesty about the race. {@code cancelled: false} with
 * {@code status: FILLED} is a successful call reporting a fill, not a failure, and the explanation
 * says so in words the agent can read to the customer. The failure mode this prevents is specific
 * and bad: an agent that says "I've cancelled that for you" about an order that filled, leaving a
 * customer who believes they are flat and is not.
 */
@Component
public class OrderCancelHandler implements ToolHandler {

    record Payload(boolean cancelled, String orderId, String symbol, String side, long quantity,
                   long filledQuantity, String status, String explanation, Instant updatedAt) {
    }

    private final Brokerage brokerage;
    private final AccountGuard guard;

    public OrderCancelHandler(Brokerage brokerage, AccountGuard guard) {
        this.brokerage = brokerage;
        this.guard = guard;
    }

    @Override
    public String name() {
        return "brokerage_order_cancel";
    }

    @Override
    public Object handle(Invocation invocation) {
        Accounts.Account account = guard.require(invocation);
        String orderId = invocation.string("orderId");
        brokerage.orders().find(orderId)
                .filter(found -> found.accountId().equals(account.accountId()))
                .orElseThrow(() -> ToolFailure.notFound("Order " + orderId,
                        "Call brokerage_orders_list for this account and use an orderId from it."));

        Orders.CancelOutcome outcome = brokerage.orders().cancel(orderId);
        Orders.Order order = outcome.order();
        return new Payload(outcome.accepted(), order.orderId(), order.symbol(),
                order.side().name(), order.quantity(), order.filledQuantity(),
                order.status().name(), outcome.explanation(), order.updatedAt());
    }
}
