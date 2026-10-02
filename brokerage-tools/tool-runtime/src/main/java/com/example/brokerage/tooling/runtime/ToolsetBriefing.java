package com.example.brokerage.tooling.runtime;

import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.TreeSet;

import com.example.brokerage.tooling.contract.ErrorCode;
import com.example.brokerage.tooling.contract.Ontology;
import com.example.brokerage.tooling.contract.Principal;

/**
 * The part of the system prompt that describes the toolset, generated from the ontology.
 *
 * <p>Generated, not written. That is the point of this class and it is worth being blunt about
 * why. A hand-written system prompt listing which tool to call for what is a second copy of the
 * catalog, maintained by hand, consulted by the model on every turn, and wrong within a month. The
 * first tool renamed, the first tool added, the first risk tier raised, and the prompt is telling
 * the model something that is no longer true — and unlike a stale comment, a stale prompt changes
 * behavior.
 *
 * <p>So the briefing is derived. The prerequisite chains come from the ontology's producer graph.
 * The list of what cannot be invented comes from the identifier types marked opaque. The error
 * handling rules come from the taxonomy's own {@code retryable} and {@code callerCanFix} flags.
 * Nothing here is typed twice, which means nothing here can disagree with the catalog.
 *
 * <p>What it deliberately does <em>not</em> contain is per-tool advice. "Use
 * brokerage_balances_get when the customer asks about money" belongs in that tool's description,
 * where it is reviewed with the tool, versioned with the tool, and loaded only when the tool is
 * published. A system prompt that duplicates descriptions is paying twice for the same tokens.
 */
public final class ToolsetBriefing {

    private ToolsetBriefing() {
    }

    /** The toolset section of the system prompt, for one principal's visible catalog. */
    public static String forPrincipal(ToolRegistry registry, Principal principal) {
        Ontology ontology = registry.ontology();
        List<ToolRegistry.Entry> visible = registry.visibleTo(principal);
        Set<String> names = new TreeSet<>(visible.stream().map(ToolRegistry.Entry::name).toList());

        StringBuilder out = new StringBuilder();
        out.append("## The tools you have\n\n");
        out.append("You have ").append(visible.size())
                .append(" brokerage tools. Each one's description is the contract: read it, and "
                        + "do not do anything it says not to. Where a description and this "
                        + "briefing disagree, the description wins.\n\n");

        Map<String, List<String>> producers = ontology.producers();
        StringBuilder opaque = new StringBuilder();
        ontology.identifierTypes().forEach((type, identifier) -> {
            List<String> from = producers.getOrDefault(type, List.of()).stream()
                    .filter(names::contains).toList();
            if (identifier.opaque() && !from.isEmpty()) {
                opaque.append("- ").append(article(type)).append(' ').append(type)
                        .append(" looks like ")
                        .append(identifier.example())
                        .append(" and can only come from ").append(humanList(from))
                        .append(". Never construct one, never edit one, never reuse one from "
                                + "earlier in the conversation if anything has changed.\n");
            }
        });
        section(out, "Identifiers you cannot invent", opaque);

        StringBuilder first = new StringBuilder();
        for (ToolRegistry.Entry entry : visible) {
            Set<String> prerequisites = ontology.prerequisitesOf(entry.name()).stream()
                    .filter(names::contains)
                    .collect(java.util.stream.Collectors.toCollection(TreeSet::new));
            if (!prerequisites.isEmpty()) {
                first.append("- ").append(entry.name()).append(" needs values from ")
                        .append(humanList(List.copyOf(prerequisites))).append(".\n");
            }
        }
        section(out, "What has to happen first", first);

        out.append("## Approval\n\n");
        List<String> needApproval = visible.stream()
                .filter(entry -> entry.governance().requiresApproval())
                .map(ToolRegistry.Entry::name).toList();
        if (needApproval.isEmpty()) {
            out.append("None of your tools changes anything. If the customer asks you to trade, "
                    + "say you can look but not act, and offer what you can see.\n");
        } else {
            out.append("These tools change something real and need the customer's explicit "
                    + "approval first: ").append(humanList(needApproval)).append(".\n\n")
                    .append("The approval is not yours to give and not yours to infer. Ask, in "
                            + "plain words, with the amount and the instrument in the question. "
                            + "If the tool answers APPROVAL_REQUIRED, stop and wait; when the "
                            + "customer agrees, call it again with exactly the same arguments. "
                            + "If they say no, say so and stop — do not look for a different "
                            + "tool that achieves the same thing.\n");
        }

        out.append("\n## Reading an error\n\n");
        out.append("Every tool returns an envelope. When ok is false, read error.code and follow "
                + "error.remediation. The codes behave like this:\n\n");
        for (ErrorCode code : ErrorCode.values()) {
            out.append("- ").append(code.name()).append(": ").append(code.guidance()).append('\n');
        }

        out.append("\n## Calling well\n\n");
        out.append("""
                - Call the batch tools once with everything, not once per item.
                - Ask for the smallest window or page that answers the question.
                - When a result says it is incomplete, say so rather than presenting it as all.
                - When a result carries an asOf, quote it if any time has passed.
                - When two instruments could match what the customer said, ask. Do not pick.
                - Never report an action as done because you called the tool; report what the
                  result says happened.
                """);
        return out.toString();
    }

    /** Append a heading and its body, or nothing at all when the body came out empty. */
    private static void section(StringBuilder out, String heading, StringBuilder body) {
        if (body.isEmpty()) {
            return;
        }
        out.append("## ").append(heading).append("\n\n").append(body).append('\n');
    }

    private static String article(String word) {
        return "AEIOU".indexOf(word.charAt(0)) >= 0 ? "An" : "A";
    }

    private static String humanList(List<String> items) {
        if (items.isEmpty()) {
            return "nothing";
        }
        if (items.size() == 1) {
            return items.getFirst();
        }
        return String.join(", ", items.subList(0, items.size() - 1)) + " or "
                + items.getLast();
    }
}
