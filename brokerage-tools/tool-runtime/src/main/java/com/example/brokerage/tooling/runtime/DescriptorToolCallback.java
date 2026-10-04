package com.example.brokerage.tooling.runtime;

import com.example.brokerage.tooling.contract.DescriptorCodec;
import com.example.brokerage.tooling.contract.Principal;
import com.example.brokerage.tooling.contract.ToolDescriptor;

import org.springframework.ai.chat.model.ToolContext;
import org.springframework.ai.tool.ToolCallback;
import org.springframework.ai.tool.definition.DefaultToolDefinition;
import org.springframework.ai.tool.definition.ToolDefinition;
import org.springframework.ai.tool.metadata.ToolMetadata;

/**
 * One descriptor, presented to Spring AI as a tool.
 *
 * <p>This class is the whole binding between the specification and the framework, and its size is
 * the argument for writing descriptors as data. There is no {@code @Tool} annotation anywhere in
 * this project. The model's view of a tool is a JSON file that a linter checks, a reviewer reads
 * and a desktop client can be handed; Spring AI gets the same three strings it would have derived
 * from an annotation, and every other runtime gets them too, from an adapter about this long.
 *
 * <p>Contrast the annotated approach. {@code @Tool(description = "...")} puts the model-facing
 * contract inside a Java method signature, where the schema is inferred from parameter types, the
 * description is a string literal nobody lints, and the only way to see what the model is being
 * shown is to start the application. It is faster for the first three tools and it does not scale
 * past a dozen.
 *
 * <p>The callback is deliberately dumb: it hands the raw argument string to
 * {@link InvocationPipeline} and returns whatever JSON comes back. It does not validate, authorize,
 * catch or format, because doing any of those here would mean they happened only for callers who
 * came through Spring AI.
 */
public class DescriptorToolCallback implements ToolCallback {

    private final ToolDescriptor descriptor;
    private final InvocationPipeline pipeline;
    private final Principal principal;
    private final ToolTrace trace;

    public DescriptorToolCallback(ToolDescriptor descriptor, InvocationPipeline pipeline,
                                  Principal principal, ToolTrace trace) {
        this.descriptor = descriptor;
        this.pipeline = pipeline;
        this.principal = principal;
        this.trace = trace;
    }

    @Override
    public ToolDefinition getToolDefinition() {
        return DefaultToolDefinition.builder()
                .name(descriptor.name())
                .description(descriptor.description())
                .inputSchema(DescriptorCodec.json(descriptor.inputSchema()))
                .build();
    }

    @Override
    public ToolMetadata getToolMetadata() {
        // returnDirect is false for every tool in this catalog: the model must always get a turn
        // to read the result, because every result can carry a blocker or an error it has to relay.
        return ToolMetadata.builder().returnDirect(false).build();
    }

    @Override
    public String call(String arguments) {
        InvocationPipeline.Outcome outcome = pipeline.invoke(descriptor.name(), arguments,
                principal);
        if (trace != null) {
            trace.record(outcome, arguments);
        }
        return outcome.json();
    }

    @Override
    public String call(String arguments, ToolContext context) {
        return call(arguments);
    }

    public ToolDescriptor descriptor() {
        return descriptor;
    }

    /**
     * Route a name this session does not have through the pipeline anyway, so that a model's
     * invented tool name produces a real UNKNOWN_TOOL envelope and a real audit record. An agent
     * guessing at names is worth knowing about.
     */
    String callUnknown(String tool, String arguments) {
        InvocationPipeline.Outcome outcome = pipeline.invoke(tool, arguments, principal);
        if (trace != null) {
            trace.record(outcome, arguments);
        }
        return outcome.json();
    }
}
