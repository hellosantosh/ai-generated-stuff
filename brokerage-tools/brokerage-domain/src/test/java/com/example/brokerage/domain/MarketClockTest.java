package com.example.brokerage.domain;

import java.time.Instant;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.CsvSource;

import static org.assertj.core.api.Assertions.assertThat;

/**
 * Sessions, because "the market is closed" is the single most common reason a correct-looking
 * order is refused, and getting the boundaries wrong is invisible until a customer trades at
 * 09:28.
 */
class MarketClockTest {

    private static MarketClock at(String instant) {
        MarketClock clock = new MarketClock();
        clock.set(Instant.parse(instant));
        return clock;
    }

    @ParameterizedTest(name = "{0} is {1}")
    @CsvSource({
            // Thursday 1 October 2026, in UTC. New York is on daylight time, so UTC minus four.
            "2026-10-01T07:59:00Z, CLOSED",          // 03:59 — before the pre-market opens
            "2026-10-01T08:00:00Z, PRE_MARKET",      // 04:00 — the pre-market opens
            "2026-10-01T13:29:00Z, PRE_MARKET",      // 09:29 — one minute before the bell
            "2026-10-01T13:30:00Z, REGULAR",         // 09:30 — the bell
            "2026-10-01T19:59:00Z, REGULAR",         // 15:59 — one minute before the close
            "2026-10-01T20:00:00Z, AFTER_HOURS",     // 16:00 — the close
            "2026-10-01T23:59:00Z, AFTER_HOURS",     // 19:59 — the last minute of the session
            "2026-10-02T00:00:00Z, CLOSED",          // 20:00 — after-hours ends
            "2026-10-03T17:00:00Z, CLOSED",          // Saturday
            "2026-10-04T17:00:00Z, CLOSED",          // Sunday
    })
    void sessionBoundaries(String instant, MarketClock.Session expected) {
        assertThat(at(instant).session()).isEqualTo(expected);
    }

    @Test
    @DisplayName("only the regular session is tradeable without a limit order")
    void tradeability() {
        assertThat(MarketClock.Session.REGULAR.tradeable()).isTrue();
        assertThat(MarketClock.Session.PRE_MARKET.tradeable()).isTrue();
        assertThat(MarketClock.Session.AFTER_HOURS.tradeable()).isTrue();
        assertThat(MarketClock.Session.CLOSED.tradeable()).isFalse();
    }

    @Test
    @DisplayName("the next open skips the weekend, because a customer wants to know how long")
    void theNextOpenIsUseful() {
        // Friday evening: the next regular session is Monday morning.
        assertThat(at("2026-10-02T23:00:00Z").nextRegularOpen())
                .isEqualTo(Instant.parse("2026-10-05T13:30:00Z"));
        // Thursday before the bell: later the same morning.
        assertThat(at("2026-10-01T10:00:00Z").nextRegularOpen())
                .isEqualTo(Instant.parse("2026-10-01T13:30:00Z"));
        // Thursday after the close: tomorrow morning.
        assertThat(at("2026-10-01T21:00:00Z").nextRegularOpen())
                .isEqualTo(Instant.parse("2026-10-02T13:30:00Z"));
    }

    @Test
    @DisplayName("the clock can be moved, which is the only reason it is a component")
    void theClockMoves() {
        MarketClock clock = at("2026-10-01T14:30:00Z");
        clock.advance(java.time.Duration.ofHours(8));
        assertThat(clock.now()).isEqualTo(Instant.parse("2026-10-01T22:30:00Z"));
        assertThat(clock.session()).isEqualTo(MarketClock.Session.AFTER_HOURS);
    }
}
