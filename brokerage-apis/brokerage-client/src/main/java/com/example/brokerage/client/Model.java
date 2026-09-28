package com.example.brokerage.client;

import java.math.BigDecimal;
import java.net.URI;
import java.time.Instant;
import java.util.List;
import java.util.Optional;

import com.fasterxml.jackson.annotation.JsonProperty;
import tools.jackson.databind.JsonNode;

/**
 * The client's view of the API's representations: only the fields it uses. Enumerations
 * are read as strings, so a status the server adds next year cannot break this client.
 */
public final class Model {

    private Model() {
    }

    public record Account(String id, String nickname, String type, String registration, String currency) {
    }

    public record Balances(String currency, BigDecimal cash, BigDecimal reservedForOrders,
            BigDecimal buyingPower, BigDecimal positionsMarketValue, BigDecimal totalEquity,
            BigDecimal unrealizedPnl) {
    }

    public record Position(String instrumentId, String symbol, BigDecimal quantity, BigDecimal averageCost,
            BigDecimal lastPrice, BigDecimal marketValue, BigDecimal unrealizedPnl,
            BigDecimal unrealizedPnlPercent) {
    }

    public record Instrument(String id, String symbol, String name, String type, boolean tradable) {
    }

    /** A quote, as served by API version 2. */
    public record Quote(String symbol, BigDecimal last, PriceLevel bid, PriceLevel ask, BigDecimal change,
            BigDecimal changePercent, Instant asOf) {

        public record PriceLevel(BigDecimal price, BigDecimal size) {
        }
    }

    /** What to trade: the body of a preview or a new order. */
    public record OrderTicket(String instrumentId, String side, String type, BigDecimal quantity,
            BigDecimal limitPrice, String timeInForce, String clientOrderId) {

        public static OrderTicket market(String instrumentId, String side, BigDecimal quantity) {
            return new OrderTicket(instrumentId, side, "MARKET", quantity, null, "DAY", null);
        }

        public static OrderTicket limit(String instrumentId, String side, BigDecimal quantity,
                BigDecimal price) {
            return new OrderTicket(instrumentId, side, "LIMIT", quantity, price, "DAY", null);
        }
    }

    public record OrderPreview(String symbol, BigDecimal estimatedPrice, BigDecimal estimatedTotal,
            BigDecimal buyingPower, boolean acceptable, List<Issue> issues) {

        public record Issue(String type, String detail) {
        }
    }

    /** An order, with the links the server offered for it at the time it was read. */
    public record Order(String id, String symbol, String side, String type, BigDecimal quantity,
            BigDecimal limitPrice, String status, BigDecimal filledQuantity, BigDecimal averageFillPrice,
            Instant createdAt, @JsonProperty("_links") JsonNode links) {

        public URI self() {
            return link("self").orElseThrow();
        }

        /** Present only while the server allows the action it names. */
        public Optional<URI> link(String rel) {
            JsonNode href = links == null ? null : links.path(rel).path("href");
            return href == null || href.isMissingNode() ? Optional.empty()
                    : Optional.of(URI.create(href.asString()));
        }

        public boolean isCancellable() {
            return link("bk:cancel").isPresent();
        }
    }
}
