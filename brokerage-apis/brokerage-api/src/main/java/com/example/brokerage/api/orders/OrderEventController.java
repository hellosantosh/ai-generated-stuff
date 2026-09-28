package com.example.brokerage.api.orders;

import java.math.BigDecimal;
import java.time.Instant;
import java.util.Map;

import com.example.brokerage.api.accounts.Ledger;
import com.example.brokerage.api.platform.Caller;
import com.fasterxml.jackson.annotation.JsonProperty;

import org.springframework.http.MediaType;
import org.springframework.security.core.annotation.AuthenticationPrincipal;
import org.springframework.security.oauth2.jwt.Jwt;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.RequestHeader;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.servlet.mvc.method.annotation.SseEmitter;
import org.springframework.web.servlet.support.ServletUriComponentsBuilder;

/**
 * A live stream of an account's order events (text/event-stream). Scope: orders:read.
 */
@RestController
public class OrderEventController {

    /** The data of one event: a summary, and a link to the full order. */
    public record OrderEventData(String orderId, Order.Status status, BigDecimal filledQuantity,
            BigDecimal remainingQuantity, BigDecimal averageFillPrice, Instant occurredAt,
            @JsonProperty("_links") Map<String, Map<String, String>> links) {
    }

    private final OrderEvents events;
    private final Ledger ledger;

    public OrderEventController(OrderEvents events, Ledger ledger) {
        this.events = events;
        this.ledger = ledger;
    }

    @GetMapping(path = "/accounts/{accountId}/order-events", produces = MediaType.TEXT_EVENT_STREAM_VALUE)
    public SseEmitter events(@PathVariable String accountId,
            @RequestHeader(name = "Last-Event-ID", required = false) Long lastEventId,
            @AuthenticationPrincipal Jwt jwt) {
        ledger.account(Caller.of(jwt).customerId(), accountId);
        // Events are sent later, outside this request, so capture the base URI now.
        String base = ServletUriComponentsBuilder.fromCurrentContextPath().toUriString();
        return events.subscribe(accountId, lastEventId, event -> render(base, event));
    }

    static OrderEventData render(String base, OrderEvents.Event event) {
        Order order = event.order();
        String href = base + "/accounts/" + order.accountId() + "/orders/" + order.id();
        return new OrderEventData(order.id(), order.status(), order.filledQuantity(),
                order.remainingQuantity(), order.averageFillPrice(), event.at(),
                Map.of("order", Map.of("href", href)));
    }
}
