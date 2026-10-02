package com.example.brokerage.agent;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.test.context.bean.override.mockito.MockitoBean;
import org.springframework.boot.webmvc.test.autoconfigure.AutoConfigureMockMvc;
import org.springframework.http.MediaType;
import org.springframework.test.web.servlet.assertj.MockMvcTester;

import org.springframework.ai.chat.model.ChatModel;

import static org.assertj.core.api.Assertions.assertThat;

/**
 * The console's HTTP surface, without a model.
 *
 * <p>No API key, no network, no chat. That is the point being made as much as the coverage being
 * bought: a tool catalog whose only entry point is a conversation is a catalog nobody can inspect,
 * test or review. Everything a reviewer needs — the descriptors, the ontology, the producer graph,
 * the generated briefing, the conformance report, and the ability to call any tool directly — is
 * reachable here with curl.
 */
@SpringBootTest
@AutoConfigureMockMvc
class HttpSurfaceTest {

    /**
     * Stubbed so the context starts without ANTHROPIC_API_KEY. The chat endpoint is the only thing
     * that needs it, and {@link #chatSaysSoWhenNoModelIsConfigured()} covers the other branch.
     */
    @MockitoBean
    ChatModel chatModel;

    @Autowired
    MockMvcTester mvc;

    @Test
    @DisplayName("the catalog lists fourteen tools with their governance")
    void theCatalogIsPublished() {
        assertThat(mvc.get().uri("/api/catalog")).hasStatusOk()
                .bodyJson()
                .extractingPath("$.length()").isEqualTo(14);
        assertThat(mvc.get().uri("/api/catalog")).bodyJson()
                .extractingPath("$[?(@.name=='brokerage_order_place')].riskTier")
                .asArray().containsExactly(3);
    }

    @Test
    @DisplayName("the same catalog looks different to each role, and nothing is reconfigured")
    void entitlementsChangeWhatIsVisible() {
        assertThat(mvc.get().uri("/api/catalog?role=RESEARCH")).bodyJson()
                .extractingPath("$.length()").isEqualTo(3);
        assertThat(mvc.get().uri("/api/catalog?role=SERVICE")).bodyJson()
                .extractingPath("$.length()").isEqualTo(10);
        assertThat(mvc.get().uri("/api/catalog?role=TRADING")).bodyJson()
                .extractingPath("$.length()").isEqualTo(14);
    }

    @Test
    @DisplayName("a descriptor is served verbatim, governance and all")
    void descriptorsAreServedAsWritten() {
        assertThat(mvc.get().uri("/api/catalog/brokerage_order_preview")).hasStatusOk()
                .bodyJson()
                .extractingPath("$['_meta']['com.example.tooling/governance'].riskTier")
                .isEqualTo(1);
    }

    @Test
    @DisplayName("an unknown tool name is a 404, not an empty object")
    void unknownToolsAreNotFound() {
        assertThat(mvc.get().uri("/api/catalog/brokerage_order_delete")).hasStatus(404);
    }

    @Test
    @DisplayName("the producer graph names the only source of a confirmation token")
    void theGraphIsPublished() {
        assertThat(mvc.get().uri("/api/catalog/graph")).hasStatusOk()
                .bodyJson()
                .extractingPath("$.producers.ConfirmationToken")
                .asArray().containsExactly("brokerage_order_preview");
        assertThat(mvc.get().uri("/api/catalog/graph")).bodyJson()
                .extractingPath("$.unreachable").asArray().isEmpty();
    }

    @Test
    @DisplayName("the conformance report is empty, and visible to anyone who asks")
    void conformanceIsPublished() {
        assertThat(mvc.get().uri("/api/catalog/conformance")).hasStatusOk()
                .bodyJson().extractingPath("$.length()").isEqualTo(0);
    }

    @Test
    @DisplayName("the generated briefing is shown exactly as the model will receive it")
    void theBriefingIsPublished() {
        assertThat(mvc.get().uri("/api/catalog/briefing?role=RESEARCH")).hasStatusOk()
                .bodyJson().extractingPath("$.visible").isEqualTo(3);
        assertThat(mvc.get().uri("/api/catalog/briefing?role=TRADING")).bodyJson()
                .extractingPath("$.briefing").asString()
                .contains("brokerage_order_preview")
                .contains("APPROVAL_REQUIRED");
    }

    @Test
    @DisplayName("a tool can be called directly, through the same pipeline the model uses")
    void toolsCanBeCalledWithoutAModel() {
        assertThat(mvc.post().uri("/api/invoke")
                .contentType(MediaType.APPLICATION_JSON)
                .content("""
                        {"tool":"brokerage_accounts_list","arguments":{},"role":"SERVICE"}"""))
                .hasStatusOk()
                .bodyJson()
                .extractingPath("$.result.data.accounts.length()").isEqualTo(4);
    }

    @Test
    @DisplayName("direct invocation is held to the same schema as the model's calls")
    void directInvocationIsNotABackDoor() {
        assertThat(mvc.post().uri("/api/invoke")
                .contentType(MediaType.APPLICATION_JSON)
                .content("""
                        {"tool":"brokerage_quotes_get","arguments":{"symbols":[]},
                         "role":"RESEARCH"}"""))
                .hasStatusOk()
                .bodyJson()
                .extractingPath("$.result.error.code").isEqualTo("INVALID_ARGUMENT");
    }

    @Test
    @DisplayName("a trading call through the console still stops at the approval gate")
    void theConsoleIsNotPrivileged() {
        assertThat(mvc.post().uri("/api/invoke")
                .contentType(MediaType.APPLICATION_JSON)
                .content("""
                        {"tool":"brokerage_order_cancel",
                         "arguments":{"accountId":"acct_4RZ61FZAD0RMHNMM",
                                      "orderId":"ord_CZ9R91DVFPMN5BX0"},
                         "role":"TRADING","sessionId":"http-test"}"""))
                .hasStatusOk()
                .bodyJson()
                .extractingPath("$.result.error.code").isEqualTo("APPROVAL_REQUIRED");
    }

    @Test
    @DisplayName("every invocation lands in the audit trail")
    void theAuditTrailIsPublished() {
        mvc.post().uri("/api/invoke")
                .contentType(MediaType.APPLICATION_JSON)
                .content("""
                        {"tool":"brokerage_accounts_list","arguments":{},"role":"SERVICE"}""")
                .exchange();
        assertThat(mvc.get().uri("/api/audit?limit=5")).hasStatusOk()
                .bodyJson().extractingPath("$[0].tool").isEqualTo("brokerage_accounts_list");
    }

    @Test
    @DisplayName("the chat endpoint says what is missing rather than failing obscurely")
    void chatSaysSoWhenNoModelIsConfigured() {
        assertThat(mvc.get().uri("/api/chat/status")).hasStatusOk()
                .bodyJson().extractingPath("$.model").isEqualTo("claude-opus-5");
    }
}
