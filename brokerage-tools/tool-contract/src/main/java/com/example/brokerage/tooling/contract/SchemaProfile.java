package com.example.brokerage.tooling.contract;

import java.util.List;
import java.util.Set;

import tools.jackson.databind.JsonNode;

/**
 * The JSON Schema profile a descriptor's schemas must stay inside.
 *
 * <p>JSON Schema is far larger than any tool-calling runtime supports, and the parts that are
 * supported unevenly are exactly the parts that fail silently: a model shown
 * {@code oneOf} will cheerfully produce an object matching none of the branches, and the runtime
 * that dropped the keyword on the way to the model has no way to say so. So the profile is a
 * whitelist, not a blacklist, and the linter enforces it.
 *
 * <p>The rule behind the whitelist: a keyword is permitted when it either constrains a scalar in a
 * way every runtime forwards, or describes structure. Keywords that express alternation,
 * negation or conditionals are not permitted — model them as an enum and separate fields, or as
 * two tools.
 */
public final class SchemaProfile {

    /** Keywords any schema in a descriptor may use. */
    public static final Set<String> PERMITTED = Set.of(
            "type", "properties", "required", "additionalProperties", "items", "description",
            "enum", "const", "default", "format", "pattern", "minimum", "maximum",
            "exclusiveMinimum", "exclusiveMaximum", "minLength", "maxLength", "minItems",
            "maxItems", "uniqueItems", "$defs", "$ref", "title", "examples");

    /**
     * Keywords that are rejected rather than ignored, each for a reason the author needs to hear.
     * The message is the one the linter prints, so it says what to do instead.
     */
    public static final List<Prohibition> PROHIBITED = List.of(
            new Prohibition("oneOf", "Alternation is forwarded inconsistently and the model cannot "
                    + "see which branch it failed. Use an enum discriminator, or split the tool."),
            new Prohibition("anyOf", "Same as oneOf, with the added problem that a partially valid "
                    + "object passes. Name the variants explicitly."),
            new Prohibition("allOf", "Composition the model cannot see through. Inline the fields."),
            new Prohibition("not", "Negative constraints give the model nothing to aim at. State "
                    + "the permitted values."),
            new Prohibition("if", "Conditional schemas are dropped by most runtimes, so the model "
                    + "never learns the condition. Split into two tools or validate in the handler."),
            new Prohibition("then", "The branch of a conditional the model never sees. Split the "
                    + "tool, or enforce the rule in the handler and state it in the description."),
            new Prohibition("else", "The other branch of a conditional the model never sees. Split "
                    + "the tool, or enforce the rule in the handler and state it in the "
                    + "description."),
            new Prohibition("dependentRequired", "Cross-field requirements belong in the description "
                    + "and in the handler, where a violation can be explained."),
            new Prohibition("dependentSchemas", "See dependentRequired."),
            new Prohibition("patternProperties", "An object whose keys carry meaning is a list of "
                    + "records. Model it as an array."),
            new Prohibition("additionalItems", "Tuple typing is not forwarded. Use a fixed object."),
            new Prohibition("unevaluatedProperties", "Depends on annotation collection the runtime "
                    + "does not perform. Close the object with additionalProperties: false."),
            new Prohibition("$dynamicRef", "Resolution varies by implementation."),
            new Prohibition("$recursiveRef", "Withdrawn from the specification."),
            new Prohibition("contentEncoding", "Binary in an argument is a design mistake. Pass a "
                    + "reference the tool can resolve."),
            new Prohibition("contentMediaType", "See contentEncoding."));

    /** Parameter names a descriptor may not use, because a runtime or the firm has claimed them. */
    public static final Set<String> RESERVED_PARAMETERS = Set.of(
            // Identity. Established by the runtime from the session, never by the model.
            "principal", "principalId", "customerId", "customerNumber", "userId", "user",
            "subject", "onBehalfOf", "impersonate",
            // Authorization. A scope the caller can name is not a scope.
            "role", "roles", "scope", "scopes", "entitlement", "entitlements",
            // Credentials. If a tool needs one, the runtime holds it.
            "token", "accessToken", "apiKey", "secret", "password",
            // Overrides. Every one of these is a way around a check somebody wrote on purpose.
            "dryRun", "force", "skipValidation", "skipChecks", "override", "sudo", "admin",
            // Runtime bookkeeping. Supplied by the pipeline, echoed in the envelope.
            "requestId", "traceId", "idempotencyKey", "toolName", "_meta");

    public record Prohibition(String keyword, String why) {
    }

    private SchemaProfile() {
    }

    public static Prohibition prohibitionFor(String keyword) {
        return PROHIBITED.stream().filter(p -> p.keyword().equals(keyword)).findFirst().orElse(null);
    }

    /**
     * True when this node is a schema object rather than a map of schemas. {@code properties} and
     * {@code $defs} hold schemas under arbitrary names, so their children are schemas and they
     * themselves are not; everything else that has a {@code type} or a {@code $ref} is.
     */
    public static boolean isSchemaObject(JsonNode node) {
        return node != null && node.isObject()
                && (node.has("type") || node.has("$ref") || node.has("enum") || node.has("const"));
    }
}
