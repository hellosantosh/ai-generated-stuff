package com.example.brokerage;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.ObjectNode;

import java.awt.Desktop;
import java.io.IOException;
import java.io.OutputStream;
import java.net.InetSocketAddress;
import java.net.URI;
import java.net.URLDecoder;
import java.net.URLEncoder;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardCopyOption;
import java.nio.file.attribute.PosixFilePermission;
import java.nio.file.attribute.PosixFilePermissions;
import java.security.MessageDigest;
import java.security.SecureRandom;
import java.time.Duration;
import java.time.Instant;
import java.util.Base64;
import java.util.HashMap;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.TimeUnit;
import java.util.stream.Collectors;

import com.sun.net.httpserver.HttpServer;

/**
 * OAuth 2.1 authorization code flow with PKCE, for the Brokerage REST API.
 *
 * <p>Java 17+. The only third-party dependency is Jackson for JSON; the HTTP
 * client, crypto, and callback server are all JDK built-ins. No SDK is
 * published for this API.
 *
 * <p>What OAuth 2.1 changes, and why this class looks the way it does:
 *
 * <ul>
 *   <li>PKCE is mandatory for every client, not just public ones. The {@code state}
 *       parameter still defends against CSRF on the redirect; {@code code_verifier}
 *       defends against interception of the authorization code itself. They solve
 *       different problems, so we send both.
 *   <li>The implicit and resource-owner-password grants are gone. There is no
 *       supported way to exchange a username and password for a token, so never ask
 *       a customer for their brokerage credentials.
 *   <li>Refresh tokens rotate. Every refresh returns a new refresh token and
 *       invalidates the old one. Persist it atomically — see {@link TokenStore#save}.
 *   <li>Replaying a rotated refresh token is treated as theft and revokes the whole
 *       token family. That bounds the damage from a stolen token to a single use.
 * </ul>
 */
public final class PkceAuth {

    public static final String AUTH_BASE =
            envOrDefault("BROKERAGE_AUTH_BASE", "https://auth.brokerage.example.com");
    public static final String CLIENT_ID =
            envOrDefault("BROKERAGE_CLIENT_ID", "your-client-id");
    public static final String REDIRECT_URI =
            envOrDefault("BROKERAGE_REDIRECT_URI", "http://127.0.0.1:8723/callback");

    public static final List<String> SCOPES = List.of(
            "accounts:read",
            "positions:read",
            "orders:read",
            "orders:write",
            "transactions:read",
            "instruments:read",
            "offline_access");

    private static final ObjectMapper MAPPER = new ObjectMapper();
    private static final SecureRandom RANDOM = new SecureRandom();
    private static final HttpClient HTTP = HttpClient.newBuilder()
            .connectTimeout(Duration.ofSeconds(15))
            // Never follow redirects on the token endpoint — a redirect there is
            // either a misconfiguration or an attempt to harvest the code.
            .followRedirects(HttpClient.Redirect.NEVER)
            .build();

    private PkceAuth() {}

    // -----------------------------------------------------------------------
    // Errors
    // -----------------------------------------------------------------------

    public static final class OAuthException extends RuntimeException {
        private final String code;
        private final String description;

        public OAuthException(String code, String description) {
            super(code + ": " + description);
            this.code = code;
            this.description = description;
        }

        public String code() {
            return code;
        }

        public String description() {
            return description;
        }
    }

    // -----------------------------------------------------------------------
    // Tokens
    // -----------------------------------------------------------------------

    /**
     * @param expiresAt absolute instant, not a duration — durations go stale the
     *     moment you serialize them.
     */
    public record Tokens(
            String accessToken, String refreshToken, Instant expiresAt, String scope, String tokenType) {

        /**
         * Refresh 60s early. A token that passes this check locally can still be
         * rejected by the server — clocks drift, and tokens can be revoked at any
         * time — so the client must also handle a 401 on any request.
         */
        public boolean expired() {
            return Instant.now().isAfter(expiresAt.minusSeconds(60));
        }

        static Tokens from(JsonNode payload, Tokens previous) {
            // A refresh response that omits refresh_token means the old one is
            // still current. Keep it rather than dropping to null.
            String refresh = payload.hasNonNull("refresh_token")
                    ? payload.get("refresh_token").asText()
                    : (previous != null ? previous.refreshToken() : null);

            long expiresIn = payload.has("expires_in") ? payload.get("expires_in").asLong() : 900L;

            return new Tokens(
                    payload.get("access_token").asText(),
                    refresh,
                    Instant.now().plusSeconds(expiresIn),
                    payload.path("scope").asText(""),
                    payload.path("token_type").asText("Bearer"));
        }
    }

    /**
     * File-backed token storage.
     *
     * <p>Two details that matter more than they look:
     *
     * <ul>
     *   <li>Permissions 0600. A refresh token is a 90-day bearer credential for
     *       someone's brokerage account; it must not be world-readable.
     *   <li>Atomic replace. Rotation means the old refresh token dies the moment the
     *       new one is issued, so a partial write during rotation leaves the user
     *       with no usable token at all.
     * </ul>
     *
     * <p>For anything multi-user, use a real secret manager instead.
     */
    public static final class TokenStore {
        private final Path path;

        public TokenStore() {
            this(Path.of(".brokerage-tokens.json"));
        }

        public TokenStore(Path path) {
            this.path = path;
        }

        public Tokens load() {
            try {
                if (!Files.exists(path)) {
                    return null;
                }
                JsonNode node = MAPPER.readTree(Files.readString(path));
                return new Tokens(
                        node.get("accessToken").asText(),
                        node.hasNonNull("refreshToken") ? node.get("refreshToken").asText() : null,
                        Instant.parse(node.get("expiresAt").asText()),
                        node.path("scope").asText(""),
                        node.path("tokenType").asText("Bearer"));
            } catch (Exception e) {
                return null;
            }
        }

        public void save(Tokens tokens) {
            try {
                ObjectNode node = MAPPER.createObjectNode();
                node.put("accessToken", tokens.accessToken());
                node.put("refreshToken", tokens.refreshToken());
                node.put("expiresAt", tokens.expiresAt().toString());
                node.put("scope", tokens.scope());
                node.put("tokenType", tokens.tokenType());

                Path tmp = path.resolveSibling(path.getFileName() + "." + ProcessHandle.current().pid() + ".tmp");
                Files.writeString(tmp, MAPPER.writeValueAsString(node));
                trySetOwnerOnly(tmp);
                Files.move(tmp, path, StandardCopyOption.REPLACE_EXISTING, StandardCopyOption.ATOMIC_MOVE);
            } catch (IOException e) {
                throw new UncheckedIoException(e);
            }
        }

        private static void trySetOwnerOnly(Path file) {
            try {
                Set<PosixFilePermission> ownerOnly = PosixFilePermissions.fromString("rw-------");
                Files.setPosixFilePermissions(file, ownerOnly);
            } catch (UnsupportedOperationException | IOException ignored) {
                // Windows and some filesystems have no POSIX permissions. The
                // token is still written; secure the directory instead.
            }
        }
    }

    static final class UncheckedIoException extends RuntimeException {
        UncheckedIoException(Throwable cause) {
            super(cause);
        }
    }

    // -----------------------------------------------------------------------
    // PKCE
    // -----------------------------------------------------------------------

    /** Base64url without padding, per RFC 7636. */
    private static String b64url(byte[] raw) {
        return Base64.getUrlEncoder().withoutPadding().encodeToString(raw);
    }

    public record PkcePair(String codeVerifier, String codeChallenge) {}

    /**
     * Generate a PKCE verifier/challenge pair using the S256 method.
     *
     * <p>32 random bytes yields a 43-character verifier, the shortest RFC 7636
     * permits. {@link SecureRandom}, never {@code Random}: the verifier is the only
     * thing stopping an attacker who intercepts the authorization code from
     * redeeming it, so it has to be cryptographically unpredictable.
     *
     * <p>The {@code plain} challenge method exists in the RFC but is not accepted by
     * this authorization server, and should not be used anywhere.
     */
    public static PkcePair generatePkcePair() {
        byte[] entropy = new byte[32];
        RANDOM.nextBytes(entropy);
        String verifier = b64url(entropy);

        try {
            byte[] digest = MessageDigest.getInstance("SHA-256")
                    .digest(verifier.getBytes(StandardCharsets.US_ASCII));
            return new PkcePair(verifier, b64url(digest));
        } catch (Exception e) {
            throw new IllegalStateException("SHA-256 unavailable", e);
        }
    }

    public static String buildAuthorizationUrl(String codeChallenge, String state) {
        Map<String, String> params = new LinkedHashMap<>();
        params.put("response_type", "code");
        params.put("client_id", CLIENT_ID);
        params.put("redirect_uri", REDIRECT_URI);
        params.put("scope", String.join(" ", SCOPES));
        params.put("state", state);
        params.put("code_challenge", codeChallenge);
        params.put("code_challenge_method", "S256");
        return AUTH_BASE + "/oauth2/authorize?" + formEncode(params);
    }

    // -----------------------------------------------------------------------
    // Token endpoint
    // -----------------------------------------------------------------------

    private static String formEncode(Map<String, String> form) {
        return form.entrySet().stream()
                .map(e -> URLEncoder.encode(e.getKey(), StandardCharsets.UTF_8)
                        + "="
                        + URLEncoder.encode(e.getValue(), StandardCharsets.UTF_8))
                .collect(Collectors.joining("&"));
    }

    private static JsonNode postForm(String url, Map<String, String> form) {
        HttpRequest request = HttpRequest.newBuilder(URI.create(url))
                .header("Content-Type", "application/x-www-form-urlencoded")
                .header("Accept", "application/json")
                .timeout(Duration.ofSeconds(30))
                .POST(HttpRequest.BodyPublishers.ofString(formEncode(form)))
                .build();

        try {
            HttpResponse<String> response = HTTP.send(request, HttpResponse.BodyHandlers.ofString());
            JsonNode payload;
            try {
                payload = MAPPER.readTree(response.body());
            } catch (IOException e) {
                throw new OAuthException(
                        "http_error", response.statusCode() + ": " + truncate(response.body()));
            }

            if (response.statusCode() >= 400) {
                throw new OAuthException(
                        payload.path("error").asText("unknown_error"),
                        payload.path("error_description").asText(truncate(response.body())));
            }
            return payload;
        } catch (IOException e) {
            throw new OAuthException("network_error", e.toString());
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            throw new OAuthException("interrupted", e.toString());
        }
    }

    private static String truncate(String s) {
        return s == null ? "" : s.substring(0, Math.min(s.length(), 400));
    }

    /**
     * Trade an authorization code for tokens.
     *
     * <p>Note there is no {@code client_secret}. This is a public client, so PKCE
     * does the work a secret would have done — and shipping a secret in a desktop
     * binary only creates the illusion of confidentiality. A confidential
     * server-side client would add {@code private_key_jwt} client authentication
     * here instead of a shared secret.
     */
    public static Tokens exchangeCode(String code, String codeVerifier) {
        Map<String, String> form = new LinkedHashMap<>();
        form.put("grant_type", "authorization_code");
        form.put("code", code);
        form.put("redirect_uri", REDIRECT_URI);
        form.put("client_id", CLIENT_ID);
        form.put("code_verifier", codeVerifier);
        return Tokens.from(postForm(AUTH_BASE + "/oauth2/token", form), null);
    }

    /**
     * Exchange a refresh token for a fresh pair.
     *
     * <p>The returned refresh token replaces the one you sent. Save it before you
     * use the new access token for anything, so a crash between the two leaves you
     * with the token that still works.
     */
    public static Tokens refreshTokens(Tokens tokens) {
        if (tokens.refreshToken() == null) {
            throw new OAuthException("no_refresh_token", "Request the offline_access scope to receive one.");
        }
        Map<String, String> form = new LinkedHashMap<>();
        form.put("grant_type", "refresh_token");
        form.put("refresh_token", tokens.refreshToken());
        form.put("client_id", CLIENT_ID);
        return Tokens.from(postForm(AUTH_BASE + "/oauth2/token", form), tokens);
    }

    /**
     * Revoke a token (RFC 7009). Call this on logout — dropping tokens on the floor
     * leaves a live account credential in whatever logs or backups captured it.
     */
    public static void revoke(String token, String tokenTypeHint) {
        Map<String, String> form = new LinkedHashMap<>();
        form.put("token", token);
        form.put("token_type_hint", tokenTypeHint);
        form.put("client_id", CLIENT_ID);
        postForm(AUTH_BASE + "/oauth2/revoke", form);
    }

    // -----------------------------------------------------------------------
    // Interactive authorization
    // -----------------------------------------------------------------------

    /**
     * Run the full browser-based flow and return tokens.
     *
     * <p>Suitable for a desktop or CLI tool. A web application would instead
     * redirect the user's browser and handle the callback in a normal controller,
     * keeping {@code state} and {@code codeVerifier} in the session.
     */
    public static Tokens authorizeInteractively(Duration timeout) {
        PkcePair pkce = generatePkcePair();
        byte[] stateBytes = new byte[16];
        RANDOM.nextBytes(stateBytes);
        String state = b64url(stateBytes);

        URI redirect = URI.create(REDIRECT_URI);
        CompletableFuture<Map<String, String>> callback = new CompletableFuture<>();
        HttpServer server;

        try {
            server = HttpServer.create(new InetSocketAddress(redirect.getHost(), redirect.getPort()), 0);
        } catch (IOException e) {
            throw new OAuthException("server_error", "Could not bind " + REDIRECT_URI + ": " + e.getMessage());
        }

        server.createContext(redirect.getPath(), exchange -> {
            Map<String, String> query = parseQuery(exchange.getRequestURI().getRawQuery());
            boolean ok = query.containsKey("code");
            String message = ok
                    ? "Authorization complete. You can close this tab."
                    : "Authorization failed: " + query.getOrDefault("error", "unknown");
            byte[] body = ("<html><body><p>" + message + "</p></body></html>")
                    .getBytes(StandardCharsets.UTF_8);

            exchange.getResponseHeaders().add("Content-Type", "text/html; charset=utf-8");
            exchange.sendResponseHeaders(200, body.length);
            try (OutputStream out = exchange.getResponseBody()) {
                out.write(body);
            }
            callback.complete(query);
        });

        server.start();
        String authUrl = buildAuthorizationUrl(pkce.codeChallenge(), state);
        System.out.println("Opening your browser to authorize...");
        System.out.println("  If it does not open, visit:\n  " + authUrl + "\n");
        openBrowser(authUrl);

        try {
            Map<String, String> result = callback.get(timeout.toSeconds(), TimeUnit.SECONDS);

            if (result.containsKey("error")) {
                throw new OAuthException(result.get("error"), result.getOrDefault("error_description", ""));
            }
            // Constant-time compare. `state` is short and this is cheap, but
            // comparing secrets with equals() is a habit worth not having.
            if (!MessageDigest.isEqual(
                    result.getOrDefault("state", "").getBytes(StandardCharsets.UTF_8),
                    state.getBytes(StandardCharsets.UTF_8))) {
                throw new OAuthException("state_mismatch", "Possible CSRF; discarding the authorization code.");
            }
            return exchangeCode(result.get("code"), pkce.codeVerifier());

        } catch (java.util.concurrent.TimeoutException e) {
            throw new OAuthException("timeout", "No callback received within " + timeout.toSeconds() + "s.");
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            throw new OAuthException("interrupted", e.toString());
        } catch (java.util.concurrent.ExecutionException e) {
            throw new OAuthException("callback_failed", String.valueOf(e.getCause()));
        } finally {
            server.stop(0);
        }
    }

    private static Map<String, String> parseQuery(String rawQuery) {
        Map<String, String> out = new HashMap<>();
        if (rawQuery == null || rawQuery.isEmpty()) {
            return out;
        }
        for (String pair : rawQuery.split("&")) {
            int eq = pair.indexOf('=');
            if (eq < 0) {
                continue;
            }
            out.put(
                    URLDecoder.decode(pair.substring(0, eq), StandardCharsets.UTF_8),
                    URLDecoder.decode(pair.substring(eq + 1), StandardCharsets.UTF_8));
        }
        return out;
    }

    private static void openBrowser(String url) {
        try {
            if (Desktop.isDesktopSupported() && Desktop.getDesktop().isSupported(Desktop.Action.BROWSE)) {
                Desktop.getDesktop().browse(URI.create(url));
            }
        } catch (Exception ignored) {
            // Headless or no browser — the URL is already printed above.
        }
    }

    /**
     * Return usable tokens, refreshing or re-authorizing as needed.
     *
     * <p>This is the method application code should call. It encapsulates the
     * three-way branch every OAuth client needs: no tokens, stale tokens, good
     * tokens.
     */
    public static Tokens getValidTokens(TokenStore store) {
        Tokens tokens = store.load();

        if (tokens == null) {
            tokens = authorizeInteractively(Duration.ofMinutes(5));
            store.save(tokens);
            return tokens;
        }

        if (tokens.expired()) {
            try {
                tokens = refreshTokens(tokens);
            } catch (OAuthException e) {
                // invalid_grant means the refresh token is revoked, expired, or
                // already used. The only recovery is re-authorization.
                if (!"invalid_grant".equals(e.code())) {
                    throw e;
                }
                System.out.println("Refresh failed (" + e.description() + "); re-authorizing.");
                tokens = authorizeInteractively(Duration.ofMinutes(5));
            }
            store.save(tokens);
        }

        return tokens;
    }

    private static String envOrDefault(String key, String fallback) {
        String value = System.getenv(key);
        return value == null || value.isBlank() ? fallback : value;
    }

    public static void main(String[] args) {
        Tokens tokens = getValidTokens(new TokenStore());
        System.out.println("Access token acquired.");
        System.out.println("  scope   : " + tokens.scope());
        System.out.println("  expires : " + tokens.expiresAt());
        System.out.println("  refresh : "
                + (tokens.refreshToken() != null ? "yes" : "no (offline_access not granted)"));
    }
}
