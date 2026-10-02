package com.example.brokerage.harness;

import java.util.List;

import com.example.brokerage.catalog.BrokerageApprovalContext;
import com.example.brokerage.catalog.BrokerageCatalog;
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
import com.example.brokerage.tooling.contract.ToolHandler;
import com.example.brokerage.tooling.runtime.ApprovalGate;
import com.example.brokerage.tooling.runtime.AuditLog;
import com.example.brokerage.tooling.runtime.InvocationPipeline;
import com.example.brokerage.tooling.runtime.KillSwitches;
import com.example.brokerage.tooling.runtime.RateLimiter;
import com.example.brokerage.tooling.runtime.ToolPublisher;
import com.example.brokerage.tooling.runtime.ToolRegistry;

/**
 * The whole system, built by hand in one method.
 *
 * <p>No container, no profiles, no component scan. That is a deliberate property of a test harness
 * rather than a shortcut: a suite that boots an application context tests the application context
 * too, and when it fails you are debugging dependency injection instead of a descriptor. Twenty
 * lines of constructor calls are also the clearest statement of what the system is made of that
 * exists anywhere in this project.
 *
 * <p>Each harness run gets a fresh instance, so one scenario cannot leave an order in the book for
 * the next one to trip over. Evaluation suites that share state are the reason an agent suite
 * passes on its own and fails in the full run.
 */
public class HarnessContext {

    private final BrokerageCatalog catalog;
    private final Brokerage brokerage;
    private final ToolRegistry registry;
    private final ApprovalGate approvals;
    private final AuditLog audit;
    private final ToolPublisher publisher;

    public HarnessContext() {
        this.catalog = new BrokerageCatalog();
        MarketClock clock = new MarketClock();
        Instruments instruments = new Instruments();
        MarketData marketData = new MarketData(instruments, clock);
        Accounts accounts = new Accounts(instruments, marketData, clock);
        Orders orders = new Orders(clock, marketData);
        RiskEngine risk = new RiskEngine(accounts, instruments, marketData, clock, orders);
        ConfirmationTokens confirmations = new ConfirmationTokens(clock);
        this.brokerage = new Brokerage(accounts, instruments, marketData, orders, risk,
                confirmations, clock);

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

        this.registry = new ToolRegistry(catalog.ontology(), catalog.descriptors(), handlers);
        this.approvals = new ApprovalGate();
        this.audit = new AuditLog();
        InvocationPipeline pipeline = new InvocationPipeline(registry, approvals,
                new RateLimiter(), new KillSwitches(), audit,
                new BrokerageApprovalContext(brokerage), clock::now);
        this.publisher = new ToolPublisher(registry, pipeline);
    }

    public BrokerageCatalog catalog() {
        return catalog;
    }

    public Brokerage brokerage() {
        return brokerage;
    }

    public ToolRegistry registry() {
        return registry;
    }

    public ToolPublisher publisher() {
        return publisher;
    }

    public AuditLog audit() {
        return audit;
    }

    /**
     * Stand in for the human on every approval.
     *
     * <p>Approving automatically in a harness is a defensible thing to do and an indefensible
     * thing to do silently, so it is a named method rather than a default. A trading scenario that
     * never reaches the place tool is not testing the place tool; a suite that approves without
     * saying so is not testing the approval gate. The refusal suite leaves this off, which is how
     * the gate itself gets covered.
     */
    public ApprovalGate autoApprovingGate() {
        return approvals;
    }

    public void approveEverythingPending(String grantedBy) {
        approvals.awaiting(null).forEach(pending ->
                approvals.grant(pending.reference(), grantedBy, brokerage.clock().now()));
    }
}
