package com.example.brokerage.catalog.handlers;

import java.time.Instant;
import java.time.LocalDate;
import java.util.List;

import com.example.brokerage.domain.Accounts;
import com.example.brokerage.domain.Brokerage;
import com.example.brokerage.domain.Money;
import com.example.brokerage.domain.Orders;
import com.example.brokerage.tooling.contract.Invocation;
import com.example.brokerage.tooling.contract.ToolFailure;
import com.example.brokerage.tooling.contract.ToolHandler;

import org.springframework.stereotype.Component;

/**
 * {@code brokerage_order_get}.
 *
 * <p>Returns the fills as well as the order, because {@code averageFillPrice} is a summary and a
 * customer asking "what did I pay" for a 10,000-share order filled in eleven pieces is asking
 * about the pieces. Both are in the payload; the descriptor says which answers which question.
 */
@Component
public class OrderGetHandler implements ToolHandler {

    record ExecutionView(String executionId, long quantity, String price, Instant executedAt,
                         LocalDate settlesOn, String venue) {
    }

    record Payload(String orderId, String accountId, String symbol, String side, long quantity,
                   long filledQuantity, String orderType, String limitPrice, String stopPrice,
                   String timeInForce, String status, String averageFillPrice, String commission,
                   String rejectReason, Instant placedAt, Instant updatedAt,
                   List<ExecutionView> executions) {
    }

    private final Brokerage brokerage;
    private final AccountGuard guard;

    public OrderGetHandler(Brokerage brokerage, AccountGuard guard) {
        this.brokerage = brokerage;
        this.guard = guard;
    }

    @Override
    public String name() {
        return "brokerage_order_get";
    }

    @Override
    public Object handle(Invocation invocation) {
        Accounts.Account account = guard.require(invocation);
        String orderId = invocation.string("orderId");
        Orders.Order order = brokerage.orders().find(orderId)
                .filter(found -> found.accountId().equals(account.accountId()))
                .orElseThrow(() -> ToolFailure.notFound("Order " + orderId + " in " + account.nickname(),
                        "Call brokerage_orders_list with state ALL for this account to find the "
                                + "order, and tell the customer if it is not there."));
        List<ExecutionView> fills = brokerage.orders()
                .executionsOf(account.accountId(), order.symbol(), null).stream()
                .filter(execution -> execution.orderId().equals(order.orderId()))
                .map(OrderGetHandler::view)
                .toList();
        return new Payload(order.orderId(), order.accountId(), order.symbol(),
                order.side().name(), order.quantity(), order.filledQuantity(),
                order.orderType().name(),
                order.limitPrice() == null ? null : Money.price(order.limitPrice()),
                order.stopPrice() == null ? null : Money.price(order.stopPrice()),
                order.timeInForce().name(), order.status().name(),
                order.averageFillPrice() == null ? null : Money.price(order.averageFillPrice()),
                Money.string(order.commission()), order.rejectReason(), order.placedAt(),
                order.updatedAt(), fills);
    }

    private static ExecutionView view(Orders.Execution execution) {
        return new ExecutionView(execution.executionId(), execution.quantity(),
                Money.price(execution.price()), execution.executedAt(), execution.settlesOn(),
                execution.venue());
    }
}
