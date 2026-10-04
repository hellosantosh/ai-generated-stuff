package com.example.brokerage.agent;

import java.util.List;

import com.example.brokerage.catalog.BrokerageCatalog;
import com.example.brokerage.tooling.runtime.ToolPublisher;
import com.example.brokerage.tooling.runtime.ToolTrace;

import org.springframework.ai.chat.messages.AssistantMessage;
import org.springframework.ai.chat.messages.UserMessage;
import org.springframework.ai.chat.model.ChatModel;
import org.springframework.ai.chat.model.ChatResponse;
import org.springframework.ai.tool.ToolCallback;
import org.springframework.ai.anthropic.AnthropicChatOptions;
import org.springframework.ai.chat.client.ChatClient;
import org.springframework.beans.factory.ObjectProvider;
import org.springframework.stereotype.Component;

/**
 * The agent: Claude, this session's visible tools, and nothing else.
 *
 * <p>Three decisions in this class are worth the reader's attention.
 *
 * <p><b>The tools are published per turn, not per application.</b> Each call builds fresh callbacks
 * bound to this session's principal and to a fresh {@link ToolTrace}. Two consequences follow: a
 * research session and a trading session running at the same moment see different catalogs, and
 * every turn comes back with a complete record of what was called.
 *
 * <p><b>The system prompt is assembled, not stored.</b> The conduct half is written by a human and
 * lives in a file beside the descriptors, where compliance can read it. The toolset half is
 * generated from the ontology by {@code ToolsetBriefing}. Nothing in the generated half can
 * disagree with the catalog, because it is read from the catalog on every turn.
 *
 * <p><b>Tool execution is left to the framework.</b> There is no hand-rolled call loop here,
 * because there is nothing to gain from one: the trace is collected inside each callback, so the
 * visibility a manual loop is usually written for is already there. Less machinery, same evidence.
 */
@Component
public class BrokerageAgent {


    private final ObjectProvider<ChatModel> chatModel;
    private final ToolPublisher publisher;
    private final BrokerageCatalog catalog;

    public BrokerageAgent(ObjectProvider<ChatModel> chatModel, ToolPublisher publisher,
                          BrokerageCatalog catalog) {
        this.chatModel = chatModel;
        this.publisher = publisher;
        this.catalog = catalog;
    }

    /** One turn: the assistant's reply, and the tools it used to get there. */
    public record Turn(String reply, List<ToolTrace.Step> steps, Integer promptTokens,
                       Integer completionTokens, String model) {
    }

    public boolean available() {
        return chatModel.getIfAvailable() != null;
    }

    public Turn ask(Sessions.Session session, String message, String model, int maxTokens) {
        ChatModel llm = chatModel.getIfAvailable();
        if (llm == null) {
            throw new IllegalStateException(
                    "No chat model is configured. Set ANTHROPIC_API_KEY and restart.");
        }
        ToolTrace trace = new ToolTrace();
        List<ToolCallback> callbacks = publisher.publish(session.principal(), trace);
        String system = catalog.conduct() + "\n" + publisher.briefing(session.principal());

        ChatResponse response = ChatClient.create(llm)
                .prompt()
                .system(system)
                .messages(List.copyOf(session.history()))
                .user(message)
                .toolCallbacks(callbacks)
                .options(AnthropicChatOptions.builder().model(model).maxTokens(maxTokens))
                .call()
                .chatResponse();

        String reply = response == null || response.getResult() == null
                ? "" : response.getResult().getOutput().getText();
        session.history().add(new UserMessage(message));
        session.history().add(new AssistantMessage(reply == null ? "" : reply));

        Integer prompt = null;
        Integer completion = null;
        if (response != null && response.getMetadata() != null
                && response.getMetadata().getUsage() != null) {
            prompt = response.getMetadata().getUsage().getPromptTokens();
            completion = response.getMetadata().getUsage().getCompletionTokens();
        }
        return new Turn(reply == null ? "" : reply, trace.steps(), prompt, completion, model);
    }
}
