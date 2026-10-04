package com.example.brokerage.tooling.contract;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import static org.assertj.core.api.Assertions.assertThat;

/**
 * The profile is a whitelist, and these tests exist to stop it quietly becoming a blacklist.
 *
 * <p>The failure mode they guard against is specific: somebody needs {@code oneOf} for one tool,
 * adds it to {@code PERMITTED} because the linter was in the way, and six months later a quarter
 * of the catalog uses alternation that two of the four runtimes silently drop.
 */
class SchemaProfileTest {

    @Test
    @DisplayName("alternation and conditionals are prohibited, with a reason the author can act on")
    void prohibitionsCarryReasons() {
        for (String keyword : new String[] {"oneOf", "anyOf", "allOf", "not", "if", "then", "else"}) {
            SchemaProfile.Prohibition prohibition = SchemaProfile.prohibitionFor(keyword);
            assertThat(prohibition).as("%s must be prohibited", keyword).isNotNull();
            assertThat(prohibition.why())
                    .as("%s must say what to do instead, not just that it is banned", keyword)
                    .isNotBlank()
                    .hasSizeGreaterThan(40);
        }
    }

    @Test
    @DisplayName("nothing is both permitted and prohibited")
    void theTwoListsDoNotOverlap() {
        for (SchemaProfile.Prohibition prohibition : SchemaProfile.PROHIBITED) {
            assertThat(SchemaProfile.PERMITTED).doesNotContain(prohibition.keyword());
        }
    }

    @Test
    @DisplayName("identity and override parameter names are reserved")
    void identityCannotBeAnArgument() {
        assertThat(SchemaProfile.RESERVED_PARAMETERS)
                .contains("customerId", "userId", "onBehalfOf", "impersonate")
                .as("a model must never be able to set its own identity")
                .contains("principal", "token", "scopes")
                .as("nor talk its way past a check")
                .contains("dryRun", "force", "skipValidation", "override", "sudo");
    }

    @Test
    @DisplayName("accountId is not reserved, because choosing between a customer's own accounts "
            + "is exactly what the model is for")
    void theLineIsDrawnAtIdentityNotAtScope() {
        assertThat(SchemaProfile.RESERVED_PARAMETERS)
                .doesNotContain("accountId", "orderId", "symbol");
    }
}
