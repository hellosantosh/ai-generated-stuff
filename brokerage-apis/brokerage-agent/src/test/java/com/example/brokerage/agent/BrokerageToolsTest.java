package com.example.brokerage.agent;

import static org.assertj.core.api.Assertions.assertThat;
import static org.springframework.test.web.client.match.MockRestRequestMatchers.header;
import static org.springframework.test.web.client.match.MockRestRequestMatchers.method;
import static org.springframework.test.web.client.match.MockRestRequestMatchers.requestTo;
import static org.springframework.test.web.client.response.MockRestResponseCreators.withStatus;
import static org.springframework.test.web.client.response.MockRestResponseCreators.withSuccess;

import java.net.URI;
import java.time.Clock;
import java.util.Arrays;

import com.example.brokerage.agent.PendingActions.Proposal;
import com.example.brokerage.client.BrokerageClient;
import com.example.brokerage.client.HypermediaClient;

import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.ai.support.ToolCallbacks;
import org.springframework.ai.tool.ToolCallback;
import org.springframework.http.HttpMethod;
import org.springframework.http.HttpStatus;
import org.springframework.http.MediaType;
import org.springframework.test.web.client.ExpectedCount;
import org.springframework.test.web.client.MockRestServiceServer;
import org.springframework.web.client.RestClient;

/**
 * The tools as the model will see them, and the guarantee that matters most: a proposal
 * places nothing, and only a human confirmation does.
 */
class BrokerageToolsTest {

    static final MediaType HAL = MediaType.parseMediaType("application/hal+json");
    static final String ROOT = """
            {"_links": {"self": {"href": "http://api/"}, "bk:accounts": {"href": "http://api/accounts"},
              "bk:instruments": {"href": "http://api/instruments{?symbol,type}", "templated": true}}}""";
    static final String ACCOUNTS = """
            {"_embedded": {"bk:accounts": [{"id": "ACC-1001", "nickname": "Main", "type": "MARGIN",
              "_links": {"self": {"href": "http://api/accounts/ACC-1001"},
                "bk:orders": {"href": "http://api/accounts/ACC-1001/orders{?status,symbol,cursor,limit}",
                              "templated": true},
                "bk:order-previews": {"href": "http://api/accounts/ACC-1001/order-previews"}}}]}}""";
    static final String AAPL = """
            {"_embedded": {"bk:instruments": [{"id": "EQ-AAPL", "symbol": "AAPL", "name": "Apple Inc.",
              "type": "EQUITY", "tradable": true,
              "_links": {"self": {"href": "http://api/instruments/EQ-AAPL"}}}]}}""";

    MockRestServiceServer api;
    PendingActions pending;
    BrokerageTools tools;

    @BeforeEach
    void setUp() {
        RestClient.Builder builder = RestClient.builder();
        api = MockRestServiceServer.bindTo(builder).build();
        pending = new PendingActions(Clock.systemUTC());
        var brokerage = new BrokerageClient(new HypermediaClient(builder), URI.create("http://api/"));
        tools = new BrokerageTools(brokerage, pending);
    }

    @Test
    void theModelSeesSevenToolsWithDescribedParameters() {
        ToolCallback[] callbacks = ToolCallbacks.from(tools);
        assertThat(Arrays.stream(callbacks).map(callback -> callback.getToolDefinition().name()))
                .containsExactlyInAnyOrder("listAccounts", "getBalances", "getPositions", "getQuote",
                        "getOpenOrders", "proposeOrder", "proposeCancellation");
        String proposeOrder = Arrays.stream(callbacks)
                .filter(callback -> callback.getToolDefinition().name().equals("proposeOrder"))
                .findFirst().orElseThrow().getToolDefinition().inputSchema();
        assertThat(proposeOrder).contains("\"required\"").contains("\"quantity\"").contains("decimal string");
    }

    @Test
    void proposingPlacesNothingAndConfirmingPlacesExactlyOnce() {
        expect("http://api/", ROOT);                           // symbol -> instrument
        expect("http://api/instruments?symbol=AAPL", AAPL);
        expect("http://api/", ROOT);                           // account -> its previews link
        expect("http://api/accounts", ACCOUNTS);
        api.expect(ExpectedCount.once(), requestTo("http://api/accounts/ACC-1001/order-previews"))
                .andExpect(method(HttpMethod.POST))
                .andRespond(withSuccess("""
                        {"symbol": "AAPL", "estimatedPrice": "232.42", "estimatedTotal": "2324.20",
                         "buyingPower": "55241.10", "acceptable": true, "issues": []}""", HAL));

        Object result = tools.proposeOrder("ACC-1001", "AAPL", "BUY", "10", null);

        assertThat(result).isInstanceOf(Proposal.class);
        Proposal proposal = (Proposal) result;
        assertThat(proposal.summary()).contains("BUY 10 AAPL").contains("2324.20");
        api.verify(); // a preview, and no order

        api.reset();
        expect("http://api/", ROOT);
        expect("http://api/accounts", ACCOUNTS);
        api.expect(ExpectedCount.once(), requestTo("http://api/accounts/ACC-1001/orders"))
                .andExpect(method(HttpMethod.POST))
                .andExpect(header("Idempotency-Key", proposal.code()))
                .andRespond(withStatus(HttpStatus.CREATED).contentType(HAL).body("""
                        {"id": "ORD-100009", "status": "OPEN",
                         "_links": {"self": {"href": "http://api/accounts/ACC-1001/orders/ORD-100009"}}}"""));

        assertThat(pending.confirm(proposal.code())).isEqualTo("Placed order ORD-100009: OPEN");
        assertThat(pending.confirm(proposal.code())).startsWith("There is no pending action");
        api.verify();
    }

    @Test
    void anUnacceptablePreviewIsReturnedWithoutAProposal() {
        expect("http://api/", ROOT);                           // symbol -> instrument
        expect("http://api/instruments?symbol=AAPL", AAPL);
        expect("http://api/", ROOT);                           // account -> its previews link
        expect("http://api/accounts", ACCOUNTS);
        api.expect(requestTo("http://api/accounts/ACC-1001/order-previews")).andRespond(withSuccess("""
                {"symbol": "AAPL", "acceptable": false, "issues": [{"type":
                  "https://docs.brokerage.example/problems/insufficient-buying-power",
                  "detail": "This order needs 2324200.00 but only 55241.10 is available"}]}""", HAL));

        Object result = tools.proposeOrder("ACC-1001", "AAPL", "BUY", "10000", null);

        assertThat(result).isNotInstanceOf(Proposal.class);
        assertThat(result.toString()).contains("insufficient-buying-power");
    }

    private void expect(String url, String body) {
        api.expect(ExpectedCount.once(), requestTo(url)).andExpect(method(HttpMethod.GET))
                .andRespond(withSuccess(body, HAL));
    }
}
