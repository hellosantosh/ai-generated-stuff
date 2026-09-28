package com.example.brokerage.agent;

import java.time.Clock;
import java.time.Duration;
import java.time.Instant;
import java.util.Locale;
import java.util.Map;
import java.util.UUID;
import java.util.concurrent.ConcurrentHashMap;
import java.util.function.Function;

import org.springframework.stereotype.Component;

/**
 * The human-in-the-loop gate. The model can only propose a trade; carrying it out takes a
 * person typing "/confirm CODE" at the console, a path that no tool can reach.
 *
 * <p>The confirmation code doubles as the order's Idempotency-Key, so confirming the same
 * proposal twice, or retrying after a timeout, can never place a second order.
 */
@Component
public class PendingActions {

    /** What the model is told about a proposal: enough to ask the customer, nothing to act on. */
    public record Proposal(String code, String summary, Instant expiresAt, String instructions) {
    }

    private record Pending(Proposal proposal, Function<String, String> action) {
    }

    static final Duration LIFETIME = Duration.ofMinutes(5);

    private final Map<String, Pending> pending = new ConcurrentHashMap<>();
    private final Clock clock;

    public PendingActions(Clock clock) {
        this.clock = clock;
    }

    /** Records an action to run later. The action receives the code to use as its Idempotency-Key. */
    public Proposal propose(String summary, Function<String, String> action) {
        String code = UUID.randomUUID().toString().substring(0, 8).toUpperCase(Locale.ROOT);
        Proposal proposal = new Proposal(code, summary, clock.instant().plus(LIFETIME),
                "Nothing has been done yet. Ask the customer to type /confirm " + code
                        + " to go ahead, or /discard " + code + " to drop it. It expires in 5 minutes.");
        pending.put(code, new Pending(proposal, action));
        return proposal;
    }

    /** Called only from the console, when a person types /confirm CODE. */
    public String confirm(String code) {
        Pending action = pending.remove(code.toUpperCase(Locale.ROOT));
        if (action == null) {
            return "There is no pending action " + code + ".";
        }
        if (clock.instant().isAfter(action.proposal().expiresAt())) {
            return "Proposal " + code + " expired; ask the assistant again.";
        }
        return action.action().apply(action.proposal().code());
    }

    public boolean discard(String code) {
        return pending.remove(code.toUpperCase(Locale.ROOT)) != null;
    }
}
