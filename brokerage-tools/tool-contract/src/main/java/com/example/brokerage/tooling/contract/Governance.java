package com.example.brokerage.tooling.contract;

import java.util.List;

import com.fasterxml.jackson.annotation.JsonIgnoreProperties;
import com.fasterxml.jackson.annotation.JsonInclude;
import com.fasterxml.jackson.annotation.JsonProperty;

/**
 * The governance block, carried in {@code _meta} under a reverse-DNS key so that it travels with
 * the descriptor and is never rendered into the model's context.
 *
 * <p>Every field here answers a question somebody will eventually ask in an incident review: who
 * owns this, what can it see, who may call it, who has to agree before it runs, how long do we
 * keep the record, and what breaks when it is turned off.
 */
@JsonInclude(JsonInclude.Include.NON_NULL)
@JsonIgnoreProperties(ignoreUnknown = false)
public record Governance(
        String version,
        Lifecycle lifecycle,
        ConformanceLevel conformanceLevel,
        int riskTier,
        Owner owner,
        DataClassification dataClassification,
        boolean containsPii,
        List<String> piiFields,
        List<String> entitlements,
        Approval approval,
        RecordKeeping recordKeeping,
        Cost cost,
        RateLimit rateLimit,
        List<String> dependencies,
        Availability availability,
        Evaluation evaluation) {

    /**
     * The JSON spellings are pinned with {@link JsonProperty} rather than derived from the Java
     * names, because these strings appear in published descriptors and in audit records: renaming
     * a Java constant must never silently change a wire value.
     */
    public enum Lifecycle {
        @JsonProperty("draft") DRAFT,
        @JsonProperty("active") ACTIVE,
        @JsonProperty("deprecated") DEPRECATED,
        @JsonProperty("retired") RETIRED
    }

    public enum ConformanceLevel { L0, L1, L2, L3 }

    public enum DataClassification {
        @JsonProperty("public") PUBLIC,
        @JsonProperty("internal") INTERNAL,
        @JsonProperty("customer-confidential") CUSTOMER_CONFIDENTIAL,
        @JsonProperty("restricted") RESTRICTED
    }

    /**
     * How much human agreement an invocation needs. The mode is a property of the tool, not of
     * the conversation: an agent cannot talk its way into a lower mode.
     */
    public enum ApprovalMode {
        @JsonProperty("none") NONE,
        @JsonProperty("notify") NOTIFY,
        @JsonProperty("confirm") CONFIRM,
        @JsonProperty("dualControl") DUAL_CONTROL,
        @JsonProperty("humanExecution") HUMAN_EXECUTION
    }

    public record Owner(String team, String contact, String escalation) {
    }

    public record Approval(ApprovalMode mode, String confirmationTemplate, Integer expiresInSeconds) {
    }

    public record RecordKeeping(String auditEvent, String retention, List<String> regulatoryBasis) {
    }

    public record Cost(@JsonProperty("class") String costClass, Integer p95LatencyMs) {
    }

    public record RateLimit(Integer perPrincipalPerMinute, Integer perPrincipalPerDay) {
    }

    public record Availability(String slo, String killSwitch) {
    }

    /** What the agent evaluation suite last measured. Stale numbers are a review finding. */
    public record Evaluation(String suite, String lastRunAt, Double selectionAccuracy,
                             Double refusalAccuracy, Double argumentAccuracy) {
    }

    public boolean requiresApproval() {
        return approval != null && approval.mode() != ApprovalMode.NONE
                && approval.mode() != ApprovalMode.NOTIFY;
    }
}
