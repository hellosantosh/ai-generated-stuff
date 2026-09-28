package com.example.brokerage.client;

import java.math.BigDecimal;
import java.net.URI;
import java.util.function.Consumer;

import com.fasterxml.jackson.annotation.JsonFormat;
import com.fasterxml.jackson.annotation.JsonInclude;
import tools.jackson.databind.DeserializationFeature;
import tools.jackson.databind.json.JsonMapper;

import org.springframework.hateoas.Link;
import org.springframework.http.HttpHeaders;
import org.springframework.http.HttpStatusCode;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.web.client.RestClient;

/**
 * HTTP for hypermedia: every request targets a link the server provided. There is no
 * method that takes a path to append to a base URL, by design.
 */
public class HypermediaClient {

    public static final MediaType HAL_JSON = MediaType.parseMediaType("application/hal+json");
    public static final MediaType MERGE_PATCH = MediaType.parseMediaType("application/merge-patch+json");

    private final RestClient http;
    private final JsonMapper json;

    public HypermediaClient(RestClient.Builder builder) {
        this.json = JsonMapper.builder()
                // Tolerant reader: new properties on the server must never break this client.
                .disable(DeserializationFeature.FAIL_ON_UNKNOWN_PROPERTIES)
                // The API's convention: decimals are strings, and absent fields are omitted.
                .withConfigOverride(BigDecimal.class,
                        o -> o.setFormat(JsonFormat.Value.forShape(JsonFormat.Shape.STRING)))
                .changeDefaultPropertyInclusion(i -> i.withValueInclusion(JsonInclude.Include.NON_NULL))
                .build();
        this.http = builder
                .defaultHeader(HttpHeaders.ACCEPT, HAL_JSON + ", application/problem+json")
                // Pin the API version on every request, so a new default can never change
                // what this client receives.
                .defaultHeader("API-Version", "2")
                .defaultStatusHandler(HttpStatusCode::isError, (request, response) -> {
                    throw ApiProblem.from(response, json);
                })
                .build();
    }

    public HalResource get(URI target) {
        return get(target, headers -> { });
    }

    public HalResource get(URI target, Consumer<HttpHeaders> headers) {
        return read(http.get().uri(target).headers(headers).retrieve().toEntity(String.class));
    }

    public HalResource post(Link target, Object body, Consumer<HttpHeaders> headers) {
        RestClient.RequestBodySpec request = http.post().uri(target.toUri()).headers(headers);
        if (body != null) {
            request.contentType(MediaType.APPLICATION_JSON).body(json.writeValueAsString(body));
        }
        return read(request.retrieve().toEntity(String.class));
    }

    /** A JSON merge patch, sent only if the resource still has the ETag the caller read. */
    public HalResource patch(Link target, Object changes, String ifMatch) {
        return read(http.patch().uri(target.toUri())
                .contentType(MERGE_PATCH)
                .header(HttpHeaders.IF_MATCH, ifMatch)
                .body(json.writeValueAsString(changes))
                .retrieve().toEntity(String.class));
    }

    private HalResource read(ResponseEntity<String> response) {
        String body = response.getBody();
        return new HalResource(json.readTree(body == null ? "{}" : body), response.getHeaders(), json);
    }
}
