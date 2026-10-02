package com.example.brokerage.harness;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.List;
import java.util.Map;

import com.example.brokerage.tooling.contract.DescriptorCodec;

import com.fasterxml.jackson.annotation.JsonInclude;

/**
 * One thing a customer might say, and what the agent is expected to do about it.
 *
 * <p>Scenarios are data, in files, for the same reason descriptors are. A selection suite written
 * as JUnit methods is a suite only a Java developer can extend, and the people who know what a
 * customer will actually say are not Java developers. A scenario file can be written by the
 * product owner who took the support call.
 *
 * <p>The five expectation fields answer five different questions, and most scenarios set two or
 * three of them:
 *
 * <ul>
 *   <li>{@code expectTools} — did it reach for the right tool? The headline number.</li>
 *   <li>{@code forbidTools} — did it reach for something it should not have? Usually the more
 *       interesting half, and the half nobody writes.</li>
 *   <li>{@code expectOrder} — did it do things in the right sequence? Preview before place.</li>
 *   <li>{@code expectArguments} — did it get the arguments right, not just the tool?</li>
 *   <li>{@code expectRefusal} — did it decline, when declining was the correct answer?</li>
 * </ul>
 */
@JsonInclude(JsonInclude.Include.NON_NULL)
public record Scenario(
        String id,
        String utterance,
        String why,
        Role role,
        List<String> expectTools,
        List<String> forbidTools,
        List<String> expectOrder,
        Map<String, Map<String, Object>> expectArguments,
        Boolean expectRefusal,
        Boolean autoApprove,
        Integer maxToolCalls,
        List<String> priorTurns,
        List<String> tags) {

    /** Which entitlements the session under test holds. Mirrors the service's roles. */
    public enum Role { RESEARCH, SERVICE, TRADING }

    public Scenario {
        expectTools = expectTools == null ? List.of() : List.copyOf(expectTools);
        forbidTools = forbidTools == null ? List.of() : List.copyOf(forbidTools);
        expectOrder = expectOrder == null ? List.of() : List.copyOf(expectOrder);
        expectArguments = expectArguments == null ? Map.of() : Map.copyOf(expectArguments);
        priorTurns = priorTurns == null ? List.of() : List.copyOf(priorTurns);
        tags = tags == null ? List.of() : List.copyOf(tags);
    }

    public Role roleOrDefault() {
        return role == null ? Role.TRADING : role;
    }

    /**
     * Whether the harness should answer yes to approvals during this scenario. Off by default,
     * because the default question an evaluation suite should be asking about a trading tool is
     * whether the agent stops and asks.
     */
    public boolean approvesAutomatically() {
        return Boolean.TRUE.equals(autoApprove);
    }

    public record Suite(String name, String description, List<Scenario> scenarios) {
    }

    public static Suite read(Path file) throws IOException {
        return DescriptorCodec.mapper().readValue(Files.readString(file), Suite.class);
    }

    public static Suite read(String json) {
        return DescriptorCodec.mapper().readValue(json, Suite.class);
    }
}
