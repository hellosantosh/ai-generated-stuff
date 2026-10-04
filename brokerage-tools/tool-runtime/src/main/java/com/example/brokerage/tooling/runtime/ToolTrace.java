package com.example.brokerage.tooling.runtime;

import java.util.ArrayList;
import java.util.List;

import com.example.brokerage.tooling.contract.ErrorCode;

/**
 * What the model actually did, in order.
 *
 * <p>This is the single most useful artifact in the whole system and it is almost always missing.
 * When an agent gives a wrong answer, the question is never "what did the model say" — you have
 * that — it is "which tools did it call, with what arguments, in what order, and what came back".
 * Without a trace you are reduced to reading the final prose and guessing.
 *
 * <p>The trace is also what makes the test harness possible. Selection accuracy, argument
 * accuracy and trajectory assertions are all statements about this list, so the harness is a set
 * of expectations over a trace rather than a set of expectations over English.
 */
public class ToolTrace {

    /**
     * @param step      1-based position in the turn
     * @param arguments the raw argument JSON as the model sent it, before any normalization
     */
    public record Step(int step, String tool, String arguments, boolean ok, ErrorCode errorCode,
                       long latencyMs, String approvalReference) {
    }

    private final List<Step> steps = new ArrayList<>();

    public synchronized void record(InvocationPipeline.Outcome outcome, String arguments) {
        steps.add(new Step(steps.size() + 1, outcome.tool(), arguments, outcome.ok(),
                outcome.errorCode(), outcome.latencyMs(), outcome.approvalReference()));
    }

    public synchronized List<Step> steps() {
        return List.copyOf(steps);
    }

    /** The tools called, in order, with repeats kept. The spine of a trajectory assertion. */
    public synchronized List<String> path() {
        return steps.stream().map(Step::tool).toList();
    }

    public synchronized boolean called(String tool) {
        return steps.stream().anyMatch(step -> step.tool().equals(tool));
    }

    /** Whether {@code first} was called before {@code second} ever was. */
    public synchronized boolean calledBefore(String first, String second) {
        int a = indexOf(first);
        int b = indexOf(second);
        return a >= 0 && (b < 0 || a < b);
    }

    private int indexOf(String tool) {
        List<String> path = path();
        return path.indexOf(tool);
    }

    public synchronized int size() {
        return steps.size();
    }

    public synchronized void clear() {
        steps.clear();
    }
}
