package com.example.brokerage.domain;

import java.math.BigDecimal;
import java.math.RoundingMode;
import java.time.Duration;
import java.time.Instant;
import java.time.LocalDate;
import java.util.ArrayList;
import java.util.List;

import org.springframework.stereotype.Component;

/**
 * Quotes and bars, generated deterministically.
 *
 * <p>Determinism is not laziness here, it is the point. The book prints quotes, the test harness
 * asserts on them, and the evaluation suite compares one run of an agent against another. If the
 * price moved because a random number generator felt like it, none of those three would be
 * readable. Every figure comes from a hash of the symbol and the day, so the same symbol on the
 * same day is the same price on every machine, and moving the clock moves the price.
 */
@Component
public class MarketData {

    private final Instruments instruments;
    private final MarketClock clock;

    public MarketData(Instruments instruments, MarketClock clock) {
        this.instruments = instruments;
        this.clock = clock;
    }

    /**
     * A two-sided quote. {@code spreadBasisPoints} is carried explicitly because a wide spread is
     * the most common reason a market order fills somewhere the customer did not expect, and the
     * agent should be able to warn about it without doing arithmetic.
     */
    public record Quote(String symbol, BigDecimal last, BigDecimal bid, BigDecimal ask,
                        long bidSize, long askSize, BigDecimal previousClose, BigDecimal change,
                        BigDecimal changePercent, long volume, int spreadBasisPoints,
                        Instant asOf, MarketClock.Session session) {
    }

    /** One OHLCV bar. */
    public record Bar(LocalDate date, BigDecimal open, BigDecimal high, BigDecimal low,
                      BigDecimal close, long volume) {
    }

    public Quote quote(String symbol) {
        Instrument instrument = instruments.require(symbol);
        Instant now = clock.now();
        BigDecimal previousClose = priceOn(instrument, now.minus(Duration.ofDays(1)));
        BigDecimal last = priceOn(instrument, now);
        int spread = spreadBasisPoints(instrument);
        BigDecimal half = last.multiply(BigDecimal.valueOf(spread))
                .divide(BigDecimal.valueOf(20_000), 4, RoundingMode.HALF_UP);
        BigDecimal change = last.subtract(previousClose);
        BigDecimal changePercent = previousClose.signum() == 0 ? BigDecimal.ZERO
                : change.multiply(BigDecimal.valueOf(100))
                        .divide(previousClose, 2, RoundingMode.HALF_UP);
        long volume = 250_000L + Math.abs(hash(instrument.symbol(), now, 7)) % 40_000_000L;
        return new Quote(instrument.symbol(), last, last.subtract(half), last.add(half),
                roundLot(instrument, now, 1), roundLot(instrument, now, 2), previousClose,
                change, changePercent, volume, spread, now, clock.session());
    }

    /**
     * Daily bars, newest last. The series is generated from the same hash as the quote, so the
     * last bar's close and the current quote agree — a small consistency that an agent reading
     * both in one turn will otherwise notice and comment on.
     */
    public List<Bar> history(String symbol, int days) {
        Instrument instrument = instruments.require(symbol);
        List<Bar> bars = new ArrayList<>();
        LocalDate today = clock.now().atZone(MarketClock.EXCHANGE).toLocalDate();
        for (int back = days - 1; back >= 0; back--) {
            LocalDate date = today.minusDays(back);
            if (date.getDayOfWeek().getValue() > 5) {
                continue;
            }
            Instant at = date.atStartOfDay(MarketClock.EXCHANGE).toInstant();
            BigDecimal close = priceOn(instrument, at);
            BigDecimal open = priceOn(instrument, at.minus(Duration.ofDays(1)));
            BigDecimal swing = close.multiply(BigDecimal.valueOf(
                    Math.abs(hash(instrument.symbol(), at, 3)) % 15 + 2))
                    .divide(BigDecimal.valueOf(1000), 2, RoundingMode.HALF_UP);
            BigDecimal high = close.max(open).add(swing);
            BigDecimal low = close.min(open).subtract(swing).max(BigDecimal.valueOf(0.01));
            long volume = 100_000L + Math.abs(hash(instrument.symbol(), at, 11)) % 20_000_000L;
            bars.add(new Bar(date, Money.cents(open), Money.cents(high), Money.cents(low),
                    Money.cents(close), volume));
        }
        return bars;
    }

    /**
     * The price of an instrument on a given day.
     *
     * <p>Two properties are required of this function and neither is obvious until it is got
     * wrong. It has to be <em>bounded</em>, because an unbounded walk eventually produces a
     * negative price and the first person to find that will be a reader following the book's
     * examples. And consecutive days have to be <em>close together</em>, because the day-over-day
     * change is published in every quote: draw each day independently from a wide band and the
     * demonstration account shows a blue chip down sixteen percent, which makes every figure in
     * the book look like a bug.
     *
     * <p>So the shape is a slow trend, phased per symbol so instruments do not move in lockstep,
     * plus a bounded daily jitter of at most eight tenths of a percent. The worst day-over-day
     * move that can come out of it is under two percent, which is a believable day.
     */
    private BigDecimal priceOn(Instrument instrument, Instant at) {
        long day = at.atZone(MarketClock.EXCHANGE).toLocalDate().toEpochDay();
        double phase = (Math.abs(instrument.symbol().hashCode()) % 360) * Math.PI / 180.0;
        double trend = Math.sin(day / 180.0 + phase) * 0.09 + Math.sin(day / 41.0 + phase) * 0.025;
        double jitter = (Math.floorMod(hash(instrument.symbol(), at, 1), 161L) - 80) / 10_000.0;
        BigDecimal factor = BigDecimal.valueOf(1 + trend + jitter)
                .setScale(6, RoundingMode.HALF_UP);
        return Money.cents(instrument.basePrice().multiply(factor));
    }

    private int spreadBasisPoints(Instrument instrument) {
        int base = switch (instrument.type()) {
            case ETF -> 2;
            case EQUITY -> instrument.basePrice().compareTo(BigDecimal.valueOf(50)) < 0 ? 12 : 4;
            case ADR -> 8;
        };
        return clock.session() == MarketClock.Session.REGULAR ? base : base * 6;
    }

    private long roundLot(Instrument instrument, Instant at, int salt) {
        return 100L * (1 + Math.abs(hash(instrument.symbol(), at, salt)) % 40);
    }

    private static long hash(String symbol, Instant at, int salt) {
        long day = at.atZone(MarketClock.EXCHANGE).toLocalDate().toEpochDay();
        long mixed = (symbol.hashCode() * 0x9E3779B97F4A7C15L) ^ (day * 0xBF58476D1CE4E5B9L)
                ^ (salt * 0x94D049BB133111EBL);
        mixed ^= mixed >>> 31;
        mixed *= 0xD6E8FEB86659FD93L;
        mixed ^= mixed >>> 32;
        return mixed % 10_000_019L;
    }
}
