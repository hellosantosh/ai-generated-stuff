package com.example.brokerage.api.accounts;

import java.math.BigDecimal;
import java.math.RoundingMode;
import java.time.Clock;
import java.time.DayOfWeek;
import java.time.Duration;
import java.time.Instant;
import java.time.LocalDate;
import java.time.ZoneOffset;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.concurrent.atomic.AtomicInteger;

import com.example.brokerage.api.accounts.Account.Registration;
import com.example.brokerage.api.accounts.Transaction.Type;
import com.example.brokerage.api.market.Instrument;
import com.example.brokerage.api.market.MarketData;
import com.example.brokerage.api.platform.ApiException;

import org.springframework.stereotype.Component;

/**
 * The books of record: accounts, cash, holdings and transaction history, held in memory
 * and seeded with two demo customers. Every mutation goes through {@link #recordTrade},
 * which keeps cash, holdings and history consistent with one another.
 */
@Component
public class Ledger {

    /** One holding: how much, and at what average cost per unit. */
    public record Holding(String instrumentId, BigDecimal quantity, BigDecimal averageCost) {
    }

    private static final class Book {
        BigDecimal cash;
        final Map<String, Holding> holdings = new LinkedHashMap<>();
        final List<Transaction> transactions = new ArrayList<>();

        Book(BigDecimal cash) {
            this.cash = cash;
        }
    }

    private final Map<String, Account> accounts = new LinkedHashMap<>();
    private final Map<String, Book> books = new LinkedHashMap<>();
    private final AtomicInteger transactionIds = new AtomicInteger(1000);
    private final MarketData market;
    private final Clock clock;

    public Ledger(MarketData market, Clock clock) {
        this.market = market;
        this.clock = clock;
        seed();
    }

    // ---------------------------------------------------------------- reads

    public List<Account> accounts(String customerId) {
        return accounts.values().stream().filter(a -> a.customerId().equals(customerId)).toList();
    }

    /**
     * An account the customer owns. Someone else's account is reported as not found,
     * not as forbidden: a 403 would confirm that the account exists.
     */
    public Account account(String customerId, String accountId) {
        Account account = accounts.get(accountId);
        if (account == null || !account.customerId().equals(customerId)) {
            throw ApiException.notFound("Account", accountId);
        }
        return account;
    }

    public synchronized BigDecimal cash(String accountId) {
        return books.get(accountId).cash;
    }

    public synchronized List<Holding> holdings(String accountId) {
        return List.copyOf(books.get(accountId).holdings.values());
    }

    public synchronized BigDecimal quantityHeld(String accountId, String instrumentId) {
        Holding holding = books.get(accountId).holdings.get(instrumentId);
        return holding == null ? BigDecimal.ZERO : holding.quantity();
    }

    public synchronized List<Transaction> transactions(String accountId) {
        return List.copyOf(books.get(accountId).transactions);
    }

    // ---------------------------------------------------------------- writes

    /**
     * Books one execution. A positive quantity is a purchase, a negative one a sale.
     * Cash moves by quantity x price x multiplier, plus or minus commission.
     */
    public synchronized Transaction recordTrade(String accountId, Instrument instrument,
            BigDecimal signedQuantity, BigDecimal price, BigDecimal commission, String orderId,
            Instant at) {
        Book book = books.get(accountId);
        BigDecimal quantity = signedQuantity.abs();
        BigDecimal gross = quantity.multiply(price).multiply(instrument.multiplier());
        boolean buy = signedQuantity.signum() > 0;
        BigDecimal cashChange = (buy ? gross.negate() : gross).subtract(commission)
                .setScale(2, RoundingMode.HALF_EVEN);
        book.cash = book.cash.add(cashChange);

        Holding old = book.holdings.get(instrument.id());
        BigDecimal oldQuantity = old == null ? BigDecimal.ZERO : old.quantity();
        BigDecimal newQuantity = oldQuantity.add(signedQuantity);
        if (newQuantity.signum() == 0) {
            book.holdings.remove(instrument.id());
        } else if (buy) {
            BigDecimal oldCost = old == null ? BigDecimal.ZERO
                    : old.quantity().multiply(old.averageCost());
            BigDecimal averageCost = oldCost.add(quantity.multiply(price))
                    .divide(newQuantity, 4, RoundingMode.HALF_EVEN);
            book.holdings.put(instrument.id(), new Holding(instrument.id(), newQuantity, averageCost));
        } else {
            BigDecimal averageCost = old == null ? price : old.averageCost();
            book.holdings.put(instrument.id(), new Holding(instrument.id(), newQuantity, averageCost));
        }

        String what = instrument.symbol() + (instrument.isOption() ? " option" : "");
        String description = "%s %s %s @ %s".formatted(buy ? "Bought" : "Sold",
                quantity.stripTrailingZeros().toPlainString(), what, price.toPlainString());
        Transaction transaction = new Transaction(nextTransactionId(), Type.TRADE, description, cashChange,
                instrument.id(), instrument.symbol(), quantity, price, orderId, at, settlementDate(at));
        book.transactions.add(transaction);
        return transaction;
    }

    /** US equity and option trades settle one business day after the trade date (T+1). */
    private static LocalDate settlementDate(Instant tradeTime) {
        LocalDate date = tradeTime.atZone(ZoneOffset.UTC).toLocalDate().plusDays(1);
        while (date.getDayOfWeek() == DayOfWeek.SATURDAY || date.getDayOfWeek() == DayOfWeek.SUNDAY) {
            date = date.plusDays(1);
        }
        return date;
    }

    private String nextTransactionId() {
        return "TXN-" + transactionIds.incrementAndGet();
    }

    // ---------------------------------------------------------------- seed data

    private void seed() {
        open(new Account("ACC-1001", "cust-1001", "****4821", "Individual Margin", Account.Type.MARGIN,
                Registration.INDIVIDUAL, "USD", LocalDate.of(2021, 3, 15)));
        open(new Account("ACC-1002", "cust-1001", "****7304", "Roth IRA", Account.Type.CASH,
                Registration.ROTH_IRA, "USD", LocalDate.of(2023, 1, 9)));
        open(new Account("ACC-2001", "cust-2002", "****5518", "Individual Cash", Account.Type.CASH,
                Registration.INDIVIDUAL, "USD", LocalDate.of(2024, 6, 2)));

        // Every cash movement is a transaction, so balances reconcile with the history.
        deposit("ACC-1001", "50000.00", 400);
        deposit("ACC-1001", "10000.00", 120);
        historicalBuy("ACC-1001", "EQ-AAPL", "50", "180.25", 360);
        historicalBuy("ACC-1001", "EQ-MSFT", "20", "402.10", 300);
        historicalBuy("ACC-1001", "ETF-SPY", "15", "540.00", 200);
        dividend("ACC-1001", "EQ-AAPL", "13.00", 45);
        dividend("ACC-1001", "ETF-SPY", "26.85", 20);
        historicalBuy("ACC-1001", "EQ-AMZN", "12", "205.40", 9);

        deposit("ACC-1002", "12000.00", 600);
        historicalBuy("ACC-1002", "EQ-NVDA", "40", "120.50", 500);
        dividend("ACC-1002", "EQ-NVDA", "0.40", 60);

        deposit("ACC-2001", "5000.00", 90);
        historicalBuy("ACC-2001", "EQ-AMZN", "10", "190.00", 80);
        books.values().forEach(book -> book.transactions.sort(Comparator.comparing(Transaction::occurredAt)));
    }

    private void open(Account account) {
        accounts.put(account.id(), account);
        books.put(account.id(), new Book(BigDecimal.ZERO));
    }

    private Instant daysAgo(int days) {
        return clock.instant().minus(Duration.ofDays(days)).minus(Duration.ofMinutes(days * 7L % 300));
    }

    private void deposit(String accountId, String amount, int daysAgo) {
        Book book = books.get(accountId);
        BigDecimal cash = new BigDecimal(amount);
        book.cash = book.cash.add(cash);
        Instant at = daysAgo(daysAgo);
        book.transactions.add(new Transaction(nextTransactionId(), Type.DEPOSIT, "ACH deposit",
                cash, null, null, null, null, null, at, settlementDate(at)));
    }

    private void dividend(String accountId, String instrumentId, String amount, int daysAgo) {
        Instrument instrument = market.instrument(instrumentId);
        Book book = books.get(accountId);
        BigDecimal cash = new BigDecimal(amount);
        book.cash = book.cash.add(cash);
        Instant at = daysAgo(daysAgo);
        LocalDate paid = at.atZone(ZoneOffset.UTC).toLocalDate(); // dividends are paid in cash, settled
        book.transactions.add(new Transaction(nextTransactionId(), Type.DIVIDEND,
                "Cash dividend " + instrument.symbol(), cash, instrument.id(), instrument.symbol(),
                null, null, null, at, paid));
    }

    private void historicalBuy(String accountId, String instrumentId, String quantity, String price,
            int daysAgo) {
        recordTrade(accountId, market.instrument(instrumentId), new BigDecimal(quantity),
                new BigDecimal(price), BigDecimal.ZERO, null, daysAgo(daysAgo));
    }
}
