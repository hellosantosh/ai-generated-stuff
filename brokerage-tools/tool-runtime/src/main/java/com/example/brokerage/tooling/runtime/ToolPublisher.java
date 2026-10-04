package com.example.brokerage.tooling.runtime;

import java.util.List;

import com.example.brokerage.tooling.contract.Principal;

import org.springframework.ai.tool.ToolCallback;

/**
 * Turns the registry into the list of tools one session is allowed to see.
 *
 * <p>Publishing per principal rather than once at startup is the difference between a toolset and
 * a configuration. The same application serves a read-only analytics session, a customer session
 * that may trade, and an operations session that may neither — and each of them is shown a
 * different catalog, not the same catalog with different outcomes.
 */
public class ToolPublisher {

    private final ToolRegistry registry;
    private final InvocationPipeline pipeline;

    public ToolPublisher(ToolRegistry registry, InvocationPipeline pipeline) {
        this.registry = registry;
        this.pipeline = pipeline;
    }

    public List<ToolCallback> publish(Principal principal, ToolTrace trace) {
        return registry.visibleTo(principal).stream()
                .map(entry -> (ToolCallback) new DescriptorToolCallback(entry.descriptor(),
                        pipeline, principal, trace))
                .toList();
    }

    public String briefing(Principal principal) {
        return ToolsetBriefing.forPrincipal(registry, principal);
    }

    public ToolRegistry registry() {
        return registry;
    }

    public InvocationPipeline pipeline() {
        return pipeline;
    }
}
