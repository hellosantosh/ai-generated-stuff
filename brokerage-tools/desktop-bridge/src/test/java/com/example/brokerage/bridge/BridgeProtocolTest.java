package com.example.brokerage.bridge;

import java.util.List;
import java.util.Map;

import com.example.brokerage.domain.Accounts;
import com.example.brokerage.tooling.contract.DescriptorCodec;
import com.example.brokerage.tooling.contract.Principal;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.SpringBootConfiguration;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.context.annotation.ComponentScan;
import org.springframework.context.annotation.Import;

import tools.jackson.databind.JsonNode;

import static org.assertj.core.api.Assertions.assertThat;

/**
 * The desktop connector's protocol, driven one request at a time rather than through a pipe.
 *
 * <p>Two things are being asserted and they are different in kind. The first is that the manifest
 * is a <em>projection</em>: three fields and no more, so that nothing from the governance block can
 * leak into a model's context by accident. The second is that the connector is not a privileged
 * path — it goes through the same pipeline as everything else, which means a tier 3 tool
 * stops at the approval gate even here.
 */
@SpringBootTest(classes = BridgeProtocolTest.Context.class)
class BridgeProtocolTest {

    /**
     * The protocol and what it needs, without the transport.
     *
     * <p>Booting {@code DesktopBridgeApplication} itself would start {@link BridgeLoop}, which
     * blocks reading standard input and hangs the suite — which is a fair argument that the
     * two belonged in separate classes in the first place.
     */
    @SpringBootConfiguration
    @ComponentScan(basePackages = {"com.example.brokerage.catalog", "com.example.brokerage.domain"})
    @Import(BridgeProtocol.class)
    static class Context {
    }

    @Autowired
    BridgeProtocol bridge;

    private static Principal principal(boolean trading) {
        return new Principal("test:desktop", Accounts.DEMO_CUSTOMER,
                BridgeProtocol.entitlements(trading), "desktop-test");
    }

    private JsonNode call(String request, Principal who) {
        return DescriptorCodec.tree(DescriptorCodec.json(bridge.handle(request, who)));
    }

    @Test
    @DisplayName("the manifest carries exactly name, description and input_schema")
    void theManifestIsAProjection() {
        List<Map<String, Object>> tools = bridge.manifest(principal(true));
        assertThat(tools).hasSize(14);
        for (Map<String, Object> tool : tools) {
            assertThat(tool.keySet())
                    .as("%s", tool.get("name"))
                    .containsExactly("name", "description", "input_schema");
        }
    }

    @Test
    @DisplayName("nothing from the governance block reaches the model")
    void governanceDoesNotLeak() {
        String published = DescriptorCodec.json(bridge.manifest(principal(true)));
        assertThat(published)
                .doesNotContain("riskTier", "entitlements", "killSwitch", "retention",
                        "confirmationTemplate", "conformanceLevel", "outputSchema",
                        "readOnlyHint", "_meta");
    }

    @Test
    @DisplayName("the trading flag is the only thing that changes what is published")
    void theFlagDecidesTheCatalog() {
        assertThat(bridge.manifest(principal(false))).hasSize(10);
        assertThat(bridge.manifest(principal(true))).hasSize(14);
    }

    @Test
    @DisplayName("list_tools hands over the manifest, the briefing and the conduct prompt")
    void listToolsCarriesEverythingASessionNeeds() {
        JsonNode response = call("""
                {"id":1,"method":"list_tools"}""", principal(false));
        assertThat(response.path("id").asInt()).isEqualTo(1);
        assertThat(response.path("result").path("tools").size()).isEqualTo(10);
        assertThat(response.path("result").path("briefing").asString())
                .contains("Identifiers you cannot invent");
        assertThat(response.path("result").path("conduct").asString())
                .contains("You do not give investment advice");
    }

    @Test
    @DisplayName("call_tool goes through the pipeline and returns the envelope")
    void callToolReturnsAnEnvelope() {
        JsonNode response = call("""
                {"id":2,"method":"call_tool","params":{"name":"brokerage_quotes_get",
                 "arguments":{"symbols":["AAPL","BRK.B"]}}}""", principal(false));
        JsonNode result = response.path("result");
        assertThat(result.path("ok").asBoolean()).isTrue();
        assertThat(result.path("data").path("quotes").size()).isEqualTo(2);
        assertThat(result.path("meta").path("requestId").asString()).startsWith("req_");
    }

    @Test
    @DisplayName("the same schema validation applies here as anywhere else")
    void theConnectorIsNotABackDoor() {
        JsonNode response = call("""
                {"id":3,"method":"call_tool","params":{"name":"brokerage_quotes_get",
                 "arguments":{"symbols":["not a ticker"]}}}""", principal(false));
        assertThat(response.path("result").path("error").path("code").asString())
                .isEqualTo("INVALID_ARGUMENT");
    }

    @Test
    @DisplayName("a read-only session cannot see the trading tools at all")
    void readOnlySessionsCannotTrade() {
        JsonNode response = call("""
                {"id":4,"method":"call_tool","params":{"name":"brokerage_order_cancel",
                 "arguments":{"accountId":"acct_4RZ61FZAD0RMHNMM",
                              "orderId":"ord_CZ9R91DVFPMN5BX0"}}}""", principal(false));
        assertThat(response.path("result").path("error").path("code").asString())
                .isEqualTo("UNKNOWN_TOOL");
    }

    @Test
    @DisplayName("a trading session still stops at the approval gate, and the gate can be answered")
    void theApprovalLoopCloses() {
        Principal trader = principal(true);
        String cancel = """
                {"id":5,"method":"call_tool","params":{"name":"brokerage_order_cancel",
                 "arguments":{"accountId":"acct_4RZ61FZAD0RMHNMM",
                              "orderId":"ord_CZ9R91DVFPMN5BX0"}}}""";

        JsonNode first = call(cancel, trader);
        assertThat(first.path("result").path("error").path("code").asString())
                .isEqualTo("APPROVAL_REQUIRED");
        String reference = first.path("result").path("error").path("approvalReference").asString();
        assertThat(reference).startsWith("apr_");

        JsonNode pending = call("""
                {"id":6,"method":"pending_approvals"}""", trader);
        assertThat(pending.path("result").path("pending").size()).isEqualTo(1);
        assertThat(pending.path("result").path("pending").path(0).path("summary").asString())
                .as("the person at the desktop has to see what they are agreeing to")
                .doesNotContain("(not given)")
                .contains("MSFT");

        call("{\"id\":7,\"method\":\"approve\",\"params\":{\"reference\":\"" + reference + "\"}}",
                trader);

        JsonNode second = call(cancel, trader);
        assertThat(second.path("result").path("ok").asBoolean()).isTrue();
        assertThat(second.path("result").path("data").path("cancelled").asBoolean()).isTrue();
    }

    @Test
    @DisplayName("an unknown method lists the ones that exist, rather than failing silently")
    void unknownMethodsAreHelpful() {
        JsonNode response = call("""
                {"id":8,"method":"sell_everything"}""", principal(true));
        assertThat(response.path("error").path("code").asString()).isEqualTo("UNKNOWN_METHOD");
        assertThat(DescriptorCodec.json(response)).contains("list_tools", "call_tool", "approve");
    }

    @Test
    @DisplayName("a line that is not JSON does not take the connector down")
    void malformedInputIsSurvivable() {
        JsonNode response = call("this is not json", principal(false));
        assertThat(response.path("error").path("code").asString()).isEqualTo("BAD_REQUEST");
    }
}
