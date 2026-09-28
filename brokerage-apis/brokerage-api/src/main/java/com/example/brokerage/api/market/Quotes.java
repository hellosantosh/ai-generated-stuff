package com.example.brokerage.api.market;

import java.math.BigDecimal;
import java.math.RoundingMode;
import java.time.Instant;

import org.springframework.hateoas.server.core.Relation;

/**
 * The two representations of a quote. Version 2 regroups price and size into bid and ask
 * objects and adds the day's change: a breaking change of shape, so it needs a new API
 * version rather than a new optional field.
 */
public final class Quotes {

    private Quotes() {
    }

    /** API version 1 (deprecated): flat fields. */
    @Relation(itemRelation = "quote")
    public record QuoteV1(String instrumentId, String symbol, BigDecimal last, BigDecimal bid,
            BigDecimal ask, BigDecimal bidSize, BigDecimal askSize, long volume, Instant asOf) {

        public static QuoteV1 of(Instrument instrument, MarketData.Tick tick) {
            return new QuoteV1(instrument.id(), instrument.symbol(), tick.last(), tick.bid(), tick.ask(),
                    tick.bidSize(), tick.askSize(), tick.volume(), tick.asOf());
        }
    }

    /** API version 2: bid and ask as price levels, plus the change since the previous close. */
    @Relation(itemRelation = "quote")
    public record Quote(String instrumentId, String symbol, BigDecimal last, PriceLevel bid, PriceLevel ask,
            BigDecimal change, BigDecimal changePercent, long volume, Instant asOf) {

        public record PriceLevel(BigDecimal price, BigDecimal size) {
        }

        public static Quote of(Instrument instrument, MarketData.Tick tick) {
            BigDecimal change = tick.last().subtract(tick.previousClose());
            BigDecimal percent = change.multiply(BigDecimal.valueOf(100))
                    .divide(tick.previousClose(), 2, RoundingMode.HALF_EVEN);
            return new Quote(instrument.id(), instrument.symbol(), tick.last(),
                    new PriceLevel(tick.bid(), tick.bidSize()), new PriceLevel(tick.ask(), tick.askSize()),
                    change, percent, tick.volume(), tick.asOf());
        }
    }
}
