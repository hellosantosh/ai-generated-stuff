package com.example.brokerage.api;

import static com.example.brokerage.api.ApiTest.Tokens.ACCOUNTS;
import static com.example.brokerage.api.ApiTest.Tokens.ALL;
import static com.example.brokerage.api.ApiTest.Tokens.ORDERS_READ;
import static com.example.brokerage.api.ApiTest.Tokens.alice;
import static com.example.brokerage.api.ApiTest.Tokens.bob;
import static org.assertj.core.api.Assertions.assertThat;

import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.hateoas.MediaTypes;
import org.springframework.http.HttpStatus;
import org.springframework.test.web.servlet.assertj.MockMvcTester;
import org.springframework.test.web.servlet.assertj.MvcTestResult;

/**
 * Discovery, link relations and the security boundary: what a client can learn, and
 * what it is allowed to see.
 */
@ApiTest
class HypermediaTest {

    static final String PROBLEMS = "https://docs.brokerage.example/problems/";

    @Autowired
    MockMvcTester mvc;

    @Test
    void theRootIsPublicAndLinksToEverything() {
        MvcTestResult root = mvc.get().uri("/").exchange();
        assertThat(root).hasStatusOk().hasContentType(MediaTypes.HAL_JSON);
        assertThat(root).bodyJson().extractingPath("$._links['bk:accounts'].href").asString()
                .endsWith("/accounts");
        assertThat(root).bodyJson().extractingPath("$._links['bk:instruments'].templated").isEqualTo(true);
        assertThat(root).bodyJson().extractingPath("$._links['service-desc'].href").asString()
                .endsWith("/openapi.yaml");
    }

    @Test
    void theContractIsServedBesideTheApi() {
        assertThat(mvc.get().uri("/openapi.yaml")).hasStatusOk().bodyText().contains("openapi: 3.2.1");
    }

    @Test
    void protectedResourceMetadataNamesTheAuthorizationServer() {
        assertThat(mvc.get().uri("/.well-known/oauth-protected-resource")).hasStatusOk()
                .bodyJson().extractingPath("$.authorization_servers[0]").isEqualTo("http://localhost:9000");
    }

    @Test
    void aRequestWithoutATokenGetsAProblemAndAChallenge() {
        MvcTestResult result = mvc.get().uri("/accounts").exchange();
        assertThat(result).hasStatus(HttpStatus.UNAUTHORIZED).hasContentType("application/problem+json");
        assertThat(result.getResponse().getHeader("WWW-Authenticate")).startsWith("Bearer")
                .contains("resource_metadata=");
        assertThat(result).bodyJson().extractingPath("$.type").isEqualTo(PROBLEMS + "unauthorized");
    }

    @Test
    void aTokenWithoutTheScopeIsForbidden() {
        assertThat(mvc.get().uri("/accounts").with(alice(ORDERS_READ)))
                .hasStatus(HttpStatus.FORBIDDEN)
                .bodyJson().extractingPath("$.type").isEqualTo(PROBLEMS + "insufficient-scope");
    }

    @Test
    void anotherCustomersAccountDoesNotExist() {
        assertThat(mvc.get().uri("/accounts/ACC-1001").with(bob(ALL)))
                .hasStatus(HttpStatus.NOT_FOUND)
                .bodyJson().extractingPath("$.type").isEqualTo(PROBLEMS + "not-found");
    }

    @Test
    void linksReflectWhatTheTokenMayDo() {
        // Read-only: accounts are visible, but orders are not even mentioned.
        MvcTestResult readOnly = mvc.get().uri("/accounts/ACC-1001").with(alice(ACCOUNTS)).exchange();
        assertThat(readOnly).hasStatusOk();
        assertThat(readOnly).bodyJson()
                .hasPath("$._links['bk:positions']")
                .doesNotHavePath("$._links['bk:orders']");

        // A full trading token, asking for HAL-FORMS: the placeOrder template appears.
        MvcTestResult trading = mvc.get().uri("/accounts/ACC-1001").with(alice(ALL))
                .accept(MediaTypes.HAL_FORMS_JSON).exchange();
        assertThat(trading).hasStatusOk();
        assertThat(trading).bodyJson().hasPath("$._links['bk:orders']");
        assertThat(trading).bodyJson().extractingPath("$._templates.placeOrder.method").isEqualTo("POST");
    }

    @Test
    void anOptionChainOffersEveryExpirationAsANamedLink() {
        MvcTestResult chain = mvc.get().uri("/instruments/EQ-AAPL/option-chain").with(alice(ALL)).exchange();
        assertThat(chain).hasStatusOk();
        assertThat(chain).bodyJson().extractingPath("$._links['bk:expiration']").asArray().hasSize(2);
        assertThat(chain).bodyJson().extractingPath("$._embedded['bk:instruments']").asArray().hasSize(6);
    }
}
