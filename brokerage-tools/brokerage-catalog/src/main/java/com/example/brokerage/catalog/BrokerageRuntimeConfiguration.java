package com.example.brokerage.catalog;

import java.time.Instant;
import java.util.List;
import java.util.function.Supplier;

import com.example.brokerage.domain.MarketClock;
import com.example.brokerage.tooling.contract.ApprovalContext;
import com.example.brokerage.tooling.contract.ToolHandler;
import com.example.brokerage.tooling.runtime.ApprovalGate;
import com.example.brokerage.tooling.runtime.AuditLog;
import com.example.brokerage.tooling.runtime.InvocationPipeline;
import com.example.brokerage.tooling.runtime.KillSwitches;
import com.example.brokerage.tooling.runtime.RateLimiter;
import com.example.brokerage.tooling.runtime.ToolPublisher;
import com.example.brokerage.tooling.runtime.ToolRegistry;

import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;

/**
 * Wiring. Short, because the pieces are decoupled on purpose, and it lives in the catalog module
 * rather than in any one application because three different front ends need the same runtime:
 * the agent service, the desktop connector and the evaluation harness. A catalog that each of them
 * wired up for itself would be three catalogs with the same name.
 *
 * <p>The registry is built from three inputs that know nothing about each other: an ontology, a
 * list of descriptors, and a list of handlers discovered by Spring. If any of the three disagrees
 * with the others — a handler with no descriptor, a descriptor outside the ontology, a descriptor
 * that fails the linter — the application fails to start. Shipping a half-correct catalog is not
 * one of the available outcomes.
 */
@Configuration
public class BrokerageRuntimeConfiguration {

    @Bean
    Supplier<Instant> toolClock(MarketClock clock) {
        return clock::now;
    }

    @Bean
    ToolRegistry toolRegistry(BrokerageCatalog catalog, List<ToolHandler> handlers) {
        return new ToolRegistry(catalog.ontology(), catalog.descriptors(), handlers);
    }

    @Bean
    ApprovalGate approvalGate() {
        return new ApprovalGate();
    }

    @Bean
    RateLimiter rateLimiter() {
        return new RateLimiter();
    }

    @Bean
    KillSwitches killSwitches() {
        return new KillSwitches();
    }

    @Bean
    AuditLog auditLog() {
        return new AuditLog();
    }

    @Bean
    InvocationPipeline invocationPipeline(ToolRegistry registry, ApprovalGate approvals,
                                          RateLimiter rateLimiter, KillSwitches killSwitches,
                                          AuditLog audit, ApprovalContext approvalContext,
                                          Supplier<Instant> clock) {
        return new InvocationPipeline(registry, approvals, rateLimiter, killSwitches, audit,
                approvalContext, clock);
    }

    @Bean
    ToolPublisher toolPublisher(ToolRegistry registry, InvocationPipeline pipeline) {
        return new ToolPublisher(registry, pipeline);
    }
}
