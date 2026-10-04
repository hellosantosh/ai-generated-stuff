package com.example.brokerage.tooling.contract;

/**
 * One conformance finding. The rule identifier is the point: a review comment that says "this
 * description is a bit thin" starts an argument, and one that says "TS-DSC-04: the description
 * never says when not to use the tool" starts a fix.
 */
public record Finding(String rule, Severity severity, String tool, String where, String message) {

    public enum Severity {
        /** A MUST in the specification. The build fails. */
        ERROR,
        /** A SHOULD. The build passes and the review asks about it. */
        WARNING,
        /** Worth knowing when reading a catalog, never a gate. */
        INFO
    }

    public static Finding error(String rule, String tool, String message) {
        return new Finding(rule, Severity.ERROR, tool, null, message);
    }

    public static Finding error(String rule, String tool, String where, String message) {
        return new Finding(rule, Severity.ERROR, tool, where, message);
    }

    public static Finding warning(String rule, String tool, String message) {
        return new Finding(rule, Severity.WARNING, tool, null, message);
    }

    public static Finding warning(String rule, String tool, String where, String message) {
        return new Finding(rule, Severity.WARNING, tool, where, message);
    }

    @Override
    public String toString() {
        String place = where == null || where.isEmpty() ? "" : " at " + where;
        return "%-6s %-10s %s%s: %s".formatted(severity, rule, tool, place, message);
    }
}
