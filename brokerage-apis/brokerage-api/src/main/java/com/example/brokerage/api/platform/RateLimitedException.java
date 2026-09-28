package com.example.brokerage.api.platform;

/** Raised when a client has used up its request quota for the current window. */
public class RateLimitedException extends ApiException {

    private final long retryAfterSeconds;

    public RateLimitedException(String policy, long retryAfterSeconds) {
        super(ProblemType.RATE_LIMITED, "The " + policy
                + " request quota for this client is used up; retry in " + retryAfterSeconds + " seconds");
        this.retryAfterSeconds = retryAfterSeconds;
    }

    public long retryAfterSeconds() {
        return retryAfterSeconds;
    }
}
