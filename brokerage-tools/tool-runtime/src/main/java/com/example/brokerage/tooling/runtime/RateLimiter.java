package com.example.brokerage.tooling.runtime;

import java.time.Duration;
import java.time.Instant;
import java.util.ArrayDeque;
import java.util.Deque;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;

/**
 * A per-principal, per-tool sliding-window limiter.
 *
 * <p>Rate limiting a tool catalog is not about load. It is about the one failure mode a language
 * model has that a human caller does not: a loop. An agent that misreads an error as transient
 * will retry it, and if the retry produces the same error it will retry again, politely, as many
 * times as the turn budget allows. The limit is what turns an expensive loop into a clear
 * {@code RATE_LIMITED} with a wait, which the agent can relay.
 *
 * <p>The window is counted per tool rather than per catalog, because the limits that matter are
 * different by two orders of magnitude: 180 quote calls a minute is normal and 180 order
 * placements a minute is an incident.
 */
public class RateLimiter {

    private static final Duration WINDOW = Duration.ofMinutes(1);

    private final Map<String, Deque<Instant>> calls = new ConcurrentHashMap<>();

    /** Null when the call is allowed, or the seconds to wait when it is not. */
    public Integer retryAfter(String principal, String tool, Integer perMinute, Instant now) {
        if (perMinute == null || perMinute <= 0) {
            return null;
        }
        Deque<Instant> window = calls.computeIfAbsent(principal + "|" + tool,
                ignored -> new ArrayDeque<>());
        synchronized (window) {
            Instant cutoff = now.minus(WINDOW);
            while (!window.isEmpty() && window.peekFirst().isBefore(cutoff)) {
                window.removeFirst();
            }
            if (window.size() >= perMinute) {
                long wait = WINDOW.minus(Duration.between(window.peekFirst(), now)).toSeconds();
                return (int) Math.max(1, wait);
            }
            window.addLast(now);
            return null;
        }
    }

    public void clear() {
        calls.clear();
    }
}
