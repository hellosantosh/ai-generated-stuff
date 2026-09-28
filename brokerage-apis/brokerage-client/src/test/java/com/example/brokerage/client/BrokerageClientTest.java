package com.example.brokerage.client;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.springframework.test.web.client.match.MockRestRequestMatchers.header;
import static org.springframework.test.web.client.match.MockRestRequestMatchers.method;
import static org.springframework.test.web.client.match.MockRestRequestMatchers.requestTo;
import static org.springframework.test.web.client.response.MockRestResponseCreators.withStatus;
import static org.springframework.test.web.client.response.MockRestResponseCreators.withSuccess;

import java.math.BigDecimal;
import java.net.URI;

import com.example.brokerage.client.Model.Order;
import com.example.brokerage.client.Model.OrderTicket;

import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.http.HttpMethod;
import org.springframework.http.HttpStatus;
import org.springframework.http.MediaType;
import org.springframework.test.web.client.ExpectedCount;
import org.springframework.test.web.client.MockRestServiceServer;
import org.springframework.web.client.RestClient;

/**
 * The client follows whatever links the server sends, including unusual ones, and never
 * builds a URL of its own.
 */
class BrokerageClientTest {

    static final MediaType HAL = MediaType.parseMediaType("application/hal+json");
    static final MediaType PROBLEM = MediaType.parseMediaType("application/problem+json");

    // Deliberately odd URLs: a client that constructs paths would never find them.
    static final String ROOT = """
            {"_links": {"self": {"href": "https://api.test/"},
              "bk:accounts": {"href": "https://api.test/v9/acct-list"},
              "bk:instruments": {"href": "https://api.test/catalog{?symbol,type}", "templated": true}}}""";
    static final String ACCOUNTS = """
            {"_embedded": {"bk:accounts": [{"id": "ACC-1", "nickname": "Main", "type": "MARGIN",
              "_links": {"self": {"href": "https://api.test/a/1"},
                         "bk:balances": {"href": "https://api.test/money/for/ACC-1"},
                          "bk:orders": {"href": "https://api.test/a/1/o{?status,cursor}",
                                        "templated": true}}}]},
             "_links": {"self": {"href": "https://api.test/v9/acct-list"}}}""";

    MockRestServiceServer server;
    BrokerageClient brokerage;

    @BeforeEach
    void setUp() {
        RestClient.Builder builder = RestClient.builder();
        server = MockRestServiceServer.bindTo(builder).build();
        brokerage = new BrokerageClient(new HypermediaClient(builder), URI.create("https://api.test/"));
    }

    @Test
    void itFindsBalancesByFollowingLinksFromTheRoot() {
        expectGet("https://api.test/", ROOT);
        expectGet("https://api.test/v9/acct-list", ACCOUNTS);
        expectGet("https://api.test/money/for/ACC-1", """
                {"currency": "USD", "cash": "1000.10", "buyingPower": "2000.20", "futureField": "ignored",
                 "_links": {"self": {"href": "https://api.test/money/for/ACC-1"}}}""");

        var balances = brokerage.balances("ACC-1");

        assertThat(balances.cash()).isEqualByComparingTo("1000.10");
        assertThat(balances.buyingPower()).isEqualByComparingTo("2000.20");
        server.verify();
    }

    @Test
    void aTransientFailureIsRetriedWithTheSameIdempotencyKey() {
        expectGet("https://api.test/", ROOT);
        expectGet("https://api.test/v9/acct-list", ACCOUNTS);
        server.expect(ExpectedCount.once(), requestTo("https://api.test/a/1/o"))
                .andExpect(method(HttpMethod.POST))
                .andExpect(header("Idempotency-Key", "key-42"))
                .andRespond(withStatus(HttpStatus.SERVICE_UNAVAILABLE).contentType(PROBLEM)
                        .header("Retry-After", "0")
                        .body("""
                                {"type": "https://docs.test/problems/unavailable", "title": "Busy",
                                 "status": 503}"""));
        server.expect(ExpectedCount.once(), requestTo("https://api.test/a/1/o"))
                .andExpect(method(HttpMethod.POST))
                .andExpect(header("Idempotency-Key", "key-42"))
                .andRespond(withStatus(HttpStatus.CREATED).contentType(HAL).body("""
                        {"id": "ORD-9", "status": "OPEN", "quantity": "5",
                         "_links": {"self": {"href": "https://api.test/a/1/o/9"}}}"""));

        OrderTicket ticket = OrderTicket.market("EQ-X", "BUY", new BigDecimal("5"));
        Order order = brokerage.place("ACC-1", ticket, "key-42");

        assertThat(order.id()).isEqualTo("ORD-9");
        server.verify();
    }

    @Test
    void aBusinessRuleViolationArrivesAsATypedProblem() {
        expectGet("https://api.test/", ROOT);
        expectGet("https://api.test/v9/acct-list", ACCOUNTS);
        server.expect(requestTo("https://api.test/a/1/o")).andRespond(
                withStatus(HttpStatus.UNPROCESSABLE_CONTENT).contentType(PROBLEM).body("""
                        {"type": "https://docs.brokerage.example/problems/insufficient-buying-power",
                         "title": "The account does not have enough buying power", "status": 422,
                         "detail": "This order needs 900.00 but only 10.00 is available",
                         "available": "10.00"}"""));

        OrderTicket ticket = OrderTicket.market("EQ-X", "BUY", BigDecimal.TEN);
        assertThatThrownBy(() -> brokerage.place("ACC-1", ticket, "k"))
                .isInstanceOfSatisfying(ApiProblem.class, problem -> {
                    assertThat(problem.typeName()).isEqualTo("insufficient-buying-power");
                    assertThat(problem.isTransient()).isFalse();
                    assertThat(problem.body().path("available").asString()).isEqualTo("10.00");
                });
    }

    @Test
    void anOrderWithoutACancelLinkIsNotCancelledAndNoRequestIsSent() {
        Order filled = new Order("ORD-7", "AAPL", "BUY", "MARKET", BigDecimal.ONE, null, "FILLED",
                BigDecimal.ONE, new BigDecimal("232.42"), null, null);

        assertThatThrownBy(() -> brokerage.cancel(filled))
                .isInstanceOf(IllegalStateException.class)
                .hasMessageContaining("can no longer be cancelled");
        server.verify();
    }

    private void expectGet(String url, String body) {
        server.expect(ExpectedCount.once(), requestTo(url)).andExpect(method(HttpMethod.GET))
                .andRespond(withSuccess(body, HAL));
    }
}
