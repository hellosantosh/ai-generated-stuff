package com.example.brokerage.api.accounts;

import java.math.BigDecimal;
import java.time.Instant;

import com.example.brokerage.api.market.Instrument;

import org.springframework.hateoas.server.core.Relation;

/**
 * The read models derived from the ledger and the market: positions and balances.
 */
public final class Holdings {

    private Holdings() {
    }

    /**
     * A holding valued at the current market price.
     *
     * @param averageCost per share, or per contract share for options
     */
    @Relation(collectionRelation = "positions", itemRelation = "position")
    public record Position(String instrumentId, String symbol, Instrument.Type instrumentType,
            BigDecimal quantity, BigDecimal averageCost, BigDecimal costBasis, BigDecimal lastPrice,
            BigDecimal marketValue, BigDecimal unrealizedPnl, BigDecimal unrealizedPnlPercent) {
    }

    /**
     * What the account is worth and what it can buy.
     *
     * @param reservedForOrders cash held back for open buy orders that have not filled yet
     */
    public record Balances(String accountId, String currency, BigDecimal cash, BigDecimal reservedForOrders,
            BigDecimal buyingPower, BigDecimal positionsMarketValue, BigDecimal totalEquity,
            BigDecimal unrealizedPnl, Instant asOf) {
    }

    /**
     * Buying power: unreserved cash in a cash account, twice that in a margin account
     * (the 50% initial margin of Regulation T). A deliberate simplification.
     */
    public static BigDecimal buyingPower(Account.Type type, BigDecimal cash, BigDecimal reserved) {
        BigDecimal free = cash.subtract(reserved).max(BigDecimal.ZERO);
        return type == Account.Type.MARGIN ? free.multiply(BigDecimal.TWO) : free;
    }
}
