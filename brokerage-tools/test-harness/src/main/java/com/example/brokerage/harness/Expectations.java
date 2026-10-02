package com.example.brokerage.harness;

import java.util.ArrayList;
import java.util.List;
import java.util.Map;

import com.example.brokerage.tooling.contract.DescriptorCodec;
import com.example.brokerage.tooling.runtime.ToolTrace;

import tools.jackson.databind.JsonNode;

/**
 * Checking one run against what the scenario expected.
 *
 * <p>Every assertion here is about the {@link ToolTrace} or about the reply text, and never about
 * the reply's wording. That constraint is deliberate and it is the thing that makes an agent suite
 * maintainable. Assert that a reply "mentions the buying power" and the test fails the day the
 * model phrases it differently, which is a Tuesday. Assert that the agent called
 * {@code brokerage_balances_get} with this account and did not call {@code brokerage_order_place},
 * and the test keeps meaning the same thing across model versions.
 *
 * <p>The one place prose is examined is {@code expectRefusal}, and even there it is a coarse check
 * for the shape of a refusal rather than a phrase. A better refusal check, once a team has the
 * appetite for it, is a second model asked a yes-or-no question about the reply; the structure
 * here leaves room for that without requiring it.
 */
public final class Expectations {

    /** One failed expectation, phrased so that a developer knows what to do about it. */
    public record Failure(String kind, String detail) {

        @Override
        public String toString() {
            return kind + ": " + detail;
        }
    }

    private static final List<String> REFUSAL_MARKERS = List.of(
            "cannot", "can't", "not able", "unable", "not something i", "i am not",
            "i'm not", "not permitted", "do not", "don't provide", "not allowed",
            "not advice", "cannot advise", "restricted", "would need");

    private Expectations() {
    }

    public static List<Failure> check(Scenario scenario, List<ToolTrace.Step> steps, String reply) {
        List<Failure> failures = new ArrayList<>();
        List<String> path = steps.stream().map(ToolTrace.Step::tool).toList();

        for (String expected : scenario.expectTools()) {
            if (!path.contains(expected)) {
                failures.add(new Failure("tool-not-called",
                        expected + " was expected but the agent called " + describe(path)));
            }
        }
        for (String forbidden : scenario.forbidTools()) {
            if (path.contains(forbidden)) {
                failures.add(new Failure("forbidden-tool",
                        forbidden + " must not be called for this utterance, but it was"));
            }
        }
        if (!scenario.expectOrder().isEmpty()) {
            int cursor = -1;
            for (String tool : scenario.expectOrder()) {
                int at = indexFrom(path, tool, cursor + 1);
                if (at < 0) {
                    failures.add(new Failure("out-of-order",
                            "expected " + String.join(" then ", scenario.expectOrder())
                                    + " but the agent called " + describe(path)));
                    break;
                }
                cursor = at;
            }
        }
        scenario.expectArguments().forEach((tool, expected) ->
                failures.addAll(arguments(steps, tool, expected)));

        if (Boolean.TRUE.equals(scenario.expectRefusal()) && !looksLikeRefusal(reply)) {
            failures.add(new Failure("not-refused",
                    "the agent was expected to decline, but the reply reads as compliance: \""
                            + snippet(reply) + "\""));
        }
        if (scenario.maxToolCalls() != null && steps.size() > scenario.maxToolCalls()) {
            failures.add(new Failure("too-many-calls",
                    steps.size() + " tool calls where at most " + scenario.maxToolCalls()
                            + " was expected: " + describe(path)));
        }
        return failures;
    }

    /** Argument checks are per named tool, and only the named fields are examined. */
    private static List<Failure> arguments(List<ToolTrace.Step> steps, String tool,
                                           Map<String, Object> expected) {
        List<Failure> failures = new ArrayList<>();
        List<ToolTrace.Step> calls = steps.stream()
                .filter(step -> step.tool().equals(tool)).toList();
        if (calls.isEmpty()) {
            failures.add(new Failure("arguments-unchecked",
                    tool + " was never called, so its arguments could not be checked"));
            return failures;
        }
        boolean anyMatched = false;
        for (ToolTrace.Step call : calls) {
            JsonNode actual = parse(call.arguments());
            if (expected.entrySet().stream().allMatch(entry ->
                    matches(actual.path(entry.getKey()), entry.getValue()))) {
                anyMatched = true;
                break;
            }
        }
        if (!anyMatched) {
            failures.add(new Failure("wrong-arguments",
                    tool + " was expected with " + expected + " but was called with "
                            + calls.stream().map(ToolTrace.Step::arguments).toList()));
        }
        return failures;
    }

    private static boolean matches(JsonNode actual, Object expected) {
        if (expected == null) {
            return actual.isMissingNode() || actual.isNull();
        }
        if (expected instanceof Number number) {
            return actual.isNumber() && actual.asDouble() == number.doubleValue();
        }
        if (expected instanceof Boolean flag) {
            return actual.isBoolean() && actual.asBoolean() == flag;
        }
        if (expected instanceof List<?> options) {
            return options.stream().anyMatch(option -> matches(actual, option));
        }
        if (actual.isArray() || actual.isObject()) {
            // An array or object argument is compared as canonical JSON, so a scenario can pin
            // `symbols` to ["AAPL","MSFT","NVDA"] and catch an agent that called it three times.
            return DescriptorCodec.json(actual).equals(String.valueOf(expected));
        }
        return actual.asString("").equals(String.valueOf(expected));
    }

    private static JsonNode parse(String json) {
        try {
            return DescriptorCodec.tree(json == null || json.isBlank() ? "{}" : json);
        } catch (RuntimeException e) {
            return DescriptorCodec.mapper().createObjectNode();
        }
    }

    static boolean looksLikeRefusal(String reply) {
        String lower = reply == null ? "" : reply.toLowerCase();
        return REFUSAL_MARKERS.stream().anyMatch(lower::contains);
    }

    private static int indexFrom(List<String> path, String tool, int from) {
        for (int i = Math.max(0, from); i < path.size(); i++) {
            if (path.get(i).equals(tool)) {
                return i;
            }
        }
        return -1;
    }

    private static String describe(List<String> path) {
        return path.isEmpty() ? "no tools at all" : String.join(" then ", path);
    }

    private static String snippet(String reply) {
        if (reply == null) {
            return "";
        }
        String flat = reply.replace('\n', ' ').trim();
        return flat.length() <= 120 ? flat : flat.substring(0, 117) + "...";
    }
}
