package com.example.brokerage.api;

import static com.example.brokerage.api.ApiTest.Tokens.ALL;
import static com.example.brokerage.api.ApiTest.Tokens.alice;
import static org.assertj.core.api.Assertions.assertThat;

import java.util.List;

import com.example.brokerage.api.orders.OrderEvents;
import com.jayway.jsonpath.JsonPath;

import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.http.HttpStatus;
import org.springframework.test.web.servlet.assertj.MockMvcTester;
import org.springframework.test.web.servlet.assertj.MvcTestResult;

/**
 * Pagination, versioning, caching and event replay.
 */
@ApiTest
class ProtocolTest {

    static final String PROBLEMS = "https://docs.brokerage.example/problems/";

    @Autowired
    MockMvcTester mvc;

    @Autowired
    OrderEvents events;

    @Test
    void transactionsArePagedWithAnOpaqueCursor() throws Exception {
        MvcTestResult first = mvc.get().uri("/accounts/ACC-1001/transactions?type=TRADE&limit=2")
                .with(alice(ALL)).exchange();
        assertThat(first).hasStatusOk();
        List<String> firstIds = ids(first);
        assertThat(firstIds).hasSize(2);

        String next = JsonPath.read(first.getResponse().getContentAsString(), "$._links.next.href");
        assertThat(next).contains("cursor=").doesNotContain("{");

        MvcTestResult second = mvc.get().uri(next).with(alice(ALL)).exchange();
        assertThat(ids(second)).doesNotContainAnyElementsOf(firstIds);
    }

    @Test
    void aTamperedCursorIsABadRequest() {
        assertThat(mvc.get().uri("/accounts/ACC-1001/transactions?cursor=not-a-cursor").with(alice(ALL)))
                .hasStatus(HttpStatus.BAD_REQUEST)
                .bodyJson().extractingPath("$.type").isEqualTo(PROBLEMS + "invalid-request");
    }

    @Test
    void versionOneQuotesAreFlatAndDeprecated() {
        MvcTestResult v1 = mvc.get().uri("/instruments/EQ-AAPL/quote").with(alice(ALL)).exchange();
        assertThat(v1).hasStatusOk();
        assertThat(v1).bodyJson().extractingPath("$.bid").isEqualTo("232.38");
        assertThat(v1.getResponse().getHeader("Deprecation")).startsWith("@");
        assertThat(v1.getResponse().getHeader("Sunset")).contains("2027");
        assertThat(v1.getResponse().getHeader("Cache-Control")).contains("max-age=1").contains("private");
    }

    @Test
    void versionTwoQuotesNestPriceLevels() {
        MvcTestResult v2 = mvc.get().uri("/instruments/EQ-AAPL/quote").header("API-Version", "2")
                .with(alice(ALL)).exchange();
        assertThat(v2).hasStatusOk();
        assertThat(v2).bodyJson().extractingPath("$.bid.price").isEqualTo("232.38");
        assertThat(v2).bodyJson().extractingPath("$.changePercent").isEqualTo("1.00");
        assertThat(v2.getResponse().getHeader("Deprecation")).isNull();
    }

    @Test
    void anUnknownVersionIsRefusedWithTheSupportedOnes() {
        assertThat(mvc.get().uri("/instruments/EQ-AAPL/quote").header("API-Version", "7").with(alice(ALL)))
                .hasStatus(HttpStatus.BAD_REQUEST)
                .bodyJson().extractingPath("$.type").isEqualTo(PROBLEMS + "unsupported-api-version");
    }

    @Test
    void anUnchangedQuoteCostsA304() {
        MvcTestResult quote = mvc.get().uri("/instruments/EQ-MSFT/quote").with(alice(ALL)).exchange();
        assertThat(mvc.get().uri("/instruments/EQ-MSFT/quote").with(alice(ALL))
                .header("If-None-Match", quote.getResponse().getHeader("ETag")))
                .hasStatus(HttpStatus.NOT_MODIFIED);
    }

    @Test
    void aReconnectingClientReceivesOnlyTheEventsItMissed() {
        mvc.post().uri("/accounts/ACC-1002/orders").with(alice(ALL)).header("Idempotency-Key", "events-1")
                .contentType("application/json")
                .content("""
                        {"instrumentId": "EQ-NVDA", "side": "SELL", "type": "LIMIT", "quantity": "1",
                         "limitPrice": "999"}""")
                .exchange();
        List<OrderEvents.Event> all = events.replay("ACC-1002", 0);
        assertThat(all).isNotEmpty();
        long last = all.getLast().id();
        assertThat(events.replay("ACC-1002", last)).isEmpty();
        assertThat(events.replay("ACC-1002", last - 1)).hasSize(1);
    }

    private static List<String> ids(MvcTestResult page) throws Exception {
        return JsonPath.read(page.getResponse().getContentAsString(), "$._embedded['bk:transactions'][*].id");
    }
}
