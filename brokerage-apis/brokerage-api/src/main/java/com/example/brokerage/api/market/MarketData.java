package com.example.brokerage.api.market;

import java.math.BigDecimal;
import java.math.RoundingMode;
import java.time.Clock;
import java.time.DayOfWeek;
import java.time.Instant;
import java.time.LocalDate;
import java.time.format.DateTimeFormatter;
import java.time.temporal.ChronoUnit;
import java.time.temporal.TemporalAdjusters;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Random;
import java.util.concurrent.ConcurrentHashMap;

import com.example.brokerage.api.market.Instrument.OptionTerms;
import com.example.brokerage.api.market.Instrument.Right;
import com.example.brokerage.api.market.Instrument.Type;
import com.example.brokerage.api.platform.ApiException;

import org.springframework.stereotype.Component;

/**
 * The instrument catalog and a simulated market. Prices are fictional: each stock takes a
 * small random step every tick, and option prices follow their underlying. Option
 * expirations are the third Friday of the next two months, so the demo never serves
 * contracts that have already expired.
 */
@Component
public class MarketData {

    /** The market state of one instrument at one moment. */
    public record Tick(BigDecimal last, BigDecimal bid, BigDecimal ask, BigDecimal bidSize,
            BigDecimal askSize, BigDecimal previousClose, long volume, long sequence, Instant asOf) {
    }

    private static final DateTimeFormatter OPTION_DATE = DateTimeFormatter.ofPattern("yyMMdd", Locale.US);
    private static final BigDecimal CENT = new BigDecimal("0.01");

    private final Map<String, Instrument> instruments = new LinkedHashMap<>();
    private final Map<String, Tick> ticks = new ConcurrentHashMap<>();
    private final Random random = new Random(7);
    private final Clock clock;

    public MarketData(Clock clock) {
        this.clock = clock;
        stock("EQ-AAPL", "AAPL", "Apple Inc.", Type.EQUITY, "XNAS", "232.40", "230.10", true);
        stock("EQ-MSFT", "MSFT", "Microsoft Corporation", Type.EQUITY, "XNAS", "512.75", "509.90", true);
        stock("EQ-NVDA", "NVDA", "NVIDIA Corporation", Type.EQUITY, "XNAS", "186.20", "188.45", true);
        stock("EQ-AMZN", "AMZN", "Amazon.com, Inc.", Type.EQUITY, "XNAS", "228.30", "226.00", true);
        stock("ETF-SPY", "SPY", "SPDR S&P 500 ETF Trust", Type.ETF, "ARCX", "668.10", "664.80", true);
        stock("EQ-HALT", "HALT", "Halted Holdings (suspended)", Type.EQUITY, "XNYS", "12.00", "12.00", false);

        LocalDate today = LocalDate.now(clock);
        for (int monthsAhead = 1; monthsAhead <= 2; monthsAhead++) {
            LocalDate expiration = today.plusMonths(monthsAhead).withDayOfMonth(1)
                    .with(TemporalAdjusters.dayOfWeekInMonth(3, DayOfWeek.FRIDAY));
            for (int strike : new int[] { 220, 230, 240 }) {
                option(instruments.get("EQ-AAPL"), expiration, Right.CALL, strike);
                option(instruments.get("EQ-AAPL"), expiration, Right.PUT, strike);
            }
        }
    }

    // ---------------------------------------------------------------- catalog

    public Instrument instrument(String id) {
        Instrument instrument = instruments.get(id);
        if (instrument == null) {
            throw ApiException.notFound("Instrument", id);
        }
        return instrument;
    }

    public List<Instrument> search(String symbol, Type type) {
        return instruments.values().stream()
                .filter(i -> symbol == null || i.symbol().equalsIgnoreCase(symbol))
                .filter(i -> type == null || i.type() == type)
                .toList();
    }

    public List<LocalDate> expirations(String underlyingId) {
        return options(underlyingId).stream()
                .map(i -> i.option().expiration())
                .distinct().sorted().toList();
    }

    public List<Instrument> optionChain(String underlyingId, LocalDate expiration) {
        return options(underlyingId).stream()
                .filter(i -> i.option().expiration().equals(expiration))
                .toList();
    }

    private List<Instrument> options(String underlyingId) {
        return instruments.values().stream()
                .filter(i -> i.isOption() && i.option().underlyingId().equals(underlyingId))
                .toList();
    }

    // ---------------------------------------------------------------- prices

    public Tick tick(String instrumentId) {
        return ticks.get(instrument(instrumentId).id());
    }

    /** One step of the simulated market. Called on a schedule by the order matcher. */
    public synchronized void move() {
        for (Instrument instrument : instruments.values()) {
            if (instrument.isOption() || !instrument.tradable()) {
                continue;
            }
            Tick tick = ticks.get(instrument.id());
            double step = (random.nextGaussian() * 0.0008) * tick.last().doubleValue();
            BigDecimal last = tick.last().add(BigDecimal.valueOf(step))
                    .setScale(2, RoundingMode.HALF_EVEN);
            long volume = tick.volume() + random.nextInt(400);
            ticks.put(instrument.id(), stockTick(last, tick.previousClose(), volume, tick.sequence() + 1));
        }
        instruments.values().stream().filter(Instrument::isOption).forEach(this::priceOption);
    }

    // ---------------------------------------------------------------- seeding

    private void stock(String id, String symbol, String name, Type type, String exchange,
            String last, String previousClose, boolean tradable) {
        instruments.put(id, new Instrument(id, symbol, name, type, exchange, "USD", tradable, tradable,
                BigDecimal.ONE, null));
        ticks.put(id, stockTick(new BigDecimal(last), new BigDecimal(previousClose), 1_250_000, 1));
    }

    private void option(Instrument underlying, LocalDate expiration, Right right, int strike) {
        String id = "OPT-%s-%s-%s-%d".formatted(underlying.symbol(), expiration.format(OPTION_DATE),
                right == Right.CALL ? "C" : "P", strike);
        String name = "%s %s $%d %s".formatted(underlying.symbol(), expiration, strike, right);
        OptionTerms terms = new OptionTerms(underlying.id(), underlying.symbol(), right,
                BigDecimal.valueOf(strike), expiration, "AMERICAN");
        instruments.put(id, new Instrument(id, underlying.symbol(), name, Type.OPTION, "OPRA", "USD",
                true, false, BigDecimal.valueOf(100), terms));
        priceOption(instruments.get(id));
    }

    private Tick stockTick(BigDecimal last, BigDecimal previousClose, long volume, long sequence) {
        BigDecimal halfSpread = last.multiply(new BigDecimal("0.0001")).max(CENT)
                .setScale(2, RoundingMode.HALF_EVEN);
        return new Tick(last, last.subtract(halfSpread), last.add(halfSpread), BigDecimal.valueOf(500),
                BigDecimal.valueOf(500), previousClose, volume, sequence, clock.instant());
    }

    /** Intrinsic value plus a simple time value: enough to look plausible, not a pricing model. */
    private void priceOption(Instrument option) {
        OptionTerms terms = option.option();
        BigDecimal underlying = ticks.get(terms.underlyingId()).last();
        BigDecimal intrinsic = (terms.right() == Right.CALL
                ? underlying.subtract(terms.strike())
                : terms.strike().subtract(underlying)).max(BigDecimal.ZERO);
        long days = Math.max(1, ChronoUnit.DAYS.between(LocalDate.now(clock), terms.expiration()));
        BigDecimal timeValue = underlying.multiply(BigDecimal.valueOf(0.012 * Math.sqrt(days / 30.0)));
        BigDecimal mid = intrinsic.add(timeValue).setScale(2, RoundingMode.HALF_EVEN);
        Tick previous = ticks.get(option.id());
        long sequence = previous == null ? 1 : previous.sequence() + 1;
        BigDecimal previousClose = previous == null ? mid : previous.previousClose();
        BigDecimal nickel = new BigDecimal("0.05");
        ticks.put(option.id(), new Tick(mid, mid.subtract(nickel), mid.add(nickel), BigDecimal.valueOf(50),
                BigDecimal.valueOf(50), previousClose, 3_400, sequence, clock.instant()));
    }
}
