package com.example.brokerage.agent.web;

import java.util.List;
import java.util.Map;

import com.example.brokerage.agent.BrokerageAgent;
import com.example.brokerage.agent.Sessions;
import com.example.brokerage.tooling.runtime.ToolTrace;

import org.springframework.beans.factory.annotation.Value;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.DeleteMapping;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

/**
 * The conversation endpoint.
 *
 * <p>The response carries the trace alongside the reply, and the console shows both side by side.
 * That layout is a deliberate teaching decision: the most common way to misjudge an agent is to
 * read only its prose. A reply that says "you have 182,430 dollars of buying power" looks equally
 * trustworthy whether the model read it from a tool or produced it from the shape of the
 * conversation, and the trace is the only thing that tells them apart.
 */
@RestController
@RequestMapping("/api/chat")
public class ChatController {

    public record ChatRequest(String sessionId, String message, Sessions.Role role) {
    }

    public record ChatResponseBody(String sessionId, String reply, List<ToolTrace.Step> trace,
                                   Integer promptTokens, Integer completionTokens, String model) {
    }

    private final BrokerageAgent agent;
    private final Sessions sessions;
    private final String model;
    private final int maxTokens;

    public ChatController(BrokerageAgent agent, Sessions sessions,
                          @Value("${brokerage.agent.model:claude-opus-5}") String model,
                          @Value("${brokerage.agent.max-tokens:16000}") int maxTokens) {
        this.agent = agent;
        this.sessions = sessions;
        this.model = model;
        this.maxTokens = maxTokens;
    }

    @GetMapping("/status")
    public Map<String, Object> status() {
        return Map.of("modelAvailable", agent.available(), "model", model,
                "roles", List.of(Sessions.Role.values()));
    }

    @PostMapping
    public ResponseEntity<?> chat(@RequestBody ChatRequest request) {
        if (!agent.available()) {
            return ResponseEntity.status(503).body(Map.of("error",
                    "No chat model is configured. Set ANTHROPIC_API_KEY and restart the service. "
                            + "Everything else in the console works without it."));
        }
        Sessions.Role role = request.role() == null ? Sessions.Role.SERVICE : request.role();
        Sessions.Session session = sessions.require(request.sessionId(), role);
        BrokerageAgent.Turn turn = agent.ask(session, request.message(), model, maxTokens);
        return ResponseEntity.ok(new ChatResponseBody(session.sessionId(), turn.reply(),
                turn.steps(), turn.promptTokens(), turn.completionTokens(), turn.model()));
    }

    @DeleteMapping("/{sessionId}")
    public Map<String, Object> reset(@PathVariable String sessionId) {
        sessions.close(sessionId);
        return Map.of("sessionId", sessionId, "closed", true);
    }
}
