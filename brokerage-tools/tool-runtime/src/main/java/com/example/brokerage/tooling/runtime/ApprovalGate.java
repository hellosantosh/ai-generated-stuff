package com.example.brokerage.tooling.runtime;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.time.Instant;
import java.util.HexFormat;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.concurrent.ConcurrentHashMap;

import com.example.brokerage.tooling.contract.ApprovalContext;
import com.example.brokerage.tooling.contract.DescriptorCodec;
import com.example.brokerage.tooling.contract.Governance;
import com.example.brokerage.tooling.contract.Invocation;
import com.example.brokerage.tooling.contract.ToolDescriptor;

import tools.jackson.databind.JsonNode;

/**
 * Where a human says yes.
 *
 * <p>The mechanism is a reference rather than a flag, and that distinction is the whole design. A
 * flag — {@code confirmed: true}, or a boolean on a session — can be set by the thing being
 * checked. A reference is a hash of the tool name, the exact arguments and the session, so an
 * approval granted for "buy 50 AAPL at market" cannot be spent on "buy 500", and an approval from
 * one conversation cannot be spent in another.
 *
 * <p>The flow is three steps and survives the agent restarting in the middle of it:
 *
 * <ol>
 *   <li>The agent calls a tool that needs approval. The gate has no grant for that reference, so
 *       the pipeline returns {@code APPROVAL_REQUIRED} with the reference and a rendered summary
 *       of what is about to happen.</li>
 *   <li>Something outside the model shows the summary to a human and records their answer against
 *       the reference. In this reference implementation that is the Angular console; in production
 *       it is whatever surface the customer is actually on.</li>
 *   <li>The agent calls the tool again, byte for byte. Now the grant matches and it runs.</li>
 * </ol>
 *
 * <p>Grants expire. An approval with no window is a standing instruction, and nobody means to give
 * one of those.
 */
public class ApprovalGate {

    /** What the human is shown and what their answer is recorded against. */
    public record Pending(String reference, String tool, String title, String summary,
                          int riskTier, Governance.ApprovalMode mode, Instant requestedAt,
                          Instant expiresAt, JsonNode arguments, String sessionId) {
    }

    public record Grant(String reference, String grantedBy, Instant grantedAt, Instant expiresAt) {
    }

    private final Map<String, Pending> pending = new ConcurrentHashMap<>();
    private final Map<String, Grant> grants = new ConcurrentHashMap<>();
    private volatile String standIn;

    /**
     * The stable reference for this exact call. Canonical JSON, so that a re-ordered argument
     * object is still the same approval and a changed value is not.
     */
    public static String referenceFor(Invocation invocation) {
        String canonical = invocation.descriptor().name() + "|"
                + invocation.principal().sessionId() + "|"
                + canonicalize(invocation.arguments());
        return "apr_" + hash(canonical);
    }

    static String canonicalize(JsonNode node) {
        if (node == null || node.isNull()) {
            return "null";
        }
        if (node.isObject()) {
            Map<String, String> sorted = new java.util.TreeMap<>();
            node.properties().forEach(entry ->
                    sorted.put(entry.getKey(), canonicalize(entry.getValue())));
            StringBuilder out = new StringBuilder("{");
            sorted.forEach((key, value) -> out.append(key).append(':').append(value).append(','));
            return out.append('}').toString();
        }
        if (node.isArray()) {
            StringBuilder out = new StringBuilder("[");
            node.values().forEach(child -> out.append(canonicalize(child)).append(','));
            return out.append(']').toString();
        }
        return node.asString();
    }

    private static String hash(String input) {
        try {
            MessageDigest digest = MessageDigest.getInstance("SHA-256");
            byte[] bytes = digest.digest(input.getBytes(StandardCharsets.UTF_8));
            return HexFormat.of().formatHex(bytes).substring(0, 24);
        } catch (Exception e) {
            throw new IllegalStateException("SHA-256 is not available", e);
        }
    }

    /**
     * Let the harness answer yes on the human's behalf.
     *
     * <p>This is a test affordance and it is a named, visible method rather than a quiet default,
     * because the two things it makes possible are both easy to get wrong. A trading scenario
     * that never gets past the approval gate is not testing the place tool at all; and a suite
     * that approves everything without saying so is not testing the gate. So the trading suite
     * turns this on explicitly and the refusal suite leaves it off, which is how both get covered.
     *
     * <p>It must never be reachable in a deployed service. In this project the only callers are in
     * the harness, and the gate the agent service builds is a different instance.
     */
    public void standInForTheHuman(String who) {
        this.standIn = who;
    }

    public String standingInForTheHuman() {
        return standIn;
    }

    /** True when a live grant exists for this reference. Consumed on use. */
    public boolean consume(String reference, Instant now) {
        if (standIn != null) {
            grants.put(reference, new Grant(reference, standIn, now, now.plusSeconds(120)));
        }
        Grant grant = grants.get(reference);
        if (grant == null) {
            return false;
        }
        grants.remove(reference);
        pending.remove(reference);
        return grant.expiresAt().isAfter(now);
    }

    /**
     * Record what needs approving, and what the human should be shown.
     *
     * @param extra values from the domain that the arguments do not carry — the account's
     *              nickname, the priced cost, the warnings. See {@link ApprovalContext} for why
     *              rendering from the arguments alone is not good enough.
     */
    public Pending request(Invocation invocation, String reference, Map<String, String> extra,
                           Instant now) {
        ToolDescriptor descriptor = invocation.descriptor();
        Governance governance = descriptor.governance();
        int window = governance.approval().expiresInSeconds() == null
                ? 120 : governance.approval().expiresInSeconds();
        Pending request = new Pending(reference, descriptor.name(), descriptor.title(),
                render(governance.approval().confirmationTemplate(), invocation.arguments(), extra),
                governance.riskTier(), governance.approval().mode(), now,
                now.plusSeconds(window), invocation.arguments(),
                invocation.principal().sessionId());
        pending.put(reference, request);
        return request;
    }

    public void grant(String reference, String grantedBy, Instant now) {
        Pending request = pending.get(reference);
        Instant expiry = request == null ? now.plusSeconds(120) : request.expiresAt();
        grants.put(reference, new Grant(reference, grantedBy, now, expiry));
    }

    public void deny(String reference) {
        grants.remove(reference);
        pending.remove(reference);
    }

    public Optional<Pending> peek(String reference) {
        return Optional.ofNullable(pending.get(reference));
    }

    public List<Pending> awaiting(String sessionId) {
        return pending.values().stream()
                .filter(request -> sessionId == null || request.sessionId().equals(sessionId))
                .toList();
    }

    /**
     * Fill a confirmation template from the arguments. Any placeholder with no argument is left
     * visible as {@code (not given)} rather than blanked, because a dialog with a quietly empty
     * field is how somebody approves an order whose quantity they never saw.
     */
    public static String render(String template, JsonNode arguments, Map<String, String> extra) {
        if (template == null) {
            return "";
        }
        Map<String, String> values = new LinkedHashMap<>();
        if (arguments != null && arguments.isObject()) {
            arguments.properties().forEach(entry ->
                    values.put(entry.getKey(), text(entry.getValue())));
        }
        if (extra != null) {
            values.putAll(extra);          // the domain's figures win over a raw argument
        }
        StringBuilder out = new StringBuilder();
        int index = 0;
        while (index < template.length()) {
            int open = template.indexOf("{{", index);
            if (open < 0) {
                out.append(template, index, template.length());
                break;
            }
            int close = template.indexOf("}}", open);
            if (close < 0) {
                out.append(template, index, template.length());
                break;
            }
            out.append(template, index, open);
            String key = template.substring(open + 2, close).trim();
            out.append(values.getOrDefault(key, "(not given)"));
            index = close + 2;
        }
        // A placeholder that resolved to nothing — no warnings, say — leaves a hole in the middle
        // of the dialog, and a dialog with a gap in it reads as broken rather than as reassuring.
        return out.toString().replaceAll("\n{3,}", "\n\n").trim();
    }

    private static String text(JsonNode node) {
        return node.isValueNode() ? node.asString() : DescriptorCodec.json(node);
    }
}
