package com.example.brokerage;

import com.fasterxml.jackson.databind.DeserializationFeature;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.ObjectNode;

import java.io.IOException;
import java.math.BigDecimal;
import java.net.URI;
import java.net.URLEncoder;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.charset.StandardCharsets;
import java.time.Duration;
import java.util.ArrayList;
import java.util.Collection;
import java.util.Iterator;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.Set;
import java.util.UUID;
import java.util.concurrent.ThreadLocalRandom;
import java.util.stream.Collectors;
import java.util.stream.Stream;
import java.util.stream.StreamSupport;

/**
 * HTTP client for the Brokerage REST API.
 *
 * <p>Java 17+. Jackson is the only third-party dependency.
 *
 * <p>Three decisions worth calling out, because getting them wrong is expensive in
 * this domain:
 *
 * <ul>
 *   <li><b>Money is {@link BigDecimal}, never {@code double}.</b> The API sends
 *       prices and quantities as JSON strings precisely so they survive the round
 *       trip. {@code USE_BIG_DECIMAL_FOR_FLOATS} is enabled below so that even a
 *       field that arrives as a bare number cannot silently become a binary float.
 *   <li><b>Retries are scoped by method.</b> GET is safe and always retryable. POST
 *       is retryable only because every order carries an {@code Idempotency-Key} —
 *       without one, retrying a timed-out order is how a customer ends up with two
 *       positions instead of one.
 *   <li><b>A 401 triggers exactly one refresh-and-retry.</b> Looping on 401 turns a
 *       revoked grant into an infinite request storm against the auth server.
 * </ul>
 */
public final class BrokerageClient {

    public static final String API_BASE = "https://api.sandbox.brokerage.example.com/v1";
    private static final String USER_AGENT = "brokerage-java-example/1.0";

    /** Transient by definition: rate limiting and upstream unavailability. */
    private static final Set<Integer> RETRYABLE_STATUSES = Set.of(429, 502, 503, 504);

    public static final ObjectMapper MAPPER = new ObjectMapper()
            // Numbers become BigDecimal rather than double. Belt and braces in a
            // domain where a rounding error is a real financial defect.
            .enable(DeserializationFeature.USE_BIG_DECIMAL_FOR_FLOATS);

    private final String baseUrl;
    private final PkceAuth.TokenStore store;
    private final int maxRetries;
    private final HttpClient http;
    private PkceAuth.Tokens tokens;

    public BrokerageClient() {
        this(API_BASE, new PkceAuth.TokenStore(), 4);
    }

    public BrokerageClient(String baseUrl, PkceAuth.TokenStore store, int maxRetries) {
        this.baseUrl = baseUrl.replaceAll("/$", "");
        this.store = store;
        this.maxRetries = maxRetries;
        this.http = HttpClient.newBuilder()
                .connectTimeout(Duration.ofSeconds(15))
                .followRedirects(HttpClient.Redirect.NORMAL)
                .build();
    }

    // -----------------------------------------------------------------------
    // Errors
    // -----------------------------------------------------------------------

    /**
     * An RFC 9457 problem response.
     *
     * <p>Branch on {@link #type()}, never on {@code title} or {@code detail} — those
     * are human-facing prose and are reworded without notice. {@link #traceId()} is
     * what support will ask for, so log it on every failure.
     */
    public static final class ApiException extends RuntimeException {
        private final int status;
        private final JsonNode problem;

        public ApiException(int status, JsonNode problem) {
            super("HTTP " + status + " "
                    + problem.path("title").asText("Unknown error") + ": "
                    + problem.path("detail").asText("") + " (trace "
                    + problem.path("traceId").asText("n/a") + ")");
            this.status = status;
            this.problem = problem;
        }

        public int status() {
            return status;
        }

        public JsonNode problem() {
            return problem;
        }

        public String type() {
            return problem.path("type").asText("about:blank");
        }

        public String title() {
            return problem.path("title").asText("Unknown error");
        }

        public String traceId() {
            return problem.path("traceId").asText(null);
        }

        public List<JsonNode> fieldErrors() {
            List<JsonNode> out = new ArrayList<>();
            problem.path("errors").forEach(out::add);
            return out;
        }

        public boolean isRetryable() {
            return RETRYABLE_STATUSES.contains(status);
        }
    }

    // -----------------------------------------------------------------------
    // Request options
    // -----------------------------------------------------------------------

    /** Optional per-request settings. Build with the fluent {@code with*} methods. */
    public static final class Options {
        Map<String, List<String>> params = new LinkedHashMap<>();
        Map<String, String> headers = new LinkedHashMap<>();
        Object body;
        String idempotencyKey;
        String ifMatch;
        String contentType = "application/json";

        public static Options none() {
            return new Options();
        }

        /** Repeated values become repeated query parameters: {@code status=NEW&status=FILLED}. */
        public Options param(String key, Object value) {
            if (value == null) {
                return this;
            }
            List<String> values = params.computeIfAbsent(key, k -> new ArrayList<>());
            if (value instanceof Collection<?> collection) {
                collection.forEach(v -> values.add(String.valueOf(v)));
            } else {
                values.add(String.valueOf(value));
            }
            return this;
        }

        public Options header(String key, String value) {
            if (value != null) {
                headers.put(key, value);
            }
            return this;
        }

        public Options body(Object value) {
            this.body = value;
            return this;
        }

        public Options idempotencyKey(String key) {
            this.idempotencyKey = key;
            return this;
        }

        public Options ifMatch(String etag) {
            this.ifMatch = etag;
            return this;
        }

        public Options contentType(String value) {
            this.contentType = value;
            return this;
        }
    }

    /** A response body paired with its headers, for ETag-driven flows. */
    public record Response(JsonNode body, HttpHeadersView headers, int status) {
        public boolean notModified() {
            return status == 304;
        }
    }

    /** Thin read-only view over response headers. */
    public record HttpHeadersView(Map<String, List<String>> raw) {
        public String first(String name) {
            return raw.entrySet().stream()
                    .filter(e -> e.getKey().equalsIgnoreCase(name))
                    .flatMap(e -> e.getValue().stream())
                    .findFirst()
                    .orElse(null);
        }

        public String etag() {
            return first("ETag");
        }
    }

    // -----------------------------------------------------------------------
    // Auth
    // -----------------------------------------------------------------------

    private synchronized String accessToken() {
        if (tokens == null || tokens.expired()) {
            tokens = PkceAuth.getValidTokens(store);
        }
        return tokens.accessToken();
    }

    /**
     * Refresh after a 401 even if the token looked locally valid — it can be revoked
     * server-side (the customer disconnects the app, or security invalidates a
     * family) long before it expires.
     */
    private synchronized void forceRefresh() {
        if (tokens != null && tokens.refreshToken() != null) {
            try {
                tokens = PkceAuth.refreshTokens(tokens);
                store.save(tokens);
                return;
            } catch (PkceAuth.OAuthException ignored) {
                // Fall through to full re-authorization.
            }
        }
        tokens = PkceAuth.getValidTokens(store);
    }

    // -----------------------------------------------------------------------
    // Transport
    // -----------------------------------------------------------------------

    /**
     * Issue one request.
     *
     * <p>{@code path} may be relative to the API base or an absolute URL, so a
     * HATEOAS link can be passed straight through without any parsing.
     */
    public Response request(String method, String path, Options options) {
        Options opts = options == null ? Options.none() : options;

        String url = path.startsWith("http") ? path : baseUrl + path;
        if (!opts.params.isEmpty()) {
            String query = opts.params.entrySet().stream()
                    .flatMap(e -> e.getValue().stream()
                            .map(v -> URLEncoder.encode(e.getKey(), StandardCharsets.UTF_8)
                                    + "=" + URLEncoder.encode(v, StandardCharsets.UTF_8)))
                    .collect(Collectors.joining("&"));
            if (!query.isEmpty()) {
                url += (url.contains("?") ? "&" : "?") + query;
            }
        }

        String payload = null;
        if (opts.body != null) {
            try {
                payload = MAPPER.writeValueAsString(opts.body);
            } catch (IOException e) {
                throw new IllegalArgumentException("Could not serialize request body", e);
            }
        }

        int attempt = 0;
        boolean refreshed = false;

        while (true) {
            attempt++;

            HttpRequest.Builder builder = HttpRequest.newBuilder(URI.create(url))
                    .header("Authorization", "Bearer " + accessToken())
                    .header("Accept", "application/json, application/problem+json")
                    .header("User-Agent", USER_AGENT)
                    .timeout(Duration.ofSeconds(30));

            opts.headers.forEach(builder::header);
            if (opts.idempotencyKey != null) {
                builder.header("Idempotency-Key", opts.idempotencyKey);
            }
            if (opts.ifMatch != null) {
                builder.header("If-Match", opts.ifMatch);
            }
            if (payload != null) {
                builder.header("Content-Type", opts.contentType);
                builder.method(method, HttpRequest.BodyPublishers.ofString(payload));
            } else {
                builder.method(method, HttpRequest.BodyPublishers.noBody());
            }

            HttpResponse<String> response;
            try {
                response = http.send(builder.build(), HttpResponse.BodyHandlers.ofString());
            } catch (IOException e) {
                // A transport failure is the dangerous case for writes: the order
                // may well have been accepted. Only retry when an idempotency key
                // makes that safe.
                boolean safe = method.equals("GET") || method.equals("HEAD") || opts.idempotencyKey != null;
                if (safe && attempt <= maxRetries) {
                    sleep(backoffMillis(attempt));
                    continue;
                }
                ObjectNode problem = MAPPER.createObjectNode();
                problem.put("title", "Network error");
                problem.put("detail", e.toString());
                throw new ApiException(0, problem);
            } catch (InterruptedException e) {
                Thread.currentThread().interrupt();
                throw new IllegalStateException("Interrupted", e);
            }

            HttpHeadersView headers = new HttpHeadersView(response.headers().map());
            int status = response.statusCode();

            if (status == 304) {
                return new Response(null, headers, 304);
            }

            if (status < 400) {
                JsonNode body = null;
                if (response.body() != null && !response.body().isBlank()) {
                    try {
                        body = MAPPER.readTree(response.body());
                    } catch (IOException e) {
                        throw new IllegalStateException("Malformed JSON response", e);
                    }
                }
                return new Response(body, headers, status);
            }

            // One refresh-and-retry on 401, then give up.
            if (status == 401 && !refreshed) {
                refreshed = true;
                forceRefresh();
                continue;
            }

            JsonNode problem;
            try {
                problem = MAPPER.readTree(response.body());
            } catch (Exception e) {
                ObjectNode fallback = MAPPER.createObjectNode();
                fallback.put("title", "Non-JSON error body");
                fallback.put("detail", response.body() == null
                        ? "" : response.body().substring(0, Math.min(400, response.body().length())));
                problem = fallback;
            }

            ApiException error = new ApiException(status, problem);
            boolean retryable = attempt <= maxRetries
                    && error.isRetryable()
                    && (method.equals("GET") || method.equals("HEAD") || method.equals("DELETE")
                            || opts.idempotencyKey != null);

            if (!retryable) {
                throw error;
            }

            String retryAfter = headers.first("Retry-After");
            sleep(retryAfter != null
                    ? Long.parseLong(retryAfter.trim()) * 1000 + ThreadLocalRandom.current().nextLong(1000)
                    : backoffMillis(attempt));
        }
    }

    /**
     * Exponential backoff with full jitter.
     *
     * <p>Full jitter (random between 0 and the cap) rather than fixed backoff: when
     * a venue hiccups, every client retries at once, and synchronized retries are
     * how a brief blip becomes an outage.
     */
    private static long backoffMillis(int attempt) {
        long cap = Math.min((long) Math.pow(2, attempt) * 500L, 20_000L);
        return ThreadLocalRandom.current().nextLong(cap + 1);
    }

    private static void sleep(long millis) {
        try {
            Thread.sleep(millis);
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
        }
    }

    // -----------------------------------------------------------------------
    // Verbs
    // -----------------------------------------------------------------------

    public JsonNode get(String path) {
        return get(path, Options.none());
    }

    public JsonNode get(String path, Options options) {
        return request("GET", path, options).body();
    }

    public Response getWithHeaders(String path, Options options) {
        return request("GET", path, options);
    }

    public JsonNode post(String path, Object body, Options options) {
        return request("POST", path, (options == null ? Options.none() : options).body(body)).body();
    }

    public JsonNode patch(String path, Object body, Options options) {
        Options opts = (options == null ? Options.none() : options)
                .body(body)
                .contentType("application/merge-patch+json");
        return request("PATCH", path, opts).body();
    }

    public JsonNode delete(String path, Options options) {
        return request("DELETE", path, options).body();
    }

    // -----------------------------------------------------------------------
    // Hypermedia
    // -----------------------------------------------------------------------

    /**
     * Read one link relation, empty if the action is unavailable.
     *
     * <p>This is the intended way to navigate. A missing {@code cancel} link means
     * the order is not cancellable right now — more reliable than inspecting
     * {@code status}, because new statuses can be added without a version bump but
     * the link contract holds.
     */
    public static Optional<String> link(JsonNode resource, String rel) {
        JsonNode href = resource.path("_links").path(rel).path("href");
        return href.isTextual() ? Optional.of(href.asText()) : Optional.empty();
    }

    public static boolean can(JsonNode resource, String rel) {
        return resource.path("_links").has(rel);
    }

    public String linkOrThrow(JsonNode resource, String rel) {
        return link(resource, rel).orElseThrow(() -> new IllegalStateException(
                "No '" + rel + "' link on this resource; the action is unavailable."));
    }

    public JsonNode follow(JsonNode resource, String rel) {
        return get(linkOrThrow(resource, rel));
    }

    /**
     * Stream every item across pages by following {@code next} links.
     *
     * <p>Follows the server's {@code next} href verbatim rather than constructing
     * cursors — the cursor encoding is explicitly not part of the contract.
     */
    public Stream<JsonNode> paginate(String path, Options options) {
        Iterator<JsonNode> iterator = new Iterator<>() {
            private JsonNode page = get(path, options);
            private Iterator<JsonNode> items = page.path("items").elements();

            @Override
            public boolean hasNext() {
                while (!items.hasNext()) {
                    Optional<String> next = link(page, "next");
                    if (next.isEmpty()) {
                        return false;
                    }
                    page = get(next.get());
                    items = page.path("items").elements();
                }
                return true;
            }

            @Override
            public JsonNode next() {
                return items.next();
            }
        };

        return StreamSupport.stream(
                java.util.Spliterators.spliteratorUnknownSize(iterator, java.util.Spliterator.ORDERED),
                false);
    }

    public List<JsonNode> collect(String path, Options options) {
        return paginate(path, options).collect(Collectors.toList());
    }

    // -----------------------------------------------------------------------
    // Helpers
    // -----------------------------------------------------------------------

    /**
     * One key per order <em>intent</em>.
     *
     * <p>Generate it before the first attempt and reuse it across every retry of
     * that same intent. Generating a fresh key inside a retry loop defeats the
     * entire mechanism.
     */
    public static String newIdempotencyKey() {
        return UUID.randomUUID().toString();
    }

    /** Read a decimal field as {@link BigDecimal}, defaulting to zero when absent. */
    public static BigDecimal decimal(JsonNode node, String field) {
        JsonNode value = node.path(field);
        return value.isMissingNode() || value.isNull() ? BigDecimal.ZERO : new BigDecimal(value.asText());
    }

    /** Read a nested decimal, e.g. {@code decimal(balances, "cash", "settled")}. */
    public static BigDecimal decimal(JsonNode node, String... path) {
        JsonNode current = node;
        for (String segment : path) {
            current = current.path(segment);
        }
        return current.isMissingNode() || current.isNull()
                ? BigDecimal.ZERO
                : new BigDecimal(current.asText());
    }
}
