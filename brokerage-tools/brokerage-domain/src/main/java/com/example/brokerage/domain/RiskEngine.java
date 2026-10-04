package com.example.brokerage.domain;

import java.math.BigDecimal;
import java.math.RoundingMode;
import java.util.ArrayList;
import java.util.List;

import org.springframework.stereotype.Component;

/**
 * What would happen if this order were placed.
 *
 * <p>This class is the reason the catalog has a {@code preview} tool at all. Everything a customer
 * needs to agree to an order — the cost, what it leaves them with, and the reasons it might be
 * refused — is computed here, before anything is sent anywhere, and handed back as data.
 *
 * <p>The distinction between a <em>blocker</em> and a <em>warning</em> is the most load-bearing
 * idea in the file. A blocker means the order would be refused: the agent must not proceed, and
 * the right move is to tell the customer why and, where the blocker is about size, what size
 * would work. A warning means the order would go through but the customer may not want it to.
 * Collapsing the two into one list of "issues" is how you get an agent that either ignores real
 * refusals or refuses to place anything at all.
 */
@Component
public class RiskEngine {

    /** Blocker codes. Stable strings, because the agent is taught what each one means. */
    public static final String INSUFFICIENT_BUYING_POWER = "INSUFFICIENT_BUYING_POWER";
    public static final String INSUFFICIENT_SHARES = "INSUFFICIENT_SHARES";
    public static final String MARKET_CLOSED = "MARKET_CLOSED";
    public static final String ACCOUNT_RESTRICTED = "ACCOUNT_RESTRICTED";
    public static final String SHORTING_NOT_PERMITTED = "SHORTING_NOT_PERMITTED";
    public static final String MARGIN_NOT_PERMITTED = "MARGIN_NOT_PERMITTED";
    public static final String DAY_TRADE_LIMIT = "DAY_TRADE_LIMIT";
    public static final String ORDER_SIZE_LIMIT = "ORDER_SIZE_LIMIT";
    public static final String PRICE_AWAY_FROM_MARKET = "PRICE_AWAY_FROM_MARKET";

    private static final long MAX_SHARES_PER_ORDER = 100_000L;
    private static final BigDecimal MAX_NOTIONAL = Money.of("500000.00");
    private static final BigDecimal CONCENTRATION_LIMIT = BigDecimal.valueOf(25);
    private static final int WIDE_SPREAD_BASIS_POINTS = 25;

    /**
     * One reason the order would be refused.
     *
     * @param maxPermissibleQuantity the largest size that would be accepted, when the blocker is
     *                               about size. Null otherwise — and the difference matters,
     *                               because an agent given a number can offer the customer a
     *                               smaller order instead of a dead end
     */
    public record Blocker(String code, String message, Long maxPermissibleQuantity) {

        static Blocker of(String code, String message) {
            return new Blocker(code, message, null);
        }
    }

    public record Estimate(BigDecimal referencePrice, BigDecimal estimatedPrincipal,
                           BigDecimal commission, BigDecimal estimatedCost,
                           BigDecimal buyingPowerBefore, BigDecimal buyingPowerAfter,
                           String currency, boolean isEstimate) {
    }

    public record Preview(boolean canPlace, List<Blocker> blockers, List<String> warnings,
                          Estimate estimate, String summary) {
    }

    private final Accounts accounts;
    private final Instruments instruments;
    private final MarketData marketData;
    private final MarketClock clock;
    private final Orders orders;

    public RiskEngine(Accounts accounts, Instruments instruments, MarketData marketData,
                      MarketClock clock, Orders orders) {
        this.accounts = accounts;
        this.instruments = instruments;
        this.marketData = marketData;
        this.clock = clock;
        this.orders = orders;
    }

    public Preview preview(Orders.Ticket ticket) {
        Accounts.Account account = accounts.find(ticket.accountId()).orElseThrow();
        Instrument instrument = instruments.require(ticket.symbol());
        MarketData.Quote quote = marketData.quote(ticket.symbol());
        Accounts.Balances balances =
                accounts.balancesOf(ticket.accountId(), orders.cashReservedFor(ticket.accountId()));

        boolean buying = ticket.side() == Orders.Side.BUY || ticket.side() == Orders.Side.BUY_TO_COVER;
        BigDecimal reference = switch (ticket.orderType()) {
            case MARKET -> buying ? quote.ask() : quote.bid();
            case LIMIT, STOP_LIMIT -> ticket.limitPrice();
            case STOP -> ticket.stopPrice();
        };
        BigDecimal principal = Money.times(reference, ticket.quantity());
        BigDecimal commission = commissionFor(instrument, ticket, principal);
        BigDecimal cost = buying ? principal.add(commission) : principal.subtract(commission);

        List<Blocker> blockers = new ArrayList<>();
        List<String> warnings = new ArrayList<>();

        session(ticket, blockers, warnings);
        permissions(account, instrument, ticket, blockers);
        size(ticket, principal, blockers);
        funding(account, balances, ticket, buying, reference, commission, blockers);
        inventory(ticket, buying, blockers);
        pricing(ticket, quote, blockers, warnings);
        if (blockers.isEmpty()) {
            // Only worth saying when the order could actually go through. "This would be 6,794
            // percent of the account" on an order the account cannot fund is noise, and noise in
            // a warnings array is how an agent learns to skim it.
            concentration(ticket, principal, balances, warnings);
        }
        taxes(account, ticket, warnings);

        BigDecimal after = buying ? balances.buyingPower().subtract(cost)
                : balances.buyingPower().add(cost);
        Estimate estimate = new Estimate(Money.cents(reference), Money.cents(principal),
                Money.cents(commission), Money.cents(cost), balances.buyingPower(),
                Money.cents(after.max(BigDecimal.ZERO)), account.currency(),
                ticket.orderType() == Orders.OrderType.MARKET);

        String summary = "%s — estimated %s %s, leaving %s buying power".formatted(
                ticket.describe(), Money.string(cost), account.currency(),
                Money.string(after.max(BigDecimal.ZERO)));
        return new Preview(blockers.isEmpty(), blockers, warnings, estimate, summary);
    }

    // ------------------------------------------------------------------ individual checks

    private void session(Orders.Ticket ticket, List<Blocker> blockers, List<String> warnings) {
        MarketClock.Session session = clock.session();
        if (session == MarketClock.Session.CLOSED && ticket.timeInForce() != Orders.TimeInForce.GTC) {
            blockers.add(Blocker.of(MARKET_CLOSED,
                    "The market is closed and this order would expire before it could trade. The "
                            + "regular session next opens at " + clock.nextRegularOpen()
                            + ". A good-till-canceled order can be entered now instead."));
        }
        if (session != MarketClock.Session.REGULAR && session != MarketClock.Session.CLOSED) {
            if (ticket.orderType() == Orders.OrderType.MARKET) {
                blockers.add(Blocker.of(MARKET_CLOSED,
                        "Market orders are not accepted in the " + session.label()
                                + " session, where there may be no price to trade against. Use a "
                                + "limit order."));
            } else {
                warnings.add("This is the " + session.label() + " session: volume is thin, the "
                        + "spread is wider than usual, and the fill may be far from the last price.");
            }
        }
    }

    private void permissions(Accounts.Account account, Instrument instrument, Orders.Ticket ticket,
                             List<Blocker> blockers) {
        if (account.restriction() == Accounts.Restriction.FROZEN) {
            blockers.add(Blocker.of(ACCOUNT_RESTRICTED,
                    "This account is under review and cannot place orders. The customer should be "
                            + "directed to their service team."));
        }
        if (ticket.side() == Orders.Side.SELL_SHORT && !account.type().shortingAllowed()) {
            blockers.add(Blocker.of(SHORTING_NOT_PERMITTED,
                    "A " + account.type().label() + " account cannot sell short."));
        }
        if (!instrument.marginEligible() && account.type().marginAllowed()
                && ticket.side() == Orders.Side.BUY) {
            blockers.add(Blocker.of(MARGIN_NOT_PERMITTED,
                    instrument.symbol() + " is not marginable, so it must be bought with settled "
                            + "cash rather than buying power."));
        }
        if (account.restriction() == Accounts.Restriction.DAY_TRADE_RESTRICTED) {
            blockers.add(Blocker.of(DAY_TRADE_LIMIT,
                    "This account is restricted to closing orders until the day-trade flag clears."));
        }
    }

    private void size(Orders.Ticket ticket, BigDecimal principal, List<Blocker> blockers) {
        if (ticket.quantity() > MAX_SHARES_PER_ORDER) {
            blockers.add(new Blocker(ORDER_SIZE_LIMIT,
                    "A single order may not exceed " + MAX_SHARES_PER_ORDER + " shares.",
                    MAX_SHARES_PER_ORDER));
        }
        if (principal.compareTo(MAX_NOTIONAL) > 0) {
            long permissible = MAX_NOTIONAL.divide(
                    principal.divide(BigDecimal.valueOf(ticket.quantity()), 6, RoundingMode.HALF_UP),
                    0, RoundingMode.DOWN).longValue();
            blockers.add(new Blocker(ORDER_SIZE_LIMIT,
                    "A single order may not exceed " + Money.string(MAX_NOTIONAL)
                            + " in notional value. The largest size that would be accepted at this "
                            + "price is " + permissible + " shares.", permissible));
        }
    }

    private void funding(Accounts.Account account, Accounts.Balances balances, Orders.Ticket ticket,
                         boolean buying, BigDecimal reference, BigDecimal commission,
                         List<Blocker> blockers) {
        if (!buying) {
            return;
        }
        BigDecimal need = Money.times(reference, ticket.quantity()).add(commission);
        BigDecimal available = account.type().marginAllowed()
                ? balances.buyingPower() : balances.settledCash();
        if (need.compareTo(available) > 0) {
            long permissible = available.subtract(commission).max(BigDecimal.ZERO)
                    .divide(reference, 0, RoundingMode.DOWN).longValue();
            blockers.add(new Blocker(INSUFFICIENT_BUYING_POWER,
                    "This order needs " + Money.string(need) + " " + account.currency()
                            + " and the account has " + Money.string(available)
                            + " available. The largest size this account could buy at this price "
                            + "is " + permissible + " shares.",
                    permissible));
        }
    }

    private void inventory(Orders.Ticket ticket, boolean buying, List<Blocker> blockers) {
        if (buying || ticket.side() == Orders.Side.SELL_SHORT) {
            return;
        }
        long held = accounts.sharesHeld(ticket.accountId(), ticket.symbol());
        if (ticket.quantity() > held) {
            blockers.add(new Blocker(INSUFFICIENT_SHARES,
                    "The account holds " + held + " shares of " + ticket.symbol()
                            + " and this order would sell " + ticket.quantity()
                            + ". Selling more than is held is a short sale and must be entered as "
                            + "SELL_SHORT.", held));
        }
    }

    private void pricing(Orders.Ticket ticket, MarketData.Quote quote, List<Blocker> blockers,
                         List<String> warnings) {
        if (quote.spreadBasisPoints() >= WIDE_SPREAD_BASIS_POINTS) {
            warnings.add("The spread on " + ticket.symbol() + " is "
                    + quote.spreadBasisPoints() + " basis points, which is wide. A market order "
                    + "could fill well away from the last price of " + Money.price(quote.last())
                    + "; a limit order would cap that.");
        }
        BigDecimal limit = ticket.limitPrice();
        if (limit == null) {
            return;
        }
        BigDecimal away = limit.subtract(quote.last()).abs()
                .multiply(BigDecimal.valueOf(100))
                .divide(quote.last(), 2, RoundingMode.HALF_UP);
        if (away.compareTo(BigDecimal.valueOf(30)) > 0) {
            blockers.add(Blocker.of(PRICE_AWAY_FROM_MARKET,
                    "A limit of " + Money.price(limit) + " is " + away
                            + " percent away from the last price of " + Money.price(quote.last())
                            + ". Orders that far from the market are rejected as a protection "
                            + "against a mistyped price."));
        } else if (away.compareTo(BigDecimal.valueOf(10)) > 0) {
            warnings.add("The limit of " + Money.price(limit) + " is " + away
                    + " percent away from the last price, so this order may sit unfilled for a "
                    + "long time. Confirm the price is what the customer intended.");
        }
    }

    private void concentration(Orders.Ticket ticket, BigDecimal principal,
                               Accounts.Balances balances, List<String> warnings) {
        if (balances.totalEquity().signum() <= 0) {
            return;
        }
        BigDecimal existing = accounts.positionsOf(ticket.accountId()).stream()
                .filter(position -> position.symbol().equals(ticket.symbol()))
                .map(Accounts.Position::marketValue)
                .findFirst().orElse(BigDecimal.ZERO);
        BigDecimal after = existing.add(principal).multiply(BigDecimal.valueOf(100))
                .divide(balances.totalEquity(), 1, RoundingMode.HALF_UP);
        if (after.compareTo(CONCENTRATION_LIMIT) > 0) {
            warnings.add("After this order, " + ticket.symbol() + " would be " + after
                    + " percent of the account. That is a concentrated position; say so before the "
                    + "customer confirms.");
        }
    }

    private void taxes(Accounts.Account account, Orders.Ticket ticket, List<String> warnings) {
        if (account.type().retirement() || ticket.side() != Orders.Side.SELL) {
            return;
        }
        List<Accounts.TaxLot> lots = accounts.lotsOf(ticket.accountId(), ticket.symbol());
        if (lots.stream().anyMatch(Accounts.TaxLot::washSaleRisk)) {
            warnings.add("Some lots of " + ticket.symbol() + " were bought within the last 30 days, "
                    + "so selling at a loss may trigger a wash sale. The customer may want to "
                    + "choose which lots to sell.");
        }
        if (lots.stream().anyMatch(lot -> !lot.longTerm())
                && lots.stream().anyMatch(Accounts.TaxLot::longTerm)) {
            warnings.add("This holding has both long-term and short-term lots, which are taxed "
                    + "differently. Offer the customer the lot breakdown before selling.");
        }
    }

    /**
     * Commission. Zero per trade on equities, which is the market reality, plus the regulatory
     * fees that are not zero and that a customer notices when the total is a few cents off.
     */
    private BigDecimal commissionFor(Instrument instrument, Orders.Ticket ticket,
                                     BigDecimal principal) {
        if (ticket.side() == Orders.Side.BUY || ticket.side() == Orders.Side.BUY_TO_COVER) {
            return Money.of("0.00");
        }
        BigDecimal secFee = principal.multiply(BigDecimal.valueOf(0.0000278));
        BigDecimal tafFee = BigDecimal.valueOf(ticket.quantity())
                .multiply(BigDecimal.valueOf(0.000166)).min(Money.of("8.30"));
        return Money.cents(secFee.add(tafFee));
    }
}
