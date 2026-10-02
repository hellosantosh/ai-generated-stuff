package com.example.brokerage.catalog.handlers;

import java.math.BigDecimal;
import java.time.Instant;

import com.example.brokerage.domain.Accounts;
import com.example.brokerage.domain.Brokerage;
import com.example.brokerage.domain.ConfirmationTokens;
import com.example.brokerage.domain.Money;
import com.example.brokerage.domain.Orders;
import com.example.brokerage.tooling.contract.ErrorCode;
import com.example.brokerage.tooling.contract.Invocation;
import com.example.brokerage.tooling.contract.ToolFailure;
import com.example.brokerage.tooling.contract.ToolHandler;

import org.springframework.stereotype.Component;

/**
 * {@code brokerage_order_replace}.
 *
 * <p>Tier 3, the same as placing, and the reason is worth saying to anyone who objects. Raising a
 * limit from 495 to 512 and buying at 512 are the same economic act; the customer's exposure
 * changes by exactly the same amount either way. A catalog that treats amendment as a lesser
 * operation has given the agent a cheaper route to the same outcome, and cheaper routes get used.
 *
 * <p>The {@code replaced: false} path is the one that needs care. An order can fill between the
 * customer agreeing to a change and the venue acting on it. When that happens nothing was amended,
 * the original terms are what executed, and the honest answer is a successful call reporting that.
 * Returning an error here would be wrong twice over: nothing failed, and an agent that sees an
 * error will retry.
 */
@Component
public class OrderReplaceHandler implements ToolHandler {

    record Payload(boolean replaced, String orderId, String symbol, String side, long quantity,
                   long filledQuantity, String limitPrice, String stopPrice, String status,
                   String explanation, Instant updatedAt) {
    }

    private final Brokerage brokerage;
    private final AccountGuard guard;

    public OrderReplaceHandler(Brokerage brokerage, AccountGuard guard) {
        this.brokerage = brokerage;
        this.guard = guard;
    }

    @Override
    public String name() {
        return "brokerage_order_replace";
    }

    @Override
    public Object handle(Invocation invocation) {
        Accounts.Account account = guard.requireTradeable(invocation);
        String orderId = invocation.string("orderId");
        Orders.Order order = brokerage.orders().find(orderId)
                .filter(found -> found.accountId().equals(account.accountId()))
                .orElseThrow(() -> ToolFailure.notFound("Order " + orderId,
                        "Call brokerage_orders_list for this account and use an orderId from it."));

        long quantity = invocation.integer("quantity");
        BigDecimal limit = Tickets.decimal(invocation, "limitPrice");
        BigDecimal stop = Tickets.decimal(invocation, "stopPrice");
        String token = invocation.string("confirmationToken");

        Orders.Ticket proposed = OrderPreviewHandler.fingerprintOf(
                new Orders.Ticket(account.accountId(), order.symbol(), order.side(), quantity,
                        order.orderType(), limit, stop, order.timeInForce(), false),
                orderId);
        ConfirmationTokens.Redemption redemption = brokerage.confirmations()
                .redeem(token, proposed, invocation.principal().sessionId());
        if (redemption instanceof ConfirmationTokens.Redemption.Refused refused) {
            throw ToolFailure.of(
                    refused.rejection() == ConfirmationTokens.Rejection.CHANGED
                            ? ErrorCode.INVALID_ARGUMENT : ErrorCode.CONFIRMATION_EXPIRED,
                    refused.rejection().explanation(),
                    "Call brokerage_order_preview with replacesOrderId set to " + orderId
                            + " and the terms the customer wants, show them the estimate, and try "
                            + "again only if they agree.");
        }

        if (order.status().terminal()) {
            return new Payload(false, order.orderId(), order.symbol(), order.side().name(),
                    order.quantity(), order.filledQuantity(),
                    order.limitPrice() == null ? null : Money.price(order.limitPrice()),
                    order.stopPrice() == null ? null : Money.price(order.stopPrice()),
                    order.status().name(),
                    "The order was already " + order.status() + " before the change reached the "
                            + "venue, so nothing was amended and the original terms are what "
                            + "happened. Tell the customer that, and read the order back to them "
                            + "if they want the detail.",
                    order.updatedAt());
        }
        if (quantity < order.filledQuantity()) {
            throw ToolFailure.invalidArgument(
                    "The order has already filled " + order.filledQuantity()
                            + " shares, so it cannot be reduced to " + quantity + ".",
                    "Offer the customer a quantity of at least " + order.filledQuantity()
                            + ", or cancel the remainder with brokerage_order_cancel.");
        }

        Orders.Order updated = brokerage.orders()
                .replace(orderId, quantity, limit, stop)
                .orElseThrow(() -> ToolFailure.of(ErrorCode.CONFLICT,
                        "The order changed while the amendment was in flight.",
                        "Call brokerage_order_get for " + orderId + " to see where it stands, and "
                                + "tell the customer before trying anything else."));
        return new Payload(true, updated.orderId(), updated.symbol(), updated.side().name(),
                updated.quantity(), updated.filledQuantity(),
                updated.limitPrice() == null ? null : Money.price(updated.limitPrice()),
                updated.stopPrice() == null ? null : Money.price(updated.stopPrice()),
                updated.status().name(),
                "The order was amended and keeps the identifier " + updated.orderId() + ".",
                updated.updatedAt());
    }
}
