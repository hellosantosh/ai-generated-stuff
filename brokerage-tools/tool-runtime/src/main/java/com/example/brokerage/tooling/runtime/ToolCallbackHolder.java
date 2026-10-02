package com.example.brokerage.tooling.runtime;

import java.util.LinkedHashMap;
import java.util.Map;
import java.util.Set;

import org.springframework.ai.tool.ToolCallback;

/**
 * A published toolset, callable by name.
 *
 * <p>The test harness needs this, and so does anything else that drives tools without a model in
 * the loop — a batch job replaying a trace, an operator reproducing a customer's conversation, the
 * desktop connector. Spring AI hands out a list of callbacks because that is what a chat model
 * wants; everything else wants a map.
 *
 * <p>Calling a name that is not in the set returns the pipeline's own {@code UNKNOWN_TOOL}
 * envelope rather than throwing, because that is what a model would have received, and a harness
 * that threw here would be testing something the model never experiences.
 */
public class ToolCallbackHolder {

    private final Map<String, ToolCallback> byName = new LinkedHashMap<>();
    private final ToolCallback unknown;

    public ToolCallbackHolder(java.util.List<ToolCallback> callbacks) {
        for (ToolCallback callback : callbacks) {
            byName.put(callback.getToolDefinition().name(), callback);
        }
        this.unknown = callbacks.isEmpty() ? null : callbacks.getFirst();
    }

    public Set<String> names() {
        return byName.keySet();
    }

    /** Invoke by name. An unknown name is routed through the pipeline so it is audited too. */
    public String call(String tool, String arguments) {
        ToolCallback callback = byName.get(tool);
        if (callback == null) {
            if (unknown instanceof DescriptorToolCallback descriptor) {
                return descriptor.callUnknown(tool, arguments);
            }
            return "{\"ok\":false,\"error\":{\"code\":\"UNKNOWN_TOOL\",\"message\":\"There is no "
                    + "tool called " + tool + " available in this session.\"}}";
        }
        return callback.call(arguments);
    }
}
