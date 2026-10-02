package com.example.brokerage.tooling.contract;

import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;

import com.networknt.schema.Error;
import com.networknt.schema.Schema;
import com.networknt.schema.SchemaRegistry;
import com.networknt.schema.SpecificationVersion;
import tools.jackson.databind.JsonNode;

/**
 * Validates tool arguments against a descriptor's {@code inputSchema}, and tool payloads against
 * its {@code outputSchema}.
 *
 * <p>This class exists because the model's compliance with the input schema is a statistical
 * property, not a guarantee. Claude follows a closed object schema with narrow patterns very
 * reliably, and "very reliably" is the wrong standard for an order ticket. So every invocation is
 * validated at the boundary, before the handler sees an argument, and a failure becomes an
 * {@link ErrorCode#INVALID_ARGUMENT} the model can read and correct.
 *
 * <p>The output side is validated too, which surprises people. The reason is that the output
 * schema is the only description of the payload the model ever sees; if the handler drifts away
 * from it, the model is being misled by a document the firm published. In this implementation an
 * output violation fails the call rather than returning a payload the descriptor disclaims.
 *
 * <p>Compiled schemas are cached by tool name. Compiling a 200-line schema per invocation is
 * measurable at trading volumes, and the schema cannot change while the process is running.
 */
public final class ArgumentValidator {

    private static final SchemaRegistry REGISTRY =
            SchemaRegistry.withDefaultDialect(SpecificationVersion.DRAFT_2020_12);

    private final Map<String, Schema> inputs = new ConcurrentHashMap<>();
    private final Map<String, Schema> outputs = new ConcurrentHashMap<>();

    /** One violation, named by the JSON pointer the model used, so the model can find the field. */
    public record Violation(String at, String message) {

        @Override
        public String toString() {
            return at.isEmpty() ? message : at + ": " + message;
        }
    }

    public List<Violation> validateArguments(ToolDescriptor descriptor, JsonNode arguments) {
        Schema schema = inputs.computeIfAbsent(descriptor.name(),
                ignored -> REGISTRY.getSchema(descriptor.inputSchema()));
        return violations(schema, arguments);
    }

    public List<Violation> validatePayload(ToolDescriptor descriptor, JsonNode payload) {
        Schema schema = outputs.computeIfAbsent(descriptor.name(),
                ignored -> REGISTRY.getSchema(descriptor.outputSchema()));
        return violations(schema, payload);
    }

    private static List<Violation> violations(Schema schema, JsonNode instance) {
        List<Violation> found = new ArrayList<>();
        for (Error error : schema.validate(instance == null ? missing() : instance)) {
            found.add(new Violation(error.getInstanceLocation().toString(), error.getMessage()));
        }
        return found;
    }

    private static JsonNode missing() {
        return DescriptorCodec.mapper().createObjectNode();
    }

    /**
     * The violations as one sentence for the model. Pointers first, each with the fix implied, and
     * capped at five: a model given twenty violations fixes the first three and re-sends.
     */
    public static String explain(List<Violation> violations) {
        List<String> parts = violations.stream().limit(5).map(Violation::toString).toList();
        String more = violations.size() > 5 ? " (and " + (violations.size() - 5) + " more)" : "";
        return String.join("; ", parts) + more;
    }
}
