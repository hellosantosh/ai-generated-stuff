package com.example.brokerage.tooling.runtime;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Optional;

import com.example.brokerage.tooling.contract.ConformanceLinter;
import com.example.brokerage.tooling.contract.Finding;
import com.example.brokerage.tooling.contract.Governance;
import com.example.brokerage.tooling.contract.Ontology;
import com.example.brokerage.tooling.contract.Principal;
import com.example.brokerage.tooling.contract.ToolDescriptor;
import com.example.brokerage.tooling.contract.ToolHandler;

/**
 * The tool registry: the one place that knows what tools exist, what each one promises, who may
 * call it, and what code is behind it.
 *
 * <p>A registry sounds like plumbing and is in fact the main architectural decision in a tool
 * system. Without one, the set of tools a model sees is whatever beans happened to be on the
 * classpath, which means nobody can answer four questions that get asked constantly: what is the
 * model being shown right now, who is allowed to call each of these, which of them have been
 * reviewed, and what happens if we turn one off. With one, all four are lookups.
 *
 * <p>Three rules are enforced at construction, deliberately early, so that a misconfigured
 * catalog fails at startup rather than in a conversation.
 *
 * <ol>
 *   <li>Every descriptor has a handler and every handler has a descriptor. A handler with no
 *       descriptor is dead code; a descriptor with no handler is a tool the model will choose and
 *       then be told does not work.</li>
 *   <li>Every descriptor passes the conformance linter. A registry that serves a non-conformant
 *       descriptor has made the specification advisory.</li>
 *   <li>Every descriptor is {@code active}. Draft and retired tools are not served, however
 *       complete they look.</li>
 * </ol>
 */
public class ToolRegistry {

    /** One registered tool: its contract, its code, and its place in the ontology. */
    public record Entry(ToolDescriptor descriptor, ToolHandler handler,
                        Ontology.Capability capability) {

        public Governance governance() {
            return descriptor.governance();
        }

        public String name() {
            return descriptor.name();
        }
    }

    private final Ontology ontology;
    private final Map<String, Entry> entries = new LinkedHashMap<>();
    private final List<Finding> findings;

    /**
     * @param ontology the domain ontology, or null for a registry with no ontology discipline.
     *                 Null is permitted because the ontology is an additional constraint rather
     *                 than a prerequisite — a small catalog can be conformant without one, and an
     *                 isolated test of the pipeline should not have to invent one.
     */
    public ToolRegistry(Ontology ontology, List<ToolDescriptor> descriptors,
                        List<ToolHandler> handlers) {
        this.ontology = ontology;
        Map<String, ToolHandler> byName = new LinkedHashMap<>();
        for (ToolHandler handler : handlers) {
            byName.put(handler.name(), handler);
        }
        this.findings = new ConformanceLinter(ontology).lint(descriptors);
        if (ConformanceLinter.failed(findings)) {
            throw new IllegalStateException("the catalog does not conform:\n  "
                    + String.join("\n  ", findings.stream().map(Finding::toString).toList()));
        }
        for (ToolDescriptor descriptor : descriptors) {
            ToolHandler handler = byName.remove(descriptor.name());
            if (handler == null) {
                throw new IllegalStateException("no handler for " + descriptor.name()
                        + ": the model would choose a tool that cannot run");
            }
            if (descriptor.governance().lifecycle() != Governance.Lifecycle.ACTIVE) {
                continue;
            }
            entries.put(descriptor.name(), new Entry(descriptor, handler,
                    ontology == null ? null : ontology.capabilityOf(descriptor.name())));
        }
        if (!byName.isEmpty()) {
            throw new IllegalStateException("handlers with no descriptor: " + byName.keySet()
                    + ". Either publish a descriptor or delete the handler");
        }
    }

    public Ontology ontology() {
        return ontology;
    }

    /** Non-blocking findings from the startup lint, for the console to display. */
    public List<Finding> findings() {
        return List.copyOf(findings);
    }

    public List<Entry> all() {
        return new ArrayList<>(entries.values());
    }

    public Optional<Entry> find(String name) {
        return Optional.ofNullable(entries.get(name));
    }

    public int size() {
        return entries.size();
    }

    /**
     * The tools this principal may actually call.
     *
     * <p>Filtering here rather than at execution time is not an optimization, it is the whole
     * point. A tool the principal cannot use, published into the model's context, costs tokens and
     * buys a wrong turn: the model will choose it, be refused, apologize, and choose again. Showing
     * a model only what it can do is the cheapest accuracy improvement available.
     */
    public List<Entry> visibleTo(Principal principal) {
        return entries.values().stream()
                .filter(entry -> principal.holdsAll(entry.governance().entitlements()))
                .toList();
    }

    /** Which tools would be hidden from this principal, and why. Used by the console, not the model. */
    public Map<String, List<String>> hiddenFrom(Principal principal) {
        Map<String, List<String>> hidden = new LinkedHashMap<>();
        for (Entry entry : entries.values()) {
            List<String> missing = entry.governance().entitlements().stream()
                    .filter(entitlement -> !principal.holds(entitlement)).toList();
            if (!missing.isEmpty()) {
                hidden.put(entry.name(), missing);
            }
        }
        return hidden;
    }
}
