package com.example.brokerage.tooling.runtime;

import java.time.Duration;
import java.time.Instant;
import java.util.List;
import java.util.UUID;

import com.example.brokerage.tooling.contract.ApprovalContext;
import com.example.brokerage.tooling.contract.ArgumentValidator;
import com.example.brokerage.tooling.contract.DescriptorCodec;
import com.example.brokerage.tooling.contract.ErrorCode;
import com.example.brokerage.tooling.contract.Governance;
import com.example.brokerage.tooling.contract.Invocation;
import com.example.brokerage.tooling.contract.Principal;
import com.example.brokerage.tooling.contract.ToolFailure;
import com.example.brokerage.tooling.contract.ToolResult;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import tools.jackson.databind.JsonNode;

/**
 * Everything that happens around a handler.
 *
 * <p>The ten steps below are the system. A handler is domain code; this is the part that makes a
 * tool catalog safe to point a language model at, and it is identical for all fourteen tools,
 * which is precisely why it belongs here and not in any of them.
 *
 * <pre>
 *    1  resolve      the name must be in this principal's catalog        UNKNOWN_TOOL
 *    2  kill switch  the tool must not be out of service                 UPSTREAM_UNAVAILABLE
 *    3  parse        the arguments must be a JSON object                 INVALID_ARGUMENT
 *    4  validate     they must satisfy the input schema                  INVALID_ARGUMENT
 *    5  entitle      the principal must hold every scope                 NOT_ENTITLED
 *    6  rate limit   within the per-principal budget for this tool       RATE_LIMITED
 *    7  approve      a human must have agreed, for tier 2 and above      APPROVAL_REQUIRED
 *    8  execute      the handler runs, and only now
 *    9  verify       the payload must satisfy the output schema          INTERNAL_ERROR
 *   10  envelope     wrap, stamp provenance, and audit
 * </pre>
 *
 * <p>Two of these are the ones people leave out, so they get their own note.
 *
 * <p><b>Step 5 after step 4.</b> Arguments are validated before entitlements are checked, which
 * looks backwards — surely you refuse the unauthorized caller first? For a human API, yes. For a
 * model, no: the useful answer to a malformed call is "your third argument is wrong", and giving
 * it a bare {@code NOT_ENTITLED} teaches it to retry the same malformed call against a different
 * tool. Nothing leaks, because the tool was already in the principal's visible catalog before the
 * pipeline ran.
 *
 * <p><b>Step 9 at all.</b> Validating the handler's own output sounds like distrust of your
 * colleagues. It is distrust of drift. The output schema is the only description of the payload
 * the model will ever see, and the day a handler adds a field or renames one, the model is being
 * misled by a document the firm published. Failing closed here makes that a caught bug rather than
 * a confusing conversation.
 */
public class InvocationPipeline {

    private static final Logger log = LoggerFactory.getLogger(InvocationPipeline.class);

    private final ToolRegistry registry;
    private final ArgumentValidator validator = new ArgumentValidator();
    private final ApprovalGate approvals;
    private final RateLimiter rateLimiter;
    private final KillSwitches killSwitches;
    private final AuditLog audit;
    private final ApprovalContext approvalContext;
    private final java.util.function.Supplier<Instant> clock;

    public InvocationPipeline(ToolRegistry registry, ApprovalGate approvals,
                              RateLimiter rateLimiter, KillSwitches killSwitches, AuditLog audit,
                              ApprovalContext approvalContext,
                              java.util.function.Supplier<Instant> clock) {
        this.registry = registry;
        this.approvals = approvals;
        this.rateLimiter = rateLimiter;
        this.killSwitches = killSwitches;
        this.audit = audit;
        this.approvalContext = approvalContext == null ? ApprovalContext.NONE : approvalContext;
        this.clock = clock;
    }

    /** The result, plus what the console and the harness need to explain it. */
    public record Outcome(String tool, ToolResult<Object> result, String json, long latencyMs,
                          String approvalReference) {

        public boolean ok() {
            return result.ok();
        }

        public ErrorCode errorCode() {
            return result.error() == null ? null : result.error().code();
        }
    }

    public Outcome invoke(String tool, String argumentsJson, Principal principal) {
        Instant started = clock.get();
        String requestId = "req_" + UUID.randomUUID().toString().replace("-", "").substring(0, 20);

        ToolRegistry.Entry entry = registry.find(tool).orElse(null);
        if (entry == null || !principal.holdsAll(entry.governance().entitlements())) {
            return failure(tool, null, requestId, started, ErrorCode.UNKNOWN_TOOL,
                    "There is no tool called " + tool + " available in this session.",
                    "Use one of the tools you were given. Do not guess at names.", principal, null);
        }
        Governance governance = entry.governance();

        if (governance.availability() != null
                && killSwitches.isOff(governance.availability().killSwitch())) {
            return failure(tool, governance, requestId, started, ErrorCode.UPSTREAM_UNAVAILABLE,
                    "This tool is temporarily out of service.",
                    "Tell the customer the feature is unavailable right now and do not retry in "
                            + "this conversation.", principal, null);
        }

        JsonNode arguments;
        try {
            arguments = argumentsJson == null || argumentsJson.isBlank()
                    ? DescriptorCodec.mapper().createObjectNode()
                    : DescriptorCodec.tree(argumentsJson);
        } catch (RuntimeException e) {
            return failure(tool, governance, requestId, started, ErrorCode.INVALID_ARGUMENT,
                    "The arguments were not valid JSON.",
                    "Re-send the call with a JSON object matching the tool's input schema.",
                    principal, null);
        }
        if (!arguments.isObject()) {
            return failure(tool, governance, requestId, started, ErrorCode.INVALID_ARGUMENT,
                    "Arguments must be a JSON object.",
                    "Re-send the call with a JSON object matching the tool's input schema.",
                    principal, null);
        }

        List<ArgumentValidator.Violation> violations =
                validator.validateArguments(entry.descriptor(), arguments);
        if (!violations.isEmpty()) {
            return failure(tool, governance, requestId, started, ErrorCode.INVALID_ARGUMENT,
                    "The arguments do not match this tool's input schema: "
                            + ArgumentValidator.explain(violations),
                    "Fix the fields named above and call the tool again. Do not try a different "
                            + "tool.", principal, arguments);
        }

        Integer wait = rateLimiter.retryAfter(principal.subject(), tool,
                governance.rateLimit() == null ? null : governance.rateLimit().perPrincipalPerMinute(),
                started);
        if (wait != null) {
            ToolResult.Failure error = ToolResult.Failure.of(ErrorCode.RATE_LIMITED,
                    "This tool has been called too often in this session.",
                    "Wait " + wait + " seconds before calling it again, and tell the customer "
                            + "there is a short delay.").retryAfter(wait);
            return record(tool, governance, requestId, started, principal, arguments,
                    ToolResult.failure(error, meta(governance, requestId, started, false)), null);
        }

        Invocation invocation = new Invocation(entry.descriptor(), arguments, principal, requestId,
                started);
        String approvalReference = null;
        if (governance.requiresApproval()) {
            approvalReference = ApprovalGate.referenceFor(invocation);
            if (!approvals.consume(approvalReference, started)) {
                ApprovalGate.Pending request = approvals.request(invocation, approvalReference,
                        enrich(invocation), started);
                ToolResult.Failure error = ToolResult.Failure.of(ErrorCode.APPROVAL_REQUIRED,
                        "This action needs the customer's explicit approval before it can run. "
                                + "They have been shown: " + request.summary(),
                        "Wait for the customer to approve. If they say yes, call this tool again "
                                + "with exactly the same arguments. If they say no, stop and do "
                                + "not look for another way.").withApprovalReference(approvalReference);
                return record(tool, governance, requestId, started, principal, arguments,
                        ToolResult.failure(error, meta(governance, requestId, started, false)),
                        approvalReference);
            }
        }

        try {
            Object payload = entry.handler().handle(invocation);
            JsonNode tree = DescriptorCodec.tree(payload);
            List<ArgumentValidator.Violation> output =
                    validator.validatePayload(entry.descriptor(), tree);
            if (!output.isEmpty()) {
                log.error("{} returned a payload that does not match its own output schema: {}",
                        tool, ArgumentValidator.explain(output));
                return failure(tool, governance, requestId, started, ErrorCode.INTERNAL_ERROR,
                        "This tool could not complete.",
                        "Tell the customer the request failed and quote " + requestId
                                + ". Do not retry.", principal, arguments);
            }
            return record(tool, governance, requestId, started, principal, arguments,
                    ToolResult.success(payload, meta(governance, requestId, started, true)),
                    approvalReference);
        } catch (ToolFailure failure) {
            return record(tool, governance, requestId, started, principal, arguments,
                    ToolResult.failure(failure.failure(),
                            meta(governance, requestId, started, false)), approvalReference);
        } catch (RuntimeException e) {
            log.error("{} threw {}; request {}", tool, e.getClass().getName(), requestId, e);
            return failure(tool, governance, requestId, started, ErrorCode.INTERNAL_ERROR,
                    "This tool could not complete.",
                    "Tell the customer the request failed and quote " + requestId
                            + ". Do not retry.", principal, arguments);
        }
    }

    // ------------------------------------------------------------------ helpers

    /**
     * Ask the domain for the figures a confirmation needs. Failure here must never block an
     * approval, so a broken enricher degrades the dialog rather than the trade.
     */
    private java.util.Map<String, String> enrich(Invocation invocation) {
        try {
            return approvalContext.enrich(invocation);
        } catch (RuntimeException e) {
            log.warn("approval context failed for {}: {}", invocation.descriptor().name(),
                    e.toString());
            return java.util.Map.of();
        }
    }

    private ToolResult.Meta meta(Governance governance, String requestId, Instant started,
                                 boolean complete) {
        return new ToolResult.Meta(clock.get(), governance == null ? null : governance.version(),
                requestId, complete, null, null,
                Duration.between(started, clock.get()).toMillis());
    }

    private Outcome failure(String tool, Governance governance, String requestId, Instant started,
                            ErrorCode code, String message, String remediation,
                            Principal principal, JsonNode arguments) {
        return record(tool, governance, requestId, started, principal, arguments,
                ToolResult.failure(code, message, remediation,
                        meta(governance, requestId, started, false)), null);
    }

    private Outcome record(String tool, Governance governance, String requestId, Instant started,
                           Principal principal, JsonNode arguments, ToolResult<Object> result,
                           String approvalReference) {
        long latency = Duration.between(started, clock.get()).toMillis();
        audit.write(new AuditLog.Record(requestId, started, tool,
                governance == null ? null : governance.version(),
                governance == null ? -1 : governance.riskTier(),
                governance == null ? null : governance.recordKeeping().auditEvent(),
                governance == null ? null : governance.recordKeeping().retention(),
                principal.subject(), principal.sessionId(), fields(arguments),
                ApprovalGate.canonicalize(arguments).hashCode() == 0 ? "0"
                        : Integer.toHexString(ApprovalGate.canonicalize(arguments).hashCode()),
                result.ok() ? "OK" : result.error().code().name(), latency, approvalReference,
                governance == null || governance.approval() == null
                        ? null : governance.approval().mode()));
        return new Outcome(tool, result, DescriptorCodec.json(result), latency, approvalReference);
    }

    /** Field names only. The values are the customer's business and do not belong in a log. */
    private static List<String> fields(JsonNode arguments) {
        return arguments == null || !arguments.isObject()
                ? List.of() : List.copyOf(arguments.propertyNames());
    }
}
