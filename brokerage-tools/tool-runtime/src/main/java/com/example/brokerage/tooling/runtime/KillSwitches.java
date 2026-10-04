package com.example.brokerage.tooling.runtime;

import java.util.Map;
import java.util.Set;
import java.util.concurrent.ConcurrentHashMap;

/**
 * Named switches that take tools out of service without a deployment.
 *
 * <p>Every tool at tier 2 or above must name one, and the linter enforces that, for a reason that
 * only becomes obvious the first time you need it. When a tool starts behaving badly at two in the
 * morning, the options are: turn it off, or roll back. Rolling back takes a build. Turning it off
 * takes a configuration change, and the agent's next turn simply does not see the tool — no error,
 * no retry loop, no half-working catalog.
 *
 * <p>Switches are hierarchical by prefix, so {@code tools.brokerage.trading} disables
 * {@code tools.brokerage.trading.place} and {@code tools.brokerage.trading.cancel} with it. That
 * matters when the thing misbehaving is a dependency rather than a tool.
 */
public class KillSwitches {

    private final Set<String> tripped = ConcurrentHashMap.newKeySet();

    public void trip(String switchName) {
        tripped.add(switchName);
    }

    public void reset(String switchName) {
        tripped.remove(switchName);
    }

    public Set<String> tripped() {
        return Set.copyOf(tripped);
    }

    /** True when this switch, or any switch it sits under, has been tripped. */
    public boolean isOff(String switchName) {
        if (switchName == null) {
            return false;
        }
        if (tripped.contains(switchName)) {
            return true;
        }
        int dot = switchName.lastIndexOf('.');
        return dot > 0 && isOff(switchName.substring(0, dot));
    }

    public Map<String, Boolean> status(Iterable<String> switchNames) {
        Map<String, Boolean> status = new java.util.LinkedHashMap<>();
        switchNames.forEach(name -> status.put(name, !isOff(name)));
        return status;
    }
}
