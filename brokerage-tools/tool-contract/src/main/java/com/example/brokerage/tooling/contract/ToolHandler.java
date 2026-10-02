package com.example.brokerage.tooling.contract;

/**
 * The code behind one tool.
 *
 * <p>A handler is startlingly small in a well-built catalog, and that is the measure of whether
 * the descriptor is doing its job. Everything generic — schema validation, entitlements, approval,
 * rate limiting, the envelope, redaction, audit — happens in the pipeline around it. What is left
 * is the domain call and the few rules a JSON Schema cannot express.
 *
 * <p>If a handler is long, it is usually because the tool is doing two things, and the fix is two
 * tools. The second most common reason is that it is re-validating arguments the pipeline already
 * validated, which means somebody did not trust the schema — and an untrusted schema is a schema
 * the model is also being misled by.
 */
public interface ToolHandler {

    /** The descriptor name this handler implements. Matched at startup, not at call time. */
    String name();

    /**
     * Do the work and return the payload, which must conform to the descriptor's
     * {@code outputSchema}. Throw {@link ToolFailure} to return a taxonomy error instead.
     */
    Object handle(Invocation invocation);
}
