package com.example.brokerage.catalog.handlers;

import java.time.Instant;
import java.time.LocalDate;
import java.time.temporal.ChronoUnit;
import java.util.List;

import com.example.brokerage.domain.Accounts;
import com.example.brokerage.domain.Brokerage;
import com.example.brokerage.domain.MarketClock;
import com.example.brokerage.domain.Money;
import com.example.brokerage.domain.Orders;
import com.example.brokerage.tooling.contract.Invocation;
import com.example.brokerage.tooling.contract.ToolFailure;
import com.example.brokerage.tooling.contract.ToolHandler;

import org.springframework.stereotype.Component;

/**
 * {@code brokerage_executions_list}.
 *
 * <p>The default window is 30 days. An unbounded default is how a tool that was fine in testing
 * returns four years of fills to a long-standing customer and blows the context window on the
 * first call of the conversation. Every list tool in this catalog has a bounded default, and the
 * descriptor says what it is.
 */
@Component
public class ExecutionsListHandler implements ToolHandler {

    record ExecutionView(String executionId, String orderId, String symbol, String side,
                         long quantity, String price, String principal, String commission,
                         Instant executedAt, LocalDate settlesOn, String venue) {
    }

    record Payload(String accountId, String currency, List<ExecutionView> executions,
                   String nextCursor, Instant asOf) {
    }

    private static final int DEFAULT_WINDOW_DAYS = 30;

    private final Brokerage brokerage;
    private final AccountGuard guard;

    public ExecutionsListHandler(Brokerage brokerage, AccountGuard guard) {
        this.brokerage = brokerage;
        this.guard = guard;
    }

    @Override
    public String name() {
        return "brokerage_executions_list";
    }

    @Override
    public Object handle(Invocation invocation) {
        Accounts.Account account = guard.require(invocation);
        Instant since = since(invocation);
        List<Orders.Execution> all = brokerage.orders().executionsOf(account.accountId(),
                invocation.string("symbol"), since);
        int from = Cursors.offset(invocation.string("cursor"));
        int limit = invocation.integer("limit", 20);
        List<Orders.Execution> page = all.stream().skip(from).limit(limit).toList();
        String next = from + page.size() < all.size() ? Cursors.encode(from + page.size()) : null;
        return new Payload(account.accountId(), account.currency(),
                page.stream().map(ExecutionsListHandler::view).toList(), next,
                brokerage.clock().now());
    }

    private Instant since(Invocation invocation) {
        String given = invocation.string("since");
        if (given == null) {
            return brokerage.clock().now().minus(DEFAULT_WINDOW_DAYS, ChronoUnit.DAYS);
        }
        try {
            return LocalDate.parse(given).atStartOfDay(MarketClock.EXCHANGE).toInstant();
        } catch (RuntimeException e) {
            throw ToolFailure.invalidArgument(
                    "since must be a date in the form 2026-09-01.",
                    "Re-send with an ISO date, or omit it for the last 30 days.");
        }
    }

    private static ExecutionView view(Orders.Execution execution) {
        return new ExecutionView(execution.executionId(), execution.orderId(), execution.symbol(),
                execution.side().name(), execution.quantity(), Money.price(execution.price()),
                Money.string(Money.times(execution.price(), execution.quantity())),
                Money.string(execution.commission()), execution.executedAt(),
                execution.settlesOn(), execution.venue());
    }
}
