package com.example.brokerage.tooling.contract;

/**
 * The closed error taxonomy: fifteen codes, and a tool may return no others.
 *
 * <p>A closed taxonomy exists so that the agent can be taught once what to do about each code
 * instead of guessing from prose. Two properties are attached to every code because they are the
 * only two questions the model actually needs answered: may I try this again, and is the fix
 * mine to make?
 *
 * <p>{@link #retryable} says whether an identical retry could plausibly succeed. {@link #callerCanFix}
 * says whether the caller has anything to change — a bad argument it can correct, a token it can
 * refresh — as opposed to a condition it can only report. When both are false the agent's only
 * correct move is to tell the customer and stop; an agent that loops on {@code NOT_ENTITLED} is a
 * defect in the toolset, not in the model.
 *
 * <p>Each code also carries the sentence the generated briefing shows the model. Keeping that
 * sentence here, next to the flags it has to agree with, is the difference between a prompt that
 * matches the taxonomy and a prompt that used to.
 */
public enum ErrorCode {

    /** Arguments failed the input schema, or failed a rule the schema cannot express. */
    INVALID_ARGUMENT(false, true,
            "Your arguments were wrong. The message names the field. Fix it and call the same tool "
                    + "again — a different tool will have the same problem."),

    /** The name is not in this principal's catalog. Never a hint to try a near miss. */
    UNKNOWN_TOOL(false, true,
            "That tool does not exist in this session. Use one of the tools you were given, "
                    + "spelled exactly as given. Do not guess at a similar name."),

    /** The named entity does not exist, or exists outside what this principal may see. */
    NOT_FOUND(false, true,
            "The thing you named is not there. Do not try variations of the identifier. Use the "
                    + "list tool the remediation points at, and tell the customer if it is "
                    + "genuinely absent."),

    /** The request is well formed but the principal lacks the entitlement. Do not retry. */
    NOT_ENTITLED(false, false,
            "This session is not permitted to do that. Say so plainly and stop. Do not look for "
                    + "another tool that might not check."),

    /** A human has to agree first. The accompanying approval reference says who and how. */
    APPROVAL_REQUIRED(false, true,
            "A human has to agree first. Show the customer what the message quotes, wait for a "
                    + "clear yes, then call the same tool again with identical arguments. If they "
                    + "say no, stop."),

    /** The confirmation token has expired or was already spent. Preview again. */
    CONFIRMATION_EXPIRED(false, true,
            "The confirmation is no longer good. Preview again, read the customer the fresh "
                    + "figures, and proceed only if they agree again."),

    /** The world is not in the state the call needs — market closed, position already flat. */
    PRECONDITION_FAILED(false, false,
            "The world is not in a state where this can happen. Tell the customer the reason in "
                    + "the message. Nothing you call will change it."),

    /** Someone else changed the entity first. Re-read it before deciding again. */
    CONFLICT(false, true,
            "Something changed underneath you. Read the entity again, tell the customer what it "
                    + "says now, and ask before acting on the new state."),

    /** Too many calls from this principal. The retryAfterSeconds field says how long to wait. */
    RATE_LIMITED(true, false,
            "You have called this too often. Wait the number of seconds in retryAfterSeconds, "
                    + "tell the customer there is a short delay, and do not call anything else in "
                    + "the meantime."),

    /** A budget is exhausted for the period. Waiting will not help inside this session. */
    QUOTA_EXCEEDED(false, false,
            "A budget is used up for today. Waiting will not help in this conversation. Tell the "
                    + "customer and offer what you can still see."),

    /** A system the tool depends on is down. Retrying later may work; retrying now will not. */
    UPSTREAM_UNAVAILABLE(true, false,
            "A system behind this tool is down. Tell the customer the feature is unavailable right "
                    + "now. Do not retry in this conversation."),

    /** A dependency did not answer in time. The call may or may not have taken effect. */
    UPSTREAM_TIMEOUT(true, false,
            "It is not known whether that took effect. Never say it did or did not. Read the "
                    + "entity back with the matching get tool and report what you find."),

    /** The request matches more than one entity. The candidates field lists them; ask. */
    AMBIGUOUS_REQUEST(false, true,
            "More than one thing matches. Read the candidates to the customer and ask which they "
                    + "mean. Do not pick for them."),

    /** A legitimate request this tool does not implement. Another tool may; the catalog says. */
    UNSUPPORTED(false, false,
            "This tool does not do that. If the message names another tool, use it. Otherwise tell "
                    + "the customer it is not something you can do."),

    /** Something broke that the caller cannot act on. Report the requestId and stop. */
    INTERNAL_ERROR(true, false,
            "Something broke. Tell the customer the request failed, give them the requestId from "
                    + "the envelope, and stop.");

    private final boolean retryable;
    private final boolean callerCanFix;
    private final String guidance;

    ErrorCode(boolean retryable, boolean callerCanFix, String guidance) {
        this.retryable = retryable;
        this.callerCanFix = callerCanFix;
        this.guidance = guidance;
    }

    /** The sentence the generated toolset briefing shows the model for this code. */
    public String guidance() {
        return guidance;
    }

    /** Whether an identical retry could plausibly succeed. */
    public boolean retryable() {
        return retryable;
    }

    /** Whether the caller has something to change before trying again. */
    public boolean callerCanFix() {
        return callerCanFix;
    }

    /** True when the agent's only correct move is to report the condition and stop. */
    public boolean terminal() {
        return !retryable && !callerCanFix;
    }
}
