package com.example.brokerage.catalog;

import java.util.List;

import com.example.brokerage.tooling.contract.ConformanceLinter;
import com.example.brokerage.tooling.contract.Finding;

/**
 * The conformance gate as a command, for a CI step that wants an exit status and a readable log.
 *
 * <pre>
 *   ./mvnw -q -pl brokerage-catalog exec:java -Dexec.mainClass=\
 *       com.example.brokerage.catalog.CatalogLint
 * </pre>
 *
 * <p>The same rules run as a unit test, which is what actually fails the build. Both exist on
 * purpose: the test fails the build for everyone, and the command gives whoever is writing a
 * descriptor a one-second loop instead of a Maven cycle.
 */
public final class CatalogLint {

    private CatalogLint() {
    }

    public static void main(String[] args) {
        BrokerageCatalog catalog = new BrokerageCatalog();
        List<Finding> findings = new ConformanceLinter(catalog.ontology()).lint(catalog.descriptors());
        findings.stream().sorted((a, b) -> a.severity().compareTo(b.severity()))
                .forEach(finding -> System.out.println("  " + finding));
        long errors = findings.stream().filter(f -> f.severity() == Finding.Severity.ERROR).count();
        long warnings = findings.stream().filter(f -> f.severity() == Finding.Severity.WARNING).count();
        System.out.printf("%n  %d descriptors - %d errors - %d warnings - %d capabilities%n",
                catalog.size(), errors, warnings, catalog.ontology().capabilities().size());
        if (errors > 0) {
            System.exit(1);
        }
    }
}
