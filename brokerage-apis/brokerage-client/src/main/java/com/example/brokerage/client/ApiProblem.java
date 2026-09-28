package com.example.brokerage.client;

import java.io.IOException;
import java.util.Optional;

import tools.jackson.databind.JsonNode;
import tools.jackson.databind.json.JsonMapper;

import org.springframework.http.client.ClientHttpResponse;

/**
 * An RFC 9457 problem returned by the API, as an exception. Callers branch on {@link #type()},
 * the stable identifier, and may show {@link #detail()} to a person.
 */
public class ApiProblem extends RuntimeException {

    private final int status;
    private final String type;
    private final String title;
    private final JsonNode body;
    private final Optional<Long> retryAfterSeconds;

    ApiProblem(int status, String type, String title, String detail, JsonNode body,
            Optional<Long> retryAfter) {
        super(status + " " + title + (detail == null ? "" : ": " + detail));
        this.status = status;
        this.type = type;
        this.title = title;
        this.body = body;
        this.retryAfterSeconds = retryAfter;
    }

    static ApiProblem from(ClientHttpResponse response, JsonMapper json) throws IOException {
        JsonNode body = json.readTree(response.getBody());
        int status = response.getStatusCode().value();
        Optional<Long> retryAfter = Optional.ofNullable(response.getHeaders().getFirst("Retry-After"))
                .map(Long::valueOf);
        return new ApiProblem(status, body.path("type").asString("about:blank"),
                body.path("title").asString(response.getStatusText()), body.path("detail").asString(null),
                body, retryAfter);
    }

    /** Worth retrying later with the same request (and the same Idempotency-Key). */
    public boolean isTransient() {
        return status == 429 || status == 502 || status == 503 || status == 504;
    }

    /** The problem type's short name, such as "insufficient-buying-power". */
    public String typeName() {
        return type.substring(type.lastIndexOf('/') + 1);
    }

    public int status() { return status; }
    public String type() { return type; }
    public String title() { return title; }
    public String detail() { return body.path("detail").asString(null); }
    public JsonNode body() { return body; }
    public Optional<Long> retryAfterSeconds() { return retryAfterSeconds; }
}
