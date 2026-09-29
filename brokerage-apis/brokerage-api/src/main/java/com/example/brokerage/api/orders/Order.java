package com.example.brokerage.api.orders;

import java.math.BigDecimal;
import java.math.RoundingMode;
import java.time.Instant;
import java.util.ArrayList;
import java.util.List;

import com.example.brokerage.api.market.Instrument;
import com.fasterxml.jackson.annotation.JsonIgnore;
import com.fasterxml.jackson.annotation.JsonProperty;

import org.springframework.hateoas.server.core.Relation;

/**
 * An order and its lifecycle. Orders are never deleted: a canceled or filled order stays
 * readable, which is why canceling is a state change (POST .../cancellation), not DELETE.
 *
 * <pre>
 *   OPEN ──fill──▶ PARTIALLY_FILLED ──fill──▶ FILLED
 *    │                    │
 *    └──cancel──▶ PENDING_CANCEL ◀──cancel──┘ ──▶ CANCELLED
 * </pre>
 *
 * @param version incremented on every change; exposed only as the ETag header
 */
@Relation(collectionRelation = "orders", itemRelation = "order")
public record Order(String id, String accountId, String clientOrderId, String instrumentId, String symbol,
        Instrument.Type instrumentType, Side side, Type type, TimeInForce timeInForce, BigDecimal quantity,
        BigDecimal limitPrice, BigDecimal stopPrice, Status status, BigDecimal filledQuantity,
        BigDecimal averageFillPrice, List<Fill> fills, Instant createdAt, Instant updatedAt,
        @JsonIgnore boolean stopTriggered, @JsonIgnore long version) {

    public enum Side { BUY, SELL }

    public enum Type { MARKET, LIMIT, STOP, STOP_LIMIT }

    /** DAY orders expire at the close; GTC ("good 'til canceled") orders stay working. */
    public enum TimeInForce { DAY, GTC }

    public enum Status {
        OPEN, PARTIALLY_FILLED, FILLED, PENDING_CANCEL, CANCELLED, REJECTED;

        /** Can still fill, and so can still be changed or canceled. */
        public boolean isWorking() {
            return this == OPEN || this == PARTIALLY_FILLED;
        }

        /** Not yet in a final state. */
        public boolean isOpen() {
            return isWorking() || this == PENDING_CANCEL;
        }
    }

    /** One execution against the order. */
    public record Fill(String id, BigDecimal quantity, BigDecimal price, Instant executedAt) {
    }

    @JsonProperty
    public BigDecimal remainingQuantity() {
        return quantity.subtract(filledQuantity);
    }

    /** A strong entity tag: changes whenever the order changes. */
    @JsonIgnore
    public String etag() {
        return "\"" + id + "-v" + version + "\"";
    }

    // ---------------------------------------------------------------- state changes

    Order withStatus(Status newStatus, Instant at) {
        return new Order(id, accountId, clientOrderId, instrumentId, symbol, instrumentType, side, type,
                timeInForce, quantity, limitPrice, stopPrice, newStatus, filledQuantity,
                averageFillPrice, fills, createdAt, at, stopTriggered, version + 1);
    }

    Order withStopTriggered() {
        return new Order(id, accountId, clientOrderId, instrumentId, symbol, instrumentType, side, type,
                timeInForce, quantity, limitPrice, stopPrice, status, filledQuantity, averageFillPrice, fills,
                createdAt, updatedAt, true, version);
    }

    Order withFill(Fill fill) {
        BigDecimal filled = filledQuantity.add(fill.quantity());
        BigDecimal cost = averageFillPrice == null ? BigDecimal.ZERO
                : averageFillPrice.multiply(filledQuantity);
        BigDecimal average = cost.add(fill.price().multiply(fill.quantity()))
                .divide(filled, 4, RoundingMode.HALF_EVEN);
        List<Fill> allFills = new ArrayList<>(fills);
        allFills.add(fill);
        Status newStatus = filled.compareTo(quantity) == 0 ? Status.FILLED
                : status == Status.PENDING_CANCEL ? Status.PENDING_CANCEL : Status.PARTIALLY_FILLED;
        return new Order(id, accountId, clientOrderId, instrumentId, symbol, instrumentType, side, type,
                timeInForce, quantity, limitPrice, stopPrice, newStatus, filled, average,
                List.copyOf(allFills), createdAt, fill.executedAt(), stopTriggered, version + 1);
    }

    Order withTerms(BigDecimal newQuantity, BigDecimal newLimit, BigDecimal newStop, TimeInForce newTif,
            Instant at) {
        return new Order(id, accountId, clientOrderId, instrumentId, symbol, instrumentType, side, type,
                newTif, newQuantity, newLimit, newStop, status, filledQuantity, averageFillPrice, fills,
                createdAt, at, stopTriggered, version + 1);
    }
}
