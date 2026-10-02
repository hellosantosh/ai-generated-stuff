package com.example.brokerage.catalog.handlers;

import java.time.Instant;
import java.time.LocalDate;
import java.util.Comparator;
import java.util.List;

import com.example.brokerage.domain.Accounts;
import com.example.brokerage.domain.Brokerage;
import com.example.brokerage.domain.Money;
import com.example.brokerage.tooling.contract.Invocation;
import com.example.brokerage.tooling.contract.ToolFailure;
import com.example.brokerage.tooling.contract.ToolHandler;

import org.springframework.stereotype.Component;

/**
 * {@code brokerage_tax_lots_list}.
 *
 * <p>The {@code taxable} flag is the field to notice. In a retirement account none of this means
 * anything, and an agent that launches into a long-versus-short-term explanation inside a Roth IRA
 * has just wasted the customer's time and damaged their confidence in the whole system. Rather
 * than leaving the model to infer irrelevance from the account type, the tool says so.
 *
 * <p>Note also what this tool does not do. There is no {@code sellLots} tool in this catalog.
 * Choosing lots has tax consequences that outlive the conversation, and this firm's position is
 * that a human instructs it. The agent's job is to put the choice in front of the customer, which
 * it can only do if it can see the lots.
 */
@Component
public class TaxLotsListHandler implements ToolHandler {

    record LotView(String lotId, long quantity, String costBasisPerShare, LocalDate acquiredOn,
                   boolean longTerm, String unrealizedPnl, boolean washSaleRisk) {
    }

    record Payload(String accountId, String symbol, boolean taxable, String defaultMethod,
                   List<LotView> lots, Instant asOf) {
    }

    private final Brokerage brokerage;
    private final AccountGuard guard;

    public TaxLotsListHandler(Brokerage brokerage, AccountGuard guard) {
        this.brokerage = brokerage;
        this.guard = guard;
    }

    @Override
    public String name() {
        return "brokerage_tax_lots_list";
    }

    @Override
    public Object handle(Invocation invocation) {
        Accounts.Account account = guard.require(invocation);
        String symbol = invocation.string("symbol");
        List<Accounts.TaxLot> lots = brokerage.accounts().lotsOf(account.accountId(), symbol);
        if (lots.isEmpty()) {
            throw ToolFailure.notFound("A holding in " + symbol + " in " + account.nickname(),
                    "Call brokerage_positions_list for this account to see what it actually holds, "
                            + "and tell the customer they do not hold this one.");
        }
        return new Payload(account.accountId(), symbol, !account.type().retirement(), "FIFO",
                lots.stream()
                        .sorted(Comparator.comparing(Accounts.TaxLot::acquiredOn))
                        .map(TaxLotsListHandler::view).toList(),
                brokerage.clock().now());
    }

    private static LotView view(Accounts.TaxLot lot) {
        return new LotView(lot.lotId(), lot.quantity(), Money.price(lot.costBasisPerShare()),
                lot.acquiredOn(), lot.longTerm(), Money.string(lot.unrealizedPnl()),
                lot.washSaleRisk());
    }
}
