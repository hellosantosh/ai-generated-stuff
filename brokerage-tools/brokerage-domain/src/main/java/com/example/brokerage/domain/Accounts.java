package com.example.brokerage.domain;

import java.math.BigDecimal;
import java.math.RoundingMode;
import java.time.LocalDate;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Optional;

import org.springframework.stereotype.Component;

/**
 * Accounts and what is in them.
 *
 * <p>The demonstration book of business is deliberately awkward. One customer has four accounts,
 * two of which are retirement accounts where a short sale is impossible and a wash sale is
 * irrelevant; one is a cash account, where "buying power" means something different from the
 * margin account next to it; and one is restricted. An agent that was only ever shown a single
 * margin account will confidently do the wrong thing on three of the four, and the only way to
 * find that out before a customer does is to make the test data look like the real thing.
 */
@Component
public class Accounts {

    /** What the account may do, which decides more rules than the balance does. */
    public enum AccountType {
        CASH("Cash", false, false),
        MARGIN("Margin", true, true),
        IRA_TRADITIONAL("Traditional IRA", false, false),
        ROTH_IRA("Roth IRA", false, false);

        private final String label;
        private final boolean marginAllowed;
        private final boolean shortingAllowed;

        AccountType(String label, boolean marginAllowed, boolean shortingAllowed) {
            this.label = label;
            this.marginAllowed = marginAllowed;
            this.shortingAllowed = shortingAllowed;
        }

        public String label() {
            return label;
        }

        public boolean marginAllowed() {
            return marginAllowed;
        }

        public boolean shortingAllowed() {
            return shortingAllowed;
        }

        public boolean retirement() {
            return this == IRA_TRADITIONAL || this == ROTH_IRA;
        }
    }

    /** Why an account cannot trade, when it cannot. Null for a healthy account. */
    public enum Restriction {
        /** Awaiting a deposit to settle. Buys allowed up to settled cash only. */
        UNSETTLED_FUNDS,
        /** Flagged as a pattern day trader below the equity minimum. Closing orders only. */
        DAY_TRADE_RESTRICTED,
        /** Under review. Nothing may be placed. */
        FROZEN
    }

    public record Account(String accountId, String nickname, AccountType type, String currency,
                          String customerId, LocalDate openedOn, Restriction restriction,
                          BigDecimal cash, BigDecimal settledCash, BigDecimal marginLoan,
                          int dayTradesUsed) {

        public boolean tradeable() {
            return restriction != Restriction.FROZEN;
        }
    }

    /**
     * The money view. Four numbers, and the reason there are four rather than one is that a
     * customer asking "how much do I have" means a different one each time: {@code cash} is what
     * the ledger says, {@code settledCash} is what may be spent today without a good-faith
     * violation, {@code buyingPower} is what may be spent on this instrument in this account, and
     * {@code totalEquity} is what the statement will show.
     */
    public record Balances(String accountId, String currency, BigDecimal cash,
                           BigDecimal settledCash, BigDecimal cashReservedForOrders,
                           BigDecimal buyingPower, BigDecimal marketValue, BigDecimal totalEquity,
                           BigDecimal marginLoan, BigDecimal maintenanceRequirement,
                           BigDecimal maintenanceExcess, int dayTradesUsed) {
    }

    /** One holding, aggregated over its lots. */
    public record Position(String accountId, String symbol, String name, String sector,
                           long quantity, BigDecimal averageCost, BigDecimal lastPrice,
                           BigDecimal marketValue, BigDecimal unrealizedPnl,
                           BigDecimal unrealizedPnlPercent, BigDecimal portfolioWeight) {
    }

    /**
     * One tax lot. This exists because "sell my Apple" is not one decision but two, and the second
     * one — which shares — is worth real money to the customer. An agent that can see lots can
     * ask the useful question; an agent that cannot will silently sell the oldest.
     */
    public record TaxLot(String lotId, String accountId, String symbol, long quantity,
                         BigDecimal costBasisPerShare, LocalDate acquiredOn, boolean longTerm,
                         BigDecimal unrealizedPnl, boolean washSaleRisk) {
    }

    private final Map<String, Account> accounts = new LinkedHashMap<>();
    private final List<TaxLot> lots = new ArrayList<>();
    private final Instruments instruments;
    private final MarketData marketData;
    private final MarketClock clock;

    public Accounts(Instruments instruments, MarketData marketData, MarketClock clock) {
        this.instruments = instruments;
        this.marketData = marketData;
        this.clock = clock;
        seed();
    }

    public static final String DEMO_CUSTOMER = "cust_7QK3M8W2PR5NXVTZ";

    private void seed() {
        String brokerage = add("Everyday brokerage", AccountType.MARGIN, "182430.55", "182430.55",
                "24000.00", null, 2);
        String cash = add("Cash account", AccountType.CASH, "15200.00", "9800.00", "0", null, 0);
        String roth = add("Roth IRA", AccountType.ROTH_IRA, "48110.12", "48110.12", "0", null, 0);
        add("Rollover IRA", AccountType.IRA_TRADITIONAL, "4300.00", "4300.00", "0",
                Restriction.FROZEN, 0);

        lot(brokerage, "AAPL", 240, "148.22", LocalDate.of(2022, 3, 14), false);
        lot(brokerage, "AAPL", 60, "221.40", LocalDate.of(2026, 7, 2), true);
        lot(brokerage, "MSFT", 85, "398.10", LocalDate.of(2024, 1, 19), false);
        lot(brokerage, "NVDA", 400, "132.55", LocalDate.of(2025, 11, 7), false);
        lot(brokerage, "VOO", 150, "512.90", LocalDate.of(2023, 9, 1), false);
        lot(brokerage, "GME", 500, "28.90", LocalDate.of(2026, 8, 20), true);
        lot(cash, "SCHD", 300, "25.10", LocalDate.of(2025, 2, 11), false);
        lot(cash, "KO", 120, "63.44", LocalDate.of(2024, 6, 6), false);
        lot(roth, "VOO", 70, "440.15", LocalDate.of(2021, 4, 23), false);
        lot(roth, "QQQM", 180, "191.60", LocalDate.of(2024, 10, 30), false);
    }

    private String add(String nickname, AccountType type, String cash, String settled,
                       String marginLoan, Restriction restriction, int dayTrades) {
        String id = Ids.account(accounts.size());
        accounts.put(id, new Account(id, nickname, type, Money.USD, DEMO_CUSTOMER,
                LocalDate.of(2019, 5, 2), restriction, Money.of(cash), Money.of(settled),
                Money.of(marginLoan), dayTrades));
        return id;
    }

    private void lot(String accountId, String symbol, long quantity, String costBasis,
                     LocalDate acquiredOn, boolean washSaleRisk) {
        lots.add(new TaxLot(Ids.lot(lots.size()), accountId, symbol, quantity, Money.of(costBasis),
                acquiredOn, false, BigDecimal.ZERO, washSaleRisk));
    }

    public List<Account> forCustomer(String customerId) {
        return accounts.values().stream().filter(a -> a.customerId().equals(customerId)).toList();
    }

    public Optional<Account> find(String accountId) {
        return Optional.ofNullable(accounts.get(accountId));
    }

    public void replace(Account account) {
        accounts.put(account.accountId(), account);
    }

    // ------------------------------------------------------------------ derived views

    public List<TaxLot> lotsOf(String accountId, String symbol) {
        LocalDate today = clock.now().atZone(MarketClock.EXCHANGE).toLocalDate();
        return lots.stream()
                .filter(lot -> lot.accountId().equals(accountId))
                .filter(lot -> symbol == null || lot.symbol().equals(symbol))
                .map(lot -> {
                    BigDecimal last = marketData.quote(lot.symbol()).last();
                    boolean longTerm = lot.acquiredOn().plusYears(1).isBefore(today);
                    BigDecimal pnl = last.subtract(lot.costBasisPerShare())
                            .multiply(BigDecimal.valueOf(lot.quantity()));
                    return new TaxLot(lot.lotId(), lot.accountId(), lot.symbol(), lot.quantity(),
                            lot.costBasisPerShare(), lot.acquiredOn(), longTerm,
                            Money.cents(pnl), lot.washSaleRisk());
                })
                .toList();
    }

    public List<Position> positionsOf(String accountId) {
        Map<String, List<TaxLot>> bySymbol = new LinkedHashMap<>();
        for (TaxLot lot : lotsOf(accountId, null)) {
            bySymbol.computeIfAbsent(lot.symbol(), ignored -> new ArrayList<>()).add(lot);
        }
        BigDecimal total = BigDecimal.ZERO;
        Map<String, BigDecimal> values = new LinkedHashMap<>();
        for (var entry : bySymbol.entrySet()) {
            BigDecimal last = marketData.quote(entry.getKey()).last();
            long quantity = entry.getValue().stream().mapToLong(TaxLot::quantity).sum();
            BigDecimal value = Money.times(last, quantity);
            values.put(entry.getKey(), value);
            total = total.add(value);
        }
        List<Position> positions = new ArrayList<>();
        for (var entry : bySymbol.entrySet()) {
            String symbol = entry.getKey();
            Instrument instrument = instruments.require(symbol);
            long quantity = entry.getValue().stream().mapToLong(TaxLot::quantity).sum();
            BigDecimal cost = entry.getValue().stream()
                    .map(lot -> Money.times(lot.costBasisPerShare(), lot.quantity()))
                    .reduce(BigDecimal.ZERO, BigDecimal::add);
            BigDecimal average = cost.divide(BigDecimal.valueOf(quantity), 4, RoundingMode.HALF_UP);
            BigDecimal last = marketData.quote(symbol).last();
            BigDecimal value = values.get(symbol);
            BigDecimal pnl = value.subtract(cost);
            BigDecimal pnlPercent = cost.signum() == 0 ? BigDecimal.ZERO
                    : pnl.multiply(BigDecimal.valueOf(100)).divide(cost, 2, RoundingMode.HALF_UP);
            BigDecimal weight = total.signum() == 0 ? BigDecimal.ZERO
                    : value.multiply(BigDecimal.valueOf(100)).divide(total, 2, RoundingMode.HALF_UP);
            positions.add(new Position(accountId, symbol, instrument.name(), instrument.sector(),
                    quantity, average, last, Money.cents(value), Money.cents(pnl), pnlPercent,
                    weight));
        }
        return positions;
    }

    public long sharesHeld(String accountId, String symbol) {
        return lotsOf(accountId, symbol).stream().mapToLong(TaxLot::quantity).sum();
    }

    public Balances balancesOf(String accountId, BigDecimal reservedForOrders) {
        Account account = find(accountId).orElseThrow();
        BigDecimal marketValue = positionsOf(accountId).stream()
                .map(Position::marketValue).reduce(BigDecimal.ZERO, BigDecimal::add);
        BigDecimal equity = account.cash().add(marketValue).subtract(account.marginLoan());
        BigDecimal maintenance = marketValue.multiply(BigDecimal.valueOf(0.25));
        BigDecimal buyingPower = account.type().marginAllowed()
                ? equity.multiply(BigDecimal.valueOf(2)).subtract(marketValue).max(BigDecimal.ZERO)
                : account.settledCash();
        if (account.restriction() == Restriction.UNSETTLED_FUNDS) {
            buyingPower = buyingPower.min(account.settledCash());
        }
        if (account.restriction() == Restriction.FROZEN) {
            buyingPower = BigDecimal.ZERO;
        }
        return new Balances(accountId, account.currency(), Money.cents(account.cash()),
                Money.cents(account.settledCash()), Money.cents(reservedForOrders),
                Money.cents(buyingPower.subtract(reservedForOrders).max(BigDecimal.ZERO)),
                Money.cents(marketValue), Money.cents(equity), Money.cents(account.marginLoan()),
                Money.cents(maintenance), Money.cents(equity.subtract(maintenance)),
                account.dayTradesUsed());
    }
}
