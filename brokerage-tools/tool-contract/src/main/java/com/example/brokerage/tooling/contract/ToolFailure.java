package com.example.brokerage.tooling.contract;

import java.util.List;
import java.util.Map;

/**
 * How a handler says no.
 *
 * <p>It is an exception for the handler's convenience — a guard clause reads better than threading
 * a result type through five method calls — and the runtime converts it straight into an
 * {@code ok: false} envelope. The model never sees an exception, a class name or a stack trace.
 *
 * <p>Nothing else may escape a handler. An unexpected exception is caught by the pipeline, logged
 * with the request identifier, and returned as {@link ErrorCode#INTERNAL_ERROR} with no detail,
 * because the alternative is leaking a database constraint name into a customer conversation.
 */
public class ToolFailure extends RuntimeException {

    private final transient ToolResult.Failure failure;

    public ToolFailure(ToolResult.Failure failure) {
        super(failure.code() + ": " + failure.message());
        this.failure = failure;
    }

    public ToolResult.Failure failure() {
        return failure;
    }

    public static ToolFailure of(ErrorCode code, String message, String remediation) {
        return new ToolFailure(ToolResult.Failure.of(code, message, remediation));
    }

    public static ToolFailure notFound(String what, String remediation) {
        return of(ErrorCode.NOT_FOUND, what + " was not found.", remediation);
    }

    public static ToolFailure invalidArgument(String message, String remediation) {
        return of(ErrorCode.INVALID_ARGUMENT, message, remediation);
    }

    public static ToolFailure precondition(String message, String remediation) {
        return of(ErrorCode.PRECONDITION_FAILED, message, remediation);
    }

    /** For {@link ErrorCode#AMBIGUOUS_REQUEST}: the choices the caller has to pick between. */
    public static ToolFailure ambiguous(String message, String remediation,
                                       List<Map<String, Object>> candidates) {
        return new ToolFailure(ToolResult.Failure.of(ErrorCode.AMBIGUOUS_REQUEST, message,
                remediation).withCandidates(candidates));
    }
}
