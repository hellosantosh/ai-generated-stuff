package com.example.brokerage.harness;

import java.io.IOException;
import java.io.UncheckedIOException;
import java.nio.file.Path;
import java.time.Instant;
import java.util.ArrayList;
import java.util.List;
import java.util.Set;

import com.example.brokerage.tooling.contract.Principal;
import com.example.brokerage.tooling.runtime.ApprovalGate;
import com.example.brokerage.tooling.runtime.ToolCallbackHolder;
import com.example.brokerage.tooling.runtime.ToolPublisher;
import com.example.brokerage.tooling.runtime.ToolTrace;

import org.springframework.ai.chat.client.ChatClient;
import org.springframework.ai.chat.messages.AssistantMessage;
import org.springframework.ai.chat.messages.Message;
import org.springframework.ai.chat.messages.UserMessage;
import org.springframework.ai.chat.model.ChatModel;
import org.springframework.ai.chat.model.ChatResponse;
import org.springframework.ai.tool.ToolCallback;
import org.springframework.ai.anthropic.AnthropicChatOptions;

/**
 * Runs one scenario, either against Claude or against a recorded cassette.
 *
 * <p>The two modes are not two test suites. They run the same scenario, against the same registry,
 * through the same pipeline, and check the same expectations. The only difference is where the
 * model's choices come from: the network, or a file recorded from the network. That is what makes
 * the offline suite worth trusting — it is not a mock of the system, it is the system with a
 * recording of the model's half of the conversation.
 */
public class HarnessRunner {

    public enum Mode {
        /** Call Claude. Costs money, takes a minute, catches description regressions. */
        LIVE,
        /** Call Claude and write the cassette. */
        RECORD,
        /** Replay the cassette. Free, fast, deterministic, and runs the real tools. */
        REPLAY
    }

    /** One scenario's outcome: the trace it produced, and what failed. */
    public record Result(Scenario scenario, Mode mode, List<ToolTrace.Step> steps, String reply,
                         List<Expectations.Failure> failures, long millis, Integer promptTokens,
                         Integer completionTokens) {

        public boolean passed() {
            return failures.isEmpty();
        }
    }

    private final ToolPublisher publisher;
    private final ApprovalGate approvals;
    private final ChatModel chatModel;
    private final Path cassettes;
    private final String model;
    private final int maxTokens;
    private final String conduct;

    public HarnessRunner(ToolPublisher publisher, ApprovalGate approvals, ChatModel chatModel,
                         Path cassettes, String model, int maxTokens, String conduct) {
        this.publisher = publisher;
        this.approvals = approvals;
        this.chatModel = chatModel;
        this.cassettes = cassettes;
        this.model = model;
        this.maxTokens = maxTokens;
        this.conduct = conduct;
    }

    public Result run(Scenario scenario, Mode mode) {
        long started = System.currentTimeMillis();
        Principal principal = principalFor(scenario);
        approvals.standInForTheHuman(scenario.approvesAutomatically() ? "harness" : null);
        ToolTrace trace = new ToolTrace();
        List<ToolCallback> callbacks = publisher.publish(principal, trace);
        try {
            return mode == Mode.REPLAY
                    ? replay(scenario, callbacks, trace, started)
                    : live(scenario, principal, callbacks, trace, mode, started);
        } catch (RuntimeException e) {
            return new Result(scenario, mode, trace.steps(), "",
                    List.of(new Expectations.Failure("harness-error", e.toString())),
                    System.currentTimeMillis() - started, null, null);
        }
    }

    // ------------------------------------------------------------------ live

    private Result live(Scenario scenario, Principal principal, List<ToolCallback> callbacks,
                        ToolTrace trace, Mode mode, long started) {
        if (chatModel == null) {
            throw new IllegalStateException(
                    "No chat model. Set ANTHROPIC_API_KEY, or run the suite in REPLAY mode.");
        }
        List<Message> history = new ArrayList<>();
        for (int i = 0; i < scenario.priorTurns().size(); i++) {
            history.add(i % 2 == 0 ? new UserMessage(scenario.priorTurns().get(i))
                    : new AssistantMessage(scenario.priorTurns().get(i)));
        }
        String system = conduct + "\n" + publisher.briefing(principal);
        ChatResponse response = ChatClient.create(chatModel)
                .prompt()
                .system(system)
                .messages(history)
                .user(scenario.utterance())
                .toolCallbacks(callbacks)
                .options(AnthropicChatOptions.builder().model(model).maxTokens(maxTokens))
                .call()
                .chatResponse();
        String reply = response == null || response.getResult() == null ? ""
                : String.valueOf(response.getResult().getOutput().getText());
        Integer prompt = null;
        Integer completion = null;
        if (response != null && response.getMetadata() != null
                && response.getMetadata().getUsage() != null) {
            prompt = response.getMetadata().getUsage().getPromptTokens();
            completion = response.getMetadata().getUsage().getCompletionTokens();
        }
        if (mode == Mode.RECORD) {
            record(scenario, trace, reply, prompt, completion);
        }
        return new Result(scenario, mode, trace.steps(), reply,
                Expectations.check(scenario, trace.steps(), reply),
                System.currentTimeMillis() - started, prompt, completion);
    }

    private void record(Scenario scenario, ToolTrace trace, String reply, Integer prompt,
                        Integer completion) {
        List<Cassette.Call> calls = trace.steps().stream()
                .map(step -> new Cassette.Call(step.tool(), maskTokens(step.arguments())))
                .toList();
        Cassette cassette = new Cassette(scenario.id(), Cassette.Source.RECORDED, model,
                Instant.now().toString(), null, List.of(new Cassette.Turn(calls, reply)), reply,
                prompt, completion);
        try {
            cassette.write(Cassette.pathFor(cassettes, scenario.id()));
        } catch (IOException e) {
            throw new UncheckedIOException(e);
        }
    }

    // ------------------------------------------------------------------ replay

    /**
     * Replay the recorded choices through the real tools.
     *
     * <p>Each recorded call goes through the callback, which means the pipeline validates the
     * arguments, checks entitlements, applies the rate limit, consults the approval gate and runs
     * the handler, exactly as it would for a live model. The trace this produces is a real trace;
     * only the decisions behind it are historical.
     */
    private Result replay(Scenario scenario, List<ToolCallback> callbacks, ToolTrace trace,
                          long started) {
        Cassette cassette;
        try {
            cassette = Cassette.read(Cassette.pathFor(cassettes, scenario.id()));
        } catch (IOException e) {
            return new Result(scenario, Mode.REPLAY, List.of(), "",
                    List.of(new Expectations.Failure("no-cassette",
                            "no recording for " + scenario.id()
                                    + ". Run the suite with --record once, with an API key.")),
                    System.currentTimeMillis() - started, null, null);
        }
        ToolCallbackHolder holder = new ToolCallbackHolder(callbacks);
        String liveToken = null;
        for (Cassette.Turn turn : cassette.turns()) {
            for (Cassette.Call call : turn.calls()) {
                String arguments = liveToken == null ? call.arguments()
                        : call.arguments().replace(Cassette.TOKEN_PLACEHOLDER, liveToken);
                String envelope = holder.call(call.tool(), arguments);
                String issued = tokenIn(envelope);
                if (issued != null) {
                    liveToken = issued;
                }
            }
        }
        String reply = cassette.reply() == null ? "" : cassette.reply();
        return new Result(scenario, Mode.REPLAY, trace.steps(), reply,
                Expectations.check(scenario, trace.steps(), reply),
                System.currentTimeMillis() - started, cassette.promptTokens(),
                cassette.completionTokens());
    }

    /** Rewrite a recorded token to the placeholder, so the recording stays replayable. */
    static String maskTokens(String arguments) {
        return arguments == null ? null
                : arguments.replaceAll("cnf_[0-9A-HJKMNP-TV-Z]{24}",
                        java.util.regex.Matcher.quoteReplacement(Cassette.TOKEN_PLACEHOLDER));
    }

    /** The confirmation token a preview just issued, if this envelope carries one. */
    private static String tokenIn(String envelope) {
        java.util.regex.Matcher matcher = java.util.regex.Pattern
                .compile("\"confirmationToken\"\\s*:\\s*\"(cnf_[0-9A-HJKMNP-TV-Z]{24})\"")
                .matcher(envelope == null ? "" : envelope);
        return matcher.find() ? matcher.group(1) : null;
    }

    private Principal principalFor(Scenario scenario) {
        Set<String> entitlements = switch (scenario.roleOrDefault()) {
            case RESEARCH -> Set.of("brokerage:reference:read", "brokerage:market-data:read");
            case SERVICE -> Set.of("brokerage:reference:read", "brokerage:market-data:read",
                    "brokerage:accounts:read", "brokerage:balances:read",
                    "brokerage:positions:read", "brokerage:orders:read");
            case TRADING -> Set.of("brokerage:reference:read", "brokerage:market-data:read",
                    "brokerage:accounts:read", "brokerage:balances:read",
                    "brokerage:positions:read", "brokerage:orders:read",
                    "brokerage:orders:write");
        };
        return new Principal("harness:" + scenario.id(),
                com.example.brokerage.domain.Accounts.DEMO_CUSTOMER, entitlements,
                "eval-" + scenario.id());
    }
}
