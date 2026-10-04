package com.example.brokerage.catalog;

import java.io.IOException;
import java.io.InputStream;
import java.io.UncheckedIOException;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Optional;

import com.example.brokerage.tooling.contract.DescriptorCodec;
import com.example.brokerage.tooling.contract.Ontology;
import com.example.brokerage.tooling.contract.ToolDescriptor;

import org.springframework.stereotype.Component;

/**
 * The catalog as published: the ontology and the fourteen descriptors, read from the classpath.
 *
 * <p>The descriptors are JSON files rather than Java builders, and that is a deliberate choice
 * with consequences worth stating. A descriptor in JSON can be linted by anything, diffed by a
 * reviewer who does not write Java, served to a desktop client without the application running,
 * and compared byte for byte against what production is serving. A descriptor assembled in code
 * can be none of those things, and the first time somebody asks "what exactly is the model being
 * shown right now" you will be reading a builder and guessing.
 *
 * <p>The order of {@link #NAMES} is the order the tools are presented in. It is not alphabetical.
 * Reads come before writes, and within the reads the ones that produce identifiers come first,
 * because a model reading a catalog top to bottom should meet the tools in roughly the order it
 * will need them.
 */
@Component
public class BrokerageCatalog {

    public static final String ONTOLOGY_RESOURCE = "/ontology/brokerage.ontology.json";
    public static final String CONDUCT_RESOURCE = "/prompts/conduct.md";

    /** Presentation order: entry points, then the rest of the reads, then the writes. */
    public static final List<String> NAMES = List.of(
            "brokerage_accounts_list",
            "brokerage_instruments_search",
            "brokerage_quotes_get",
            "brokerage_price_history_get",
            "brokerage_balances_get",
            "brokerage_positions_list",
            "brokerage_tax_lots_list",
            "brokerage_orders_list",
            "brokerage_order_get",
            "brokerage_executions_list",
            "brokerage_order_preview",
            "brokerage_order_place",
            "brokerage_order_replace",
            "brokerage_order_cancel");

    private final Ontology ontology;
    private final String conduct;
    private final Map<String, ToolDescriptor> descriptors = new LinkedHashMap<>();

    public BrokerageCatalog() {
        this.ontology = load(ONTOLOGY_RESOURCE, Ontology::read);
        this.conduct = load(CONDUCT_RESOURCE, in -> new String(in.readAllBytes()));
        for (String name : NAMES) {
            descriptors.put(name, load("/catalog/" + name + ".json", DescriptorCodec::read));
        }
    }

    /**
     * The conduct half of the system prompt: how the assistant behaves, as distinct from what the
     * tools are. It lives in a file beside the descriptors because it is policy — the compliance
     * team should be able to read it and propose a change without opening an IDE — and because the
     * agent and the evaluation harness must be reading the same copy. A harness that evaluates a
     * different prompt from the one production uses is measuring a system nobody ships.
     */
    public String conduct() {
        return conduct;
    }

    private interface Reader<T> {
        T read(InputStream in) throws IOException;
    }

    private static <T> T load(String resource, Reader<T> reader) {
        InputStream in = BrokerageCatalog.class.getResourceAsStream(resource);
        if (in == null) {
            throw new IllegalStateException("missing catalog resource " + resource);
        }
        try {
            return reader.read(in);
        } catch (IOException e) {
            throw new UncheckedIOException("cannot read " + resource, e);
        }
    }

    public Ontology ontology() {
        return ontology;
    }

    public List<ToolDescriptor> descriptors() {
        return new ArrayList<>(descriptors.values());
    }

    public Optional<ToolDescriptor> find(String name) {
        return Optional.ofNullable(descriptors.get(name));
    }

    public ToolDescriptor require(String name) {
        return find(name).orElseThrow(() -> new IllegalArgumentException("no tool " + name));
    }

    public int size() {
        return descriptors.size();
    }
}
