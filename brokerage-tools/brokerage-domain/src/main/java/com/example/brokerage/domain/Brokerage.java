package com.example.brokerage.domain;

import java.util.List;

import org.springframework.stereotype.Component;

/**
 * One door into the domain, so that a tool handler depends on the brokerage rather than on seven
 * of its parts.
 *
 * <p>This is not an abstraction for its own sake. A handler is the one place in the system where
 * the firm's own correctness and a language model's improvisation meet, and it should be short
 * enough to read in one sitting. Every line a handler spends wiring domain services together is a
 * line nobody reviews carefully.
 */
@Component
public class Brokerage {

    private final Accounts accounts;
    private final Instruments instruments;
    private final MarketData marketData;
    private final Orders orders;
    private final RiskEngine risk;
    private final ConfirmationTokens confirmations;
    private final MarketClock clock;

    public Brokerage(Accounts accounts, Instruments instruments, MarketData marketData,
                     Orders orders, RiskEngine risk, ConfirmationTokens confirmations,
                     MarketClock clock) {
        this.accounts = accounts;
        this.instruments = instruments;
        this.marketData = marketData;
        this.orders = orders;
        this.risk = risk;
        this.confirmations = confirmations;
        this.clock = clock;
        seedOrders();
    }

    /** Put some working orders in the demonstration account, so the read tools have something to say. */
    private void seedOrders() {
        List<Accounts.Account> demo = accounts.forCustomer(Accounts.DEMO_CUSTOMER);
        if (!demo.isEmpty()) {
            orders.seed(demo.getFirst().accountId());
        }
    }

    public Accounts accounts() {
        return accounts;
    }

    public Instruments instruments() {
        return instruments;
    }

    public MarketData marketData() {
        return marketData;
    }

    public Orders orders() {
        return orders;
    }

    public RiskEngine risk() {
        return risk;
    }

    public ConfirmationTokens confirmations() {
        return confirmations;
    }

    public MarketClock clock() {
        return clock;
    }
}
