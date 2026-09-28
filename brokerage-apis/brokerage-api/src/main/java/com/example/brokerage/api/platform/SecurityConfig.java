package com.example.brokerage.api.platform;

import java.util.List;

import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;

import org.springframework.beans.factory.annotation.Qualifier;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.http.HttpMethod;
import org.springframework.security.access.AccessDeniedException;
import org.springframework.security.config.Customizer;
import org.springframework.security.config.annotation.web.builders.HttpSecurity;
import org.springframework.security.config.annotation.web.configurers.AbstractHttpConfigurer;
import org.springframework.security.config.http.SessionCreationPolicy;
import org.springframework.security.core.AuthenticationException;
import org.springframework.security.oauth2.server.resource.web.BearerTokenAuthenticationEntryPoint;
import org.springframework.security.oauth2.server.resource.web.access.BearerTokenAccessDeniedHandler;
import org.springframework.security.web.AuthenticationEntryPoint;
import org.springframework.security.web.SecurityFilterChain;
import org.springframework.security.web.access.AccessDeniedHandler;
import org.springframework.web.servlet.HandlerExceptionResolver;

/**
 * OAuth 2.1 resource server. Every operation requires one scope; the rules below are the
 * single place where an operation's required scope is decided.
 *
 * <pre>
 *   market-data:read   instruments, quotes, option chains
 *   accounts:read      accounts, balances, positions, transactions
 *   orders:read        orders, order previews, the order event stream
 *   orders:write       placing, changing and cancelling orders
 * </pre>
 */
@Configuration(proxyBeanMethods = false)
public class SecurityConfig {

    public static final String MARKET_DATA_READ = "SCOPE_market-data:read";
    public static final String ACCOUNTS_READ = "SCOPE_accounts:read";
    public static final String ORDERS_READ = "SCOPE_orders:read";
    public static final String ORDERS_WRITE = "SCOPE_orders:write";

    @Bean
    SecurityFilterChain api(HttpSecurity http, ProblemResponses problems,
            @Value("${spring.security.oauth2.resourceserver.jwt.issuer-uri}") String issuer)
            throws Exception {
        return http
                .authorizeHttpRequests(auth -> auth
                        .requestMatchers(HttpMethod.GET, "/", "/openapi.yaml", "/actuator/health/**",
                                "/.well-known/oauth-protected-resource").permitAll()
                        .requestMatchers(HttpMethod.GET, "/instruments/**").hasAuthority(MARKET_DATA_READ)
                        .requestMatchers(HttpMethod.GET, "/accounts/*/orders/**", "/accounts/*/order-events")
                            .hasAuthority(ORDERS_READ)
                        .requestMatchers(HttpMethod.POST, "/accounts/*/order-previews")
                            .hasAuthority(ORDERS_READ)
                        .requestMatchers("/accounts/*/orders/**").hasAuthority(ORDERS_WRITE)
                        .requestMatchers(HttpMethod.GET, "/accounts/**").hasAuthority(ACCOUNTS_READ)
                        .anyRequest().denyAll())
                .oauth2ResourceServer(server -> server
                        .jwt(Customizer.withDefaults())
                        // RFC 9728: publish which authorization server and scopes this API uses.
                        .protectedResourceMetadata(metadata -> metadata.protectedResourceMetadataCustomizer(
                                resource -> resource.resourceName("Brokerage API")
                                        .authorizationServer(issuer)
                                        .tlsClientCertificateBoundAccessTokens(false)
                                        .scopes(scopes -> scopes.addAll(List.of("market-data:read",
                                                "accounts:read", "orders:read", "orders:write")))))
                        .authenticationEntryPoint(problems)
                        .accessDeniedHandler(problems))
                // Bearer tokens are sent explicitly, never automatically like cookies, so
                // there is no cross-site request forgery to defend against, and no session.
                .csrf(AbstractHttpConfigurer::disable)
                .sessionManagement(session -> session.sessionCreationPolicy(SessionCreationPolicy.STATELESS))
                .build();
    }

    /**
     * 401 and 403 responses keep the standard WWW-Authenticate header (RFC 6750) and get the
     * same problem-document body as every other error, by handing the exception to the MVC
     * exception handling in {@link ApiExceptionHandler}.
     */
    @Bean
    ProblemResponses problemResponses(
            @Qualifier("handlerExceptionResolver") HandlerExceptionResolver resolver) {
        return new ProblemResponses(resolver);
    }

    public static final class ProblemResponses implements AuthenticationEntryPoint, AccessDeniedHandler {

        private final HandlerExceptionResolver resolver;
        private final BearerTokenAuthenticationEntryPoint challenge =
                new BearerTokenAuthenticationEntryPoint();
        private final BearerTokenAccessDeniedHandler denied = new BearerTokenAccessDeniedHandler();

        ProblemResponses(HandlerExceptionResolver resolver) {
            this.resolver = resolver;
        }

        @Override
        public void commence(HttpServletRequest request, HttpServletResponse response,
                AuthenticationException ex) {
            challenge.commence(request, response, ex);
            resolver.resolveException(request, response, null, ex);
        }

        @Override
        public void handle(HttpServletRequest request, HttpServletResponse response,
                AccessDeniedException ex) {
            denied.handle(request, response, ex);
            resolver.resolveException(request, response, null, ex);
        }
    }
}
