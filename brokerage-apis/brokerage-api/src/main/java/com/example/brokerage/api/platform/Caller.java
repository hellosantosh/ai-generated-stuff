package com.example.brokerage.api.platform;

import java.math.BigDecimal;
import java.util.Collection;
import java.util.List;
import java.util.Optional;

import org.springframework.security.oauth2.jwt.Jwt;

/**
 * Who is calling, as established by the access token: the customer whose data may be
 * read, the client application acting for them, what it may do, and any trading limit
 * the customer delegated to that client.
 */
public record Caller(String customerId, String clientId, Collection<String> scopes,
        Optional<BigDecimal> tradingLimit) {

    public static Caller of(Jwt jwt) {
        // A valid token that is not bound to a customer can see no accounts at all.
        String customerId = Optional.ofNullable(jwt.getClaimAsString("customer_id")).orElse("none");
        // "scope" may be a space-delimited string (RFC 6749) or a JSON array.
        Collection<String> scopes = jwt.getClaim("scope") instanceof String text
                ? List.of(text.split(" "))
                : Optional.ofNullable(jwt.getClaimAsStringList("scope")).orElse(List.of());
        Optional<BigDecimal> limit = Optional.ofNullable(jwt.getClaimAsString("trading_limit"))
                .map(BigDecimal::new);
        return new Caller(customerId, jwt.getSubject(), scopes, limit);
    }

    public boolean canTrade() {
        return scopes.contains("orders:write");
    }

    public boolean canReadOrders() {
        return scopes.contains("orders:read");
    }
}
