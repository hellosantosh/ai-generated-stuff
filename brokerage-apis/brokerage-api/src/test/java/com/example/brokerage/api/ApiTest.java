package com.example.brokerage.api;

import static org.springframework.security.test.web.servlet.request.SecurityMockMvcRequestPostProcessors.jwt;

import java.lang.annotation.ElementType;
import java.lang.annotation.Retention;
import java.lang.annotation.RetentionPolicy;
import java.lang.annotation.Target;
import java.util.ArrayList;
import java.util.List;

import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.webmvc.test.autoconfigure.AutoConfigureMockMvc;
import org.springframework.test.web.servlet.request.RequestPostProcessor;

/**
 * The whole application, with the simulated market frozen: prices never move by
 * themselves, and orders fill only when a test calls OrderService.match().
 */
@Target(ElementType.TYPE)
@Retention(RetentionPolicy.RUNTIME)
@SpringBootTest(properties = "brokerage.simulator.enabled=false")
@AutoConfigureMockMvc
public @interface ApiTest {

    /** Access tokens for tests, as the authorization server would issue them. */
    final class Tokens {

        public static final String MARKET_DATA = "market-data:read";
        public static final String ACCOUNTS = "accounts:read";
        public static final String ORDERS_READ = "orders:read";
        public static final String ORDERS_WRITE = "orders:write";
        public static final String[] ALL = { MARKET_DATA, ACCOUNTS, ORDERS_READ, ORDERS_WRITE };

        private Tokens() {
        }

        /** Alice (customer cust-1001) through her own script, the brokerage-cli client. */
        public static RequestPostProcessor alice(String... scopes) {
            return token("brokerage-cli", "cust-1001", null, scopes);
        }

        /** Alice's AI agent: same customer, orders capped at the given notional. */
        public static RequestPostProcessor agent(String tradingLimit, String... scopes) {
            return token("brokerage-agent", "cust-1001", tradingLimit, scopes);
        }

        /** Bob (customer cust-2002), who must never see Alice's accounts. */
        public static RequestPostProcessor bob(String... scopes) {
            return token("bob-app", "cust-2002", null, scopes);
        }

        private static RequestPostProcessor token(String client, String customer, String limit,
                String... scopes) {
            return jwt().jwt(token -> {
                token.subject(client).audience(List.of("brokerage-api")).claim("customer_id", customer)
                        .claim("scope", new ArrayList<>(List.of(scopes)));
                if (limit != null) {
                    token.claim("trading_limit", limit);
                }
            });
        }
    }
}
