package com.example.brokerage.catalog.handlers;

import java.time.Instant;

import com.example.brokerage.domain.Accounts;
import com.example.brokerage.domain.Brokerage;
import com.example.brokerage.domain.Money;
import com.example.brokerage.tooling.contract.Invocation;
import com.example.brokerage.tooling.contract.ToolHandler;

import org.springframework.stereotype.Component;

/**
 * {@code brokerage_balances_get}.
 *
 * <p>Four numbers where a naive design has one. The descriptor spends several sentences on which
 * number answers which question, and that prose is load-bearing: a model shown a single
 * {@code balance} field will quote it for "what can I spend", and in a cash account with an
 * unsettled deposit that answer is wrong in the direction that creates a violation.
 */
@Component
public class BalancesGetHandler implements ToolHandler {

    record Payload(String accountId, String currency, String cash, String settledCash,
                   String cashReservedForOrders, String buyingPower, String marketValue,
                   String totalEquity, String marginLoan, String maintenanceRequirement,
                   String maintenanceExcess, int dayTradesUsed, Instant asOf) {
    }

    private final Brokerage brokerage;
    private final AccountGuard guard;

    public BalancesGetHandler(Brokerage brokerage, AccountGuard guard) {
        this.brokerage = brokerage;
        this.guard = guard;
    }

    @Override
    public String name() {
        return "brokerage_balances_get";
    }

    @Override
    public Object handle(Invocation invocation) {
        Accounts.Account account = guard.require(invocation);
        Accounts.Balances balances = brokerage.accounts().balancesOf(account.accountId(),
                brokerage.orders().cashReservedFor(account.accountId()));
        return new Payload(balances.accountId(), balances.currency(),
                Money.string(balances.cash()), Money.string(balances.settledCash()),
                Money.string(balances.cashReservedForOrders()),
                Money.string(balances.buyingPower()), Money.string(balances.marketValue()),
                Money.string(balances.totalEquity()), Money.string(balances.marginLoan()),
                Money.string(balances.maintenanceRequirement()),
                Money.string(balances.maintenanceExcess()), balances.dayTradesUsed(),
                brokerage.clock().now());
    }
}
