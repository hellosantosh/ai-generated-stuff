package com.example.brokerage.api.platform;

import java.net.URI;
import java.time.ZonedDateTime;

import org.springframework.context.annotation.Configuration;
import org.springframework.web.accept.StandardApiVersionDeprecationHandler;
import org.springframework.web.servlet.config.annotation.ApiVersionConfigurer;
import org.springframework.web.servlet.config.annotation.InterceptorRegistry;
import org.springframework.web.servlet.config.annotation.WebMvcConfigurer;

/**
 * API versioning and request interceptors.
 *
 * <p>Clients choose a version with the API-Version request header. Version 1 is the
 * default so that existing clients keep working, but it is deprecated: every version 1
 * response carries Deprecation (RFC 9745) and Sunset (RFC 8594) headers, plus a link to
 * the migration guide. Version 2 differs only in the shape of a quote.
 */
@Configuration(proxyBeanMethods = false)
public class WebConfig implements WebMvcConfigurer {

    public static final String VERSION_HEADER = "API-Version";

    private final RateLimiter rateLimiter;

    public WebConfig(RateLimiter rateLimiter) {
        this.rateLimiter = rateLimiter;
    }

    @Override
    public void configureApiVersioning(ApiVersionConfigurer versions) {
        StandardApiVersionDeprecationHandler deprecations = new StandardApiVersionDeprecationHandler();
        deprecations.configureVersion("1")
                .setDeprecationDate(ZonedDateTime.parse("2026-09-01T00:00:00Z"))
                .setSunsetDate(ZonedDateTime.parse("2027-03-01T00:00:00Z"))
                .setSunsetLink(URI.create("https://docs.brokerage.example/migrations/v2"));

        versions.useRequestHeader(VERSION_HEADER)
                .addSupportedVersions("1", "2")
                .setDefaultVersion("1")
                .setDeprecationHandler(deprecations);
    }

    @Override
    public void addInterceptors(InterceptorRegistry registry) {
        registry.addInterceptor(rateLimiter).addPathPatterns("/accounts/**", "/instruments/**");
    }
}
