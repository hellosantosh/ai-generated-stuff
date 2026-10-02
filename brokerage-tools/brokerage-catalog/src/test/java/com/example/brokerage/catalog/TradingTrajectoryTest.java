package com.example.brokerage.catalog;

import com.example.brokerage.tooling.contract.DescriptorCodec;
import com.example.brokerage.tooling.contract.ErrorCode;
import com.example.brokerage.tooling.contract.Principal;
import com.example.brokerage.tooling.runtime.InvocationPipeline;

import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import tools.jackson.databind.JsonNode;

import static org.assertj.core.api.Assertions.assertThat;

/**
 * The safety properties of the four tools that change something.
 *
 * <p>These are the tests that would be cited in a review, so each one states the property it
 * establishes rather than the mechanism it uses. Between them they say: an order cannot be placed
 * without being priced; cannot be placed on terms the customer did not see; cannot be placed
 * twice; cannot be placed in an account that may not trade; and a cancellation that lost a race
 * reports a fill rather than a cancellation.
 */
class TradingTrajectoryTest {

    private static final String BUY_50_AAPL = """
            {"accountId":"%s","symbol":"AAPL","side":"BUY","quantity":50,"orderType":"MARKET"}"""
            .formatted(CatalogFixture.MARGIN_ACCOUNT);

    private CatalogFixture fixture;
    private Principal trader;

    @BeforeEach
    void setUp() {
        fixture = new CatalogFixture();
        trader = fixture.trader();
    }

    private String previewToken(String arguments) {
        JsonNode data = fixture.data("brokerage_order_preview", arguments, trader);
        assertThat(data.path("canPlace").asBoolean())
                .as("preview should be placeable: %s", data).isTrue();
        return data.path("confirmationToken").asString();
    }

    private static String place(String token) {
        return """
                {"accountId":"%s","symbol":"AAPL","side":"BUY","quantity":50,
                 "orderType":"MARKET","confirmationToken":"%s"}"""
                .formatted(CatalogFixture.MARGIN_ACCOUNT, token);
    }

    @Test
    @DisplayName("an invented token does not become an order, even once a human has agreed")
    void placingNeedsATokenTheServiceIssued() {
        String arguments = place("cnf_AAAAAAAAAAAAAAAAAAAAAAAA");

        // The approval gate runs before the handler, so the first answer is about the human, not
        // about the token. That order matters: it means the gate never becomes an oracle for
        // whether a guessed token is real.
        InvocationPipeline.Outcome first = fixture.call("brokerage_order_place", arguments, trader);
        assertThat(first.errorCode()).isEqualTo(ErrorCode.APPROVAL_REQUIRED);

        InvocationPipeline.Outcome approved = fixture.callWithApproval("brokerage_order_place",
                arguments, trader);
        assertThat(approved.errorCode()).isEqualTo(ErrorCode.NOT_ENTITLED);
        assertThat(DescriptorCodec.tree(approved.json()).path("error").path("remediation")
                .asString()).contains("Do not retry");
    }

    @Test
    @DisplayName("a token with no approval behind it still stops at the gate")
    void aTokenIsNotAnApproval() {
        InvocationPipeline.Outcome outcome = fixture.call("brokerage_order_place",
                place(previewToken(BUY_50_AAPL)), trader);
        assertThat(outcome.errorCode()).isEqualTo(ErrorCode.APPROVAL_REQUIRED);
        assertThat(outcome.approvalReference()).isNotNull();
    }

    @Test
    @DisplayName("the confirmation a human sees carries the priced cost, not just the arguments")
    void theConfirmationShowsTheMoney() {
        InvocationPipeline.Outcome outcome = fixture.call("brokerage_order_place",
                place(previewToken(BUY_50_AAPL)), trader);
        String summary = fixture.approvals.peek(outcome.approvalReference()).orElseThrow()
                .summary();
        assertThat(summary)
                .as("a dialog that says '(not given)' where the cost should be is how somebody "
                        + "approves an order they never saw the price of")
                .doesNotContain("(not given)")
                .contains("BUY", "50", "AAPL", "Everyday brokerage")
                .containsPattern("Estimated cost\\s+[0-9,]+\\.[0-9]{2} USD");
    }

    @Test
    @DisplayName("preview, approve, place: the order fills and the account is changed")
    void theHappyPathWorks() {
        String arguments = place(previewToken(BUY_50_AAPL));
        InvocationPipeline.Outcome outcome = fixture.callWithApproval("brokerage_order_place",
                arguments, trader);
        assertThat(outcome.ok()).as("%s", outcome.json()).isTrue();
        JsonNode data = DescriptorCodec.tree(outcome.json()).path("data");
        assertThat(data.path("status").asString()).isEqualTo("FILLED");
        assertThat(data.path("filledQuantity").asInt()).isEqualTo(50);
        assertThat(data.path("duplicateOfPreviousCall").asBoolean()).isFalse();
        assertThat(data.path("orderId").asString()).matches("^ord_[0-9A-HJKMNP-TV-Z]{16}$");
    }

    @Test
    @DisplayName("a retried place returns the original order rather than a second one")
    void aRetryDoesNotDoubleBook() {
        String arguments = place(previewToken(BUY_50_AAPL));
        InvocationPipeline.Outcome first = fixture.callWithApproval("brokerage_order_place",
                arguments, trader);
        String orderId = DescriptorCodec.tree(first.json()).path("data").path("orderId").asString();

        // The retry needs its own approval, because the gate is single-use; the token is what
        // makes the second call idempotent rather than a second order.
        InvocationPipeline.Outcome second = fixture.callWithApproval("brokerage_order_place",
                arguments, trader);
        assertThat(second.ok()).as("%s", second.json()).isTrue();
        JsonNode data = DescriptorCodec.tree(second.json()).path("data");
        assertThat(data.path("orderId").asString())
                .as("the same ticket must come back as the same order")
                .isEqualTo(orderId);
        assertThat(data.path("duplicateOfPreviousCall").asBoolean())
                .as("and the agent must be told, so it describes one order and not two")
                .isTrue();
    }

    @Test
    @DisplayName("a token cannot be spent on a different order than the one priced")
    void aTokenIsBoundToItsTerms() {
        String token = previewToken(BUY_50_AAPL);
        String changed = """
                {"accountId":"%s","symbol":"AAPL","side":"BUY","quantity":500,
                 "orderType":"MARKET","confirmationToken":"%s"}"""
                .formatted(CatalogFixture.MARGIN_ACCOUNT, token);
        InvocationPipeline.Outcome outcome = fixture.callWithApproval("brokerage_order_place",
                changed, trader);
        assertThat(outcome.errorCode()).isEqualTo(ErrorCode.INVALID_ARGUMENT);
        assertThat(DescriptorCodec.tree(outcome.json()).path("error").path("message").asString())
                .contains("does not match");
    }

    @Test
    @DisplayName("an expired confirmation is refused and the agent is told to price it again")
    void tokensExpire() {
        String arguments = place(previewToken(BUY_50_AAPL));
        fixture.moveClockTo("2026-10-01T14:35:00Z");
        InvocationPipeline.Outcome outcome = fixture.callWithApproval("brokerage_order_place",
                arguments, trader);
        assertThat(outcome.errorCode()).isEqualTo(ErrorCode.CONFIRMATION_EXPIRED);
        assertThat(DescriptorCodec.tree(outcome.json()).path("error").path("remediation").asString())
                .contains("brokerage_order_preview");
    }

    @Test
    @DisplayName("a preview that cannot be placed issues no token at all")
    void arefusedPreviewMintsNoAuthority() {
        JsonNode data = fixture.data("brokerage_order_preview", """
                {"accountId":"%s","symbol":"MSFT","side":"BUY","quantity":4000,
                 "orderType":"MARKET"}""".formatted(CatalogFixture.CASH_ACCOUNT), trader);
        assertThat(data.path("canPlace").asBoolean()).isFalse();
        assertThat(data.path("confirmationToken").isNull()).isTrue();
        assertThat(data.path("expiresAt").isNull()).isTrue();
    }

    @Test
    @DisplayName("a funding blocker offers the size that would work")
    void blockersOfferAWayForward() {
        JsonNode data = fixture.data("brokerage_order_preview", """
                {"accountId":"%s","symbol":"MSFT","side":"BUY","quantity":4000,
                 "orderType":"MARKET"}""".formatted(CatalogFixture.CASH_ACCOUNT), trader);
        JsonNode funding = data.path("blockers").values().stream()
                .filter(blocker -> blocker.path("code").asString()
                        .equals("INSUFFICIENT_BUYING_POWER"))
                .findFirst().orElseThrow();
        assertThat(funding.path("maxPermissibleQuantity").asInt())
                .as("a dead end is a worse answer than a smaller order")
                .isPositive();
    }

    @Test
    @DisplayName("selling more than is held is blocked, and not reinterpreted as a short sale")
    void overSellingIsBlocked() {
        JsonNode data = fixture.data("brokerage_order_preview", """
                {"accountId":"%s","symbol":"MSFT","side":"SELL","quantity":500,
                 "orderType":"MARKET"}""".formatted(CatalogFixture.MARGIN_ACCOUNT), trader);
        assertThat(data.path("canPlace").asBoolean()).isFalse();
        JsonNode blocker = data.path("blockers").path(0);
        assertThat(blocker.path("code").asString()).isEqualTo("INSUFFICIENT_SHARES");
        assertThat(blocker.path("maxPermissibleQuantity").asInt()).isEqualTo(85);
        assertThat(blocker.path("message").asString()).contains("SELL_SHORT");
    }

    @Test
    @DisplayName("a retirement account cannot sell short, whatever the size")
    void retirementAccountsCannotShort() {
        JsonNode data = fixture.data("brokerage_order_preview", """
                {"accountId":"%s","symbol":"TSLA","side":"SELL_SHORT","quantity":10,
                 "orderType":"MARKET"}""".formatted(CatalogFixture.ROTH_ACCOUNT), trader);
        assertThat(data.path("canPlace").asBoolean()).isFalse();
        assertThat(data.path("blockers").path(0).path("code").asString())
                .isEqualTo("SHORTING_NOT_PERMITTED");
        assertThat(data.path("blockers").path(0).path("maxPermissibleQuantity").isNull())
                .as("there is no smaller short that would be allowed")
                .isTrue();
    }

    @Test
    @DisplayName("a frozen account is refused before anything is priced")
    void aFrozenAccountCannotTrade() {
        JsonNode error = fixture.error("brokerage_order_preview", """
                {"accountId":"%s","symbol":"KO","side":"BUY","quantity":10,
                 "orderType":"MARKET"}""".formatted(CatalogFixture.FROZEN_ACCOUNT), trader);
        assertThat(error.path("code").asString()).isEqualTo(ErrorCode.PRECONDITION_FAILED.name());
        assertThat(error.path("remediation").asString())
                .as("the agent must not shop the order around the customer's other accounts")
                .contains("Do not try another account");
    }

    @Test
    @DisplayName("a limit order with no price is refused with the field named")
    void crossFieldRulesAreEnforcedWhereTheSchemaCannot() {
        JsonNode error = fixture.error("brokerage_order_preview", """
                {"accountId":"%s","symbol":"MSFT","side":"BUY","quantity":25,
                 "orderType":"LIMIT"}""".formatted(CatalogFixture.MARGIN_ACCOUNT), trader);
        assertThat(error.path("code").asString()).isEqualTo(ErrorCode.INVALID_ARGUMENT.name());
        assertThat(error.path("message").asString()).contains("LIMIT", "limitPrice");
    }

    @Test
    @DisplayName("a market order carrying a limit price is refused rather than quietly reshaped")
    void aMarketOrderMayNotCarryALimit() {
        JsonNode error = fixture.error("brokerage_order_preview", """
                {"accountId":"%s","symbol":"MSFT","side":"BUY","quantity":25,
                 "orderType":"MARKET","limitPrice":"500.00"}"""
                .formatted(CatalogFixture.MARGIN_ACCOUNT), trader);
        assertThat(error.path("code").asString()).isEqualTo(ErrorCode.INVALID_ARGUMENT.name());
    }

    @Test
    @DisplayName("a market order is refused outside the regular session, with the reason given")
    void marketOrdersNeedAMarket() {
        fixture.moveClockTo("2026-10-02T02:00:00Z");   // 22:00 in New York: closed
        JsonNode data = fixture.data("brokerage_order_preview", BUY_50_AAPL, trader);
        assertThat(data.path("canPlace").asBoolean()).isFalse();
        assertThat(data.path("session").asString()).isEqualTo("CLOSED");
        assertThat(data.path("blockers").path(0).path("code").asString()).isEqualTo("MARKET_CLOSED");
        assertThat(data.path("blockers").path(0).path("message").asString())
                .as("'the market is closed' is half an answer")
                .contains("next opens");
    }

    @Test
    @DisplayName("cancelling needs no preview, and reports the order's real state")
    void cancellingIsItsOwnAsymmetry() {
        String arguments = """
                {"accountId":"%s","orderId":"%s"}"""
                .formatted(CatalogFixture.MARGIN_ACCOUNT, CatalogFixture.WORKING_MSFT_ORDER);
        InvocationPipeline.Outcome outcome = fixture.callWithApproval("brokerage_order_cancel",
                arguments, trader);
        assertThat(outcome.ok()).as("%s", outcome.json()).isTrue();
        JsonNode data = DescriptorCodec.tree(outcome.json()).path("data");
        assertThat(data.path("cancelled").asBoolean()).isTrue();
        assertThat(data.path("status").asString()).isEqualTo("CANCELLED");
        assertThat(data.path("explanation").asString()).isNotBlank();
    }

    @Test
    @DisplayName("cancelling an order that already filled is a success that reports a fill")
    void aLostRaceIsReportedHonestly() {
        String arguments = """
                {"accountId":"%s","orderId":"%s"}"""
                .formatted(CatalogFixture.MARGIN_ACCOUNT, CatalogFixture.FILLED_NVDA_ORDER);
        InvocationPipeline.Outcome outcome = fixture.callWithApproval("brokerage_order_cancel",
                arguments, trader);
        assertThat(outcome.ok())
                .as("nothing failed: the call worked and the answer is that the order filled")
                .isTrue();
        JsonNode data = DescriptorCodec.tree(outcome.json()).path("data");
        assertThat(data.path("cancelled").asBoolean()).isFalse();
        assertThat(data.path("status").asString()).isEqualTo("FILLED");
        assertThat(data.path("explanation").asString())
                .contains("filled in full", "owns the shares");
    }

    @Test
    @DisplayName("a replacement keeps the order's identifier")
    void replacementKeepsTheIdentifier() {
        String previewArguments = """
                {"accountId":"%s","symbol":"MSFT","side":"BUY","quantity":25,
                 "orderType":"LIMIT","limitPrice":"505.00","replacesOrderId":"%s"}"""
                .formatted(CatalogFixture.MARGIN_ACCOUNT, CatalogFixture.WORKING_MSFT_ORDER);
        String token = previewToken(previewArguments);
        InvocationPipeline.Outcome outcome = fixture.callWithApproval("brokerage_order_replace", """
                {"accountId":"%s","orderId":"%s","quantity":25,"limitPrice":"505.00",
                 "confirmationToken":"%s"}"""
                .formatted(CatalogFixture.MARGIN_ACCOUNT, CatalogFixture.WORKING_MSFT_ORDER, token),
                trader);
        assertThat(outcome.ok()).as("%s", outcome.json()).isTrue();
        JsonNode data = DescriptorCodec.tree(outcome.json()).path("data");
        assertThat(data.path("replaced").asBoolean()).isTrue();
        assertThat(data.path("orderId").asString())
                .as("a customer who was read an order number must still be able to ask about it")
                .isEqualTo(CatalogFixture.WORKING_MSFT_ORDER);
        assertThat(data.path("limitPrice").asString()).isEqualTo("505");
    }

    @Test
    @DisplayName("a replacement token cannot be spent on a different order")
    void aReplacementTokenIsBoundToItsOrder() {
        String token = previewToken("""
                {"accountId":"%s","symbol":"MSFT","side":"BUY","quantity":25,
                 "orderType":"LIMIT","limitPrice":"505.00","replacesOrderId":"%s"}"""
                .formatted(CatalogFixture.MARGIN_ACCOUNT, CatalogFixture.WORKING_MSFT_ORDER));
        InvocationPipeline.Outcome outcome = fixture.callWithApproval("brokerage_order_replace", """
                {"accountId":"%s","orderId":"%s","quantity":25,"limitPrice":"505.00",
                 "confirmationToken":"%s"}"""
                .formatted(CatalogFixture.MARGIN_ACCOUNT, "ord_799RE5N2G936K2H0", token), trader);
        assertThat(outcome.errorCode()).isEqualTo(ErrorCode.INVALID_ARGUMENT);
    }

    @Test
    @DisplayName("the audit trail records the approval reference against the order it authorized")
    void theAuditTrailTiesApprovalToAction() {
        String arguments = place(previewToken(BUY_50_AAPL));
        fixture.callWithApproval("brokerage_order_place", arguments, trader);
        var records = fixture.audit.forSession(trader.sessionId());
        var placements = records.stream()
                .filter(record -> record.tool().equals("brokerage_order_place")).toList();
        assertThat(placements).hasSize(2);
        assertThat(placements.getFirst().outcome()).isEqualTo("APPROVAL_REQUIRED");
        assertThat(placements.getLast().outcome()).isEqualTo("OK");
        assertThat(placements.getLast().approval())
                .as("an auditor must be able to join the action to the approval")
                .isEqualTo(placements.getFirst().approval())
                .isNotNull();
        assertThat(placements.getLast().retention()).isEqualTo("P6Y");
        assertThat(placements.getLast().riskTier()).isEqualTo(3);
    }
}
