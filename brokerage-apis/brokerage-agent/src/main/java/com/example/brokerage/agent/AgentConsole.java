package com.example.brokerage.agent;

import java.io.BufferedReader;
import java.io.IOException;
import java.io.InputStreamReader;
import java.util.UUID;

import org.springframework.ai.chat.client.ChatClient;
import org.springframework.ai.chat.memory.ChatMemory;
import org.springframework.ai.chat.model.ChatResponse;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.boot.CommandLineRunner;
import org.springframework.boot.autoconfigure.condition.ConditionalOnBooleanProperty;
import org.springframework.stereotype.Component;

/**
 * A console conversation. Lines starting with "/" are commands handled here, by code, and
 * never reach the model: that is what makes "/confirm" a human decision.
 */
@Component
@ConditionalOnBooleanProperty(name = "brokerage.console.enabled", matchIfMissing = true)
class AgentConsole implements CommandLineRunner {

    private final ChatClient chat;
    private final PendingActions pending;
    private final BrokerageTools tools;
    private final boolean modelConfigured;
    private final String conversation = UUID.randomUUID().toString();

    AgentConsole(ChatClient chat, PendingActions pending, BrokerageTools tools,
            @Value("${spring.ai.anthropic.api-key:}") String apiKey) {
        this.chat = chat;
        this.pending = pending;
        this.tools = tools;
        this.modelConfigured = !apiKey.isBlank();
    }

    @Override
    public void run(String... args) throws IOException {
        System.out.println("Brokerage assistant. Ask about your accounts or ask for a trade.");
        System.out.println("Commands: /confirm CODE, /discard CODE, /status, /quit");
        BufferedReader in = new BufferedReader(new InputStreamReader(System.in));
        for (String line; (line = prompt(in)) != null && !line.equals("/quit"); ) {
            if (line.startsWith("/confirm ")) {
                System.out.println(pending.confirm(line.substring(9).trim()));
            } else if (line.startsWith("/discard ")) {
                boolean discarded = pending.discard(line.substring(9).trim());
                System.out.println(discarded ? "Discarded." : "Nothing to discard.");
            } else if (line.equals("/status")) {
                // The tools are plain Java: call one directly to check the API and the token,
                // with no model involved.
                tools.listAccounts()
                        .forEach(account -> System.out.println(account.id() + "  " + account.nickname()));
            } else if (!line.isBlank()) {
                System.out.println(modelConfigured ? ask(line)
                        : "Set ANTHROPIC_API_KEY to talk to the assistant.");
            }
        }
    }

    private String ask(String question) {
        ChatResponse response = chat.prompt()
                .user(question)
                .advisors(advisor -> advisor.param(ChatMemory.CONVERSATION_ID, conversation))
                .call()
                .chatResponse();
        // Check why the model stopped before reading its text: a refusal has no answer to show.
        String finish = response.getResult().getMetadata().getFinishReason();
        if ("refusal".equalsIgnoreCase(finish)) {
            return "The assistant declined that request. Try rephrasing it.";
        }
        return response.getResult().getOutput().getText();
    }

    private static String prompt(BufferedReader in) throws IOException {
        System.out.print("\n> ");
        String line = in.readLine();
        return line == null ? null : line.trim();
    }
}
