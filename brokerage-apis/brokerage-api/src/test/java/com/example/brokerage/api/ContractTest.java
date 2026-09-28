package com.example.brokerage.api;

import static com.example.brokerage.api.ApiTest.Tokens.ALL;
import static com.example.brokerage.api.ApiTest.Tokens.alice;
import static org.assertj.core.api.Assertions.assertThat;

import java.io.IOException;
import java.io.InputStream;
import java.util.List;
import java.util.Map;
import java.util.UUID;

import com.networknt.schema.Error;
import com.networknt.schema.SchemaLocation;
import com.networknt.schema.SchemaRegistry;
import com.networknt.schema.SpecificationVersion;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.json.JsonMapper;
import tools.jackson.databind.node.ObjectNode;
import tools.jackson.dataformat.yaml.YAMLMapper;

import org.junit.jupiter.api.BeforeAll;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.core.io.ClassPathResource;
import org.springframework.http.MediaType;
import org.springframework.test.web.servlet.assertj.MockMvcTester;
import org.springframework.test.web.servlet.assertj.MvcTestResult;

/**
 * The OpenAPI document is a contract, so it is tested like code: it must be a valid
 * OpenAPI 3.2 document, its examples must match its own schemas, and real responses from
 * the running application must match the schemas it promises.
 */
@ApiTest
class ContractTest {

    static final String CONTRACT = "https://api.brokerage.example/openapi.yaml";
    static final String OAS_32 = "https://spec.openapis.org/oas/3.2/schema/2025-09-17";

    static JsonNode contract;
    static SchemaRegistry schemas;

    @Autowired
    MockMvcTester mvc;

    @BeforeAll
    static void loadTheContract() throws IOException {
        try (InputStream yaml = new ClassPathResource("contract/brokerage-api.yaml").getInputStream();
                InputStream meta = new ClassPathResource("oas-3.2-schema.json").getInputStream()) {
            contract = YAMLMapper.builder().build().readTree(yaml);
            String metaSchema = new String(meta.readAllBytes());
            String contractJson = JsonMapper.builder().build().writeValueAsString(contract);
            schemas = SchemaRegistry.withDefaultDialect(SpecificationVersion.DRAFT_2020_12,
                    registry -> registry.schemas(Map.of(CONTRACT, contractJson, OAS_32, metaSchema)));
        }
    }

    @Test
    void theContractIsAValidOpenApi32Document() {
        List<Error> errors = schemas.getSchema(SchemaLocation.of(OAS_32)).validate(contract);
        assertThat(errors).as("violations of the OpenAPI 3.2 meta-schema").isEmpty();
    }

    @Test
    void theMetaSchemaCheckCatchesABrokenContract() {
        ObjectNode broken = (ObjectNode) contract.deepCopy();
        broken.remove("info");
        broken.put("openapi", "3.2");
        assertThat(schemas.getSchema(SchemaLocation.of(OAS_32)).validate(broken)).isNotEmpty();
    }

    @Test
    void everyExampleMatchesItsOwnSchema() {
        JsonNode placeOrder = contract.at("/paths/~1accounts~1{accountId}~1orders/post/requestBody/content/"
                + "application~1json/examples/limitBuy/dataValue");
        assertMatches("OrderRequest", placeOrder);
        for (String response : List.of("BadRequest", "UnprocessableContent")) {
            contract.at("/components/responses/" + response + "/content/application~1problem+json/examples")
                    .forEach(example -> assertMatches("Problem", example.get("dataValue")));
        }
    }

    @Test
    void readOnlyResourcesMatchTheirSchemas() throws Exception {
        assertMatches("ApiRoot", get("/"));
        assertMatches("AccountCollection", get("/accounts"));
        assertMatches("Account", get("/accounts/ACC-1001"));
        assertMatches("Balances", get("/accounts/ACC-1001/balances"));
        assertMatches("PositionCollection", get("/accounts/ACC-1001/positions"));
        assertMatches("Position", get("/accounts/ACC-1001/positions/EQ-AAPL"));
        assertMatches("TransactionPage", get("/accounts/ACC-1001/transactions?limit=5"));
        assertMatches("InstrumentCollection", get("/instruments?type=EQUITY"));
        assertMatches("InstrumentCollection", get("/instruments/EQ-AAPL/option-chain"));
        assertMatches("QuoteV1", get("/instruments/EQ-AAPL/quote"));
        assertMatches("OrderPage", get("/accounts/ACC-1001/orders"));
        assertMatches("ProtectedResourceMetadata", get("/.well-known/oauth-protected-resource"));
    }

    @Test
    void ordersPreviewsAndProblemsMatchTheirSchemas() throws Exception {
        MvcTestResult placed = mvc.post().uri("/accounts/ACC-1001/orders").with(alice(ALL))
                .header("Idempotency-Key", UUID.randomUUID().toString())
                .contentType(MediaType.APPLICATION_JSON)
                .content("""
                        {"instrumentId": "EQ-AAPL", "side": "BUY", "type": "LIMIT", "quantity": "1",
                         "limitPrice": "200.00", "clientOrderId": "contract-test"}""")
                .exchange();
        assertMatches("Order", json(placed));

        MvcTestResult preview = mvc.post().uri("/accounts/ACC-1001/order-previews").with(alice(ALL))
                .contentType(MediaType.APPLICATION_JSON)
                .content("""
                        {"instrumentId": "EQ-AAPL", "side": "BUY", "type": "MARKET", "quantity": "3"}""")
                .exchange();
        assertMatches("OrderPreview", json(preview));

        MvcTestResult problem = mvc.post().uri("/accounts/ACC-1001/orders").with(alice(ALL))
                .header("Idempotency-Key", UUID.randomUUID().toString())
                .contentType(MediaType.APPLICATION_JSON)
                .content("""
                        {"instrumentId": "EQ-HALT", "side": "BUY", "type": "MARKET", "quantity": "1"}""")
                .exchange();
        assertThat(problem).hasStatus(422);
        assertMatches("Problem", json(problem));

        MvcTestResult v2 = mvc.get().uri("/instruments/EQ-AAPL/quote").header("API-Version", "2")
                .with(alice(ALL)).exchange();
        assertMatches("Quote", json(v2));
    }

    // ---------------------------------------------------------------- helpers

    private JsonNode get(String uri) throws Exception {
        MvcTestResult result = mvc.get().uri(uri).with(alice(ALL)).exchange();
        assertThat(result).as(uri).hasStatusOk();
        return json(result);
    }

    private static JsonNode json(MvcTestResult result) throws Exception {
        return JsonMapper.builder().build().readTree(result.getResponse().getContentAsString());
    }

    private static void assertMatches(String schemaName, JsonNode instance) {
        SchemaLocation location = SchemaLocation.of(CONTRACT + "#/components/schemas/" + schemaName);
        List<Error> errors = schemas.getSchema(location).validate(instance);
        assertThat(errors).as("%s does not match the contract:%n%s", schemaName, instance).isEmpty();
    }
}
