package com.example.brokerage.harness;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.List;

import com.example.brokerage.tooling.contract.DescriptorCodec;

import com.fasterxml.jackson.annotation.JsonInclude;

/**
 * One recorded conversation: the choices Claude made, in order, and what it finally said.
 *
 * <p>A cassette is the answer to a problem every team building agents runs into about three weeks
 * in. Evaluating tool selection means asking a frontier model what it would do, which means a
 * network call, a bill, a minute of latency and a result that is not quite the same twice. Put
 * that in CI and the suite is slow, expensive and flaky — so it gets marked {@code @Disabled} and
 * the tools drift unchecked.
 *
 * <p>What the cassette holds is only the model's part: which tool it chose, with what arguments,
 * in what order, and the prose it ended on. The tools themselves are <em>not</em> recorded. On
 * replay the real pipeline runs, the real handlers run, the real schemas validate and the real
 * approval gate fires. So a replayed scenario still fails when somebody breaks a handler, narrows
 * a pattern, renames an output field or changes a risk tier — which is most of what changes.
 *
 * <p>What replay cannot catch is a change to a <em>description</em>, because the model's reading of
 * it is what was recorded. That is why the suite has two modes and why the live mode is wired to
 * descriptor changes in CI: edit a description and the cassettes for that tool are stale by
 * definition.
 *
 * <p>A recorded confirmation token is a dead token — single-use, long expired, and bound to a
 * session that no longer exists. So the recorder rewrites any token argument to the placeholder
 * {@value #TOKEN_PLACEHOLDER}, and on replay the harness substitutes the live token issued by the
 * preview that ran a moment earlier. That substitution is the one place replay is not byte for
 * byte, and it is the place it has to be.
 */
@JsonInclude(JsonInclude.Include.NON_NULL)
public record Cassette(String scenarioId, Source source, String model, String recordedAt,
                       String note, List<Turn> turns, String reply, Integer promptTokens,
                       Integer completionTokens) {

    /**
     * Where this cassette came from. The distinction is published rather than implied, because a
     * suite that cannot tell the two apart will quietly claim evidence it does not have.
     */
    public enum Source {
        /** Captured from a real call to the model. Evidence of what it actually chose. */
        RECORDED,
        /**
         * Written by hand as the trajectory the catalog's authors intend. It exercises the whole
         * runtime — schemas, entitlements, the approval gate, the handlers — and it is not evidence
         * about the model. Run the suite with {@code --record} and an API key to replace it.
         */
        AUTHORED
    }

    public Cassette {
        turns = turns == null ? List.of() : List.copyOf(turns);
        source = source == null ? Source.AUTHORED : source;
    }

    /** One assistant turn: the tools it asked for, or the text it ended with. */
    public record Turn(List<Call> calls, String text) {

        public Turn {
            calls = calls == null ? List.of() : List.copyOf(calls);
        }
    }

    /** One tool call as the model emitted it. The arguments are kept verbatim, warts and all. */
    public record Call(String tool, String arguments) {
    }

    public static Cassette read(Path file) throws IOException {
        return DescriptorCodec.mapper().readValue(Files.readString(file), Cassette.class);
    }

    public void write(Path file) throws IOException {
        Files.createDirectories(file.getParent());
        Files.writeString(file, DescriptorCodec.mapper().writerWithDefaultPrettyPrinter()
                .writeValueAsString(this) + "\n");
    }

    /** Stands in for a confirmation token, which cannot meaningfully be recorded. */
    public static final String TOKEN_PLACEHOLDER = "{{confirmationToken}}";

    public static Path pathFor(Path directory, String scenarioId) {
        return directory.resolve(scenarioId + ".json");
    }
}
