package com.example.brokerage.api;

import static com.example.brokerage.api.ApiTest.Tokens.ALL;
import static com.example.brokerage.api.ApiTest.Tokens.agent;
import static com.example.brokerage.api.ApiTest.Tokens.alice;
import static org.assertj.core.api.Assertions.assertThat;

import java.util.UUID;

import com.example.brokerage.api.orders.OrderService;

import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.http.HttpStatus;
import org.springframework.http.MediaType;
import org.springframework.test.web.servlet.assertj.MockMvcTester;
import org.springframework.test.web.servlet.assertj.MvcTestResult;

/**
 * Placing, retrying, changing and cancelling orders, and the links that follow each state.
 */
@ApiTest
class OrderLifecycleTest {

    static final String ORDERS = "/accounts/ACC-1001/orders";
    static final String PROBLEMS = "https://docs.brokerage.example/problems/";
    static final MediaType MERGE_PATCH = MediaType.parseMediaType("application/merge-patch+json");

    @Autowired
    MockMvcTester mvc;

    @Autowired
    OrderService orders;

    @Test
    void aMarketOrderIsCreatedThenFilledAndLosesItsCancelLink() {
        MvcTestResult placed = place(UUID.randomUUID().toString(), """
                {"instrumentId": "EQ-AAPL", "side": "BUY", "type": "MARKET", "quantity": "2"}""");
        assertThat(placed).hasStatus(HttpStatus.CREATED);
        assertThat(placed.getResponse().getHeader("Location")).contains(ORDERS + "/ORD-");
        assertThat(placed.getResponse().getHeader("ETag")).isNotBlank();
        assertThat(placed).bodyJson().extractingPath("$.status").isEqualTo("OPEN");
        assertThat(placed).bodyJson().hasPath("$._links['bk:cancel']").hasPath("$._links.edit");

        orders.match();

        String url = placed.getResponse().getHeader("Location");
        MvcTestResult filled = mvc.get().uri(url).with(alice(ALL)).exchange();
        assertThat(filled).bodyJson().extractingPath("$.status").isEqualTo("FILLED");
        // AAPL is frozen at 232.40, so the ask is 232.42.
        assertThat(filled).bodyJson().extractingPath("$.averageFillPrice").isEqualTo("232.4200");
        assertThat(filled).bodyJson()
                .doesNotHavePath("$._links['bk:cancel']")
                .doesNotHavePath("$._links.edit");
    }

    @Test
    void retryingWithTheSameKeyReturnsTheSameOrder() {
        String key = UUID.randomUUID().toString();
        String body = """
                {"instrumentId": "EQ-MSFT", "side": "BUY", "type": "LIMIT", "quantity": "1",
                 "limitPrice": "400.00"}""";
        MvcTestResult first = place(key, body);
        MvcTestResult retry = place(key, body);

        assertThat(retry).hasStatus(HttpStatus.CREATED);
        assertThat(retry.getResponse().getHeader("Location"))
                .isEqualTo(first.getResponse().getHeader("Location"));
        assertThat(retry.getResponse().getHeader("Idempotent-Replayed")).isEqualTo("true");

        MvcTestResult different = place(key, body.replace("\"1\"", "\"2\""));
        assertThat(different).hasStatus(HttpStatus.UNPROCESSABLE_CONTENT)
                .bodyJson().extractingPath("$.type").isEqualTo(PROBLEMS + "idempotency-key-reused");
    }

    @Test
    void anOrderWithoutAnIdempotencyKeyIsRefused() {
        assertThat(mvc.post().uri(ORDERS).with(alice(ALL)).contentType(MediaType.APPLICATION_JSON)
                .content("""
                        {"instrumentId": "EQ-AAPL", "side": "BUY", "type": "MARKET", "quantity": "1"}"""))
                .hasStatus(HttpStatus.BAD_REQUEST)
                .bodyJson().extractingPath("$.type").isEqualTo(PROBLEMS + "idempotency-key-missing");
    }

    @Test
    void businessRuleViolationsAre422WithTheDetailsAClientNeeds() {
        MvcTestResult tooBig = place(UUID.randomUUID().toString(), """
                {"instrumentId": "EQ-MSFT", "side": "BUY", "type": "LIMIT", "quantity": "1000",
                 "limitPrice": "500"}""");
        assertThat(tooBig).hasStatus(HttpStatus.UNPROCESSABLE_CONTENT);
        assertThat(tooBig).bodyJson().extractingPath("$.type")
                .isEqualTo(PROBLEMS + "insufficient-buying-power");
        assertThat(tooBig).bodyJson().extractingPath("$.required").isEqualTo("500000.00");
        assertThat(tooBig).bodyJson().hasPath("$.available");
    }

    @Test
    void theAgentsDelegatedTradingLimitIsEnforcedByTheApi() {
        // 30 x 232.42 = 6,972.60, above the agent's 5,000 limit.
        assertThat(mvc.post().uri(ORDERS).with(agent("5000", ALL))
                .header("Idempotency-Key", UUID.randomUUID().toString())
                .contentType(MediaType.APPLICATION_JSON)
                .content("""
                        {"instrumentId": "EQ-AAPL", "side": "BUY", "type": "MARKET", "quantity": "30"}"""))
                .hasStatus(HttpStatus.FORBIDDEN)
                .bodyJson().extractingPath("$.type").isEqualTo(PROBLEMS + "trading-limit-exceeded");
    }

    @Test
    void changingAnOrderRequiresItsCurrentEtag() {
        MvcTestResult placed = place(UUID.randomUUID().toString(), """
                {"instrumentId": "EQ-NVDA", "side": "BUY", "type": "LIMIT", "quantity": "5",
                 "limitPrice": "150.00"}""");
        String url = placed.getResponse().getHeader("Location");
        String etag = placed.getResponse().getHeader("ETag");

        // Polling an unchanged order costs a 304 and no body.
        assertThat(mvc.get().uri(url).with(alice(ALL)).header("If-None-Match", etag))
                .hasStatus(HttpStatus.NOT_MODIFIED);

        assertThat(amend(url, null, """
                {"limitPrice": "151.00"}""")).hasStatus(HttpStatus.PRECONDITION_REQUIRED);

        MvcTestResult changed = amend(url, etag, """
                {"limitPrice": "151.00"}""");
        assertThat(changed).hasStatusOk();
        assertThat(changed).bodyJson().extractingPath("$.limitPrice").isEqualTo("151.00");
        assertThat(changed.getResponse().getHeader("ETag")).isNotEqualTo(etag);

        // The old ETag is now stale: a second writer holding it is refused, not merged.
        assertThat(amend(url, etag, """
                {"limitPrice": "152.00"}"""))
                .hasStatus(HttpStatus.PRECONDITION_FAILED)
                .bodyJson().extractingPath("$.type").isEqualTo(PROBLEMS + "precondition-failed");
    }

    @Test
    void cancellationIsAcceptedThenCompletes() {
        MvcTestResult placed = place(UUID.randomUUID().toString(), """
                {"instrumentId": "ETF-SPY", "side": "SELL", "type": "LIMIT", "quantity": "1",
                 "limitPrice": "900.00"}""");
        String url = placed.getResponse().getHeader("Location");

        MvcTestResult cancel = mvc.post().uri(url + "/cancellation").with(alice(ALL)).exchange();
        assertThat(cancel).hasStatus(HttpStatus.ACCEPTED);
        assertThat(cancel.getResponse().getHeader("Location")).isEqualTo(url);
        assertThat(cancel).bodyJson().extractingPath("$.status").isEqualTo("PENDING_CANCEL");

        orders.match();

        assertThat(mvc.get().uri(url).with(alice(ALL)))
                .bodyJson().extractingPath("$.status").isEqualTo("CANCELLED");
        assertThat(mvc.post().uri(url + "/cancellation").with(alice(ALL)))
                .hasStatus(HttpStatus.CONFLICT)
                .bodyJson().extractingPath("$.type").isEqualTo(PROBLEMS + "order-not-cancellable");
    }

    @Test
    void aPreviewReportsProblemsWithoutPlacingAnything() {
        MvcTestResult preview = mvc.post().uri("/accounts/ACC-1001/order-previews").with(alice(ALL))
                .contentType(MediaType.APPLICATION_JSON)
                .content("""
                        {"instrumentId": "EQ-AMZN", "side": "SELL", "type": "MARKET", "quantity": "500"}""")
                .exchange();
        assertThat(preview).hasStatusOk();
        assertThat(preview).bodyJson().extractingPath("$.acceptable").isEqualTo(false);
        assertThat(preview).bodyJson().extractingPath("$.issues[0].type")
                .isEqualTo(PROBLEMS + "insufficient-position");
        assertThat(preview).bodyJson().doesNotHavePath("$._links['bk:place-order']");
    }

    // ---------------------------------------------------------------- helpers

    private MvcTestResult place(String idempotencyKey, String json) {
        return mvc.post().uri(ORDERS).with(alice(ALL)).header("Idempotency-Key", idempotencyKey)
                .contentType(MediaType.APPLICATION_JSON).content(json).exchange();
    }

    private MvcTestResult amend(String url, String ifMatch, String json) {
        var request = mvc.patch().uri(url).with(alice(ALL)).contentType(MERGE_PATCH).content(json);
        return (ifMatch == null ? request : request.header("If-Match", ifMatch)).exchange();
    }
}
