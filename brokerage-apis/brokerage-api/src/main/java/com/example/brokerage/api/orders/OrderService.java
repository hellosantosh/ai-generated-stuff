package com.example.brokerage.api.orders;

import java.math.BigDecimal;
import java.math.RoundingMode;
import java.time.Clock;
import java.time.Instant;
import java.util.ArrayList;
import java.util.Collection;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.concurrent.atomic.AtomicInteger;

import com.example.brokerage.api.accounts.Account;
import com.example.brokerage.api.accounts.Holdings;
import com.example.brokerage.api.accounts.Ledger;
import com.example.brokerage.api.accounts.Reservations;
import com.example.brokerage.api.market.Instrument;
import com.example.brokerage.api.market.MarketData;
import com.example.brokerage.api.orders.Order.Side;
import com.example.brokerage.api.orders.Order.Status;
import com.example.brokerage.api.orders.Order.TimeInForce;
import com.example.brokerage.api.orders.Order.Type;
import com.example.brokerage.api.platform.ApiException;
import com.example.brokerage.api.platform.Caller;
import com.example.brokerage.api.platform.CursorPage;
import com.example.brokerage.api.platform.ProblemType;

import org.springframework.stereotype.Service;

/**
 * Order placement, preview, amendment, cancellation and matching.
 *
 * <p>All state changes are serialized by one lock: simple and obviously correct, which
 * is what an example should be. A real system would partition by account.
 */
@Service
public class OrderService implements Reservations {

    /** Placement result: the order, and whether this was a replay of an earlier request. */
    public record Placement(Order order, boolean replayed) {
    }

    private static final BigDecimal OPTION_COMMISSION = new BigDecimal("0.65");

    private final Map<String, Order> orders = new LinkedHashMap<>();
    private final AtomicInteger orderIds = new AtomicInteger(100_000);
    private final AtomicInteger fillIds = new AtomicInteger(500_000);
    private final Ledger ledger;
    private final MarketData market;
    private final IdempotencyKeys idempotencyKeys;
    private final OrderEvents events;
    private final Clock clock;

    public OrderService(Ledger ledger, MarketData market, IdempotencyKeys idempotencyKeys,
            OrderEvents events, Clock clock) {
        this.ledger = ledger;
        this.market = market;
        this.idempotencyKeys = idempotencyKeys;
        this.events = events;
        this.clock = clock;
        // A resting limit order, so that there is something to change and cancel.
        Instant yesterday = clock.instant().minusSeconds(86_400);
        save(new Order(nextOrderId(), "ACC-1001", "rebalance-msft-01", "EQ-MSFT", "MSFT",
                Instrument.Type.EQUITY, Side.BUY, Type.LIMIT, TimeInForce.GTC, new BigDecimal("10"),
                new BigDecimal("480.00"), null, Status.OPEN, BigDecimal.ZERO, null, List.of(),
                yesterday, yesterday, false, 1));
    }

    // ---------------------------------------------------------------- queries

    public synchronized Order order(Caller caller, String accountId, String orderId) {
        ledger.account(caller.customerId(), accountId);
        Order order = orders.get(orderId);
        if (order == null || !order.accountId().equals(accountId)) {
            throw ApiException.notFound("Order", orderId);
        }
        return order;
    }

    public synchronized CursorPage<Order> orders(Caller caller, String accountId,
            Collection<Status> statuses, String symbol, String cursor, Integer limit) {
        ledger.account(caller.customerId(), accountId);
        List<Order> matching = orders.values().stream()
                .filter(o -> o.accountId().equals(accountId))
                .filter(o -> statuses == null || statuses.isEmpty() || statuses.contains(o.status()))
                .filter(o -> symbol == null || o.symbol().equalsIgnoreCase(symbol))
                .toList();
        return CursorPage.of(matching, Order::createdAt, Order::id, cursor, limit);
    }

    // ---------------------------------------------------------------- commands

    public synchronized Placement place(Caller caller, String accountId, OrderRequest request,
            String idempotencyKey) {
        Account account = ledger.account(caller.customerId(), accountId);
        if (idempotencyKey == null || idempotencyKey.isBlank() || idempotencyKey.length() > 255) {
            throw new ApiException(ProblemType.IDEMPOTENCY_KEY_MISSING,
                    "Send a unique Idempotency-Key header (up to 255 characters) with every new order");
        }
        String scope = caller.clientId() + "|" + accountId;
        String fingerprint = request.fingerprint();
        Optional<String> earlier = idempotencyKeys.existingOrder(scope, idempotencyKey, fingerprint);
        if (earlier.isPresent()) {
            return new Placement(orders.get(earlier.get()), true);
        }

        Assessment assessment = assess(caller, account, request);
        assessment.issues().stream().findFirst().ifPresent(issue -> {
            throw issue;
        });

        Instant now = clock.instant();
        Instrument instrument = assessment.instrument();
        Order order = new Order(nextOrderId(), accountId, request.getClientOrderId(), instrument.id(),
                instrument.symbol(), instrument.type(), request.getSide(), request.getType(),
                request.timeInForceOrDefault(), request.getQuantity(), request.getLimitPrice(),
                request.getStopPrice(), Status.OPEN, BigDecimal.ZERO, null, List.of(), now, now,
                false, 1);
        save(order);
        idempotencyKeys.remember(scope, idempotencyKey, fingerprint, order.id());
        events.publish("order.created", order);
        return new Placement(order, false);
    }

    public synchronized OrderPreview preview(Caller caller, String accountId, OrderRequest request) {
        Account account = ledger.account(caller.customerId(), accountId);
        Assessment assessment = assess(caller, account, request);
        List<OrderPreview.Issue> issues = assessment.issues().stream()
                .map(issue -> new OrderPreview.Issue(issue.type().uri(), issue.getMessage()))
                .toList();
        Instrument instrument = assessment.instrument();
        return new OrderPreview(accountId, request.getInstrumentId(),
                instrument == null ? null : instrument.symbol(), request.getSide(), request.getType(),
                request.getQuantity(), assessment.price(), assessment.notional(), assessment.commission(),
                assessment.total(), assessment.buyingPower(), issues.isEmpty(), issues);
    }

    /**
     * Changes a working order. The If-Match header must carry the order's current ETag:
     * two clients editing the same order cannot silently overwrite one another.
     */
    public synchronized Order amend(Caller caller, String accountId, String orderId,
            OrderAmendment change, String ifMatch) {
        Order order = order(caller, accountId, orderId);
        if (ifMatch == null) {
            throw new ApiException(ProblemType.PRECONDITION_REQUIRED,
                    "Send If-Match with the order's current ETag to change it");
        }
        if (!ifMatch.equals(order.etag()) && !ifMatch.equals("*")) {
            throw new ApiException(ProblemType.PRECONDITION_FAILED,
                    "The order has changed since you read it; fetch it again and reapply your change")
                    .with("currentEtag", order.etag());
        }
        if (!order.status().isWorking()) {
            throw new ApiException(ProblemType.ORDER_NOT_REPLACEABLE,
                    "Order " + orderId + " is " + order.status() + " and can no longer be changed");
        }
        BigDecimal quantity = orElse(change.getQuantity(), order.quantity());
        if (quantity.compareTo(order.filledQuantity()) <= 0) {
            throw new ApiException(ProblemType.INVALID_REQUEST, "quantity must be greater than the "
                    + order.filledQuantity().toPlainString() + " already filled");
        }
        OrderRequest amended = new OrderRequest(order.instrumentId(), order.side(), order.type(),
                orElse(change.getTimeInForce(), order.timeInForce()), quantity,
                orElse(change.getLimitPrice(), order.limitPrice()),
                orElse(change.getStopPrice(), order.stopPrice()), order.clientOrderId());

        Account account = ledger.account(caller.customerId(), accountId);
        orders.remove(orderId); // assess the new terms without this order's own reservation
        try {
            assess(caller, account, amended).issues().stream().findFirst().ifPresent(issue -> {
                throw issue;
            });
        } finally {
            orders.put(orderId, order);
        }
        Order updated = order.withTerms(amended.getQuantity(), amended.getLimitPrice(),
                amended.getStopPrice(), amended.getTimeInForce(), clock.instant());
        save(updated);
        events.publish("order.replaced", updated);
        return updated;
    }

    /**
     * Requests cancellation. Like a real exchange, the cancel is asynchronous: the order
     * moves to PENDING_CANCEL now and to CANCELLED at the next matching pass, and it may
     * still fill in between.
     */
    public synchronized Order cancel(Caller caller, String accountId, String orderId) {
        Order order = order(caller, accountId, orderId);
        if (order.status() == Status.PENDING_CANCEL) {
            return order;
        }
        if (!order.status().isWorking()) {
            throw new ApiException(ProblemType.ORDER_NOT_CANCELLABLE,
                    "Order " + orderId + " is " + order.status() + " and cannot be canceled");
        }
        Order pending = order.withStatus(Status.PENDING_CANCEL, clock.instant());
        save(pending);
        events.publish("order.cancel-requested", pending);
        return pending;
    }

    // ---------------------------------------------------------------- matching

    /** One pass of the simulated exchange over every open order. */
    public synchronized void match() {
        for (Order order : List.copyOf(orders.values())) {
            if (order.status() == Status.PENDING_CANCEL) {
                Order cancelled = order.withStatus(Status.CANCELLED, clock.instant());
                save(cancelled);
                events.publish("order.cancelled", cancelled);
            } else if (order.status().isWorking()) {
                tryToFill(order);
            }
        }
    }

    private void tryToFill(Order order) {
        Instrument instrument = market.instrument(order.instrumentId());
        MarketData.Tick tick = market.tick(instrument.id());
        boolean buy = order.side() == Side.BUY;
        Order current = order;

        if ((order.type() == Type.STOP || order.type() == Type.STOP_LIMIT) && !order.stopTriggered()) {
            boolean triggered = buy ? tick.last().compareTo(order.stopPrice()) >= 0
                    : tick.last().compareTo(order.stopPrice()) <= 0;
            if (!triggered) {
                return;
            }
            current = current.withStopTriggered();
            save(current);
        }

        BigDecimal price = buy ? tick.ask() : tick.bid();
        if (order.type() == Type.LIMIT || order.type() == Type.STOP_LIMIT) {
            boolean marketable = buy ? price.compareTo(order.limitPrice()) <= 0
                    : price.compareTo(order.limitPrice()) >= 0;
            if (!marketable) {
                return;
            }
        }

        BigDecimal displayed = buy ? tick.askSize() : tick.bidSize();
        BigDecimal quantity = current.remainingQuantity().min(displayed);
        Instant now = clock.instant();
        ledger.recordTrade(order.accountId(), instrument, buy ? quantity : quantity.negate(), price,
                commission(instrument, quantity), order.id(), now);
        String fillId = "FIL-" + fillIds.incrementAndGet();
        Order filled = current.withFill(new Order.Fill(fillId, quantity, price, now));
        save(filled);
        boolean complete = filled.status() == Status.FILLED;
        events.publish(complete ? "order.filled" : "order.partially-filled", filled);
    }

    // ---------------------------------------------------------------- reservations

    @Override
    public synchronized BigDecimal reservedCash(String accountId) {
        return orders.values().stream()
                .filter(o -> o.accountId().equals(accountId))
                .filter(o -> o.side() == Side.BUY && o.status().isOpen())
                .map(o -> {
                    Instrument instrument = market.instrument(o.instrumentId());
                    BigDecimal price = referencePrice(o.type(), Side.BUY, o.limitPrice(),
                            o.stopPrice(), instrument);
                    return price.multiply(o.remainingQuantity()).multiply(instrument.multiplier())
                            .add(commission(instrument, o.remainingQuantity()));
                })
                .reduce(BigDecimal.ZERO, BigDecimal::add)
                .setScale(2, RoundingMode.HALF_EVEN);
    }

    @Override
    public synchronized BigDecimal reservedQuantity(String accountId, String instrumentId) {
        return orders.values().stream()
                .filter(o -> o.accountId().equals(accountId) && o.instrumentId().equals(instrumentId))
                .filter(o -> o.side() == Side.SELL && o.status().isOpen())
                .map(Order::remainingQuantity)
                .reduce(BigDecimal.ZERO, BigDecimal::add);
    }

    // ---------------------------------------------------------------- the rules

    /** The outcome of checking an order request against the rules, without acting on it. */
    private record Assessment(Instrument instrument, BigDecimal price, BigDecimal notional,
            BigDecimal commission, BigDecimal total, BigDecimal buyingPower,
            List<ApiException> issues) {

        static Assessment failed(Instrument instrument, List<ApiException> issues) {
            return new Assessment(instrument, null, null, null, null, null, issues);
        }
    }

    private Assessment assess(Caller caller, Account account, OrderRequest request) {
        List<ApiException> issues = new ArrayList<>();
        Instrument instrument;
        try {
            instrument = market.instrument(request.getInstrumentId());
        } catch (ApiException notFound) {
            issues.add(new ApiException(ProblemType.INSTRUMENT_NOT_TRADABLE,
                    "Instrument " + request.getInstrumentId() + " does not exist"));
            return Assessment.failed(null, issues);
        }

        checkPrices(request, issues);
        if (!instrument.tradable()) {
            issues.add(new ApiException(ProblemType.INSTRUMENT_NOT_TRADABLE,
                    instrument.symbol() + " is not tradable"));
        }
        boolean wholeUnits = request.getQuantity().stripTrailingZeros().scale() <= 0;
        if (!wholeUnits && (!instrument.fractionable() || request.getType() != Type.MARKET)) {
            issues.add(new ApiException(ProblemType.INVALID_REQUEST,
                    "Fractional quantities are allowed only in market orders for fractionable stocks"));
        }
        if (!issues.isEmpty()) {
            return Assessment.failed(instrument, issues);
        }

        BigDecimal price = referencePrice(request.getType(), request.getSide(),
                request.getLimitPrice(), request.getStopPrice(), instrument);
        BigDecimal notional = price.multiply(request.getQuantity())
                .multiply(instrument.multiplier())
                .setScale(2, RoundingMode.HALF_EVEN);
        BigDecimal commission = commission(instrument, request.getQuantity());
        boolean buy = request.getSide() == Side.BUY;
        BigDecimal total = buy ? notional.add(commission) : notional.subtract(commission);
        BigDecimal buyingPower = Holdings.buyingPower(account.type(), ledger.cash(account.id()),
                reservedCash(account.id()));

        if (buy && total.compareTo(buyingPower) > 0) {
            issues.add(new ApiException(ProblemType.INSUFFICIENT_BUYING_POWER,
                    "This order needs " + total.toPlainString() + " but only "
                            + buyingPower.toPlainString() + " is available")
                    .with("required", total).with("available", buyingPower));
        }
        if (!buy) {
            BigDecimal available = ledger.quantityHeld(account.id(), instrument.id())
                    .subtract(reservedQuantity(account.id(), instrument.id()));
            if (request.getQuantity().compareTo(available) > 0) {
                issues.add(new ApiException(ProblemType.INSUFFICIENT_POSITION,
                        "Only " + available.toPlainString() + " " + instrument.symbol()
                                + " are available to sell; short selling is not supported")
                        .with("available", available));
            }
        }
        caller.tradingLimit().filter(limit -> notional.compareTo(limit) > 0).ifPresent(limit ->
                issues.add(new ApiException(ProblemType.TRADING_LIMIT_EXCEEDED,
                        "This client may place orders up to " + limit.toPlainString()
                                + " USD; this one is " + notional.toPlainString())
                        .with("tradingLimit", limit)));
        return new Assessment(instrument, price, notional, commission, total, buyingPower, issues);
    }

    private static void checkPrices(OrderRequest request, List<ApiException> issues) {
        Type type = request.getType();
        boolean needsLimit = type == Type.LIMIT || type == Type.STOP_LIMIT;
        boolean needsStop = type == Type.STOP || type == Type.STOP_LIMIT;
        if (needsLimit != (request.getLimitPrice() != null)) {
            issues.add(new ApiException(ProblemType.INVALID_REQUEST, needsLimit
                    ? "limitPrice is required for " + type + " orders"
                    : "limitPrice is not allowed for " + type + " orders"));
        }
        if (needsStop != (request.getStopPrice() != null)) {
            issues.add(new ApiException(ProblemType.INVALID_REQUEST, needsStop
                    ? "stopPrice is required for " + type + " orders"
                    : "stopPrice is not allowed for " + type + " orders"));
        }
    }

    /** The price used to estimate cost and reserve cash: the worst price the order can get. */
    private BigDecimal referencePrice(Type type, Side side, BigDecimal limit, BigDecimal stop,
            Instrument instrument) {
        return switch (type) {
            case LIMIT, STOP_LIMIT -> limit;
            case STOP -> stop;
            case MARKET -> {
                MarketData.Tick tick = market.tick(instrument.id());
                yield side == Side.BUY ? tick.ask() : tick.bid();
            }
        };
    }

    private static BigDecimal commission(Instrument instrument, BigDecimal quantity) {
        BigDecimal perUnit = instrument.isOption() ? OPTION_COMMISSION : BigDecimal.ZERO;
        return perUnit.multiply(quantity).setScale(2, RoundingMode.HALF_EVEN);
    }

    private static <T> T orElse(T value, T fallback) {
        return value != null ? value : fallback;
    }

    private void save(Order order) {
        orders.put(order.id(), order);
    }

    private String nextOrderId() {
        return "ORD-" + orderIds.incrementAndGet();
    }
}
