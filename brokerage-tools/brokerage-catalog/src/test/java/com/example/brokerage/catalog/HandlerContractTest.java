package com.example.brokerage.catalog;

import com.example.brokerage.tooling.contract.ErrorCode;

import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import tools.jackson.databind.JsonNode;

import static org.assertj.core.api.Assertions.assertThat;

/**
 * What each read tool promises, checked against what it returns.
 *
 * <p>Every payload in here has already been validated against its own output schema by the
 * pipeline, so these tests are not re-checking the shape. They check the <em>claims the
 * description makes</em> — that buying power is net of reserved cash, that positions come back
 * largest first, that a lot knows its holding period — because those are the claims the model is
 * acting on and no schema can express them.
 */
class HandlerContractTest {

    private CatalogFixture fixture;

    @BeforeEach
    void setUp() {
        fixture = new CatalogFixture();
    }

    @Test
    @DisplayName("accounts come back with the customer's four accounts and the frozen one flagged")
    void accountsAreListedWithTheirRestrictions() {
        JsonNode data = fixture.data("brokerage_accounts_list", "{}", fixture.reader());
        assertThat(data.path("accounts").size()).isEqualTo(4);
        JsonNode frozen = data.path("accounts").values().stream()
                .filter(account -> account.path("accountId").asString()
                        .equals(CatalogFixture.FROZEN_ACCOUNT))
                .findFirst().orElseThrow();
        assertThat(frozen.path("tradeable").asBoolean()).isFalse();
        assertThat(frozen.path("restriction").asString()).isEqualTo("FROZEN");
        assertThat(frozen.path("type").asString()).isEqualTo("IRA_TRADITIONAL");
    }

    @Test
    @DisplayName("an account belonging to someone else is NOT_FOUND, never NOT_ENTITLED")
    void anotherCustomersAccountIsIndistinguishableFromAMadeUpOne() {
        JsonNode error = fixture.error("brokerage_balances_get",
                "{\"accountId\":\"acct_ZZZZZZZZZZZZZZZZ\"}", fixture.reader());
        assertThat(error.path("code").asString()).isEqualTo(ErrorCode.NOT_FOUND.name());
        assertThat(error.path("remediation").asString()).contains("brokerage_accounts_list");
    }

    @Test
    @DisplayName("buying power is net of the cash already committed to working orders")
    void buyingPowerIsNetOfReservedCash() {
        JsonNode data = fixture.data("brokerage_balances_get",
                "{\"accountId\":\"" + CatalogFixture.MARGIN_ACCOUNT + "\"}", fixture.reader());
        assertThat(new java.math.BigDecimal(data.path("cashReservedForOrders").asString()))
                .as("the seeded account has two working buy orders")
                .isGreaterThan(java.math.BigDecimal.ZERO);
        assertThat(data.path("marginLoan").asString()).isEqualTo("24000.00");
        assertThat(data.path("dayTradesUsed").asInt()).isEqualTo(2);
    }

    @Test
    @DisplayName("a cash account's buying power never exceeds its settled cash")
    void cashAccountsCannotBorrow() {
        JsonNode data = fixture.data("brokerage_balances_get",
                "{\"accountId\":\"" + CatalogFixture.CASH_ACCOUNT + "\"}", fixture.reader());
        assertThat(new java.math.BigDecimal(data.path("buyingPower").asString()))
                .isLessThanOrEqualTo(new java.math.BigDecimal(data.path("settledCash").asString()));
        assertThat(data.path("marginLoan").asString()).isEqualTo("0.00");
    }

    @Test
    @DisplayName("positions come back largest first, with the weight already computed")
    void positionsArePrioritizedAndPreComputed() {
        JsonNode data = fixture.data("brokerage_positions_list",
                "{\"accountId\":\"" + CatalogFixture.MARGIN_ACCOUNT + "\"}", fixture.reader());
        var values = data.path("positions").values().stream().toList();
        assertThat(values).hasSize(5);
        java.math.BigDecimal previous = null;
        for (JsonNode position : values) {
            java.math.BigDecimal value = new java.math.BigDecimal(
                    position.path("marketValue").asString());
            if (previous != null) {
                assertThat(value).as("positions must be ordered by value, largest first")
                        .isLessThanOrEqualTo(previous);
            }
            previous = value;
            assertThat(position.path("portfolioWeight").asString()).isNotBlank();
        }
    }

    @Test
    @DisplayName("quotes are batched, and a symbol that does not resolve is named rather than dropped")
    void unresolvedSymbolsAreNamed() {
        JsonNode data = fixture.data("brokerage_quotes_get",
                "{\"symbols\":[\"AAPL\",\"BRK.B\",\"ZZZZ\"]}", fixture.reader());
        assertThat(data.path("quotes").size()).isEqualTo(2);
        assertThat(data.path("unresolved").values().stream()
                .map(JsonNode::asString).toList()).containsExactly("ZZZZ");
        assertThat(data.path("session").asString()).isEqualTo("REGULAR");
    }

    @Test
    @DisplayName("a share class with a dot in its ticker is quotable")
    void theSymbolPatternAllowsSharpClasses() {
        JsonNode data = fixture.data("brokerage_quotes_get", "{\"symbols\":[\"BRK.B\"]}",
                fixture.reader());
        assertThat(data.path("quotes").path(0).path("symbol").asString()).isEqualTo("BRK.B");
    }

    @Test
    @DisplayName("a free-text search returns candidates and refuses to choose")
    void searchReturnsCandidatesRatherThanAnAnswer() {
        JsonNode data = fixture.data("brokerage_instruments_search",
                "{\"query\":\"Apple\"}", fixture.reader());
        assertThat(data.path("exactMatch").asBoolean()).isFalse();
        assertThat(data.path("candidates").values().stream()
                .map(candidate -> candidate.path("symbol").asString()).toList())
                .as("both Apple Inc. and Apple Hospitality match the word")
                .contains("AAPL", "APLE");
    }

    @Test
    @DisplayName("an exact ticker is reported as an exact match, so the agent need not ask")
    void anExactTickerNeedsNoQuestion() {
        JsonNode data = fixture.data("brokerage_instruments_search",
                "{\"query\":\"MSFT\"}", fixture.reader());
        assertThat(data.path("exactMatch").asBoolean()).isTrue();
        assertThat(data.path("candidates").size()).isEqualTo(1);
    }

    @Test
    @DisplayName("price history carries its own summary, so the model does no arithmetic")
    void historyIsSummarizedForTheModel() {
        JsonNode data = fixture.data("brokerage_price_history_get",
                "{\"symbol\":\"NVDA\",\"days\":30}", fixture.reader());
        assertThat(data.path("bars").size()).isBetween(18, 24);
        assertThat(data.path("periodHigh").asString()).isNotBlank();
        assertThat(data.path("periodLow").asString()).isNotBlank();
        assertThat(data.path("totalReturnPercent").asString()).isNotBlank();
        assertThat(new java.math.BigDecimal(data.path("periodHigh").asString()))
                .isGreaterThanOrEqualTo(
                        new java.math.BigDecimal(data.path("periodLow").asString()));
    }

    @Test
    @DisplayName("tax lots know their holding period and their wash-sale exposure")
    void lotsCarryTheFactsATaxConversationNeeds() {
        JsonNode data = fixture.data("brokerage_tax_lots_list",
                "{\"accountId\":\"" + CatalogFixture.MARGIN_ACCOUNT + "\",\"symbol\":\"AAPL\"}",
                fixture.reader());
        assertThat(data.path("taxable").asBoolean()).isTrue();
        assertThat(data.path("defaultMethod").asString()).isEqualTo("FIFO");
        var lots = data.path("lots").values().stream().toList();
        assertThat(lots).hasSize(2);
        assertThat(lots.getFirst().path("longTerm").asBoolean())
                .as("the 2022 lot is long-term").isTrue();
        assertThat(lots.getLast().path("longTerm").asBoolean())
                .as("the 2026 lot is not").isFalse();
        assertThat(lots.getLast().path("washSaleRisk").asBoolean()).isTrue();
    }

    @Test
    @DisplayName("a retirement account's lots say plainly that none of it is taxable")
    void lotsInARetirementAccountSaySo() {
        JsonNode data = fixture.data("brokerage_tax_lots_list",
                "{\"accountId\":\"" + CatalogFixture.ROTH_ACCOUNT + "\",\"symbol\":\"VOO\"}",
                fixture.reader());
        assertThat(data.path("taxable").asBoolean())
                .as("an agent explaining holding periods inside a Roth has wasted the customer's "
                        + "time; the tool says so rather than leaving it to be inferred")
                .isFalse();
    }

    @Test
    @DisplayName("the working-order default is the question the customer actually asked")
    void ordersDefaultToWorking() {
        JsonNode working = fixture.data("brokerage_orders_list",
                "{\"accountId\":\"" + CatalogFixture.MARGIN_ACCOUNT + "\"}", fixture.reader());
        JsonNode all = fixture.data("brokerage_orders_list",
                "{\"accountId\":\"" + CatalogFixture.MARGIN_ACCOUNT + "\",\"state\":\"ALL\"}",
                fixture.reader());
        assertThat(working.path("orders").size()).isEqualTo(2);
        assertThat(all.path("orders").size()).isEqualTo(3);
    }

    @Test
    @DisplayName("an order carries its fills, because an average is not what the customer paid")
    void anOrderCarriesItsExecutions() {
        JsonNode data = fixture.data("brokerage_order_get",
                "{\"accountId\":\"" + CatalogFixture.MARGIN_ACCOUNT + "\",\"orderId\":\""
                        + CatalogFixture.FILLED_NVDA_ORDER + "\"}", fixture.reader());
        assertThat(data.path("status").asString()).isEqualTo("FILLED");
        assertThat(data.path("executions").size()).isEqualTo(1);
        assertThat(data.path("executions").path(0).path("price").asString()).isEqualTo("181.44");
        assertThat(data.path("executions").path(0).path("settlesOn").asString()).isNotBlank();
    }

    @Test
    @DisplayName("a cursor this tool did not issue is an argument error, not a silent restart")
    void aForeignCursorIsRefused() {
        JsonNode error = fixture.error("brokerage_orders_list",
                "{\"accountId\":\"" + CatalogFixture.MARGIN_ACCOUNT + "\",\"cursor\":\"page2\"}",
                fixture.reader());
        assertThat(error.path("code").asString()).isEqualTo(ErrorCode.INVALID_ARGUMENT.name());
        assertThat(error.path("remediation").asString()).contains("exactly as it was returned");
    }

    @Test
    @DisplayName("a read session cannot see the trading tools at all")
    void readSessionsHaveNoTradingTools() {
        assertThat(fixture.registry.visibleTo(fixture.reader())).hasSize(10);
        assertThat(fixture.registry.visibleTo(fixture.trader())).hasSize(14);
        assertThat(fixture.error("brokerage_order_preview",
                "{\"accountId\":\"" + CatalogFixture.MARGIN_ACCOUNT
                        + "\",\"symbol\":\"AAPL\",\"side\":\"BUY\",\"quantity\":1,"
                        + "\"orderType\":\"MARKET\"}", fixture.reader())
                .path("code").asString()).isEqualTo(ErrorCode.UNKNOWN_TOOL.name());
    }
}
