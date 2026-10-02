package com.example.brokerage.domain;

import java.time.DayOfWeek;
import java.time.Duration;
import java.time.Instant;
import java.time.LocalTime;
import java.time.ZoneId;
import java.time.ZonedDateTime;

import org.springframework.stereotype.Component;

/**
 * What time it is, as a trading venue understands the question.
 *
 * <p>The clock is a component rather than a static call because "is the market open" is the single
 * most common reason a correct-looking order is refused, and a test that cannot move the clock
 * cannot cover that path. Every tool that asks about the session asks here.
 */
@Component
public class MarketClock {

    public static final ZoneId EXCHANGE = ZoneId.of("America/New_York");
    private static final LocalTime PREMARKET_OPEN = LocalTime.of(4, 0);
    private static final LocalTime REGULAR_OPEN = LocalTime.of(9, 30);
    private static final LocalTime REGULAR_CLOSE = LocalTime.of(16, 0);
    private static final LocalTime AFTERHOURS_CLOSE = LocalTime.of(20, 0);

    /** Which session the venue is in. The model is told this in words, never left to infer it. */
    public enum Session {
        CLOSED("closed"),
        PRE_MARKET("pre-market"),
        REGULAR("regular"),
        AFTER_HOURS("after-hours");

        private final String label;

        Session(String label) {
            this.label = label;
        }

        public String label() {
            return label;
        }

        public boolean tradeable() {
            return this != CLOSED;
        }
    }

    private Instant now = Instant.parse("2026-10-01T14:30:00Z");

    /** Freeze the clock. Used by the seeded demonstration and by every test that cares. */
    public void set(Instant instant) {
        this.now = instant;
    }

    public void advance(Duration amount) {
        this.now = now.plus(amount);
    }

    public Instant now() {
        return now;
    }

    public Session session() {
        ZonedDateTime local = now.atZone(EXCHANGE);
        if (local.getDayOfWeek() == DayOfWeek.SATURDAY || local.getDayOfWeek() == DayOfWeek.SUNDAY) {
            return Session.CLOSED;
        }
        LocalTime time = local.toLocalTime();
        if (time.isBefore(PREMARKET_OPEN) || !time.isBefore(AFTERHOURS_CLOSE)) {
            return Session.CLOSED;
        }
        if (time.isBefore(REGULAR_OPEN)) {
            return Session.PRE_MARKET;
        }
        return time.isBefore(REGULAR_CLOSE) ? Session.REGULAR : Session.AFTER_HOURS;
    }

    /**
     * When the regular session next opens, in words a model can relay. "The market is closed" is
     * half an answer; a customer wants to know whether to wait twenty minutes or until Monday.
     */
    public Instant nextRegularOpen() {
        ZonedDateTime local = now.atZone(EXCHANGE);
        ZonedDateTime candidate = local.with(REGULAR_OPEN);
        if (!candidate.toInstant().isAfter(now)) {
            candidate = candidate.plusDays(1);
        }
        while (candidate.getDayOfWeek() == DayOfWeek.SATURDAY
                || candidate.getDayOfWeek() == DayOfWeek.SUNDAY) {
            candidate = candidate.plusDays(1);
        }
        return candidate.toInstant();
    }
}
