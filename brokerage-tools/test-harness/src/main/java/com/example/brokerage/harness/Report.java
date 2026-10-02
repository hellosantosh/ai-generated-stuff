package com.example.brokerage.harness;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

import com.example.brokerage.tooling.contract.DescriptorCodec;
import com.example.brokerage.tooling.runtime.ToolTrace;

/**
 * The scores, and the per-scenario detail behind them.
 *
 * <p>Four numbers, because they fail independently and a single pass rate hides which one moved.
 *
 * <ul>
 *   <li><b>Selection accuracy</b> — of the scenarios that expected a tool, how many got it. This
 *       is the one everybody measures and it is the least interesting on its own.</li>
 *   <li><b>Refusal accuracy</b> — of the scenarios where declining was correct, how many declined.
 *       An agent can score 100 percent on selection and still be unshippable because it answers
 *       questions it has no business answering.</li>
 *   <li><b>Argument accuracy</b> — of the scenarios that pinned arguments, how many matched. The
 *       right tool with the wrong account is not a near miss.</li>
 *   <li><b>Over-calling</b> — average tool calls per scenario against the budget. This is the
 *       number that shows up on the invoice.</li>
 * </ul>
 *
 * <p>Each score is published back into the governance block of the descriptors it covers, which is
 * what makes the {@code evaluation} field in a descriptor an assertion somebody can check rather
 * than a number somebody typed.
 */
public record Report(String suite, HarnessRunner.Mode mode, int scenarios, int passed, int failed,
                     double selectionAccuracy, double refusalAccuracy, double argumentAccuracy,
                     double averageToolCalls, long totalMillis, Integer totalPromptTokens,
                     Integer totalCompletionTokens, List<Entry> entries) {

    public record Entry(String id, String utterance, boolean passed, List<String> path,
                        List<String> failures, long millis, String reply) {
    }

    public static Report of(String suite, HarnessRunner.Mode mode,
                            List<HarnessRunner.Result> results) {
        int passed = (int) results.stream().filter(HarnessRunner.Result::passed).count();
        long millis = results.stream().mapToLong(HarnessRunner.Result::millis).sum();
        return new Report(suite, mode, results.size(), passed, results.size() - passed,
                rate(results, scenario -> !scenario.expectTools().isEmpty(), "tool-not-called"),
                rate(results, scenario -> Boolean.TRUE.equals(scenario.expectRefusal()),
                        "not-refused"),
                rate(results, scenario -> !scenario.expectArguments().isEmpty(),
                        "wrong-arguments"),
                results.isEmpty() ? 0
                        : results.stream().mapToInt(result -> result.steps().size()).average()
                                .orElse(0),
                millis,
                sum(results, HarnessRunner.Result::promptTokens),
                sum(results, HarnessRunner.Result::completionTokens),
                results.stream().map(Report::entry).toList());
    }

    /** The share of in-scope scenarios with no failure of the given kind. */
    private static double rate(List<HarnessRunner.Result> results,
                               java.util.function.Predicate<Scenario> inScope, String kind) {
        List<HarnessRunner.Result> scoped = results.stream()
                .filter(result -> inScope.test(result.scenario())).toList();
        if (scoped.isEmpty()) {
            return Double.NaN;
        }
        long clean = scoped.stream().filter(result -> result.failures().stream()
                .noneMatch(failure -> failure.kind().equals(kind))).count();
        return Math.round(1000.0 * clean / scoped.size()) / 1000.0;
    }

    private static Integer sum(List<HarnessRunner.Result> results,
                               java.util.function.Function<HarnessRunner.Result, Integer> field) {
        int total = 0;
        boolean any = false;
        for (HarnessRunner.Result result : results) {
            Integer value = field.apply(result);
            if (value != null) {
                total += value;
                any = true;
            }
        }
        return any ? total : null;
    }

    private static Entry entry(HarnessRunner.Result result) {
        return new Entry(result.scenario().id(), result.scenario().utterance(), result.passed(),
                result.steps().stream().map(ToolTrace.Step::tool).toList(),
                result.failures().stream().map(Expectations.Failure::toString).toList(),
                result.millis(), result.reply());
    }

    // ------------------------------------------------------------------ rendering

    /** The console form: one line per scenario, then the scores. */
    public String text() {
        StringBuilder out = new StringBuilder();
        out.append("  ").append(suite).append("  (").append(mode).append(")\n\n");
        for (Entry entry : entries) {
            out.append(entry.passed() ? "  pass  " : "  FAIL  ")
                    .append(String.format("%-36s", entry.id()))
                    .append(entry.path().isEmpty() ? "(no tools)"
                            : String.join(" > ", entry.path()))
                    .append('\n');
            for (String failure : entry.failures()) {
                out.append("        ").append(failure).append('\n');
            }
        }
        out.append('\n');
        out.append("  ").append(passed).append('/').append(scenarios).append(" passed\n");
        out.append(score("  selection accuracy", selectionAccuracy));
        out.append(score("  refusal accuracy  ", refusalAccuracy));
        out.append(score("  argument accuracy ", argumentAccuracy));
        out.append(String.format("  tool calls per scenario  %.2f%n", averageToolCalls));
        if (totalPromptTokens != null) {
            out.append(String.format("  tokens  %d in, %d out%n", totalPromptTokens,
                    totalCompletionTokens == null ? 0 : totalCompletionTokens));
        }
        out.append(String.format("  elapsed  %.1fs%n", totalMillis / 1000.0));
        return out.toString();
    }

    private static String score(String label, double value) {
        return Double.isNaN(value) ? label + "  (no scenarios)\n"
                : String.format("%s  %.1f%%%n", label, value * 100);
    }

    public Map<String, Object> summary() {
        Map<String, Object> summary = new LinkedHashMap<>();
        summary.put("suite", suite);
        summary.put("mode", mode);
        summary.put("scenarios", scenarios);
        summary.put("passed", passed);
        summary.put("selectionAccuracy", selectionAccuracy);
        summary.put("refusalAccuracy", refusalAccuracy);
        summary.put("argumentAccuracy", argumentAccuracy);
        summary.put("averageToolCalls", averageToolCalls);
        return summary;
    }

    public void write(Path file) throws IOException {
        Files.createDirectories(file.getParent());
        Files.writeString(file, DescriptorCodec.mapper().writerWithDefaultPrettyPrinter()
                .writeValueAsString(this) + "\n");
    }

    public boolean clean() {
        return failed == 0;
    }
}
