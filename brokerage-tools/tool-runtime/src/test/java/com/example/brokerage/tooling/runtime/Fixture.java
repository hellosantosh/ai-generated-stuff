package com.example.brokerage.tooling.runtime;

import java.time.Instant;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.concurrent.atomic.AtomicInteger;

import com.example.brokerage.tooling.contract.DescriptorCodec;
import com.example.brokerage.tooling.contract.ErrorCode;
import com.example.brokerage.tooling.contract.Invocation;
import com.example.brokerage.tooling.contract.Principal;
import com.example.brokerage.tooling.contract.ToolDescriptor;
import com.example.brokerage.tooling.contract.ToolFailure;
import com.example.brokerage.tooling.contract.ToolHandler;

/**
 * A two-tool catalog, built in this file, for testing the pipeline in isolation.
 *
 * <p>The brokerage catalog is a fine thing to test the pipeline <em>through</em> and a poor thing
 * to test it <em>with</em>: when a pipeline test fails against fourteen real tools, the first
 * question is whether the pipeline broke or a handler did. Two tools defined here — one read, one
 * that needs approval — answer that question before it is asked.
 */
final class Fixture {

    static final String READ = "fixture_thing_get";
    static final String WRITE = "fixture_thing_place";
    static final String READ_SCOPE = "fixture:thing:read";
    static final String WRITE_SCOPE = "fixture:thing:write";

    private static final String DESCRIPTION_READ =
            "Echoes its arguments back, so that a test of the pipeline has something to assert "
            + "on without a domain behind it. Use when a test needs a conformant descriptor that "
            + "changes nothing. Do not use for anything real; it is a fixture. Read the label and "
            + "count fields in the result, which are the ones that were sent. This changes "
            + "nothing and may be called freely.";

    private static final String DESCRIPTION_WRITE =
            "Pretends to commit something, and echoes the arguments back as the record of what it "
            + "did. Use when a test needs a tool that requires the customer's approval before it "
            + "runs. Do not use for anything real; it is a fixture. Read the label and count in "
            + "the result to confirm what was committed. The effect cannot be undone, so it "
            + "requires an approval the runtime enforces.";

    private Fixture() {
    }

    static ToolDescriptor read() {
        return descriptor(READ, "Read a fixture thing", DESCRIPTION_READ, true, 0, 60);
    }

    static ToolDescriptor write() {
        return descriptor(WRITE, "Commit a fixture thing", DESCRIPTION_WRITE, false, 3, 5);
    }

    /**
     * Titles are distinct per fixture, because the linter requires it — a human approving one of
     * two tools called "a fixture tool" cannot tell which. The first thing this fixture caught was
     * itself.
     */
    static ToolDescriptor descriptor(String name, String title, String description,
                                     boolean readOnly, int riskTier, int perMinute) {
        String approval = riskTier >= 2
                ? """
                  {
                    "mode": "confirm",
                    "confirmationTemplate": "Do {{label}}, {{count}} times? This cannot be undone.",
                    "expiresInSeconds": 60
                  }"""
                : "{ \"mode\": \"none\" }";
        String json = """
                {
                  "name": "%s",
                  "title": "%s",
                  "description": "%s",
                  "inputSchema": {
                    "type": "object",
                    "properties": {
                      "label": { "type": "string", "pattern": "^[a-z]{1,16}$",
                                 "description": "A lowercase label, at most sixteen letters." },
                      "count": { "type": "integer", "minimum": 1, "maximum": 10,
                                 "description": "How many, between one and ten." }
                    },
                    "required": ["label"],
                    "additionalProperties": false
                  },
                  "outputSchema": {
                    "type": "object",
                    "properties": {
                      "label": { "type": "string", "maxLength": 16, "description": "The label." },
                      "count": { "type": "integer", "description": "The count that was used." }
                    },
                    "required": ["label", "count"],
                    "additionalProperties": false
                  },
                  "annotations": {
                    "readOnlyHint": %b,
                    "destructiveHint": %b,
                    "idempotentHint": true,
                    "openWorldHint": false
                  },
                  "_meta": {
                    "com.example.tooling/governance": {
                      "version": "1.0.0",
                      "lifecycle": "active",
                      "conformanceLevel": "L2",
                      "riskTier": %d,
                      "owner": { "team": "t", "contact": "t@example.com", "escalation": "page://t" },
                      "dataClassification": "internal",
                      "containsPii": false,
                      "piiFields": [],
                      "entitlements": ["%s"],
                      "approval": %s,
                      "recordKeeping": { "auditEvent": "tool.fixture", "retention": "P1Y" },
                      "rateLimit": { "perPrincipalPerMinute": %d },
                      "availability": { "slo": "99.9", "killSwitch": "tools.fixture.%s" },
                      "evaluation": { "suite": "evals/fixture", "lastRunAt": "2026-09-28T07:00:00Z",
                                      "selectionAccuracy": 1.0, "refusalAccuracy": 1.0 }
                    }
                  }
                }
                """.formatted(name, title, description, readOnly, !readOnly, riskTier,
                readOnly ? READ_SCOPE : WRITE_SCOPE, approval, perMinute,
                readOnly ? "read" : "write");
        return DescriptorCodec.read(json);
    }

    /** A handler that echoes, counts its calls, and can be told to misbehave. */
    static final class Echo implements ToolHandler {

        enum Behavior { NORMAL, REFUSE, THROW, BAD_PAYLOAD }

        record Payload(String label, int count) {
        }

        final AtomicInteger calls = new AtomicInteger();
        private final String name;
        private final Behavior behavior;

        Echo(String name, Behavior behavior) {
            this.name = name;
            this.behavior = behavior;
        }

        static Echo of(String name) {
            return new Echo(name, Behavior.NORMAL);
        }

        @Override
        public String name() {
            return name;
        }

        @Override
        public Object handle(Invocation invocation) {
            calls.incrementAndGet();
            return switch (behavior) {
                case REFUSE -> throw ToolFailure.of(ErrorCode.NOT_FOUND,
                        "Nothing by that label.", "Ask the customer for a different label.");
                case THROW -> throw new IllegalStateException(
                        "a leaked constraint name the model must never see");
                case BAD_PAYLOAD -> Map.of("label", invocation.string("label"));
                case NORMAL -> new Payload(invocation.string("label"),
                        invocation.integer("count", 1));
            };
        }
    }

    static Principal principal(String... entitlements) {
        return new Principal("test:subject", "cust_test", Set.of(entitlements), "session-1");
    }

    static InvocationPipeline pipeline(ToolRegistry registry, ApprovalGate approvals,
                                       KillSwitches switches, AuditLog audit) {
        return new InvocationPipeline(registry, approvals, new RateLimiter(), switches, audit,
                null, () -> Instant.parse("2026-10-01T14:30:00Z"));
    }

    static ToolRegistry registry(List<ToolDescriptor> descriptors, List<ToolHandler> handlers) {
        return new ToolRegistry(null, descriptors, handlers);
    }
}
