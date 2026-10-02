package com.example.brokerage.agent.web;

import java.util.List;
import java.util.Map;

import com.example.brokerage.agent.Sessions;
import com.example.brokerage.tooling.contract.DescriptorCodec;
import com.example.brokerage.tooling.runtime.ApprovalGate;
import com.example.brokerage.tooling.runtime.AuditLog;
import com.example.brokerage.tooling.runtime.InvocationPipeline;

import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;
import tools.jackson.databind.JsonNode;

/**
 * Calling a tool directly, without a model, and approving what needs approving.
 *
 * <p>The direct-invocation endpoint is the most useful debugging tool in the project. When an
 * agent gives a strange answer, the question is always whether the tool was wrong or the model
 * read it wrong, and this is how you find out in ten seconds: send the arguments the trace
 * recorded and look at the envelope. It is also how the console's catalog page lets a reviewer
 * exercise a tool while reading its descriptor.
 *
 * <p>Note that it goes through exactly the same pipeline as the model's calls. An endpoint that
 * bypassed validation or approval would be a second, unreviewed way into the brokerage, which is
 * the sort of convenience that turns up in an audit finding.
 */
@RestController
@RequestMapping("/api")
public class InvokeController {

    public record InvokeRequest(String tool, JsonNode arguments, Sessions.Role role,
                                String sessionId) {
    }

    public record InvokeResponse(String tool, boolean ok, long latencyMs, String approvalReference,
                                 JsonNode result) {
    }

    private final InvocationPipeline pipeline;
    private final ApprovalGate approvals;
    private final AuditLog audit;
    private final Sessions sessions;
    private final java.util.function.Supplier<java.time.Instant> clock;

    public InvokeController(InvocationPipeline pipeline, ApprovalGate approvals, AuditLog audit,
                            Sessions sessions,
                            java.util.function.Supplier<java.time.Instant> clock) {
        this.pipeline = pipeline;
        this.approvals = approvals;
        this.audit = audit;
        this.sessions = sessions;
        this.clock = clock;
    }

    @PostMapping("/invoke")
    public InvokeResponse invoke(@RequestBody InvokeRequest request) {
        Sessions.Role role = request.role() == null ? Sessions.Role.TRADING : request.role();
        String sessionId = request.sessionId() == null ? "console" : request.sessionId();
        Sessions.Session session = sessions.require(sessionId, role);
        InvocationPipeline.Outcome outcome = pipeline.invoke(request.tool(),
                request.arguments() == null ? "{}" : DescriptorCodec.json(request.arguments()),
                session.principal());
        return new InvokeResponse(outcome.tool(), outcome.ok(), outcome.latencyMs(),
                outcome.approvalReference(), DescriptorCodec.tree(outcome.json()));
    }

    @GetMapping("/approvals")
    public List<ApprovalGate.Pending> awaiting(@RequestParam(required = false) String sessionId) {
        return approvals.awaiting(sessionId);
    }

    @PostMapping("/approvals/{reference}/grant")
    public Map<String, Object> grant(@PathVariable String reference,
                                     @RequestParam(defaultValue = "customer") String by) {
        approvals.grant(reference, by, clock.get());
        return Map.of("reference", reference, "granted", true, "by", by);
    }

    @PostMapping("/approvals/{reference}/deny")
    public Map<String, Object> deny(@PathVariable String reference) {
        approvals.deny(reference);
        return Map.of("reference", reference, "granted", false);
    }

    @GetMapping("/audit")
    public List<AuditLog.Record> auditTrail(@RequestParam(defaultValue = "50") int limit) {
        return audit.recent(limit);
    }
}
