package com.example.brokerage.tooling.contract;

import java.util.List;
import java.util.Map;

import com.fasterxml.jackson.annotation.JsonIgnore;
import com.fasterxml.jackson.annotation.JsonInclude;
import tools.jackson.databind.JsonNode;

/**
 * A tool descriptor: everything a runtime needs to publish one tool to a model, and everything
 * the firm needs to govern it. Eight fields, and only eight, because the descriptor is the
 * contract and anything else is an implementation detail of whoever is serving it.
 *
 * <p>Four of the fields are rendered into the model's context and the model's decisions turn on
 * them: {@code name}, {@code title}, {@code description} and {@code inputSchema}. One field,
 * {@code outputSchema}, is a promise to the caller rather than a prompt to the model. The rest
 * never reach the model at all: {@code annotations} tell the runtime how much to trust the call,
 * {@code icons} decorate a user interface, and {@code _meta} carries governance.
 *
 * <p>The descriptor is transport-neutral on purpose. It names no protocol and no framework, which
 * is what lets one catalog serve a Spring AI agent, a desktop client and a batch job without any
 * of them renaming a thing.
 */
@JsonInclude(JsonInclude.Include.NON_NULL)
public record ToolDescriptor(
        String name,
        String title,
        String description,
        JsonNode inputSchema,
        JsonNode outputSchema,
        Annotations annotations,
        List<Icon> icons,
        Map<String, JsonNode> _meta) {

    /** The reverse-DNS namespace this book's governance block is published under. */
    public static final String GOVERNANCE_KEY = "com.example.tooling/governance";

    public ToolDescriptor {
        icons = icons == null ? List.of() : List.copyOf(icons);
        _meta = _meta == null ? Map.of() : Map.copyOf(_meta);
    }

    /**
     * Behavioral hints. All four are stated explicitly on every descriptor, even where the value
     * is the specification's default, because "absent" and "false" are read differently by
     * different runtimes and a trading tool cannot afford that ambiguity.
     */
    public record Annotations(boolean readOnlyHint, boolean destructiveHint,
                              boolean idempotentHint, boolean openWorldHint) {

        public static Annotations readOnly() {
            return new Annotations(true, false, true, true);
        }
    }

    /** Decorative only. No control anywhere may depend on an icon being present or absent. */
    @JsonInclude(JsonInclude.Include.NON_NULL)
    public record Icon(String src, String mimeType, String theme, List<String> sizes) {
    }

    /** The governance block, parsed. Absent only on a descriptor that has not been reviewed yet. */
    @JsonIgnore
    public Governance governance() {
        JsonNode node = _meta.get(GOVERNANCE_KEY);
        return node == null ? null : DescriptorCodec.convert(node, Governance.class);
    }

    /** The catalog identifier: dotted, hierarchical, and never sent over the wire. */
    @JsonIgnore
    public String catalogId() {
        return name.replace('_', '.');
    }
}
