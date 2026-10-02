package com.example.brokerage.tooling.contract;

import java.io.IOException;
import java.io.InputStream;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.List;

import tools.jackson.databind.DeserializationFeature;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.json.JsonMapper;

/**
 * Reads descriptors from JSON and writes them back. One mapper, configured once, shared by the
 * linter, the registry, the handlers and the tests, so that a descriptor cannot pass the linter
 * under one set of Jackson settings and fail in production under another.
 *
 * <p>Two settings matter, and one default is worth naming. Unknown properties fail, because a
 * misspelled governance key is a review finding rather than something to shrug off at load time.
 * Null fields are left out, so the descriptor a reviewer reads is the descriptor its author wrote.
 * And Jackson 3 writes {@code java.time} values as ISO-8601 strings without being asked, which is
 * what the model needs: it reads {@code 2026-10-01T14:30:00Z} as a time and reads
 * {@code 1790865000} as a quantity.
 */
public final class DescriptorCodec {

    private static final JsonMapper MAPPER = JsonMapper.builder()
            .enable(DeserializationFeature.FAIL_ON_UNKNOWN_PROPERTIES)
            .build();

    private DescriptorCodec() {
    }

    public static JsonMapper mapper() {
        return MAPPER;
    }

    public static ToolDescriptor read(String json) {
        return MAPPER.readValue(json, ToolDescriptor.class);
    }

    public static ToolDescriptor read(Path file) throws IOException {
        return read(Files.readString(file));
    }

    public static ToolDescriptor read(InputStream in) throws IOException {
        try (in) {
            return read(new String(in.readAllBytes()));
        }
    }

    /** Pretty JSON, two-space indent, trailing newline: the form the catalog is committed in. */
    public static String write(ToolDescriptor descriptor) {
        return MAPPER.writerWithDefaultPrettyPrinter().writeValueAsString(descriptor) + "\n";
    }

    public static <T> T convert(JsonNode node, Class<T> type) {
        return MAPPER.treeToValue(node, type);
    }

    public static JsonNode tree(String json) {
        return MAPPER.readTree(json);
    }

    public static JsonNode tree(Object value) {
        return MAPPER.valueToTree(value);
    }

    public static String json(Object value) {
        return MAPPER.writeValueAsString(value);
    }

    /**
     * Every {@code *.json} file in a directory, read in name order so that a catalog listing is
     * stable across machines and the book's figures do not reshuffle between builds.
     */
    public static List<ToolDescriptor> readDirectory(Path directory) throws IOException {
        List<ToolDescriptor> descriptors = new ArrayList<>();
        try (var files = Files.list(directory)) {
            for (Path file : files.filter(f -> f.toString().endsWith(".json"))
                    .sorted(Comparator.comparing(Path::getFileName)).toList()) {
                descriptors.add(read(file));
            }
        }
        return descriptors;
    }
}
