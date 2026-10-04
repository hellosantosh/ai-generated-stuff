package com.example.brokerage.domain;

import java.time.Duration;
import java.time.Instant;
import java.util.Map;
import java.util.Optional;
import java.util.concurrent.ConcurrentHashMap;

import org.springframework.stereotype.Component;

/**
 * Single-use tokens that bind an approval to one exact order.
 *
 * <p>This is the mechanism that turns "the agent should preview before it places" from a hope into
 * a fact. {@code brokerage_order_place} requires a token; only
 * {@code brokerage_order_preview} issues one; the token carries the fingerprint of the ticket it
 * was issued for; and it is spent on first use. An agent cannot place an order it has not priced,
 * cannot place a different order than the one the customer saw, and cannot place the same order
 * twice by retrying.
 *
 * <p>Compare the usual alternative, a {@code confirmed: true} parameter on the place tool. A
 * boolean the model sets is a boolean the model can set — it will be set, eventually, by a model
 * that has convinced itself the customer already agreed three turns ago. The token cannot be
 * guessed, cannot be reused, and expires, so the guarantee does not depend on the model's judgment.
 *
 * <p>The window is deliberately short. Two minutes is long enough for a customer to read a summary
 * and say yes, and short enough that the price they agreed to is still roughly the price they get.
 */
@Component
public class ConfirmationTokens {

    public static final Duration DEFAULT_TTL = Duration.ofSeconds(120);

    /**
     * @param fingerprint the ticket this token authorizes, exactly
     * @param issuedTo    the principal the preview was run for; another principal's token is no token
     */
    public record Issued(String token, String fingerprint, String issuedTo, Instant expiresAt) {
    }

    /** Why a token was refused, in the words the error's remediation will use. */
    public enum Rejection {
        UNKNOWN("That confirmation token was not issued by this service."),
        EXPIRED("The confirmation has expired. Preview the order again to get a fresh one."),
        SPENT("That confirmation was already used to place an order. Preview again if the "
                + "customer wants a second one."),
        WRONG_PRINCIPAL("That confirmation was issued for a different session."),
        CHANGED("The order does not match the one that was confirmed. Preview the new terms and "
                + "have the customer approve them.");

        private final String explanation;

        Rejection(String explanation) {
            this.explanation = explanation;
        }

        public String explanation() {
            return explanation;
        }
    }

    public sealed interface Redemption {
        record Accepted(Issued issued) implements Redemption {
        }

        record Refused(Rejection rejection) implements Redemption {
        }
    }

    private final Map<String, Issued> live = new ConcurrentHashMap<>();
    private final Map<String, Instant> spent = new ConcurrentHashMap<>();
    private final MarketClock clock;

    public ConfirmationTokens(MarketClock clock) {
        this.clock = clock;
    }

    public Issued issue(Orders.Ticket ticket, String principal, Duration ttl) {
        Issued issued = new Issued(Ids.random("cnf_", 24), ticket.fingerprint(), principal,
                clock.now().plus(ttl));
        live.put(issued.token(), issued);
        return issued;
    }

    public Issued issue(Orders.Ticket ticket, String principal) {
        return issue(ticket, principal, DEFAULT_TTL);
    }

    /**
     * Spend a token for a ticket. Every refusal path returns a {@link Rejection} rather than
     * throwing, because each one is something the agent can act on and the customer can be told.
     */
    public Redemption redeem(String token, Orders.Ticket ticket, String principal) {
        if (token == null || token.isBlank()) {
            return new Redemption.Refused(Rejection.UNKNOWN);
        }
        if (spent.containsKey(token)) {
            return new Redemption.Refused(Rejection.SPENT);
        }
        Issued issued = live.get(token);
        if (issued == null) {
            return new Redemption.Refused(Rejection.UNKNOWN);
        }
        if (!issued.expiresAt().isAfter(clock.now())) {
            live.remove(token);
            return new Redemption.Refused(Rejection.EXPIRED);
        }
        if (!issued.issuedTo().equals(principal)) {
            return new Redemption.Refused(Rejection.WRONG_PRINCIPAL);
        }
        if (!issued.fingerprint().equals(ticket.fingerprint())) {
            return new Redemption.Refused(Rejection.CHANGED);
        }
        live.remove(token);
        spent.put(token, clock.now());
        return new Redemption.Accepted(issued);
    }

    public Optional<Issued> peek(String token) {
        return Optional.ofNullable(live.get(token));
    }

    public int outstanding() {
        return live.size();
    }
}
