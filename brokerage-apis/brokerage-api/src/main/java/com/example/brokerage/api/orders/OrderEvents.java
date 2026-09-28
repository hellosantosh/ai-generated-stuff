package com.example.brokerage.api.orders;

import java.io.IOException;
import java.time.Clock;
import java.time.Instant;
import java.util.ArrayDeque;
import java.util.Deque;
import java.util.List;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.CopyOnWriteArrayList;
import java.util.concurrent.atomic.AtomicLong;
import java.util.function.Function;

import org.springframework.http.MediaType;
import org.springframework.stereotype.Component;
import org.springframework.web.servlet.mvc.method.annotation.SseEmitter;

/**
 * Order lifecycle events, pushed to subscribers as Server-Sent Events.
 *
 * <p>Every event gets an increasing ID, and the last 500 events of each account are kept.
 * A client that loses its connection reconnects with a Last-Event-ID header and receives
 * exactly the events it missed: no polling, and no gaps.
 */
@Component
public class OrderEvents {

    /** Something happened to an order. The type is order.created, order.filled, etc. */
    public record Event(long id, String type, Order order, Instant at) {
    }

    private record Subscriber(SseEmitter emitter, Function<Event, Object> renderer) {
    }

    private static final int HISTORY = 500;
    private static final long TIMEOUT_MILLIS = 30 * 60 * 1000L;

    private final AtomicLong sequence = new AtomicLong();
    private final Map<String, Deque<Event>> history = new ConcurrentHashMap<>();
    private final Map<String, List<Subscriber>> subscribers = new ConcurrentHashMap<>();
    private final Clock clock;

    public OrderEvents(Clock clock) {
        this.clock = clock;
    }

    void publish(String type, Order order) {
        Event event = new Event(sequence.incrementAndGet(), type, order, clock.instant());
        Deque<Event> events = history.computeIfAbsent(order.accountId(), id -> new ArrayDeque<>());
        synchronized (events) {
            events.addLast(event);
            if (events.size() > HISTORY) {
                events.removeFirst();
            }
        }
        for (Subscriber subscriber : subscribers.getOrDefault(order.accountId(), List.of())) {
            send(order.accountId(), subscriber, event);
        }
    }

    /** Opens a stream for one account, first replaying anything after lastEventId. */
    public SseEmitter subscribe(String accountId, Long lastEventId, Function<Event, Object> renderer) {
        Subscriber subscriber = new Subscriber(new SseEmitter(TIMEOUT_MILLIS), renderer);
        subscriber.emitter().onCompletion(() -> unsubscribe(accountId, subscriber));
        subscriber.emitter().onTimeout(() -> unsubscribe(accountId, subscriber));
        if (lastEventId != null) {
            replay(accountId, lastEventId).forEach(event -> send(accountId, subscriber, event));
        }
        subscribers.computeIfAbsent(accountId, id -> new CopyOnWriteArrayList<>()).add(subscriber);
        return subscriber.emitter();
    }

    /** Events for an account with an ID greater than lastEventId, oldest first. */
    public List<Event> replay(String accountId, long lastEventId) {
        Deque<Event> events = history.getOrDefault(accountId, new ArrayDeque<>());
        synchronized (events) {
            return events.stream().filter(event -> event.id() > lastEventId).toList();
        }
    }

    private void send(String accountId, Subscriber subscriber, Event event) {
        try {
            subscriber.emitter().send(SseEmitter.event()
                    .id(Long.toString(event.id()))
                    .name(event.type())
                    .data(subscriber.renderer().apply(event), MediaType.APPLICATION_JSON));
        } catch (IOException | IllegalStateException gone) {
            unsubscribe(accountId, subscriber);
        }
    }

    private void unsubscribe(String accountId, Subscriber subscriber) {
        subscribers.getOrDefault(accountId, List.of()).remove(subscriber);
    }
}
