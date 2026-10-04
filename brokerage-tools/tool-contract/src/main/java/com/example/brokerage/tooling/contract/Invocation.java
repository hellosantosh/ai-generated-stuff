package com.example.brokerage.tooling.contract;

import java.time.Instant;

import tools.jackson.databind.JsonNode;

/**
 * One attempt to call one tool. Everything a handler is allowed to know.
 *
 * <p>Note what is <em>not</em> here: the conversation. A handler cannot read the chat history,
 * cannot see the system prompt, and cannot be talked into anything, because it is never shown the
 * talking. All it has is a descriptor, a set of arguments that have already been validated against
 * that descriptor's schema, and a principal the runtime established. That is the whole reason a
 * tool is a safer surface than a prompt.
 *
 * @param descriptor the contract this call is being held to
 * @param arguments  the model's arguments, already validated; a handler never re-checks the schema
 * @param principal  who the call is for
 * @param requestId  the correlation identifier, echoed in the envelope and in the audit record
 * @param startedAt  when the runtime accepted the call, for the latency the envelope reports
 */
public record Invocation(ToolDescriptor descriptor, JsonNode arguments, Principal principal,
                         String requestId, Instant startedAt) {

    public String string(String name) {
        JsonNode node = arguments.path(name);
        return node.isMissingNode() || node.isNull() ? null : node.asString();
    }

    public String string(String name, String fallback) {
        String value = string(name);
        return value == null ? fallback : value;
    }

    public Integer integer(String name) {
        JsonNode node = arguments.path(name);
        return node.isMissingNode() || node.isNull() ? null : node.asInt();
    }

    public int integer(String name, int fallback) {
        Integer value = integer(name);
        return value == null ? fallback : value;
    }

    public boolean flag(String name, boolean fallback) {
        JsonNode node = arguments.path(name);
        return node.isMissingNode() || node.isNull() ? fallback : node.asBoolean();
    }

    public JsonNode node(String name) {
        return arguments.path(name);
    }

    public boolean has(String name) {
        JsonNode node = arguments.path(name);
        return !node.isMissingNode() && !node.isNull();
    }
}
