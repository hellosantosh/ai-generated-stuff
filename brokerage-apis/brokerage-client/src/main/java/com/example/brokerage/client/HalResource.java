package com.example.brokerage.client;

import java.util.ArrayList;
import java.util.List;
import java.util.Optional;

import tools.jackson.databind.JsonNode;
import tools.jackson.databind.json.JsonMapper;

import org.springframework.hateoas.Link;
import org.springframework.http.HttpHeaders;

/**
 * A HAL representation as a client sees it: properties, links by relation, embedded
 * resources, and the response headers that came with it.
 */
public final class HalResource {

    private final JsonNode body;
    private final HttpHeaders headers;
    private final JsonMapper json;

    HalResource(JsonNode body, HttpHeaders headers, JsonMapper json) {
        this.body = body;
        this.headers = headers;
        this.json = json;
    }

    /** The first link with this relation, if the server offered one. */
    public Optional<Link> link(String rel) {
        return links(rel).stream().findFirst();
    }

    /** The link with this relation; its absence means the action is not available now. */
    public Link requiredLink(String rel) {
        return link(rel).orElseThrow(() -> new IllegalStateException(
                "The server offered no '" + rel + "' link, so that action is not available"));
    }

    /** Every link with this relation. HAL sends one link as an object, several as an array. */
    public List<Link> links(String rel) {
        JsonNode node = body.path("_links").path(rel);
        List<Link> links = new ArrayList<>();
        for (JsonNode each : node.isArray() ? node : List.of(node)) {
            if (each.hasNonNull("href")) {
                Link link = Link.of(each.get("href").asString(), rel);
                links.add(each.hasNonNull("name") ? link.withName(each.get("name").asString()) : link);
            }
        }
        return links;
    }

    /** Resources embedded under a relation, such as the items of a collection. */
    public List<HalResource> embedded(String rel) {
        List<HalResource> items = new ArrayList<>();
        body.path("_embedded").path(rel)
                .forEach(item -> items.add(new HalResource(item, new HttpHeaders(), json)));
        return items;
    }

    /** The properties as a Java type. Unknown properties are ignored: a tolerant reader. */
    public <T> T as(Class<T> type) {
        return json.treeToValue(body, type);
    }

    public String etag() {
        return headers.getETag();
    }

    public HttpHeaders headers() {
        return headers;
    }

    public JsonNode body() {
        return body;
    }
}
