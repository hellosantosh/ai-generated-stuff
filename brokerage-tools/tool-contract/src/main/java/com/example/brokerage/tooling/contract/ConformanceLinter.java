package com.example.brokerage.tooling.contract;

import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.TreeSet;
import java.util.regex.Pattern;

import tools.jackson.databind.JsonNode;

/**
 * The conformance gate. Every rule has an identifier, every finding names one, and the build fails
 * on any {@link Finding.Severity#ERROR}.
 *
 * <p>The linter is the reason this book's catalog is worth copying. A specification that lives only
 * in prose decays into a style guide: the first ten tools follow it, the next forty follow whoever
 * reviewed them, and by the time anyone notices, the model is being shown four different ways to
 * name an account. What a reviewer should be arguing about is whether the description tells the
 * truth and whether the risk tier is honest. Everything mechanical belongs here.
 *
 * <p>Rules come in seven families. NAM and DSC govern the two fields the model's choice turns on.
 * INP and OUT govern the schemas. ANN governs the hints the runtime trusts. GOV governs the
 * metadata the firm needs. ONT checks the descriptor against the domain ontology, which is where
 * drift between a catalog and its own design shows up first.
 */
public final class ConformanceLinter {

    private static final Pattern NAME = Pattern.compile("^[a-z][a-z0-9_]{2,63}$");
    private static final int DESCRIPTION_MIN = 120;
    private static final int DESCRIPTION_MAX = 2000;
    private static final int PARAMETER_LIMIT = 12;

    /** Phrases a seven-part description is expected to contain, each with the rule that wants it. */
    private static final List<Expectation> DESCRIPTION_PARTS = List.of(
            new Expectation("TS-DSC-03", List.of("use when", "use this when", "call this when",
                    "use it when", "use whenever", "use before", "use only after", "use after"),
                    "when to use the tool"),
            new Expectation("TS-DSC-04", List.of("do not use", "does not ", "not for ", "never use"),
                    "when not to use the tool, and what to use instead"),
            new Expectation("TS-DSC-07", List.of("read ", "returns", "shows", "says"),
                    "how to read the result"));

    private record Expectation(String rule, List<String> anyOf, String what) {
    }

    private final Ontology ontology;

    public ConformanceLinter(Ontology ontology) {
        this.ontology = ontology;
    }

    /** Lint one catalog as a whole: per-descriptor rules, then the rules about the set. */
    public List<Finding> lint(List<ToolDescriptor> catalog) {
        List<Finding> findings = new ArrayList<>();
        Set<String> names = new TreeSet<>(catalog.stream().map(ToolDescriptor::name).toList());
        for (ToolDescriptor descriptor : catalog) {
            findings.addAll(lintOne(descriptor, names));
        }
        findings.addAll(lintSet(catalog));
        return findings;
    }

    public List<Finding> lintOne(ToolDescriptor descriptor, Set<String> catalogNames) {
        List<Finding> findings = new ArrayList<>();
        naming(descriptor, findings);
        description(descriptor, catalogNames, findings);
        schema(descriptor, "inputSchema", descriptor.inputSchema(), findings);
        schema(descriptor, "outputSchema", descriptor.outputSchema(), findings);
        inputs(descriptor, findings);
        annotations(descriptor, findings);
        governance(descriptor, findings);
        if (ontology != null) {
            ontology(descriptor, findings);
        }
        return findings;
    }

    // ------------------------------------------------------------------ NAM

    private void naming(ToolDescriptor d, List<Finding> out) {
        if (d.name() == null || !NAME.matcher(d.name()).matches()) {
            out.add(Finding.error("TS-NAM-01", String.valueOf(d.name()),
                    "name must match ^[a-z][a-z0-9_]{2,63}$ — the intersection of what every "
                            + "runtime accepts; camelCase, dots and hyphens are not portable"));
            return;
        }
        if (d.name().split("_").length < 3) {
            out.add(Finding.error("TS-NAM-02", d.name(),
                    "name must be <domain>_<entity>_<operation>; two segments leave the model "
                            + "guessing which catalog a tool belongs to"));
        }
        if (d.title() == null || d.title().length() < 3 || d.title().length() > 60) {
            out.add(Finding.error("TS-NAM-07", d.name(),
                    "title must be 3 to 60 characters: it is what a human sees in an approval "
                            + "dialog, not a second description"));
        }
        if (d.title() != null && d.title().endsWith(".")) {
            out.add(Finding.warning("TS-NAM-08", d.name(), "title should not end in a period"));
        }
    }

    // ------------------------------------------------------------------ DSC

    private void description(ToolDescriptor d, Set<String> catalogNames, List<Finding> out) {
        String text = d.description();
        if (text == null || text.length() < DESCRIPTION_MIN) {
            out.add(Finding.error("TS-DSC-01", d.name(),
                    "description must be at least %d characters; a one-line description is the "
                            + "single most common cause of the wrong tool being chosen"
                            .formatted(DESCRIPTION_MIN)));
            return;
        }
        if (text.length() > DESCRIPTION_MAX) {
            out.add(Finding.error("TS-DSC-02", d.name(),
                    "description must be at most %d characters; past that it crowds out the "
                            + "conversation and the model skims it".formatted(DESCRIPTION_MAX)));
        }
        String lower = text.toLowerCase();
        for (Expectation part : DESCRIPTION_PARTS) {
            if (part.anyOf().stream().noneMatch(lower::contains)) {
                out.add(Finding.warning(part.rule(), d.name(),
                        "description does not appear to say " + part.what()));
            }
        }
        if (text.contains("`") || text.contains("**") || text.contains("\n- ")) {
            out.add(Finding.warning("TS-DSC-08", d.name(),
                    "description should be plain prose; markdown is rendered inconsistently and "
                            + "costs tokens that buy nothing"));
        }
        if (lower.contains(" i ") || lower.startsWith("i ") || lower.contains("we ")) {
            out.add(Finding.warning("TS-DSC-09", d.name(),
                    "description should be written about the tool, not in the first person"));
        }
        for (String referenced : referencedTools(text)) {
            if (!referenced.equals(d.name()) && !catalogNames.contains(referenced)) {
                out.add(Finding.error("TS-DSC-10", d.name(),
                        "description points the model at " + referenced
                                + ", which is not in this catalog — a dangling redirection is "
                                + "worse than none, because the model will try it"));
            }
        }
        if (d.annotations() != null && !d.annotations().readOnlyHint()
                && !lower.contains("confirm") && !lower.contains("approve")
                && !lower.contains("nothing is") && !lower.contains("irreversible")
                && !lower.contains("cannot be undone")) {
            out.add(Finding.warning("TS-DSC-06", d.name(),
                    "a tool that changes state should say in its description what the change is "
                            + "and whether it can be undone"));
        }
    }

    /** Tool names mentioned in prose, so that cross-references can be checked mechanically. */
    public static Set<String> referencedTools(String text) {
        Set<String> found = new TreeSet<>();
        var matcher = Pattern.compile("\\b([a-z][a-z0-9]*(?:_[a-z0-9]+){2,})\\b").matcher(text);
        while (matcher.find()) {
            found.add(matcher.group(1));
        }
        return found;
    }

    // ------------------------------------------------------------------ INP / OUT

    private void schema(ToolDescriptor d, String where, JsonNode root, List<Finding> out) {
        if (root == null || !root.isObject()) {
            out.add(Finding.error("TS-INP-01", d.name(), where, "must be a JSON Schema object"));
            return;
        }
        if (!"object".equals(root.path("type").asString(""))) {
            out.add(Finding.error("TS-INP-02", d.name(), where,
                    "top level must be type: object — a bare array or scalar leaves the model no "
                            + "field names to reason about"));
        }
        walk(d, where, root, out);
    }

    private void walk(ToolDescriptor d, String at, JsonNode node, List<Finding> out) {
        if (node == null || !node.isObject()) {
            return;
        }
        node.propertyNames().forEach(key -> {
            SchemaProfile.Prohibition prohibited = SchemaProfile.prohibitionFor(key);
            if (prohibited != null) {
                out.add(Finding.error("TS-INP-03", d.name(), at + "/" + key, prohibited.why()));
            } else if (!SchemaProfile.PERMITTED.contains(key)) {
                out.add(Finding.warning("TS-INP-04", d.name(), at + "/" + key,
                        "keyword is outside the profile; confirm every runtime forwards it before "
                                + "relying on it"));
            }
        });
        if ("object".equals(node.path("type").asString(""))
                && !node.path("additionalProperties").isBoolean()) {
            out.add(Finding.error("TS-INP-05", d.name(), at,
                    "every object must set additionalProperties: false, or the model's invented "
                            + "extra field is silently accepted and silently ignored"));
        }
        if (node.path("additionalProperties").isBoolean()
                && node.path("additionalProperties").asBoolean()) {
            out.add(Finding.error("TS-INP-06", d.name(), at, "additionalProperties must be false"));
        }
        JsonNode properties = node.path("properties");
        if (properties.isObject()) {
            properties.propertyNames().forEach(name -> {
                JsonNode child = properties.get(name);
                if (!child.has("description") && !child.has("$ref")) {
                    out.add(Finding.error("TS-INP-07", d.name(), at + "/properties/" + name,
                            "every property needs a description; the field name is not the "
                                    + "contract and the model will infer the wrong units"));
                }
                if (child.has("type") && "number".equals(child.path("type").asString(""))
                        && at.startsWith("input")) {
                    out.add(Finding.warning("TS-INP-12", d.name(), at + "/properties/" + name,
                            "money and prices must be decimal strings, not JSON numbers; "
                                    + "0.1 + 0.2 is not 0.3 and an order ticket cannot absorb that"));
                }
                walk(d, at + "/properties/" + name, child, out);
            });
        }
        JsonNode defs = node.path("$defs");
        if (defs.isObject()) {
            defs.propertyNames().forEach(name -> walk(d, at + "/$defs/" + name, defs.get(name), out));
        }
        if (node.has("items")) {
            walk(d, at + "/items", node.get("items"), out);
            if (!node.has("maxItems") && at.startsWith("output")) {
                out.add(Finding.warning("TS-OUT-05", d.name(), at,
                        "an unbounded array in a result is an unbounded token bill; set maxItems "
                                + "and page the rest"));
            }
        }
    }

    private void inputs(ToolDescriptor d, List<Finding> out) {
        JsonNode properties = d.inputSchema() == null
                ? null : d.inputSchema().path("properties");
        if (properties == null || !properties.isObject()) {
            return;
        }
        int count = properties.size();
        if (count > PARAMETER_LIMIT) {
            out.add(Finding.warning("TS-INP-08", d.name(),
                    "%d parameters; past %d the model starts omitting the ones it is least sure "
                            + "about. Consider two tools".formatted(count, PARAMETER_LIMIT)));
        }
        properties.propertyNames().forEach(name -> {
            if (SchemaProfile.RESERVED_PARAMETERS.contains(name)) {
                out.add(Finding.error("TS-INP-09", d.name(), "inputSchema/properties/" + name,
                        "reserved parameter name: identity, entitlement and override flags are "
                                + "established by the runtime and must never be arguments the "
                                + "model can set"));
            }
            JsonNode property = properties.get(name);
            if (property.path("description").asString("").isBlank()) {
                return;
            }
            if ("string".equals(property.path("type").asString(""))
                    && !property.has("enum") && !property.has("pattern")
                    && !property.has("format") && !property.has("maxLength")) {
                out.add(Finding.warning("TS-INP-10", d.name(), "inputSchema/properties/" + name,
                        "an unconstrained string accepts anything the model invents; add an enum, "
                                + "a pattern, a format or at least a maxLength"));
            }
        });
        JsonNode required = d.inputSchema().path("required");
        if (required.isArray()) {
            for (JsonNode name : required.values()) {
                if (!properties.has(name.asString(""))) {
                    out.add(Finding.error("TS-INP-11", d.name(),
                            "required names " + name.asString("") + ", which is not a property"));
                }
            }
        }
    }

    // ------------------------------------------------------------------ ANN

    private void annotations(ToolDescriptor d, List<Finding> out) {
        ToolDescriptor.Annotations a = d.annotations();
        if (a == null) {
            out.add(Finding.error("TS-ANN-01", d.name(),
                    "all four annotations must be stated explicitly, even at their defaults"));
            return;
        }
        if (a.readOnlyHint() && a.destructiveHint()) {
            out.add(Finding.error("TS-ANN-02", d.name(),
                    "readOnlyHint and destructiveHint cannot both be true"));
        }
        if (a.readOnlyHint() && !a.idempotentHint()) {
            out.add(Finding.error("TS-ANN-03", d.name(),
                    "a read-only tool is idempotent by construction; idempotentHint must be true"));
        }
        Governance g = d.governance();
        if (g == null) {
            return;
        }
        if (!a.readOnlyHint() && !g.requiresApproval() && g.riskTier() >= 2) {
            out.add(Finding.error("TS-ANN-04", d.name(),
                    "a tier " + g.riskTier() + " tool that changes state must require approval"));
        }
        if (g.requiresApproval() && (g.approval().confirmationTemplate() == null
                || g.approval().confirmationTemplate().isBlank())) {
            out.add(Finding.error("TS-ANN-05", d.name(),
                    "approval mode " + g.approval().mode() + " needs a confirmationTemplate; "
                            + "a dialog that says \"allow this tool?\" is not informed consent"));
        }
        if (g.requiresApproval() && g.approval().expiresInSeconds() == null) {
            out.add(Finding.warning("TS-ANN-06", d.name(),
                    "approval should expire; an approval with no window is a standing instruction"));
        }
        if (a.readOnlyHint() && g.riskTier() > 1) {
            out.add(Finding.warning("TS-ANN-07", d.name(),
                    "a read-only tool at tier " + g.riskTier() + " is unusual; if it is that "
                            + "sensitive, say why in the capability note"));
        }
    }

    // ------------------------------------------------------------------ GOV

    private void governance(ToolDescriptor d, List<Finding> out) {
        if (!d._meta().containsKey(ToolDescriptor.GOVERNANCE_KEY)) {
            out.add(Finding.error("TS-GOV-01", d.name(),
                    "_meta must carry a governance block under " + ToolDescriptor.GOVERNANCE_KEY));
            return;
        }
        Governance g = d.governance();
        if (g.version() == null || !g.version().matches("^\\d+\\.\\d+\\.\\d+$")) {
            out.add(Finding.error("TS-GOV-02", d.name(), "version must be semantic (major.minor.patch)"));
        }
        if (g.owner() == null || g.owner().team() == null || g.owner().escalation() == null) {
            out.add(Finding.error("TS-GOV-03", d.name(),
                    "owner must name a team, a contact and a paging escalation; an unowned tool "
                            + "in production is an incident waiting for a volunteer"));
        }
        if (g.entitlements() == null || g.entitlements().isEmpty()) {
            out.add(Finding.error("TS-GOV-04", d.name(),
                    "at least one entitlement; a tool anyone may call is a tool nobody checked"));
        }
        if (g.recordKeeping() == null || g.recordKeeping().auditEvent() == null
                || g.recordKeeping().retention() == null) {
            out.add(Finding.error("TS-GOV-05", d.name(),
                    "recordKeeping must name an audit event and a retention period"));
        } else if (!g.recordKeeping().retention().startsWith("P")) {
            out.add(Finding.error("TS-GOV-06", d.name(),
                    "retention must be an ISO 8601 duration such as P6Y"));
        }
        if (g.containsPii() && (g.piiFields() == null || g.piiFields().isEmpty())) {
            out.add(Finding.error("TS-GOV-07", d.name(),
                    "containsPii is true but piiFields is empty; masking and retention are "
                            + "mechanical and need the pointers"));
        }
        if (g.lifecycle() == Governance.Lifecycle.ACTIVE
                && g.conformanceLevel() == Governance.ConformanceLevel.L0) {
            out.add(Finding.error("TS-GOV-08", d.name(),
                    "an active tool must be at least L1"));
        }
        if (g.rateLimit() == null || g.rateLimit().perPrincipalPerMinute() == null) {
            out.add(Finding.warning("TS-GOV-09", d.name(),
                    "no per-principal rate limit; an agent in a retry loop will find out why "
                            + "that matters"));
        }
        if (g.evaluation() == null && g.riskTier() >= 2) {
            out.add(Finding.error("TS-GOV-10", d.name(),
                    "a tier " + g.riskTier() + " tool must cite the evaluation suite that "
                            + "measures whether the model picks it correctly"));
        }
        if (g.riskTier() >= 2 && (g.availability() == null || g.availability().killSwitch() == null)) {
            out.add(Finding.error("TS-GOV-11", d.name(),
                    "a tier " + g.riskTier() + " tool must name a kill switch; somebody will "
                            + "need to turn it off at two in the morning"));
        }
        if (g.lifecycle() == Governance.Lifecycle.DEPRECATED
                && !d.description().toLowerCase().contains("deprecat")) {
            out.add(Finding.warning("TS-GOV-12", d.name(),
                    "a deprecated tool should say so in its description and name its replacement"));
        }
    }

    // ------------------------------------------------------------------ ONT

    private void ontology(ToolDescriptor d, List<Finding> out) {
        Ontology.Capability capability = ontology.capabilityOf(d.name());
        if (capability == null) {
            out.add(Finding.error("TS-ONT-01", d.name(),
                    "no capability in the ontology; either the ontology is missing a row or the "
                            + "tool is doing something the domain was never designed to do"));
            return;
        }
        String expected = ontology.nameFor(capability);
        if (!expected.equals(d.name())) {
            out.add(Finding.error("TS-ONT-02", d.name(),
                    "the ontology derives the name " + expected + " for this capability"));
        }
        Ontology.Entity entity = ontology.entities().get(capability.entity());
        Ontology.Operation operation = ontology.operations().get(capability.operation());
        if (entity == null || operation == null) {
            out.add(Finding.error("TS-ONT-03", d.name(),
                    "capability names an entity or operation the ontology does not declare"));
            return;
        }
        if (d.annotations() != null && d.annotations().readOnlyHint() != operation.readOnly()) {
            out.add(Finding.error("TS-ONT-04", d.name(),
                    "operation " + capability.operation() + " is "
                            + (operation.readOnly() ? "read-only" : "state-changing")
                            + " in the ontology; readOnlyHint disagrees"));
        }
        Governance g = d.governance();
        if (g == null) {
            return;
        }
        int tier = ontology.riskTierFor(capability);
        if (g.riskTier() != tier) {
            out.add(Finding.error("TS-ONT-05", d.name(),
                    "the ontology sets tier " + tier + " for this capability; the descriptor says "
                            + g.riskTier() + ". Change the override in the ontology, with a note, "
                            + "or change the descriptor"));
        }
        if (g.approval() != null && g.approval().mode() != ontology.approvalFor(capability)) {
            out.add(Finding.error("TS-ONT-06", d.name(),
                    "tier " + tier + " requires approval mode " + ontology.approvalFor(capability)));
        }
        if (entity.entitlement() != null && !g.entitlements().contains(entity.entitlement())
                && operation.readOnly()) {
            out.add(Finding.error("TS-ONT-07", d.name(),
                    "entity " + capability.entity() + " requires the entitlement "
                            + entity.entitlement()));
        }
        if (g.dataClassification() != null && entity.classification() != null
                && g.dataClassification().ordinal() < entity.classification().ordinal()) {
            out.add(Finding.error("TS-ONT-08", d.name(),
                    "entity " + capability.entity() + " is classified "
                            + entity.classification() + "; a tool returning it cannot be "
                            + g.dataClassification()));
        }
        patterns(d, capability, out);
    }

    /** Every identifier-typed parameter must carry the ontology's pattern, character for character. */
    private void patterns(ToolDescriptor d, Ontology.Capability capability, List<Finding> out) {
        JsonNode properties = d.inputSchema().path("properties");
        for (String type : capability.consumes()) {
            Ontology.IdentifierType identifier = ontology.identifierTypes().get(type);
            if (identifier == null || identifier.pattern() == null) {
                continue;
            }
            boolean carried = properties.values().stream().anyMatch(property ->
                    identifier.pattern().equals(property.path("pattern").asString(""))
                            || identifier.pattern()
                                    .equals(property.path("items").path("pattern").asString("")));
            if (!carried) {
                out.add(Finding.error("TS-ONT-09", d.name(),
                        "consumes " + type + " but no parameter carries its pattern "
                                + identifier.pattern() + "; a looser pattern here is how a "
                                + "malformed identifier reaches the handler"));
            }
        }
    }

    // ------------------------------------------------------------------ the set

    /** Rules about the catalog rather than about any one descriptor. */
    public List<Finding> lintSet(List<ToolDescriptor> catalog) {
        List<Finding> out = new ArrayList<>();
        Map<String, Long> byTitle = new java.util.HashMap<>();
        for (ToolDescriptor d : catalog) {
            byTitle.merge(d.title() == null ? "" : d.title().toLowerCase(), 1L, Long::sum);
        }
        byTitle.forEach((title, count) -> {
            if (count > 1) {
                out.add(Finding.error("TS-SET-01", "(catalog)",
                        count + " tools share the title \"" + title + "\"; a human approving one "
                                + "of them cannot tell which"));
            }
        });
        if (ontology != null) {
            Set<String> present = new TreeSet<>(catalog.stream().map(ToolDescriptor::name).toList());
            for (Ontology.Capability capability : ontology.capabilities()) {
                if (!present.contains(capability.tool())) {
                    out.add(Finding.error("TS-SET-02", capability.tool(),
                            "the ontology declares this capability but no descriptor implements it"));
                }
            }
            for (String dead : ontology.unreachableIdentifiers()) {
                out.add(Finding.error("TS-SET-03", "(catalog)",
                        dead + " is consumed by a tool but produced by none; the agent has no "
                                + "way to obtain it except from the customer typing it exactly"));
            }
        }
        long writers = catalog.stream()
                .filter(d -> d.annotations() != null && !d.annotations().readOnlyHint()).count();
        if (writers > 0 && catalog.size() / writers < 2) {
            out.add(Finding.warning("TS-SET-04", "(catalog)",
                    "most of this catalog changes state; a model needs reads to orient itself "
                            + "before it is safe to let it write"));
        }
        return out;
    }

    public static boolean failed(List<Finding> findings) {
        return findings.stream().anyMatch(f -> f.severity() == Finding.Severity.ERROR);
    }
}
