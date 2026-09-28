package com.example.brokerage.api.platform;

import java.net.URI;

import org.springframework.http.HttpStatus;

/**
 * The catalog of error types this API can return. Each becomes the "type" URI of an
 * RFC 9457 problem document, so clients branch on a stable identifier, never on the
 * human-readable title or detail.
 */
public enum ProblemType {

    INVALID_REQUEST("invalid-request", HttpStatus.BAD_REQUEST, "The request is not valid"),
    UNAUTHORIZED("unauthorized", HttpStatus.UNAUTHORIZED, "Authentication is required"),
    INSUFFICIENT_SCOPE("insufficient-scope", HttpStatus.FORBIDDEN, "The access token lacks a required scope"),
    NOT_FOUND("not-found", HttpStatus.NOT_FOUND, "The resource does not exist"),
    IDEMPOTENCY_KEY_MISSING("idempotency-key-missing", HttpStatus.BAD_REQUEST,
            "An Idempotency-Key header is required"),
    IDEMPOTENCY_KEY_REUSED("idempotency-key-reused", HttpStatus.UNPROCESSABLE_CONTENT,
            "The Idempotency-Key was already used for a different request"),
    PRECONDITION_REQUIRED("precondition-required", HttpStatus.PRECONDITION_REQUIRED,
            "An If-Match header is required"),
    PRECONDITION_FAILED("precondition-failed", HttpStatus.PRECONDITION_FAILED, "The resource has changed"),
    ORDER_NOT_CANCELLABLE("order-not-cancellable", HttpStatus.CONFLICT,
            "The order can no longer be cancelled"),
    ORDER_NOT_REPLACEABLE("order-not-replaceable", HttpStatus.CONFLICT, "The order can no longer be changed"),
    INSUFFICIENT_BUYING_POWER("insufficient-buying-power", HttpStatus.UNPROCESSABLE_CONTENT,
            "The account does not have enough buying power"),
    INSUFFICIENT_POSITION("insufficient-position", HttpStatus.UNPROCESSABLE_CONTENT,
            "The account does not hold enough of the instrument"),
    INSTRUMENT_NOT_TRADABLE("instrument-not-tradable", HttpStatus.UNPROCESSABLE_CONTENT,
            "The instrument cannot be traded this way"),
    TRADING_LIMIT_EXCEEDED("trading-limit-exceeded", HttpStatus.FORBIDDEN,
            "The order exceeds the trading limit delegated to this client"),
    UNSUPPORTED_API_VERSION("unsupported-api-version", HttpStatus.BAD_REQUEST,
            "The requested API version is not supported"),
    RATE_LIMITED("rate-limited", HttpStatus.TOO_MANY_REQUESTS, "Too many requests");

    /** Problem type URIs resolve to human-readable documentation for each error. */
    public static final String BASE = "https://docs.brokerage.example/problems/";

    private final String slug;
    private final HttpStatus status;
    private final String title;

    ProblemType(String slug, HttpStatus status, String title) {
        this.slug = slug;
        this.status = status;
        this.title = title;
    }

    public URI uri() {
        return URI.create(BASE + slug);
    }

    public HttpStatus status() {
        return status;
    }

    public String title() {
        return title;
    }
}
