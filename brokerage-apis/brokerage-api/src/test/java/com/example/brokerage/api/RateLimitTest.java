package com.example.brokerage.api;

import static com.example.brokerage.api.ApiTest.Tokens.ALL;
import static com.example.brokerage.api.ApiTest.Tokens.alice;
import static org.assertj.core.api.Assertions.assertThat;

import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.webmvc.test.autoconfigure.AutoConfigureMockMvc;
import org.springframework.http.HttpStatus;
import org.springframework.test.web.servlet.assertj.MockMvcTester;
import org.springframework.test.web.servlet.assertj.MvcTestResult;

/**
 * Quotas are advertised on every response, and enforced with 429 and Retry-After.
 */
@SpringBootTest(properties = {
        "brokerage.simulator.enabled=false",
        "brokerage.rate-limit.requests-per-minute=3" })
@AutoConfigureMockMvc
class RateLimitTest {

    @Autowired
    MockMvcTester mvc;

    @Test
    void aClientOverItsQuotaIsToldWhenToComeBack() {
        MvcTestResult first = mvc.get().uri("/instruments").with(alice(ALL)).exchange();
        assertThat(first.getResponse().getHeader("RateLimit-Policy")).isEqualTo("\"default\";q=3;w=60");
        assertThat(first.getResponse().getHeader("RateLimit")).startsWith("\"default\";r=2;");

        mvc.get().uri("/instruments").with(alice(ALL)).exchange();
        mvc.get().uri("/instruments").with(alice(ALL)).exchange();
        MvcTestResult refused = mvc.get().uri("/instruments").with(alice(ALL)).exchange();

        assertThat(refused).hasStatus(HttpStatus.TOO_MANY_REQUESTS);
        assertThat(refused.getResponse().getHeader("Retry-After")).isNotBlank();
        assertThat(refused).bodyJson().extractingPath("$.type")
                .isEqualTo("https://docs.brokerage.example/problems/rate-limited");
    }
}
