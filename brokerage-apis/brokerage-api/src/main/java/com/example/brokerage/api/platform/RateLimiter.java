package com.example.brokerage.api.platform;

import java.time.Clock;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;

import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;

import org.springframework.boot.context.properties.ConfigurationProperties;
import org.springframework.http.HttpMethod;
import org.springframework.security.core.Authentication;
import org.springframework.security.core.context.SecurityContextHolder;
import org.springframework.stereotype.Component;
import org.springframework.web.servlet.HandlerInterceptor;

/**
 * A fixed-window rate limiter per client, with two policies: "default" for every request
 * and a stricter "orders" policy for requests that change orders. Every response
 * advertises the policy and what is left of it, using the RateLimit-Policy and RateLimit
 * fields from the IETF httpapi working group's draft, so well-behaved clients can slow
 * down before they are refused.
 */
@Component
public class RateLimiter implements HandlerInterceptor {

    @ConfigurationProperties("brokerage.rate-limit")
    public record Quotas(int requestsPerMinute, int orderChangesPerMinute) {
        public Quotas {
            requestsPerMinute = requestsPerMinute > 0 ? requestsPerMinute : 120;
            orderChangesPerMinute = orderChangesPerMinute > 0 ? orderChangesPerMinute : 30;
        }
    }

    private static final int WINDOW_SECONDS = 60;

    private final Quotas quotas;
    private final Clock clock;
    private final Map<String, Window> windows = new ConcurrentHashMap<>();

    public RateLimiter(Quotas quotas) {
        this.quotas = quotas;
        this.clock = Clock.systemUTC();
    }

    @Override
    public boolean preHandle(HttpServletRequest request, HttpServletResponse response, Object handler) {
        Authentication caller = SecurityContextHolder.getContext().getAuthentication();
        if (caller == null || !caller.isAuthenticated()) {
            return true;
        }
        boolean changesOrders = !HttpMethod.GET.matches(request.getMethod())
                && request.getRequestURI().contains("/orders");
        String policy = changesOrders ? "orders" : "default";
        int quota = changesOrders ? quotas.orderChangesPerMinute() : quotas.requestsPerMinute();

        long now = clock.instant().getEpochSecond();
        long windowStart = now - (now % WINDOW_SECONDS);
        Window window = windows.compute(caller.getName() + "|" + policy,
                (key, existing) -> existing == null || existing.start() != windowStart
                        ? new Window(windowStart, 1) : new Window(windowStart, existing.used() + 1));

        long resetIn = windowStart + WINDOW_SECONDS - now;
        int remaining = Math.max(0, quota - window.used());
        response.setHeader("RateLimit-Policy", "\"%s\";q=%d;w=%d".formatted(policy, quota, WINDOW_SECONDS));
        response.setHeader("RateLimit", "\"%s\";r=%d;t=%d".formatted(policy, remaining, resetIn));
        if (window.used() > quota) {
            throw new RateLimitedException(policy, resetIn);
        }
        return true;
    }

    private record Window(long start, int used) {
    }
}
