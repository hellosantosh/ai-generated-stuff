package com.example.brokerage.bridge;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;

import com.example.brokerage.catalog.BrokerageCatalog;
import com.example.brokerage.tooling.contract.DescriptorCodec;
import com.example.brokerage.tooling.contract.Principal;
import com.example.brokerage.tooling.contract.ToolDescriptor;
import com.example.brokerage.tooling.runtime.ApprovalGate;
import com.example.brokerage.tooling.runtime.InvocationPipeline;
import com.example.brokerage.tooling.runtime.ToolRegistry;
import com.example.brokerage.tooling.runtime.ToolsetBriefing;

import org.springframework.stereotype.Component;
import tools.jackson.databind.JsonNode;

/**
 * What the connector says, as distinct from how it says it.
 *
 * <p>Five methods, no input and no output. Everything about pipes, line endings and which stream
 * is the channel lives next door in {@link BridgeLoop}, and the split is the point: this book
 * claims that publishing the catalog to a different desktop client changes one file, and the claim
 * is only true if the protocol and the transport are two files to begin with.
 *
 * <p>It is also what makes the connector testable. A class that reads standard input cannot be
 * driven by a unit test without a pipe, and a connector nobody tests is a connector that fails on
 * the one Monday somebody tries it.
 */
@Component
public class BridgeProtocol {

    /** What a connector published without {@code --trading} may do. Reads, and nothing else. */
    static final Set<String> READ_ONLY = Set.of("brokerage:reference:read",
            "brokerage:market-data:read", "brokerage:accounts:read", "brokerage:balances:read",
            "brokerage:positions:read", "brokerage:orders:read");

    private final BrokerageCatalog catalog;
    private final ToolRegistry registry;
    private final InvocationPipeline pipeline;
    private final ApprovalGate approvals;
    private final java.util.function.Supplier<java.time.Instant> clock;

    public BridgeProtocol(BrokerageCatalog catalog, ToolRegistry registry,
                          InvocationPipeline pipeline, ApprovalGate approvals,
                          java.util.function.Supplier<java.time.Instant> clock) {
        this.catalog = catalog;
        this.registry = registry;
        this.pipeline = pipeline;
        this.approvals = approvals;
        this.clock = clock;
    }

    /**
     * The scopes a session gets. Fixed when the connector starts, from a command-line flag, and
     * never from anything that arrives on the wire — a desktop client feels like a trusted
     * peer and is not, because whatever is on the other end of the pipe is relaying a language
     * model's output.
     */
    public static Set<String> entitlements(boolean trading) {
        if (!trading) {
            return READ_ONLY;
        }
        Set<String> all = new LinkedHashSet<>(READ_ONLY);
        all.add("brokerage:orders:write");
        return all;
    }

    /**
     * The catalog in the shape a frontier model's API takes it: a name, a description and an input
     * schema, per tool. Nothing is translated and nothing is renamed — which is the point of
     * having written the descriptors as data in the first place.
     *
     * <p>Note what is absent. The output schema, the annotations, the icons and the whole
     * governance block stay behind, because the model never sees them and publishing them would
     * cost tokens on every turn to no purpose.
     */
    public List<Map<String, Object>> manifest(Principal principal) {
        List<Map<String, Object>> tools = new ArrayList<>();
        for (ToolRegistry.Entry entry : registry.visibleTo(principal)) {
            ToolDescriptor descriptor = entry.descriptor();
            Map<String, Object> tool = new LinkedHashMap<>();
            tool.put("name", descriptor.name());
            tool.put("description", descriptor.description());
            tool.put("input_schema", descriptor.inputSchema());
            tools.add(tool);
        }
        return tools;
    }

    /** The toolset half of the system prompt, for a client that wants to show or forward it. */
    public String briefing(Principal principal) {
        return ToolsetBriefing.forPrincipal(registry, principal);
    }

    /**
     * One request, one response.
     *
     * <p>{@code approve} and {@code deny} are here because a desktop assistant is the one runtime
     * where the approval loop can actually close: there is a person sitting in front of it. Without
     * them the trading tools would be publishable but not usable, which is worse than not
     * publishing them — an agent that can see a tool it can never successfully call will keep
     * trying.
     */
    public Map<String, Object> handle(String line, Principal principal) {
        Map<String, Object> response = new LinkedHashMap<>();
        JsonNode request;
        try {
            request = DescriptorCodec.tree(line);
        } catch (RuntimeException e) {
            response.put("error", Map.of("code", "BAD_REQUEST", "message", "not JSON"));
            return response;
        }
        response.put("id", request.path("id").asInt(0));
        String method = request.path("method").asString("");
        JsonNode params = request.path("params");
        switch (method) {
            case "list_tools" -> response.put("result", Map.of(
                    "tools", manifest(principal),
                    "briefing", briefing(principal),
                    "conduct", catalog.conduct()));
            case "call_tool" -> {
                InvocationPipeline.Outcome outcome = pipeline.invoke(
                        params.path("name").asString(""),
                        DescriptorCodec.json(params.path("arguments")), principal);
                response.put("result", DescriptorCodec.tree(outcome.json()));
            }
            case "pending_approvals" -> response.put("result",
                    Map.of("pending", approvals.awaiting(principal.sessionId())));
            case "approve" -> {
                approvals.grant(params.path("reference").asString(""), "desktop-user", clock.get());
                response.put("result", Map.of("granted", true));
            }
            case "deny" -> {
                approvals.deny(params.path("reference").asString(""));
                response.put("result", Map.of("granted", false));
            }
            default -> response.put("error", Map.of("code", "UNKNOWN_METHOD",
                    "message", "no method " + method,
                    "methods", List.of("list_tools", "call_tool", "pending_approvals", "approve",
                            "deny")));
        }
        return response;
    }
}
