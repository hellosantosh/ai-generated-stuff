package com.example.brokerage.catalog.handlers;

import java.time.Instant;

import com.example.brokerage.domain.Accounts;
import com.example.brokerage.domain.Brokerage;
import com.example.brokerage.domain.ConfirmationTokens;
import com.example.brokerage.domain.Money;
import com.example.brokerage.domain.Orders;
import com.example.brokerage.domain.RiskEngine;
import com.example.brokerage.tooling.contract.ErrorCode;
import com.example.brokerage.tooling.contract.Invocation;
import com.example.brokerage.tooling.contract.ToolFailure;
import com.example.brokerage.tooling.contract.ToolHandler;

import org.springframework.stereotype.Component;

/**
 * {@code brokerage_order_place}. The only tool here that spends the customer's money.
 *
 * <p>Read the order of operations, because it is the whole design. The token is redeemed
 * <em>before</em> the order is sent, and redeeming it is what proves that these exact terms were
 * priced and approved. Then the risk check is run again, because two minutes have passed and the
 * market has moved; a preview is a promise about the moment it was taken, not a license.
 *
 * <p>The idempotency key is derived from the token, not supplied by the model. That is a small line
 * with a large consequence. A model asked for a unique key will sometimes reuse one and sometimes
 * invent a new one on a retry, and either failure mode defeats the guarantee. Deriving the key
 * from the single-use token means a retried call is recognizably the same call, and
 * {@code duplicateOfPreviousCall} tells the agent so in a field it cannot misread.
 */
@Component
public class OrderPlaceHandler implements ToolHandler {

    record Payload(String orderId, String accountId, String symbol, String side, long quantity,
                   long filledQuantity, String status, String averageFillPrice, String commission,
                   String rejectReason, boolean duplicateOfPreviousCall, Instant placedAt) {
    }

    private final Brokerage brokerage;
    private final AccountGuard guard;
    private final Tickets tickets;

    public OrderPlaceHandler(Brokerage brokerage, AccountGuard guard, Tickets tickets) {
        this.brokerage = brokerage;
        this.guard = guard;
        this.tickets = tickets;
    }

    @Override
    public String name() {
        return "brokerage_order_place";
    }

    @Override
    public Object handle(Invocation invocation) {
        Accounts.Account account = guard.requireTradeable(invocation);
        Orders.Ticket ticket = tickets.from(invocation, account.accountId());
        String token = invocation.string("confirmationToken");

        ConfirmationTokens.Redemption redemption =
                brokerage.confirmations().redeem(token, ticket, invocation.principal().sessionId());
        if (redemption instanceof ConfirmationTokens.Redemption.Refused refused) {
            if (refused.rejection() == ConfirmationTokens.Rejection.SPENT) {
                Orders.Order original =
                        brokerage.orders().byIdempotencyKey(idempotencyKey(token)).orElse(null);
                if (original != null) {
                    return view(original, true);          // the retry, answered with the first order
                }
            }
            throw refusal(refused.rejection());
        }

        RiskEngine.Preview now = brokerage.risk().preview(ticket);
        if (!now.canPlace()) {
            throw ToolFailure.of(ErrorCode.PRECONDITION_FAILED,
                    "The order can no longer be placed: " + now.blockers().getFirst().message(),
                    "Tell the customer what changed since they approved it, then call "
                            + "brokerage_order_preview again if they still want to trade.");
        }
        return view(brokerage.orders().place(ticket, idempotencyKey(token)), false);
    }

    /** The key the order book is written under, derived from the token rather than from the model. */
    private static String idempotencyKey(String token) {
        return "place:" + token;
    }

    private static ToolFailure refusal(ConfirmationTokens.Rejection rejection) {
        ErrorCode code = switch (rejection) {
            case EXPIRED, SPENT -> ErrorCode.CONFIRMATION_EXPIRED;
            case CHANGED -> ErrorCode.INVALID_ARGUMENT;
            case UNKNOWN, WRONG_PRINCIPAL -> ErrorCode.NOT_ENTITLED;
        };
        String remediation = switch (rejection) {
            case CHANGED -> "Call brokerage_order_preview with the terms the customer actually "
                    + "wants, show them the new estimate, and place that.";
            case EXPIRED, SPENT -> "Call brokerage_order_preview again, read the fresh estimate to "
                    + "the customer, and place the order only if they agree again.";
            case UNKNOWN, WRONG_PRINCIPAL -> "Do not retry. Tell the customer the confirmation "
                    + "could not be verified and start the order again from a preview.";
        };
        return ToolFailure.of(code, rejection.explanation(), remediation);
    }

    private static Payload view(Orders.Order order, boolean duplicate) {
        return new Payload(order.orderId(), order.accountId(), order.symbol(),
                order.side().name(), order.quantity(), order.filledQuantity(),
                order.status().name(),
                order.averageFillPrice() == null ? null : Money.price(order.averageFillPrice()),
                Money.string(order.commission()), order.rejectReason(), duplicate,
                order.placedAt());
    }
}
