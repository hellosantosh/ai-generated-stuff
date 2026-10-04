package com.example.brokerage.domain;

import java.math.BigDecimal;
import java.time.Instant;
import java.util.List;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import static org.assertj.core.api.Assertions.assertThat;

/**
 * The simulated market has to be boring in two specific ways, and neither is guaranteed by
 * anything except these tests.
 *
 * <p>It has to be <b>repeatable</b>, because the book prints these numbers, the evaluation
 * cassettes were recorded against them and the contract tests assert on them. A price that moved
 * because a random number generator felt like it would make all three unreadable.
 *
 * <p>And consecutive days have to be <b>close together</b>, because the day-over-day change is
 * published in every quote. The first version of this generator drew each day independently from a
 * wide band and produced a blue chip down sixteen percent, which made every figure in the book look
 * like a bug. {@link #theDayOverDayMoveIsBelievable()} is what stops that coming back.
 */
class MarketDataTest {

    private static final List<String> SYMBOLS =
            List.of("AAPL", "MSFT", "NVDA", "BRK.B", "VOO", "KO", "GME", "TSM");

    private MarketData at(String instant) {
        MarketClock clock = new MarketClock();
        clock.set(Instant.parse(instant));
        return new MarketData(new Instruments(), clock);
    }

    @Test
    @DisplayName("the same symbol on the same day is the same price on every run")
    void pricesAreRepeatable() {
        for (String symbol : SYMBOLS) {
            BigDecimal first = at("2026-10-01T14:30:00Z").quote(symbol).last();
            BigDecimal again = at("2026-10-01T18:00:00Z").quote(symbol).last();
            assertThat(again).as("%s within the same trading day", symbol).isEqualByComparingTo(first);
        }
    }

    @Test
    @DisplayName("no day-over-day move exceeds two percent")
    void theDayOverDayMoveIsBelievable() {
        for (String symbol : SYMBOLS) {
            for (int day = 1; day <= 60; day++) {
                MarketData market = at("2026-%02d-%02dT14:30:00Z"
                        .formatted(8 + day / 31, 1 + day % 28));
                MarketData.Quote quote = market.quote(symbol);
                assertThat(quote.changePercent().abs())
                        .as("%s on day %d moved %s percent", symbol, day, quote.changePercent())
                        .isLessThan(BigDecimal.valueOf(2));
            }
        }
    }

    @Test
    @DisplayName("prices stay inside a believable band around the instrument's base")
    void pricesAreBounded() {
        Instruments instruments = new Instruments();
        for (String symbol : SYMBOLS) {
            BigDecimal base = instruments.require(symbol).basePrice();
            for (int month = 1; month <= 12; month++) {
                BigDecimal last = at("2026-%02d-15T14:30:00Z".formatted(month)).quote(symbol).last();
                assertThat(last).as("%s in month %d", symbol, month)
                        .isGreaterThan(base.multiply(BigDecimal.valueOf(0.8)))
                        .isLessThan(base.multiply(BigDecimal.valueOf(1.2)));
            }
        }
    }

    @Test
    @DisplayName("the bid is below the ask, and the spread widens outside the regular session")
    void theSpreadReflectsTheSession() {
        MarketData.Quote regular = at("2026-10-01T14:30:00Z").quote("AAPL");
        MarketData.Quote afterHours = at("2026-10-01T21:00:00Z").quote("AAPL");

        assertThat(regular.bid()).isLessThan(regular.ask());
        assertThat(regular.session()).isEqualTo(MarketClock.Session.REGULAR);
        assertThat(afterHours.session()).isEqualTo(MarketClock.Session.AFTER_HOURS);
        assertThat(afterHours.spreadBasisPoints())
                .as("a thin session is a wider market, and the agent is told so")
                .isGreaterThan(regular.spreadBasisPoints());
    }

    @Test
    @DisplayName("the last bar's close agrees with the quote, so one turn cannot contradict itself")
    void theSeriesAndTheQuoteAgree() {
        MarketData market = at("2026-10-01T14:30:00Z");
        List<MarketData.Bar> bars = market.history("NVDA", 30);
        assertThat(bars.getLast().close()).isEqualByComparingTo(market.quote("NVDA").last());
        assertThat(bars.getLast().date()).isEqualTo(java.time.LocalDate.of(2026, 10, 1));
    }

    @Test
    @DisplayName("history covers trading days only, and every bar is internally consistent")
    void barsAreWellFormed() {
        for (MarketData.Bar bar : at("2026-10-01T14:30:00Z").history("MSFT", 30)) {
            assertThat(bar.date().getDayOfWeek().getValue())
                    .as("%s is a weekend", bar.date()).isLessThanOrEqualTo(5);
            assertThat(bar.high()).isGreaterThanOrEqualTo(bar.open().max(bar.close()));
            assertThat(bar.low()).isLessThanOrEqualTo(bar.open().min(bar.close()));
            assertThat(bar.low()).isGreaterThan(BigDecimal.ZERO);
            assertThat(bar.volume()).isPositive();
        }
    }
}
