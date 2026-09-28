package com.example.brokerage.api.accounts;

import java.math.BigDecimal;
import java.math.RoundingMode;
import java.time.Clock;
import java.util.List;

import com.example.brokerage.api.accounts.Holdings.Balances;
import com.example.brokerage.api.accounts.Holdings.Position;
import com.example.brokerage.api.market.Instrument;
import com.example.brokerage.api.market.MarketData;
import com.example.brokerage.api.platform.ApiException;

import org.springframework.stereotype.Service;

/**
 * Values the ledger at current market prices: positions and balances are computed on
 * every request, never stored, so they can never drift from the books of record.
 */
@Service
public class Portfolio {

    private final Ledger ledger;
    private final MarketData market;
    private final Reservations reservations;
    private final Clock clock;

    public Portfolio(Ledger ledger, MarketData market, Reservations reservations, Clock clock) {
        this.ledger = ledger;
        this.market = market;
        this.reservations = reservations;
        this.clock = clock;
    }

    public List<Position> positions(Account account) {
        return ledger.holdings(account.id()).stream().map(this::value).toList();
    }

    public Position position(Account account, String instrumentId) {
        return ledger.holdings(account.id()).stream()
                .filter(h -> h.instrumentId().equals(instrumentId))
                .findFirst()
                .map(this::value)
                .orElseThrow(() -> ApiException.notFound("Position", instrumentId));
    }

    public Balances balances(Account account) {
        List<Position> positions = positions(account);
        BigDecimal cash = ledger.cash(account.id());
        BigDecimal reserved = reservations.reservedCash(account.id());
        BigDecimal marketValue = positions.stream().map(Position::marketValue)
                .reduce(BigDecimal.ZERO, BigDecimal::add);
        BigDecimal unrealized = positions.stream().map(Position::unrealizedPnl)
                .reduce(BigDecimal.ZERO, BigDecimal::add);
        return new Balances(account.id(), account.currency(), cash, reserved,
                Holdings.buyingPower(account.type(), cash, reserved), marketValue, cash.add(marketValue),
                unrealized, clock.instant());
    }

    private Position value(Ledger.Holding holding) {
        Instrument instrument = market.instrument(holding.instrumentId());
        BigDecimal last = market.tick(instrument.id()).last();
        BigDecimal multiplier = instrument.multiplier();
        BigDecimal costBasis = money(holding.quantity().multiply(holding.averageCost()).multiply(multiplier));
        BigDecimal marketValue = money(holding.quantity().multiply(last).multiply(multiplier));
        BigDecimal pnl = marketValue.subtract(costBasis);
        BigDecimal pnlPercent = costBasis.signum() == 0 ? BigDecimal.ZERO
                : pnl.multiply(BigDecimal.valueOf(100)).divide(costBasis, 2, RoundingMode.HALF_EVEN);
        return new Position(instrument.id(), instrument.symbol(), instrument.type(), holding.quantity(),
                holding.averageCost().setScale(2, RoundingMode.HALF_EVEN), costBasis, last, marketValue, pnl,
                pnlPercent);
    }

    private static BigDecimal money(BigDecimal amount) {
        return amount.setScale(2, RoundingMode.HALF_EVEN);
    }
}
