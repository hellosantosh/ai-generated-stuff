package com.example.brokerage.catalog.handlers;

import java.time.Instant;
import java.util.List;

import com.example.brokerage.domain.Accounts;
import com.example.brokerage.domain.Brokerage;
import com.example.brokerage.domain.ConfirmationTokens;
import com.example.brokerage.domain.Money;
import com.example.brokerage.domain.Orders;
import com.example.brokerage.domain.RiskEngine;
import com.example.brokerage.tooling.contract.Invocation;
import com.example.brokerage.tooling.contract.ToolFailure;
import com.example.brokerage.tooling.contract.ToolHandler;

import org.springframework.stereotype.Component;

/**
 * {@code brokerage_order_preview}. The most important tool in the catalog.
 *
 * <p>Everything safe about this catalog is built on one property of this handler: it issues a
 * {@code confirmationToken}, and nothing else does. The token is bound to the ticket's fingerprint
 * and to the session, is single-use, and expires in two minutes. Because
 * {@code brokerage_order_place} and {@code brokerage_order_replace} require one, an agent
 * physically cannot trade without first pricing the trade, and cannot trade terms that differ by
 * so much as a cent from the ones the customer was shown.
 *
 * <p>Note the token is only issued when {@code canPlace} is true. A refused order has nothing to
 * approve, and handing back a token "just in case" would let an agent carry authority past the
 * reason it was refused.
 *
 * <p>Note also that this handler is read-only. It is a tier 1 tool rather than tier 0 because it
 * reaches the risk and margin engines and because minting authority is not nothing, but it changes
 * no state a customer can observe, which is why the descriptor can honestly tell the model to call
 * it as often as it likes while the customer adjusts the order.
 */
@Component
public class OrderPreviewHandler implements ToolHandler {

    record BlockerView(String code, String message, Long maxPermissibleQuantity) {
    }

    record EstimateView(String referencePrice, String estimatedPrincipal, String commission,
                        String estimatedCost, String buyingPowerBefore, String buyingPowerAfter,
                        String currency, boolean isEstimate) {
    }

    record Payload(boolean canPlace, List<BlockerView> blockers, List<String> warnings,
                   EstimateView estimate, String confirmationToken, Instant expiresAt,
                   String summary, String session, Instant asOf) {
    }

    private final Brokerage brokerage;
    private final AccountGuard guard;
    private final Tickets tickets;

    public OrderPreviewHandler(Brokerage brokerage, AccountGuard guard, Tickets tickets) {
        this.brokerage = brokerage;
        this.guard = guard;
        this.tickets = tickets;
    }

    @Override
    public String name() {
        return "brokerage_order_preview";
    }

    @Override
    public Object handle(Invocation invocation) {
        Accounts.Account account = guard.requireTradeable(invocation);
        Orders.Ticket ticket = tickets.from(invocation, account.accountId());
        String replaces = invocation.string("replacesOrderId");
        if (replaces != null) {
            ticket = asAmendmentOf(requireWorking(account, replaces), ticket);
        }
        RiskEngine.Preview preview = brokerage.risk().preview(ticket);

        ConfirmationTokens.Issued issued = null;
        if (preview.canPlace()) {
            issued = brokerage.confirmations().issue(fingerprintOf(ticket, replaces),
                    invocation.principal().sessionId());
        }
        return new Payload(preview.canPlace(),
                preview.blockers().stream().map(OrderPreviewHandler::view).toList(),
                preview.warnings(), view(preview.estimate()),
                issued == null ? null : issued.token(),
                issued == null ? null : issued.expiresAt(),
                preview.summary(), brokerage.clock().session().name(), brokerage.clock().now());
    }

    /**
     * The fingerprint a replacement's token is bound to includes the order being replaced, so a
     * token minted for "move ord_A to 500" cannot be spent on ord_B.
     */
    static Orders.Ticket fingerprintOf(Orders.Ticket ticket, String replacesOrderId) {
        if (replacesOrderId == null) {
            return ticket;
        }
        return new Orders.Ticket(ticket.accountId() + "/" + replacesOrderId, ticket.symbol(),
                ticket.side(), ticket.quantity(), ticket.orderType(), ticket.limitPrice(),
                ticket.stopPrice(), ticket.timeInForce(), ticket.extendedHours());
    }

    private Orders.Order requireWorking(Accounts.Account account, String orderId) {
        Orders.Order order = brokerage.orders().find(orderId)
                .filter(found -> found.accountId().equals(account.accountId()))
                .orElseThrow(() -> ToolFailure.notFound("Order " + orderId,
                        "Call brokerage_orders_list for this account and use an orderId from it."));
        if (order.status().terminal()) {
            throw ToolFailure.precondition(
                    "Order " + orderId + " is already " + order.status()
                            + " and cannot be changed.",
                    "Tell the customer the order has finished, and read it back to them with "
                            + "brokerage_order_get if they want the details.");
        }
        return order;
    }

    /**
     * Rebuild the ticket as an amendment of a live order.
     *
     * <p>An amendment can change quantity, limit price and stop price. It cannot change side,
     * order type or time in force; those are properties of the order and a request to change one
     * is a request to cancel and re-enter. So they are taken from the order rather than from the
     * arguments, and a conflicting argument is an error rather than a silent reinterpretation.
     *
     * <p>This is also what keeps the fingerprint honest. {@code brokerage_order_replace} builds
     * its ticket from the order's own attributes, so if this handler took the time in force from
     * the arguments instead, a preview of a good-till-canceled order would mint a token the
     * replace tool could never spend — and the failure would surface as "the order does not match
     * the one that was confirmed", which is true and completely unhelpful.
     */
    private static Orders.Ticket asAmendmentOf(Orders.Order order, Orders.Ticket requested) {
        conflict("side", order.side().name(), requested.side().name());
        conflict("orderType", order.orderType().name(), requested.orderType().name());
        return new Orders.Ticket(requested.accountId(), order.symbol(), order.side(),
                requested.quantity(), order.orderType(), requested.limitPrice(),
                requested.stopPrice(), order.timeInForce(), requested.extendedHours());
    }

    private static void conflict(String field, String onTheOrder, String requested) {
        if (!onTheOrder.equals(requested)) {
            throw ToolFailure.invalidArgument(
                    "That order's " + field + " is " + onTheOrder + " and an amendment cannot "
                            + "change it to " + requested + ".",
                    "Cancel the order with brokerage_order_cancel and place a new one, if that is "
                            + "what the customer wants. Tell them that is what it takes.");
        }
    }

    private static BlockerView view(RiskEngine.Blocker blocker) {
        return new BlockerView(blocker.code(), blocker.message(),
                blocker.maxPermissibleQuantity());
    }

    private static EstimateView view(RiskEngine.Estimate estimate) {
        return new EstimateView(Money.price(estimate.referencePrice()),
                Money.string(estimate.estimatedPrincipal()), Money.string(estimate.commission()),
                Money.string(estimate.estimatedCost()), Money.string(estimate.buyingPowerBefore()),
                Money.string(estimate.buyingPowerAfter()), estimate.currency(),
                estimate.isEstimate());
    }
}
