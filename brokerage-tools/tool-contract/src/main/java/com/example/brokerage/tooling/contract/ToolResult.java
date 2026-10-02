package com.example.brokerage.tooling.contract;

import java.time.Instant;
import java.util.List;
import java.util.Map;

import com.fasterxml.jackson.annotation.JsonInclude;

/**
 * The result envelope. Every tool in the catalog returns this shape, successful or not, which is
 * why the envelope is specified once here rather than repeated in fourteen output schemas.
 *
 * <p>Two decisions in this record do most of the work.
 *
 * <p>The first is that failures arrive as data, not as exceptions. A tool that throws gives the
 * runtime a stack trace and gives the model a wall of text it was never taught to read. A tool
 * that returns {@code ok: false} with a code from the closed taxonomy gives the model something it
 * can branch on, and gives the agent's author something to assert in a test.
 *
 * <p>The second is that {@code meta} is never optional. {@code asOf} tells the model how old the
 * answer is, which matters the moment a quote is involved; {@code complete} tells it whether it is
 * looking at everything or at a page; {@code requestId} is the only thing a customer can quote back
 * to support. Leaving any of the three to the handler's discretion means some handler will omit it.
 *
 * @param <T> the payload type, which must conform to the descriptor's {@code outputSchema}
 */
@JsonInclude(JsonInclude.Include.NON_NULL)
public record ToolResult<T>(boolean ok, T data, Failure error, Meta meta) {

    /**
     * A failure the model is expected to read and act on. {@code message} is written for the model
     * and is safe to paraphrase to a customer; {@code remediation} is written in the imperative
     * because that is the form an agent follows most reliably.
     */
    @JsonInclude(JsonInclude.Include.NON_NULL)
    public record Failure(ErrorCode code, String message, String remediation, boolean retryable,
                          Integer retryAfterSeconds, List<Map<String, Object>> candidates,
                          String approvalReference) {

        public static Failure of(ErrorCode code, String message, String remediation) {
            return new Failure(code, message, remediation, code.retryable(), null, null, null);
        }

        public Failure retryAfter(int seconds) {
            return new Failure(code, message, remediation, true, seconds, candidates, approvalReference);
        }

        /** Attach the choices the caller must pick between, for {@link ErrorCode#AMBIGUOUS_REQUEST}. */
        public Failure withCandidates(List<Map<String, Object>> options) {
            return new Failure(code, message, remediation, retryable, retryAfterSeconds, options,
                    approvalReference);
        }

        public Failure withApprovalReference(String reference) {
            return new Failure(code, message, remediation, retryable, retryAfterSeconds, candidates,
                    reference);
        }
    }

    /**
     * Provenance for the payload. Present on success and on failure, because "when did you ask"
     * is as interesting after a timeout as after a fill.
     *
     * @param asOf      the instant the data describes, not the instant the envelope was written
     * @param complete  false when the payload was paged or truncated
     * @param nextCursor an opaque forward cursor, or null when complete
     * @param truncated what was dropped to fit the budget, in words the model can relay
     */
    @JsonInclude(JsonInclude.Include.NON_NULL)
    public record Meta(Instant asOf, String toolVersion, String requestId, boolean complete,
                       String nextCursor, String truncated, Long latencyMs) {
    }

    public static <T> ToolResult<T> success(T data, Meta meta) {
        return new ToolResult<>(true, data, null, meta);
    }

    public static <T> ToolResult<T> failure(Failure error, Meta meta) {
        return new ToolResult<>(false, null, error, meta);
    }

    public static <T> ToolResult<T> failure(ErrorCode code, String message, String remediation,
                                            Meta meta) {
        return failure(Failure.of(code, message, remediation), meta);
    }
}
