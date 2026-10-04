package com.example.brokerage.harness;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.List;

import org.springframework.ai.anthropic.AnthropicChatModel;
import org.springframework.ai.anthropic.AnthropicChatOptions;
import org.springframework.ai.chat.model.ChatModel;

/**
 * The harness as a command.
 *
 * <pre>
 *   ./harness.sh                      every suite, offline, from the cassettes
 *   ./harness.sh --live               every suite against Claude
 *   ./harness.sh --record             against Claude, writing new cassettes
 *   ./harness.sh --suite trading      one suite
 * </pre>
 *
 * <p>Offline is the default, and that choice is the difference between a suite that runs on every
 * commit and a suite that runs when somebody remembers. A conformance gate nobody runs is a style
 * guide.
 */
public final class HarnessMain {

    private static final Path SUITES = Path.of("test-harness/src/main/resources/evals");
    private static final Path CASSETTES = Path.of("test-harness/src/main/resources/cassettes");
    private static final Path REPORTS = Path.of("test-harness/target/reports");

    private HarnessMain() {
    }

    public static void main(String[] args) throws IOException {
        List<String> flags = List.of(args);
        HarnessRunner.Mode mode = flags.contains("--record") ? HarnessRunner.Mode.RECORD
                : flags.contains("--live") ? HarnessRunner.Mode.LIVE : HarnessRunner.Mode.REPLAY;
        String only = value(flags, "--suite");
        String model = value(flags, "--model") == null ? "claude-opus-5" : value(flags, "--model");

        ChatModel chatModel = mode == HarnessRunner.Mode.REPLAY ? null : anthropic(model);
        boolean clean = true;
        for (Path file : suiteFiles(only)) {
            Scenario.Suite suite = Scenario.read(file);
            List<HarnessRunner.Result> results = new ArrayList<>();
            for (Scenario scenario : suite.scenarios()) {
                HarnessContext context = new HarnessContext();   // a fresh world per scenario
                HarnessRunner runner = new HarnessRunner(context.publisher(),
                        context.autoApprovingGate(), chatModel, CASSETTES, model, 16000,
                        context.catalog().conduct());
                results.add(runner.run(scenario, mode));
            }
            Report report = Report.of(suite.name(), mode, results);
            System.out.println(report.text());
            report.write(REPORTS.resolve(suite.name() + ".json"));
            clean &= report.clean();
        }
        if (!clean) {
            System.exit(1);
        }
    }

    private static List<Path> suiteFiles(String only) throws IOException {
        try (var files = Files.list(SUITES)) {
            return files.filter(path -> path.toString().endsWith(".json"))
                    .filter(path -> only == null || path.getFileName().toString().contains(only))
                    .sorted()
                    .toList();
        }
    }

    private static ChatModel anthropic(String model) {
        String key = System.getenv("ANTHROPIC_API_KEY");
        if (key == null || key.isBlank()) {
            throw new IllegalStateException(
                    "ANTHROPIC_API_KEY is not set. Run without --live to replay the cassettes.");
        }
        return AnthropicChatModel.builder()
                .options(AnthropicChatOptions.builder()
                        .apiKey(key)
                        .model(model)
                        .maxTokens(16000)
                        .build())
                .build();
    }

    private static String value(List<String> flags, String name) {
        int at = flags.indexOf(name);
        return at >= 0 && at + 1 < flags.size() ? flags.get(at + 1) : null;
    }
}
