package com.example.brokerage.agent.web;

import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

import com.example.brokerage.agent.Sessions;
import com.example.brokerage.tooling.contract.Finding;
import com.example.brokerage.tooling.contract.Governance;
import com.example.brokerage.tooling.contract.Ontology;
import com.example.brokerage.tooling.contract.Principal;
import com.example.brokerage.tooling.contract.ToolDescriptor;
import com.example.brokerage.tooling.runtime.KillSwitches;
import com.example.brokerage.tooling.runtime.ToolRegistry;
import com.example.brokerage.tooling.runtime.ToolsetBriefing;

import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

/**
 * The registry, over HTTP.
 *
 * <p>This controller is why the console exists and, more importantly, why it is worth building one
 * early. Everything here answers a question somebody will ask repeatedly during development and
 * forever afterwards in review: what exactly is the model shown, who can see which tool, what does
 * the generated briefing say today, and what does the catalog's own conformance report look like.
 * All of it is available without a language model and without a conversation.
 */
@RestController
@RequestMapping("/api/catalog")
public class CatalogController {

    /** A tool as the console lists it: the governance a human needs, not the schema. */
    public record ToolCard(String name, String title, String catalogId, int riskTier,
                           String conformanceLevel, String lifecycle, boolean readOnly,
                           boolean destructive, boolean idempotent, boolean openWorld,
                           String approvalMode, List<String> entitlements, String owner,
                           String operation, String entity, List<String> consumes,
                           List<String> produces, String costClass, Integer p95LatencyMs,
                           Integer perMinute, String killSwitch, boolean enabled,
                           Double selectionAccuracy, int descriptionCharacters,
                           int inputProperties) {
    }

    private final ToolRegistry registry;
    private final KillSwitches killSwitches;
    private final Sessions sessions;

    public CatalogController(ToolRegistry registry, KillSwitches killSwitches, Sessions sessions) {
        this.registry = registry;
        this.killSwitches = killSwitches;
        this.sessions = sessions;
    }

    @GetMapping
    public List<ToolCard> tools(@RequestParam(required = false) Sessions.Role role) {
        List<ToolRegistry.Entry> entries = role == null
                ? registry.all()
                : registry.visibleTo(principalFor(role));
        return entries.stream().map(this::card).toList();
    }

    @GetMapping("/{name}")
    public ResponseEntity<ToolDescriptor> descriptor(@PathVariable String name) {
        return registry.find(name)
                .map(entry -> ResponseEntity.ok(entry.descriptor()))
                .orElseGet(() -> ResponseEntity.notFound().build());
    }

    @GetMapping("/ontology")
    public Ontology ontology() {
        return registry.ontology();
    }

    /**
     * The producer graph: which tool hands out each identifier, and what each tool needs first.
     * The console draws this; the briefing is generated from the same two maps.
     */
    @GetMapping("/graph")
    public Map<String, Object> graph() {
        Map<String, Object> graph = new LinkedHashMap<>();
        graph.put("producers", registry.ontology().producers());
        Map<String, Object> prerequisites = new LinkedHashMap<>();
        registry.all().forEach(entry ->
                prerequisites.put(entry.name(), registry.ontology().prerequisitesOf(entry.name())));
        graph.put("prerequisites", prerequisites);
        graph.put("unreachable", registry.ontology().unreachableIdentifiers());
        return graph;
    }

    /** The conformance report, so a reviewer can see what the gate saw. */
    @GetMapping("/conformance")
    public List<Finding> conformance() {
        return registry.findings();
    }

    /** The toolset half of the system prompt, exactly as the model will receive it. */
    @GetMapping("/briefing")
    public Map<String, Object> briefing(@RequestParam(defaultValue = "TRADING") Sessions.Role role) {
        Principal principal = principalFor(role);
        String text = ToolsetBriefing.forPrincipal(registry, principal);
        return Map.of("role", role, "visible", registry.visibleTo(principal).size(),
                "hidden", registry.hiddenFrom(principal), "characters", text.length(),
                "briefing", text);
    }

    @PostMapping("/switches/{name}")
    public Map<String, Object> toggle(@PathVariable String name,
                                      @RequestParam boolean off) {
        if (off) {
            killSwitches.trip(name);
        } else {
            killSwitches.reset(name);
        }
        return Map.of("switch", name, "off", killSwitches.isOff(name),
                "tripped", killSwitches.tripped());
    }

    private Principal principalFor(Sessions.Role role) {
        return sessions.open("preview-" + role, role).principal();
    }

    private ToolCard card(ToolRegistry.Entry entry) {
        ToolDescriptor d = entry.descriptor();
        Governance g = entry.governance();
        Ontology.Capability capability = entry.capability();
        return new ToolCard(d.name(), d.title(), d.catalogId(), g.riskTier(),
                g.conformanceLevel().name(), g.lifecycle().name(),
                d.annotations().readOnlyHint(), d.annotations().destructiveHint(),
                d.annotations().idempotentHint(), d.annotations().openWorldHint(),
                g.approval() == null ? "none" : g.approval().mode().name(),
                g.entitlements(), g.owner().team(), capability.operation(), capability.entity(),
                capability.consumes(), capability.produces(),
                g.cost() == null ? null : g.cost().costClass(),
                g.cost() == null ? null : g.cost().p95LatencyMs(),
                g.rateLimit() == null ? null : g.rateLimit().perPrincipalPerMinute(),
                g.availability() == null ? null : g.availability().killSwitch(),
                g.availability() == null || !killSwitches.isOff(g.availability().killSwitch()),
                g.evaluation() == null ? null : g.evaluation().selectionAccuracy(),
                d.description().length(),
                d.inputSchema().path("properties").size());
    }
}
