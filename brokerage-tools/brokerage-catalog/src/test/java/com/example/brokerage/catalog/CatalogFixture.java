package com.example.brokerage.catalog;

import java.time.Instant;
import java.util.List;
import java.util.Set;

import com.example.brokerage.catalog.handlers.AccountGuard;
import com.example.brokerage.catalog.handlers.AccountsListHandler;
import com.example.brokerage.catalog.handlers.BalancesGetHandler;
import com.example.brokerage.catalog.handlers.ExecutionsListHandler;
import com.example.brokerage.catalog.handlers.InstrumentsSearchHandler;
import com.example.brokerage.catalog.handlers.OrderCancelHandler;
import com.example.brokerage.catalog.handlers.OrderGetHandler;
import com.example.brokerage.catalog.handlers.OrderPlaceHandler;
import com.example.brokerage.catalog.handlers.OrderPreviewHandler;
import com.example.brokerage.catalog.handlers.OrderReplaceHandler;
import com.example.brokerage.catalog.handlers.OrdersListHandler;
import com.example.brokerage.catalog.handlers.PositionsListHandler;
import com.example.brokerage.catalog.handlers.PriceHistoryGetHandler;
import com.example.brokerage.catalog.handlers.QuotesGetHandler;
import com.example.brokerage.catalog.handlers.TaxLotsListHandler;
import com.example.brokerage.catalog.handlers.Tickets;
import com.example.brokerage.domain.Accounts;
import com.example.brokerage.domain.Brokerage;
import com.example.brokerage.domain.ConfirmationTokens;
import com.example.brokerage.domain.Instruments;
import com.example.brokerage.domain.MarketClock;
import com.example.brokerage.domain.MarketData;
import com.example.brokerage.domain.Orders;
import com.example.brokerage.domain.RiskEngine;
import com.example.brokerage.tooling.contract.DescriptorCodec;
import com.example.brokerage.tooling.contract.Principal;
import com.example.brokerage.tooling.contract.ToolHandler;
import com.example.brokerage.tooling.runtime.ApprovalGate;
import com.example.brokerage.tooling.runtime.AuditLog;
import com.example.brokerage.tooling.runtime.InvocationPipeline;
import com.example.brokerage.tooling.runtime.KillSwitches;
import com.example.brokerage.tooling.runtime.RateLimiter;
import com.example.brokerage.tooling.runtime.ToolRegistry;

import tools.jackson.databind.JsonNode;

/**
 * The whole catalog, wired by hand, with no Spring and no network.
 *
 * <p>These are the identifiers the seeded book of business always has. They are constants rather
 * than lookups because a test that first calls {@code brokerage_accounts_list} to find out which
 * account to use is testing two things, and when it fails you do not know which.
 */
class CatalogFixture {

    static final String MARGIN_ACCOUNT = "acct_4RZ61FZAD0RMHNMM";
    static final String CASH_ACCOUNT = "acct_2GDR417WM0WF031F";
    static final String ROTH_ACCOUNT = "acct_TF7KNH2A136X695X";
    static final String FROZEN_ACCOUNT = "acct_V5WR2E9BFKVE7J8F";
    static final String WORKING_MSFT_ORDER = "ord_CZ9R91DVFPMN5BX0";
    static final String FILLED_NVDA_ORDER = "ord_M07KB16SEA01HW70";

    static final Set<String> READ_SCOPES = Set.of("brokerage:reference:read",
            "brokerage:market-data:read", "brokerage:accounts:read", "brokerage:balances:read",
            "brokerage:positions:read", "brokerage:orders:read");

    final MarketClock clock = new MarketClock();
    final Brokerage brokerage;
    final BrokerageCatalog catalog = new BrokerageCatalog();
    final ToolRegistry registry;
    final ApprovalGate approvals = new ApprovalGate();
    final AuditLog audit = new AuditLog();
    final KillSwitches switches = new KillSwitches();
    final InvocationPipeline pipeline;

    CatalogFixture() {
        Instruments instruments = new Instruments();
        MarketData marketData = new MarketData(instruments, clock);
        Accounts accounts = new Accounts(instruments, marketData, clock);
        Orders orders = new Orders(clock, marketData);
        RiskEngine risk = new RiskEngine(accounts, instruments, marketData, clock, orders);
        ConfirmationTokens confirmations = new ConfirmationTokens(clock);
        brokerage = new Brokerage(accounts, instruments, marketData, orders, risk, confirmations,
                clock);

        AccountGuard guard = new AccountGuard(brokerage);
        Tickets tickets = new Tickets(brokerage);
        List<ToolHandler> handlers = List.of(
                new AccountsListHandler(brokerage),
                new InstrumentsSearchHandler(brokerage),
                new QuotesGetHandler(brokerage),
                new PriceHistoryGetHandler(brokerage),
                new BalancesGetHandler(brokerage, guard),
                new PositionsListHandler(brokerage, guard),
                new TaxLotsListHandler(brokerage, guard),
                new OrdersListHandler(brokerage, guard),
                new OrderGetHandler(brokerage, guard),
                new ExecutionsListHandler(brokerage, guard),
                new OrderPreviewHandler(brokerage, guard, tickets),
                new OrderPlaceHandler(brokerage, guard, tickets),
                new OrderReplaceHandler(brokerage, guard),
                new OrderCancelHandler(brokerage, guard));
        registry = new ToolRegistry(catalog.ontology(), catalog.descriptors(), handlers);
        pipeline = new InvocationPipeline(registry, approvals, new RateLimiter(), switches, audit,
                new BrokerageApprovalContext(brokerage), clock::now);
    }

    Principal reader() {
        return new Principal("test:reader", Accounts.DEMO_CUSTOMER, READ_SCOPES, "session-read");
    }

    Principal trader() {
        Set<String> scopes = new java.util.LinkedHashSet<>(READ_SCOPES);
        scopes.add("brokerage:orders:write");
        return new Principal("test:trader", Accounts.DEMO_CUSTOMER, scopes, "session-trade");
    }

    /** Call a tool and return the payload, failing the test if the envelope was not ok. */
    JsonNode data(String tool, String arguments, Principal principal) {
        InvocationPipeline.Outcome outcome = pipeline.invoke(tool, arguments, principal);
        if (!outcome.ok()) {
            throw new AssertionError(tool + " failed: " + outcome.json());
        }
        return DescriptorCodec.tree(outcome.json()).path("data");
    }

    InvocationPipeline.Outcome call(String tool, String arguments, Principal principal) {
        return pipeline.invoke(tool, arguments, principal);
    }

    JsonNode error(String tool, String arguments, Principal principal) {
        return DescriptorCodec.tree(pipeline.invoke(tool, arguments, principal).json())
                .path("error");
    }

    /** Stand in for the customer saying yes, then call again with the identical arguments. */
    InvocationPipeline.Outcome callWithApproval(String tool, String arguments,
                                                Principal principal) {
        InvocationPipeline.Outcome first = pipeline.invoke(tool, arguments, principal);
        if (first.approvalReference() == null) {
            return first;
        }
        approvals.grant(first.approvalReference(), "test-customer", clock.now());
        return pipeline.invoke(tool, arguments, principal);
    }

    void moveClockTo(String instant) {
        clock.set(Instant.parse(instant));
    }
}
