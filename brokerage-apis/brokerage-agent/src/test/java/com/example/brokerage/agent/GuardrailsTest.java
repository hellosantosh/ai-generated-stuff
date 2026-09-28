package com.example.brokerage.agent;

import static org.assertj.core.api.Assertions.assertThat;

import java.time.Clock;
import java.time.Instant;
import java.time.ZoneOffset;
import java.util.concurrent.atomic.AtomicInteger;

import org.junit.jupiter.api.Test;

/**
 * Proposals expire, can be discarded, and run at most once; every Messages API request is
 * opted into server-side refusal fallbacks.
 */
class GuardrailsTest {

    @Test
    void anExpiredProposalIsNotCarriedOut() {
        MutableClock clock = new MutableClock();
        PendingActions pending = new PendingActions(clock);
        AtomicInteger runs = new AtomicInteger();
        var proposal = pending.propose("Buy 1 AAPL", key -> "ran " + runs.incrementAndGet());

        clock.advanceSeconds(301);

        assertThat(pending.confirm(proposal.code())).contains("expired");
        assertThat(runs).hasValue(0);
    }

    @Test
    void aDiscardedProposalCannotBeConfirmed() {
        PendingActions pending = new PendingActions(Clock.systemUTC());
        var proposal = pending.propose("Sell 1 MSFT", key -> "ran");

        assertThat(pending.discard(proposal.code())).isTrue();
        assertThat(pending.confirm(proposal.code())).startsWith("There is no pending action");
    }

    @Test
    void theConfirmationCodeIsTheIdempotencyKey() {
        PendingActions pending = new PendingActions(Clock.systemUTC());
        var proposal = pending.propose("Buy 1 AAPL", key -> key);

        assertThat(pending.confirm(proposal.code().toLowerCase())).isEqualTo(proposal.code());
    }

    @Test
    void everyMessagesRequestAsksForServerSideFallbacks() {
        String request = "{\"model\":\"claude-opus-5\",\"max_tokens\":16000,\"messages\":[]}";

        assertThat(RefusalFallbacks.withFallbacks(request))
                .contains("\"fallbacks\":\"default\"")
                .contains("\"model\":\"claude-opus-5\"")
                .contains("\"max_tokens\":16000");
    }

    /** A clock that tests can move forward. */
    static final class MutableClock extends Clock {
        private Instant now = Instant.parse("2026-09-28T15:00:00Z");

        void advanceSeconds(long seconds) {
            now = now.plusSeconds(seconds);
        }

        @Override
        public Instant instant() {
            return now;
        }

        @Override
        public ZoneOffset getZone() {
            return ZoneOffset.UTC;
        }

        @Override
        public Clock withZone(java.time.ZoneId zone) {
            return this;
        }
    }
}
