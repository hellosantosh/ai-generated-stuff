package com.example.brokerage.tooling.runtime;

import java.time.Instant;
import java.util.List;

import com.example.brokerage.tooling.contract.ErrorCode;
import com.example.brokerage.tooling.contract.Principal;

import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import static org.assertj.core.api.Assertions.assertThat;

/**
 * The ten steps, one at a time.
 *
 * <p>Each test here corresponds to a line in the pipeline's documented order of operations, and
 * together they are the argument that the order is deliberate. The two that are easiest to get
 * wrong, and the two most worth reading, are
 * {@link #schemaViolationsAreExplainedBeforeEntitlementsAreChecked()} and
 * {@link #aHandlerThatDriftsFromItsOwnOutputSchemaFailsClosed()}.
 */
class InvocationPipelineTest {

    private static final Instant NOW = Instant.parse("2026-10-01T14:30:00Z");

    private Fixture.Echo readHandler;
    private Fixture.Echo writeHandler;
    private ToolRegistry registry;
    private ApprovalGate approvals;
    private KillSwitches switches;
    private AuditLog audit;
    private InvocationPipeline pipeline;
    private Principal reader;
    private Principal trader;

    @BeforeEach
    void setUp() {
        readHandler = Fixture.Echo.of(Fixture.READ);
        writeHandler = Fixture.Echo.of(Fixture.WRITE);
        registry = Fixture.registry(List.of(Fixture.read(), Fixture.write()),
                List.of(readHandler, writeHandler));
        approvals = new ApprovalGate();
        switches = new KillSwitches();
        audit = new AuditLog();
        pipeline = Fixture.pipeline(registry, approvals, switches, audit);
        reader = Fixture.principal(Fixture.READ_SCOPE);
        trader = Fixture.principal(Fixture.READ_SCOPE, Fixture.WRITE_SCOPE);
    }

    @Test
    @DisplayName("a valid call runs the handler and comes back in an envelope with provenance")
    void aValidCallSucceeds() {
        var outcome = pipeline.invoke(Fixture.READ, "{\"label\":\"hello\",\"count\":3}", reader);
        assertThat(outcome.ok()).isTrue();
        assertThat(readHandler.calls).hasValue(1);
        assertThat(outcome.result().meta().requestId()).startsWith("req_");
        assertThat(outcome.result().meta().asOf()).isEqualTo(NOW);
        assertThat(outcome.result().meta().complete()).isTrue();
        assertThat(outcome.json()).contains("\"label\":\"hello\"", "\"count\":3");
    }

    @Test
    @DisplayName("an unknown name is UNKNOWN_TOOL and never a suggestion to try a near miss")
    void unknownToolsAreRefusedWithoutHints() {
        var outcome = pipeline.invoke("fixture_thing_delete", "{}", trader);
        assertThat(outcome.errorCode()).isEqualTo(ErrorCode.UNKNOWN_TOOL);
        assertThat(outcome.result().error().remediation()).contains("Do not guess");
        assertThat(readHandler.calls).hasValue(0);
    }

    @Test
    @DisplayName("a tool the principal cannot see is UNKNOWN_TOOL, not NOT_ENTITLED")
    void invisibleToolsAreIndistinguishableFromAbsentOnes() {
        var outcome = pipeline.invoke(Fixture.WRITE, "{\"label\":\"go\"}", reader);
        assertThat(outcome.errorCode())
                .as("telling the model the tool exists but is off limits is an oracle it will probe")
                .isEqualTo(ErrorCode.UNKNOWN_TOOL);
        assertThat(writeHandler.calls).hasValue(0);
    }

    @Test
    @DisplayName("schema violations are explained before entitlements are checked")
    void schemaViolationsAreExplainedBeforeEntitlementsAreChecked() {
        var outcome = pipeline.invoke(Fixture.READ, "{\"label\":\"NOT LOWERCASE\"}", reader);
        assertThat(outcome.errorCode()).isEqualTo(ErrorCode.INVALID_ARGUMENT);
        assertThat(outcome.result().error().message())
                .as("the model needs the field name, not a bare refusal")
                .contains("label");
        assertThat(outcome.result().error().remediation())
                .contains("Do not try a different tool");
        assertThat(readHandler.calls).hasValue(0);
    }

    @Test
    @DisplayName("an extra field the model invented is rejected, not silently dropped")
    void additionalPropertiesAreRejected() {
        var outcome = pipeline.invoke(Fixture.READ,
                "{\"label\":\"hi\",\"urgency\":\"high\"}", reader);
        assertThat(outcome.errorCode()).isEqualTo(ErrorCode.INVALID_ARGUMENT);
        assertThat(readHandler.calls).hasValue(0);
    }

    @Test
    @DisplayName("malformed JSON is an argument error with a readable explanation")
    void malformedArgumentsAreAnArgumentError() {
        var outcome = pipeline.invoke(Fixture.READ, "{label: hi", reader);
        assertThat(outcome.errorCode()).isEqualTo(ErrorCode.INVALID_ARGUMENT);
        assertThat(outcome.result().error().message()).contains("valid JSON");
    }

    @Test
    @DisplayName("a tier 3 tool stops at the approval gate and says what the human will be shown")
    void writingStopsForApproval() {
        var outcome = pipeline.invoke(Fixture.WRITE, "{\"label\":\"commit\",\"count\":2}", trader);
        assertThat(outcome.errorCode()).isEqualTo(ErrorCode.APPROVAL_REQUIRED);
        assertThat(outcome.approvalReference()).startsWith("apr_");
        assertThat(writeHandler.calls).hasValue(0);
        assertThat(approvals.peek(outcome.approvalReference())).isPresent();
        assertThat(approvals.peek(outcome.approvalReference()).orElseThrow().summary())
                .as("the dialog must name the actual arguments, not just the tool")
                .isEqualTo("Do commit, 2 times? This cannot be undone.");
    }

    @Test
    @DisplayName("the same call goes through once the human has agreed")
    void approvalLetsTheCallThrough() {
        String arguments = "{\"label\":\"commit\",\"count\":2}";
        String reference = pipeline.invoke(Fixture.WRITE, arguments, trader).approvalReference();
        approvals.grant(reference, "customer", NOW);
        assertThat(pipeline.invoke(Fixture.WRITE, arguments, trader).ok()).isTrue();
        assertThat(writeHandler.calls).hasValue(1);
    }

    @Test
    @DisplayName("an approval is spent once and does not authorize a second identical call")
    void approvalsAreSingleUse() {
        String arguments = "{\"label\":\"commit\",\"count\":2}";
        String reference = pipeline.invoke(Fixture.WRITE, arguments, trader).approvalReference();
        approvals.grant(reference, "customer", NOW);
        assertThat(pipeline.invoke(Fixture.WRITE, arguments, trader).ok()).isTrue();
        assertThat(pipeline.invoke(Fixture.WRITE, arguments, trader).errorCode())
                .isEqualTo(ErrorCode.APPROVAL_REQUIRED);
        assertThat(writeHandler.calls).hasValue(1);
    }

    @Test
    @DisplayName("an approval for one set of arguments cannot be spent on another")
    void approvalsAreBoundToTheirArguments() {
        String reference = pipeline.invoke(Fixture.WRITE, "{\"label\":\"commit\",\"count\":2}",
                trader).approvalReference();
        approvals.grant(reference, "customer", NOW);
        var outcome = pipeline.invoke(Fixture.WRITE, "{\"label\":\"commit\",\"count\":9}", trader);
        assertThat(outcome.errorCode())
                .as("a changed quantity is a different thing to agree to")
                .isEqualTo(ErrorCode.APPROVAL_REQUIRED);
        assertThat(writeHandler.calls).hasValue(0);
    }

    @Test
    @DisplayName("a reordered argument object is the same approval")
    void approvalReferencesAreCanonical() {
        String reference = pipeline.invoke(Fixture.WRITE, "{\"label\":\"commit\",\"count\":2}",
                trader).approvalReference();
        approvals.grant(reference, "customer", NOW);
        assertThat(pipeline.invoke(Fixture.WRITE, "{\"count\":2,\"label\":\"commit\"}", trader).ok())
                .isTrue();
    }

    @Test
    @DisplayName("an approval from another session is not an approval")
    void approvalsDoNotCrossSessions() {
        String arguments = "{\"label\":\"commit\",\"count\":2}";
        String reference = pipeline.invoke(Fixture.WRITE, arguments, trader).approvalReference();
        approvals.grant(reference, "customer", NOW);
        Principal elsewhere = new Principal("test:subject", "cust_test",
                trader.entitlements(), "session-2");
        assertThat(pipeline.invoke(Fixture.WRITE, arguments, elsewhere).errorCode())
                .isEqualTo(ErrorCode.APPROVAL_REQUIRED);
    }

    @Test
    @DisplayName("a tripped kill switch takes the tool out of service without an error the agent "
            + "will retry")
    void killSwitchesTakeToolsOutOfService() {
        switches.trip("tools.fixture");
        var outcome = pipeline.invoke(Fixture.READ, "{\"label\":\"hi\"}", reader);
        assertThat(outcome.errorCode()).isEqualTo(ErrorCode.UPSTREAM_UNAVAILABLE);
        assertThat(outcome.result().error().remediation()).contains("do not retry");
        assertThat(readHandler.calls).hasValue(0);
    }

    @Test
    @DisplayName("the rate limit answers with a wait rather than letting the agent loop")
    void rateLimitsCarryAWait() {
        ToolRegistry small = Fixture.registry(
                List.of(Fixture.descriptor(Fixture.READ, "Read a fixture thing",
                        Fixture.read().description(), true, 0, 2)),
                List.of(readHandler));
        InvocationPipeline limited = Fixture.pipeline(small, approvals, switches, audit);
        assertThat(limited.invoke(Fixture.READ, "{\"label\":\"a\"}", reader).ok()).isTrue();
        assertThat(limited.invoke(Fixture.READ, "{\"label\":\"b\"}", reader).ok()).isTrue();
        var third = limited.invoke(Fixture.READ, "{\"label\":\"c\"}", reader);
        assertThat(third.errorCode()).isEqualTo(ErrorCode.RATE_LIMITED);
        assertThat(third.result().error().retryAfterSeconds()).isNotNull().isPositive();
    }

    @Test
    @DisplayName("a handler's own refusal reaches the model as data, with its remediation intact")
    void handlerRefusalsBecomeEnvelopes() {
        Fixture.Echo refusing = new Fixture.Echo(Fixture.READ, Fixture.Echo.Behavior.REFUSE);
        InvocationPipeline refusingPipeline = Fixture.pipeline(
                Fixture.registry(List.of(Fixture.read()), List.of(refusing)), approvals, switches,
                audit);
        var outcome = refusingPipeline.invoke(Fixture.READ, "{\"label\":\"hi\"}", reader);
        assertThat(outcome.errorCode()).isEqualTo(ErrorCode.NOT_FOUND);
        assertThat(outcome.result().error().remediation()).contains("different label");
    }

    @Test
    @DisplayName("an unexpected exception becomes INTERNAL_ERROR and leaks nothing")
    void unexpectedExceptionsLeakNothing() {
        Fixture.Echo broken = new Fixture.Echo(Fixture.READ, Fixture.Echo.Behavior.THROW);
        InvocationPipeline brokenPipeline = Fixture.pipeline(
                Fixture.registry(List.of(Fixture.read()), List.of(broken)), approvals, switches,
                audit);
        var outcome = brokenPipeline.invoke(Fixture.READ, "{\"label\":\"hi\"}", reader);
        assertThat(outcome.errorCode()).isEqualTo(ErrorCode.INTERNAL_ERROR);
        assertThat(outcome.json())
                .doesNotContain("constraint", "IllegalStateException", "leaked");
        assertThat(outcome.result().error().remediation()).contains("req_");
    }

    @Test
    @DisplayName("a handler that drifts from its own output schema fails closed")
    void aHandlerThatDriftsFromItsOwnOutputSchemaFailsClosed() {
        Fixture.Echo drifting = new Fixture.Echo(Fixture.READ, Fixture.Echo.Behavior.BAD_PAYLOAD);
        InvocationPipeline driftingPipeline = Fixture.pipeline(
                Fixture.registry(List.of(Fixture.read()), List.of(drifting)), approvals, switches,
                audit);
        var outcome = driftingPipeline.invoke(Fixture.READ, "{\"label\":\"hi\"}", reader);
        assertThat(outcome.errorCode())
                .as("the output schema is the model's only description of the payload; serving "
                        + "something else is misleading it with the firm's own document")
                .isEqualTo(ErrorCode.INTERNAL_ERROR);
    }

    @Test
    @DisplayName("every invocation is audited, with field names but never values")
    void everythingIsAuditedAndNothingSensitiveIsLogged() {
        pipeline.invoke(Fixture.READ, "{\"label\":\"secretish\",\"count\":4}", reader);
        pipeline.invoke("fixture_thing_delete", "{}", reader);
        List<AuditLog.Record> records = audit.recent(10);
        assertThat(records).hasSize(2);
        assertThat(records.get(1).tool()).isEqualTo(Fixture.READ);
        assertThat(records.get(1).outcome()).isEqualTo("OK");
        assertThat(records.get(1).argumentFields()).containsExactlyInAnyOrder("label", "count");
        assertThat(records.get(1).argumentHash()).isNotBlank();
        assertThat(records.toString())
                .as("argument values must not reach a six-year retention log")
                .doesNotContain("secretish");
        assertThat(records.get(0).outcome()).isEqualTo("UNKNOWN_TOOL");
    }

    @Test
    @DisplayName("the registry refuses to start with a handler that has no descriptor")
    void aMismatchedCatalogFailsAtStartup() {
        org.assertj.core.api.Assertions.assertThatThrownBy(() ->
                        Fixture.registry(List.of(Fixture.read()),
                                List.of(readHandler, writeHandler)))
                .isInstanceOf(IllegalStateException.class)
                .hasMessageContaining("handlers with no descriptor");
    }

    @Test
    @DisplayName("the registry refuses to start with a descriptor that has no handler")
    void aToolWithNoCodeBehindItFailsAtStartup() {
        org.assertj.core.api.Assertions.assertThatThrownBy(() ->
                        Fixture.registry(List.of(Fixture.read(), Fixture.write()),
                                List.of(readHandler)))
                .isInstanceOf(IllegalStateException.class)
                .hasMessageContaining("no handler for");
    }

    @Test
    @DisplayName("a principal sees only the tools its scopes cover")
    void visibilityFollowsEntitlements() {
        assertThat(registry.visibleTo(reader)).hasSize(1);
        assertThat(registry.visibleTo(trader)).hasSize(2);
        assertThat(registry.hiddenFrom(reader)).containsKey(Fixture.WRITE);
    }
}
