package com.example.brokerage.catalog;

import java.util.List;
import java.util.Set;
import java.util.TreeSet;

import com.example.brokerage.tooling.contract.ConformanceLinter;
import com.example.brokerage.tooling.contract.Finding;
import com.example.brokerage.tooling.contract.Governance;
import com.example.brokerage.tooling.contract.Ontology;
import com.example.brokerage.tooling.contract.ToolDescriptor;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import static org.assertj.core.api.Assertions.assertThat;

/**
 * The conformance gate. This is the test that makes the specification a specification rather than
 * a document, and it is the reason the other tests in this project can be about behavior instead
 * of about style.
 *
 * <p>It runs with no model, no network and no application context, in a few milliseconds, on every
 * commit. That is the whole argument for writing descriptors as data and rules as code.
 */
class CatalogConformanceTest {

    private final BrokerageCatalog catalog = new BrokerageCatalog();

    @Test
    @DisplayName("every descriptor conforms, with no warnings either")
    void theCatalogConforms() {
        List<Finding> findings = new ConformanceLinter(catalog.ontology())
                .lint(catalog.descriptors());
        assertThat(findings)
                .as("conformance findings:%n  %s",
                        String.join("\n  ", findings.stream().map(Finding::toString).toList()))
                .isEmpty();
    }

    @Test
    @DisplayName("the catalog and the ontology describe the same fourteen capabilities")
    void theOntologyAndTheCatalogAgree() {
        Set<String> descriptors = new TreeSet<>(
                catalog.descriptors().stream().map(ToolDescriptor::name).toList());
        Set<String> capabilities = new TreeSet<>(
                catalog.ontology().capabilities().stream().map(Ontology.Capability::tool).toList());
        assertThat(descriptors).isEqualTo(capabilities).hasSize(14);
    }

    @Test
    @DisplayName("every identifier a tool consumes is produced by some other tool")
    void nothingIsADeadEnd() {
        assertThat(catalog.ontology().unreachableIdentifiers())
                .as("an identifier nothing produces is an argument the agent can only guess at")
                .isEmpty();
    }

    @Test
    @DisplayName("only the preview tool can mint a confirmation token")
    void previewIsTheOnlyWayToTrade() {
        assertThat(catalog.ontology().producers().get("ConfirmationToken"))
                .containsExactly("brokerage_order_preview");
        for (String tool : List.of("brokerage_order_place", "brokerage_order_replace")) {
            assertThat(catalog.ontology().capabilityOf(tool).consumes())
                    .as("%s must require a token", tool)
                    .contains("ConfirmationToken");
            assertThat(catalog.ontology().prerequisitesOf(tool))
                    .contains("brokerage_order_preview");
        }
    }

    @Test
    @DisplayName("every tool that changes state is tier 2 or 3 and needs a human")
    void writingNeedsApproval() {
        for (ToolDescriptor descriptor : catalog.descriptors()) {
            if (descriptor.annotations().readOnlyHint()) {
                continue;
            }
            Governance governance = descriptor.governance();
            assertThat(governance.riskTier()).as("%s tier", descriptor.name())
                    .isGreaterThanOrEqualTo(2);
            assertThat(governance.requiresApproval()).as("%s approval", descriptor.name()).isTrue();
            assertThat(governance.approval().confirmationTemplate())
                    .as("%s must show the human what they are agreeing to", descriptor.name())
                    .isNotBlank();
            assertThat(governance.availability().killSwitch())
                    .as("%s must be switchable off", descriptor.name()).isNotBlank();
        }
    }

    @Test
    @DisplayName("ten of the fourteen tools are read-only, so an agent can orient itself safely")
    void theCatalogIsMostlyReads() {
        long readOnly = catalog.descriptors().stream()
                .filter(descriptor -> descriptor.annotations().readOnlyHint()).count();
        assertThat(readOnly).isEqualTo(11);
    }

    @Test
    @DisplayName("no description points the model at a tool that does not exist")
    void crossReferencesResolve() {
        Set<String> names = new TreeSet<>(
                catalog.descriptors().stream().map(ToolDescriptor::name).toList());
        for (ToolDescriptor descriptor : catalog.descriptors()) {
            for (String referenced : ConformanceLinter.referencedTools(descriptor.description())) {
                assertThat(names).as("%s points at %s", descriptor.name(), referenced)
                        .contains(referenced);
            }
        }
    }

    @Test
    @DisplayName("the conduct prompt is present and says nothing about individual tools")
    void conductIsPolicyNotDocumentation() {
        String conduct = catalog.conduct();
        assertThat(conduct).hasSizeGreaterThan(500);
        for (ToolDescriptor descriptor : catalog.descriptors()) {
            assertThat(conduct)
                    .as("conduct must not name %s; per-tool advice belongs in the descriptor",
                            descriptor.name())
                    .doesNotContain(descriptor.name());
        }
    }
}
