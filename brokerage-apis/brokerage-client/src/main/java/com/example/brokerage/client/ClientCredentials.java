package com.example.brokerage.client;

import java.net.URI;
import java.time.Instant;
import java.util.function.Supplier;

import tools.jackson.databind.JsonNode;

import org.springframework.http.MediaType;
import org.springframework.util.LinkedMultiValueMap;
import org.springframework.util.MultiValueMap;
import org.springframework.web.client.RestClient;

/**
 * Access tokens by the OAuth 2.1 client credentials grant, cached until shortly before they
 * expire. Enough for scripts and the demo; a Spring application should use Spring
 * Security's OAuth 2.0 client instead, as the agent does.
 */
public class ClientCredentials implements Supplier<String> {

    private final RestClient http = RestClient.create();
    private final URI tokenEndpoint;
    private final String clientId;
    private final String clientSecret;
    private final String scope;
    private String token;
    private Instant expiresAt = Instant.EPOCH;

    public ClientCredentials(URI tokenEndpoint, String clientId, String clientSecret, String scope) {
        this.tokenEndpoint = tokenEndpoint;
        this.clientId = clientId;
        this.clientSecret = clientSecret;
        this.scope = scope;
    }

    @Override
    public synchronized String get() {
        if (token == null || Instant.now().isAfter(expiresAt.minusSeconds(30))) {
            MultiValueMap<String, String> form = new LinkedMultiValueMap<>();
            form.add("grant_type", "client_credentials");
            form.add("scope", scope);
            JsonNode response = http.post().uri(tokenEndpoint)
                    .headers(headers -> headers.setBasicAuth(clientId, clientSecret))
                    .contentType(MediaType.APPLICATION_FORM_URLENCODED)
                    .body(form)
                    .retrieve()
                    .body(JsonNode.class);
            token = response.get("access_token").asString();
            expiresAt = Instant.now().plusSeconds(response.path("expires_in").asLong(60));
        }
        return token;
    }
}
