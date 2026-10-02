package com.example.brokerage.tooling.contract;

import java.io.IOException;
import java.io.InputStream;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;

import com.fasterxml.jackson.annotation.JsonInclude;

/**
 * The domain ontology: the things a brokerage tool can operate on, the operations it can perform,
 * the identifiers that join one call to the next, and the policy that follows from both.
 *
 * <p>An ontology sounds like an academic luxury until the catalog passes about a dozen tools. Then
 * three problems arrive together. Names stop agreeing with each other, so the model cannot
 * generalize from one tool to the next. Nobody can say whether the catalog is complete, because
 * there is no statement of what it ought to cover. And the risk tier on each descriptor becomes a
 * matter of whoever wrote it, which is exactly the kind of judgment an auditor will ask you to
 * justify two years later.
 *
 * <p>This ontology fixes all three mechanically. The tool name is <em>derived</em> from the entity
 * and the operation rather than chosen, so {@code brokerage_order_cancel} could not have been
 * called {@code cancelOrder} or {@code brokerage_cancel_order}. Completeness is a question about
 * the capability table rather than about anyone's memory. And the risk tier comes from the
 * operation, with every exception recorded as an override that a reviewer can see.
 *
 * <p>It also produces the one thing an agent needs that no single descriptor can give it: the
 * <em>producer graph</em>. {@code brokerage_order_place} requires a {@code ConfirmationToken},
 * and exactly one capability in the catalog produces one. That is why preview-before-place is
 * structural here rather than advisory — not because the description asks nicely, but because
 * there is no other way for the agent to obtain the argument.
 */
@JsonInclude(JsonInclude.Include.NON_NULL)
public record Ontology(
        String id,
        String version,
        String domain,
        String description,
        Map<String, IdentifierType> identifierTypes,
        Map<String, Entity> entities,
        Map<String, Operation> operations,
        List<Capability> capabilities,
        Policies policies) {

    /**
     * A value that joins one call to the next. Identifier types are declared once here and
     * referenced by the schemas, which is what stops one descriptor accepting
     * {@code ^acct_[A-Z0-9]+$} while its neighbour accepts {@code ^ACC-\d{4}$}.
     */
    public record IdentifierType(String description, String pattern, String example,
                                 boolean opaque) {
    }

    /**
     * A thing the domain is about. {@code term} and {@code collectionTerm} are the slugs that
     * appear in tool names; {@code classification} and {@code entitlement} are the floor that
     * every descriptor touching this entity must meet or exceed.
     */
    public record Entity(String label, String term, String collectionTerm, String identifier,
                         String description,
                         Governance.DataClassification classification,
                         String entitlement, boolean mutable, List<Relation> relations) {

        public Entity {
            relations = relations == null ? List.of() : List.copyOf(relations);
        }
    }

    /** How one entity reaches another. The agent reads these as paths, not as decoration. */
    public record Relation(String name, String target, String cardinality, String description) {
    }

    /**
     * A verb from the closed vocabulary. The vocabulary is closed so that {@code readOnly},
     * {@code idempotent} and the base risk tier can be checked against the name instead of
     * trusted from the descriptor, which is the one place a tool author's optimism is most
     * expensive.
     */
    public record Operation(String gloss, boolean readOnly, boolean idempotent, int baseRiskTier,
                            String semantics) {
    }

    /**
     * One tool, stated as (operation, entity) with the identifiers it needs and the identifiers it
     * hands back. This table <em>is</em> the catalog's specification: the descriptors implement it.
     *
     * @param cardinality {@code one} or {@code many}; decides the singular or plural term in the name
     * @param consumes    identifier types that must be in hand before this tool can be called
     * @param produces    identifier types this tool returns, making further calls possible
     * @param riskTier    null to take the operation's base tier; a number to override it
     * @param note        why an override exists, or what the capability is for; printed by reviews
     */
    @JsonInclude(JsonInclude.Include.NON_NULL)
    public record Capability(String tool, String operation, String entity, String cardinality,
                             List<String> consumes, List<String> produces, Integer riskTier,
                             String note) {

        public Capability {
            consumes = consumes == null ? List.of() : List.copyOf(consumes);
            produces = produces == null ? List.of() : List.copyOf(produces);
        }

        public boolean many() {
            return "many".equals(cardinality);
        }
    }

    /**
     * The rules that turn a risk tier into an approval mode, and the ceiling on what a single
     * agent turn may do without a human. These live in the ontology rather than in each
     * descriptor so that raising the bar is one edit, reviewable in one diff.
     */
    public record Policies(Map<Integer, Governance.ApprovalMode> approvalByRiskTier,
                           Map<Integer, String> evidenceByRiskTier,
                           int maxTierWithoutApproval,
                           List<String> dualControlAbove) {
    }

    // ------------------------------------------------------------------ derived views

    /** The name this capability must carry: {@code <domain>_<term>_<operation>}. */
    public String nameFor(Capability capability) {
        Entity entity = entities.get(capability.entity());
        String term = capability.many() ? entity.collectionTerm() : entity.term();
        return domain + "_" + term + "_" + capability.operation();
    }

    /** The tier this capability must carry: the operation's base tier unless overridden. */
    public int riskTierFor(Capability capability) {
        return capability.riskTier() != null
                ? capability.riskTier()
                : operations.get(capability.operation()).baseRiskTier();
    }

    public Governance.ApprovalMode approvalFor(Capability capability) {
        return policies.approvalByRiskTier().getOrDefault(riskTierFor(capability),
                Governance.ApprovalMode.CONFIRM);
    }

    public Capability capabilityOf(String toolName) {
        return capabilities.stream().filter(c -> c.tool().equals(toolName)).findFirst().orElse(null);
    }

    /**
     * Identifier type to the tools that hand one out. This is the index behind every "from
     * {@code brokerage_accounts_list}" in a description, and the linter checks the prose against
     * it so the two cannot drift.
     */
    public Map<String, List<String>> producers() {
        Map<String, List<String>> index = new LinkedHashMap<>();
        for (String type : identifierTypes.keySet()) {
            index.put(type, capabilities.stream().filter(c -> c.produces().contains(type))
                    .map(Capability::tool).toList());
        }
        return index;
    }

    /**
     * The tools that must run before this one, because they are the only source of something it
     * consumes. An agent that is told this once stops guessing identifiers.
     */
    public Set<String> prerequisitesOf(String toolName) {
        Capability capability = capabilityOf(toolName);
        if (capability == null) {
            return Set.of();
        }
        Map<String, List<String>> producers = producers();
        Set<String> needed = new LinkedHashSet<>();
        for (String type : capability.consumes()) {
            producers.getOrDefault(type, List.of()).stream()
                    .filter(tool -> !tool.equals(toolName))
                    .forEach(needed::add);
        }
        return needed;
    }

    /**
     * Identifier types nothing in the catalog produces. Every one is a dead end: a tool the agent
     * can read about but can never correctly call, because the argument has to come from the
     * customer typing it exactly right.
     */
    public List<String> unreachableIdentifiers() {
        Map<String, List<String>> producers = producers();
        List<String> dead = new ArrayList<>();
        for (Capability capability : capabilities) {
            for (String type : capability.consumes()) {
                if (producers.getOrDefault(type, List.of()).isEmpty() && !dead.contains(type)) {
                    dead.add(type);
                }
            }
        }
        return dead;
    }

    // ------------------------------------------------------------------ loading

    public static Ontology read(String json) {
        return DescriptorCodec.mapper().readValue(json, Ontology.class);
    }

    public static Ontology read(Path file) throws IOException {
        return read(Files.readString(file));
    }

    public static Ontology read(InputStream in) throws IOException {
        try (in) {
            return read(new String(in.readAllBytes()));
        }
    }
}
