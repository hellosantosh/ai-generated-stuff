package com.example.brokerage.catalog.handlers;

import java.time.Instant;
import java.util.List;
import java.util.function.Predicate;

import com.example.brokerage.domain.Accounts;
import com.example.brokerage.domain.Brokerage;
import com.example.brokerage.domain.Money;
import com.example.brokerage.domain.Orders;
import com.example.brokerage.tooling.contract.Invocation;
import com.example.brokerage.tooling.contract.ToolHandler;

import org.springframework.stereotype.Component;

/**
 * {@code brokerage_orders_list}.
 *
 * <p>The default for {@code state} is {@code WORKING}, and the default is the design. "Do I have
 * any orders open" is the question nine times in ten, and a tool whose default answer is six
 * months of history makes the model pay for the other one and then summarize the wrong thing.
 * Defaults in a tool schema are not conveniences; they are the behavior you get.
 *
 * <p>Pagination is an opaque forward cursor, never a page number. The order book moves while the
 * agent reads it: page two of an offset-paginated list can repeat a row or skip one, and the agent
 * will report whatever it was handed. A cursor that encodes a position in a stable ordering cannot
 * do that.
 */
@Component
public class OrdersListHandler implements ToolHandler {

    record OrderView(String orderId, String symbol, String side, long quantity,
                     long filledQuantity, String orderType, String limitPrice, String stopPrice,
                     String timeInForce, String status, String averageFillPrice,
                     String rejectReason, Instant placedAt, Instant updatedAt) {
    }

    record Payload(String accountId, List<OrderView> orders, String nextCursor, Instant asOf) {
    }

    private final Brokerage brokerage;
    private final AccountGuard guard;

    public OrdersListHandler(Brokerage brokerage, AccountGuard guard) {
        this.brokerage = brokerage;
        this.guard = guard;
    }

    @Override
    public String name() {
        return "brokerage_orders_list";
    }

    @Override
    public Object handle(Invocation invocation) {
        Accounts.Account account = guard.require(invocation);
        String state = invocation.string("state", "WORKING");
        String symbol = invocation.string("symbol");
        int limit = invocation.integer("limit", 20);

        Predicate<Orders.Order> byState = switch (state) {
            case "TERMINAL" -> order -> order.status().terminal();
            case "ALL" -> order -> true;
            default -> order -> order.status().working();
        };
        Predicate<Orders.Order> bySymbol = order -> symbol == null || order.symbol().equals(symbol);
        List<Orders.Order> all = brokerage.orders().of(account.accountId(), byState.and(bySymbol));

        int from = Cursors.offset(invocation.string("cursor"));
        List<Orders.Order> page = all.stream().skip(from).limit(limit).toList();
        String next = from + page.size() < all.size()
                ? Cursors.encode(from + page.size()) : null;
        return new Payload(account.accountId(),
                page.stream().map(OrdersListHandler::view).toList(), next,
                brokerage.clock().now());
    }

    static OrderView view(Orders.Order order) {
        return new OrderView(order.orderId(), order.symbol(), order.side().name(),
                order.quantity(), order.filledQuantity(), order.orderType().name(),
                order.limitPrice() == null ? null : Money.price(order.limitPrice()),
                order.stopPrice() == null ? null : Money.price(order.stopPrice()),
                order.timeInForce().name(), order.status().name(),
                order.averageFillPrice() == null ? null : Money.price(order.averageFillPrice()),
                order.rejectReason(), order.placedAt(), order.updatedAt());
    }
}
