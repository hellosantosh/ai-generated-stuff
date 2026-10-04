package com.example.brokerage.domain;

import java.math.BigDecimal;
import java.time.Instant;
import java.time.LocalDate;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Optional;

import org.springframework.stereotype.Component;

/**
 * The order book: tickets, orders, state transitions and fills.
 *
 * <p>Two things in here exist only because an agent is the caller.
 *
 * <p>The first is the idempotency store. An agent retries. It retries because a socket closed,
 * because a runtime timed out, because the model decided the first answer looked incomplete. A
 * REST API that double-books an order under retry is a bug; a tool that double-books one is a
 * customer complaint and a regulatory finding. So {@link #place} is keyed, and a repeat of the
 * same key returns the original order rather than a second one.
 *
 * <p>The second is that cancellation is a <em>request</em>, not a result. A working order may fill
 * in the moment between the agent deciding to cancel it and the venue hearing about it, and an
 * agent told "cancelled" when the order actually filled will tell the customer something false.
 * {@link #cancel} returns the order's real state and says whether the request was accepted.
 */
@Component
public class Orders {

    public enum Side { BUY, SELL, SELL_SHORT, BUY_TO_COVER }

    public enum OrderType { MARKET, LIMIT, STOP, STOP_LIMIT }

    public enum TimeInForce { DAY, GTC, IOC, FOK }

    /**
     * Order status. {@code CANCELLED} keeps its double-L spelling because that is how it is
     * written in the systems of record this book's domain borrows from, and an enumeration member
     * is a name rather than prose: renaming it to match the surrounding US English would break
     * every consumer that already matches on the string.
     */
    public enum Status {
        PENDING_NEW, WORKING, PARTIALLY_FILLED, FILLED, CANCELLED, REJECTED, EXPIRED;

        public boolean working() {
            return this == PENDING_NEW || this == WORKING || this == PARTIALLY_FILLED;
        }

        public boolean terminal() {
            return !working();
        }
    }

    /** What the customer asked for. Separate from the order so a preview can exist without one. */
    public record Ticket(String accountId, String symbol, Side side, long quantity,
                         OrderType orderType, BigDecimal limitPrice, BigDecimal stopPrice,
                         TimeInForce timeInForce, boolean extendedHours) {

        /** The fingerprint a confirmation token is bound to. Change anything, preview again. */
        public String fingerprint() {
            return String.join("|", accountId, symbol, side.name(), String.valueOf(quantity),
                    orderType.name(), String.valueOf(limitPrice), String.valueOf(stopPrice),
                    timeInForce.name(), String.valueOf(extendedHours));
        }

        public String describe() {
            String price = switch (orderType) {
                case MARKET -> "at market";
                case LIMIT -> "limit " + Money.price(limitPrice);
                case STOP -> "stop " + Money.price(stopPrice);
                case STOP_LIMIT -> "stop " + Money.price(stopPrice)
                        + " limit " + Money.price(limitPrice);
            };
            return "%s %d %s %s, %s".formatted(side, quantity, symbol, price, timeInForce);
        }
    }

    public record Order(String orderId, String accountId, String symbol, Side side, long quantity,
                        long filledQuantity, OrderType orderType, BigDecimal limitPrice,
                        BigDecimal stopPrice, TimeInForce timeInForce, Status status,
                        BigDecimal averageFillPrice, BigDecimal commission, String rejectReason,
                        Instant placedAt, Instant updatedAt, String idempotencyKey) {

        public long remainingQuantity() {
            return quantity - filledQuantity;
        }
    }

    public record Execution(String executionId, String orderId, String accountId, String symbol,
                            Side side, long quantity, BigDecimal price, BigDecimal commission,
                            Instant executedAt, LocalDate settlesOn, String venue) {
    }

    /** The outcome of a cancellation request, which is not the same thing as a cancellation. */
    public record CancelOutcome(boolean accepted, Order order, String explanation) {
    }

    private final Map<String, Order> orders = new LinkedHashMap<>();
    private final List<Execution> executions = new ArrayList<>();
    private final Map<String, String> byIdempotencyKey = new LinkedHashMap<>();
    private final MarketClock clock;
    private final MarketData marketData;

    public Orders(MarketClock clock, MarketData marketData) {
        this.clock = clock;
        this.marketData = marketData;
    }

    public void seed(String accountId) {
        Instant now = clock.now();
        record(new Order(Ids.seededOrder(0), accountId, "MSFT", Side.BUY, 25, 0, OrderType.LIMIT,
                Money.of("495.00"), null, TimeInForce.GTC, Status.WORKING, null,
                Money.of("0.00"), null, now.minusSeconds(7200), now.minusSeconds(7200), null));
        record(new Order(Ids.seededOrder(1), accountId, "TSLA", Side.BUY, 10, 4, OrderType.LIMIT,
                Money.of("405.00"), null, TimeInForce.DAY, Status.PARTIALLY_FILLED,
                Money.of("404.88"), Money.of("0.00"), null, now.minusSeconds(3600),
                now.minusSeconds(900), null));
        Order filled = new Order(Ids.seededOrder(2), accountId, "NVDA", Side.BUY, 100, 100,
                OrderType.MARKET, null, null, TimeInForce.DAY, Status.FILLED,
                Money.of("181.44"), Money.of("0.00"), null, now.minusSeconds(86_400),
                now.minusSeconds(86_300), null);
        record(filled);
        executions.add(new Execution(Ids.seeded("exec_", 0, 16), filled.orderId(), accountId, "NVDA",
                Side.BUY, 100, Money.of("181.44"), Money.of("0.00"), now.minusSeconds(86_300),
                settlement(now.minusSeconds(86_300)), "NASDAQ"));
    }

    private void record(Order order) {
        orders.put(order.orderId(), order);
    }

    public Optional<Order> find(String orderId) {
        return Optional.ofNullable(orders.get(orderId));
    }

    /**
     * The order a given idempotency key already produced, if any. This is what lets a retried
     * place be answered with "here is your order" instead of "that token is spent".
     */
    public Optional<Order> byIdempotencyKey(String key) {
        return Optional.ofNullable(byIdempotencyKey.get(key)).map(orders::get);
    }

    public List<Order> of(String accountId, java.util.function.Predicate<Order> filter) {
        return orders.values().stream()
                .filter(order -> order.accountId().equals(accountId))
                .filter(filter)
                .sorted(Comparator.comparing(Order::placedAt).reversed())
                .toList();
    }

    public List<Execution> executionsOf(String accountId, String symbol, Instant since) {
        return executions.stream()
                .filter(execution -> execution.accountId().equals(accountId))
                .filter(execution -> symbol == null || execution.symbol().equals(symbol))
                .filter(execution -> since == null || !execution.executedAt().isBefore(since))
                .sorted(Comparator.comparing(Execution::executedAt).reversed())
                .toList();
    }

    public BigDecimal cashReservedFor(String accountId) {
        return of(accountId, order -> order.status().working()).stream()
                .filter(order -> order.side() == Side.BUY)
                .map(order -> Money.times(
                        order.limitPrice() != null ? order.limitPrice()
                                : marketData.quote(order.symbol()).ask(),
                        order.remainingQuantity()))
                .reduce(BigDecimal.ZERO, BigDecimal::add);
    }

    /**
     * Place an order, once per idempotency key. The key is minted by the runtime from the
     * confirmation token, never by the model: a model asked to supply a unique key will
     * occasionally supply the same one twice, or a different one on a retry, and either way the
     * guarantee is gone.
     */
    public Order place(Ticket ticket, String idempotencyKey) {
        String existing = byIdempotencyKey.get(idempotencyKey);
        if (existing != null) {
            return orders.get(existing);
        }
        Instant now = clock.now();
        boolean fillsNow = ticket.orderType() == OrderType.MARKET
                && clock.session() == MarketClock.Session.REGULAR;
        String orderId = Ids.order();
        Order order;
        if (fillsNow) {
            MarketData.Quote quote = marketData.quote(ticket.symbol());
            BigDecimal fill = ticket.side() == Side.BUY || ticket.side() == Side.BUY_TO_COVER
                    ? quote.ask() : quote.bid();
            order = new Order(orderId, ticket.accountId(), ticket.symbol(), ticket.side(),
                    ticket.quantity(), ticket.quantity(), ticket.orderType(), ticket.limitPrice(),
                    ticket.stopPrice(), ticket.timeInForce(), Status.FILLED, Money.cents(fill),
                    Money.of("0.00"), null, now, now, idempotencyKey);
            executions.add(new Execution(Ids.execution(), orderId, ticket.accountId(),
                    ticket.symbol(), ticket.side(), ticket.quantity(), Money.cents(fill),
                    Money.of("0.00"), now, settlement(now), "NASDAQ"));
        } else {
            order = new Order(orderId, ticket.accountId(), ticket.symbol(), ticket.side(),
                    ticket.quantity(), 0, ticket.orderType(), ticket.limitPrice(),
                    ticket.stopPrice(), ticket.timeInForce(), Status.WORKING, null,
                    Money.of("0.00"), null, now, now, idempotencyKey);
        }
        record(order);
        byIdempotencyKey.put(idempotencyKey, orderId);
        return order;
    }

    /**
     * Replace a working order. The replacement keeps the original identifier, because a customer
     * who was told "order ord_X is in" and then hears about ord_Y has lost the thread, and an
     * agent asked to "move my limit up a dollar" has no business creating a second order.
     */
    public Optional<Order> replace(String orderId, long quantity, BigDecimal limitPrice,
                                   BigDecimal stopPrice) {
        Order order = orders.get(orderId);
        if (order == null || order.status().terminal()) {
            return Optional.empty();
        }
        Order updated = new Order(order.orderId(), order.accountId(), order.symbol(), order.side(),
                quantity, order.filledQuantity(), order.orderType(), limitPrice, stopPrice,
                order.timeInForce(), order.status(), order.averageFillPrice(), order.commission(),
                null, order.placedAt(), clock.now(), order.idempotencyKey());
        record(updated);
        return Optional.of(updated);
    }

    public CancelOutcome cancel(String orderId) {
        Order order = orders.get(orderId);
        if (order == null) {
            return new CancelOutcome(false, null, "No such order.");
        }
        if (order.status() == Status.FILLED) {
            return new CancelOutcome(false, order,
                    "The order filled in full before the cancellation reached the venue, so there "
                            + "is nothing to cancel. The customer now owns the shares.");
        }
        if (order.status().terminal()) {
            return new CancelOutcome(false, order,
                    "The order was already " + order.status() + "; no action was taken.");
        }
        Order cancelled = new Order(order.orderId(), order.accountId(), order.symbol(),
                order.side(), order.quantity(), order.filledQuantity(), order.orderType(),
                order.limitPrice(), order.stopPrice(), order.timeInForce(), Status.CANCELLED,
                order.averageFillPrice(), order.commission(), null, order.placedAt(), clock.now(),
                order.idempotencyKey());
        record(cancelled);
        String partial = order.filledQuantity() > 0
                ? " " + order.filledQuantity() + " of " + order.quantity()
                        + " shares had already filled and those remain."
                : "";
        return new CancelOutcome(true, cancelled, "Cancellation accepted." + partial);
    }

    /** T+1, the convention since 2024. Weekend-aware, because settlement dates are quoted. */
    private static LocalDate settlement(Instant executedAt) {
        LocalDate date = executedAt.atZone(MarketClock.EXCHANGE).toLocalDate().plusDays(1);
        while (date.getDayOfWeek().getValue() > 5) {
            date = date.plusDays(1);
        }
        return date;
    }
}
