package com.example.brokerage.tooling.contract;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import static org.assertj.core.api.Assertions.assertThat;

/**
 * The taxonomy is closed and every code has to be usable by an agent without further explanation.
 */
class ErrorTaxonomyTest {

    @Test
    @DisplayName("fifteen codes, and the list is closed")
    void theTaxonomyIsClosed() {
        assertThat(ErrorCode.values()).hasSize(15);
    }

    @Test
    @DisplayName("every code tells the agent what to do, in the imperative")
    void everyCodeCarriesGuidance() {
        for (ErrorCode code : ErrorCode.values()) {
            assertThat(code.guidance()).as("%s", code).isNotBlank().hasSizeGreaterThan(50);
        }
    }

    @Test
    @DisplayName("a terminal code is neither retryable nor the caller's to fix")
    void terminalMeansStop() {
        assertThat(ErrorCode.NOT_ENTITLED.terminal()).isTrue();
        assertThat(ErrorCode.PRECONDITION_FAILED.terminal()).isTrue();
        assertThat(ErrorCode.INVALID_ARGUMENT.terminal()).isFalse();
        assertThat(ErrorCode.RATE_LIMITED.terminal()).isFalse();
    }

    @Test
    @DisplayName("the codes an agent must not loop on say so")
    void nothingTellsTheAgentToLoop() {
        assertThat(ErrorCode.NOT_ENTITLED.retryable()).isFalse();
        assertThat(ErrorCode.QUOTA_EXCEEDED.retryable()).isFalse();
        assertThat(ErrorCode.CONFIRMATION_EXPIRED.retryable()).isFalse();
    }

    @Test
    @DisplayName("an ambiguous request is the caller's to resolve, by asking")
    void ambiguityIsResolvedByAsking() {
        assertThat(ErrorCode.AMBIGUOUS_REQUEST.callerCanFix()).isTrue();
        assertThat(ErrorCode.AMBIGUOUS_REQUEST.guidance()).contains("ask");
    }
}
