package com.example.brokerage.auth;

import java.math.BigDecimal;
import java.util.List;
import java.util.Map;

import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;
import org.springframework.boot.context.properties.ConfigurationProperties;
import org.springframework.boot.context.properties.EnableConfigurationProperties;
import org.springframework.context.annotation.Bean;
import org.springframework.security.oauth2.server.authorization.OAuth2TokenType;
import org.springframework.security.oauth2.server.authorization.token.JwtEncodingContext;
import org.springframework.security.oauth2.server.authorization.token.OAuth2TokenCustomizer;

/**
 * A development-only OAuth 2.1 authorization server. Clients, scopes and the demo user are
 * declared in application.yaml; this class adds the two brokerage-specific claims that the
 * API relies on.
 */
@SpringBootApplication
@EnableConfigurationProperties(AuthServerApplication.Identities.class)
public class AuthServerApplication {

    public static void main(String[] args) {
        SpringApplication.run(AuthServerApplication.class, args);
    }

    /**
     * Who each token is for. A user signs in as themselves; a confidential client that uses
     * the client_credentials grant is registered to one customer, like a personal API key.
     * An optional per-client trading limit caps the notional value of any single order.
     */
    @ConfigurationProperties("brokerage.identities")
    record Identities(Map<String, String> customers, Map<String, BigDecimal> tradingLimits) {
        Identities {
            customers = customers == null ? Map.of() : customers;
            tradingLimits = tradingLimits == null ? Map.of() : tradingLimits;
        }
    }

    /** Stamps the audience, customer_id and (if configured) trading_limit into every access token. */
    @Bean
    OAuth2TokenCustomizer<JwtEncodingContext> brokerageClaims(Identities identities) {
        return context -> {
            if (!OAuth2TokenType.ACCESS_TOKEN.equals(context.getTokenType())) {
                return;
            }
            // Tokens are minted for one API, which checks that it is the intended audience.
            context.getClaims().audience(List.of("brokerage-api"));
            // The principal is the signed-in user, or the client itself for client_credentials.
            String principal = context.getPrincipal().getName();
            String customerId = identities.customers().get(principal);
            if (customerId != null) {
                context.getClaims().claim("customer_id", customerId);
            }
            String clientId = context.getRegisteredClient().getClientId();
            BigDecimal limit = identities.tradingLimits().get(clientId);
            if (limit != null) {
                context.getClaims().claim("trading_limit", limit.toPlainString());
            }
        };
    }
}
