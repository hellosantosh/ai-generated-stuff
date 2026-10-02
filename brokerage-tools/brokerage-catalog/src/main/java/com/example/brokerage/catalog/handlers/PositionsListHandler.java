package com.example.brokerage.catalog.handlers;

import java.math.BigDecimal;
import java.time.Instant;
import java.util.Comparator;
import java.util.List;

import com.example.brokerage.domain.Accounts;
import com.example.brokerage.domain.Brokerage;
import com.example.brokerage.domain.Money;
import com.example.brokerage.tooling.contract.Invocation;
import com.example.brokerage.tooling.contract.ToolHandler;

import org.springframework.stereotype.Component;

/**
 * {@code brokerage_positions_list}.
 *
 * <p>Sorted by market value, largest first, which is not a cosmetic choice. A model reads a list
 * from the top and a long list from the top only; putting the largest holdings first means that
 * whatever gets dropped from a truncated answer is the part that mattered least. Ordering a result
 * is the cheapest form of prioritization a tool has.
 */
@Component
public class PositionsListHandler implements ToolHandler {

    record PositionView(String symbol, String name, String sector, long quantity,
                        String averageCost, String lastPrice, String marketValue,
                        String unrealizedPnl, String unrealizedPnlPercent,
                        String portfolioWeight) {
    }

    record Payload(String accountId, String currency, String totalMarketValue,
                   List<PositionView> positions, Instant asOf) {
    }

    private final Brokerage brokerage;
    private final AccountGuard guard;

    public PositionsListHandler(Brokerage brokerage, AccountGuard guard) {
        this.brokerage = brokerage;
        this.guard = guard;
    }

    @Override
    public String name() {
        return "brokerage_positions_list";
    }

    @Override
    public Object handle(Invocation invocation) {
        Accounts.Account account = guard.require(invocation);
        String symbol = invocation.string("symbol");
        List<Accounts.Position> positions = brokerage.accounts().positionsOf(account.accountId())
                .stream()
                .filter(position -> symbol == null || position.symbol().equals(symbol))
                .sorted(Comparator.comparing(Accounts.Position::marketValue).reversed())
                .toList();
        BigDecimal total = positions.stream().map(Accounts.Position::marketValue)
                .reduce(BigDecimal.ZERO, BigDecimal::add);
        return new Payload(account.accountId(), account.currency(), Money.string(total),
                positions.stream().map(PositionsListHandler::view).toList(),
                brokerage.clock().now());
    }

    private static PositionView view(Accounts.Position position) {
        return new PositionView(position.symbol(), position.name(), position.sector(),
                position.quantity(), Money.price(position.averageCost()),
                Money.price(position.lastPrice()), Money.string(position.marketValue()),
                Money.string(position.unrealizedPnl()),
                position.unrealizedPnlPercent().toPlainString(),
                position.portfolioWeight().toPlainString());
    }
}
