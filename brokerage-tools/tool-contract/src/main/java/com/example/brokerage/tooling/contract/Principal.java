package com.example.brokerage.tooling.contract;

import java.util.Set;

/**
 * Who the call is for. Established by the runtime from the session, and never, under any
 * circumstances, from an argument the model supplied.
 *
 * <p>This is the single most important boundary in a tool system, and it is easy to cross by
 * accident. The temptation is obvious: an {@code accountId} parameter is already in the schema, so
 * why not a {@code customerId} parameter too, and then one agent can serve a whole book of
 * business. The answer is that the model's arguments are derived from the conversation, the
 * conversation contains whatever the customer typed, and a customer who types another person's
 * identifier has just read their account. Identity is not negotiable by the party being
 * identified.
 *
 * <p>So {@code accountId} is a legitimate parameter — the model chooses between accounts the
 * customer holds — while {@code customerId} is not, and the linter rejects it by name.
 *
 * @param subject     the authenticated subject, as the audit record will show it
 * @param customerId  the customer whose data this session may see, resolved from the subject
 * @param entitlements scopes the subject holds, checked against each descriptor before execution
 * @param sessionId   the conversation, for binding confirmation tokens and for correlating audit
 */
public record Principal(String subject, String customerId, Set<String> entitlements,
                        String sessionId) {

    public Principal {
        entitlements = Set.copyOf(entitlements);
    }

    public boolean holds(String entitlement) {
        return entitlements.contains(entitlement);
    }

    public boolean holdsAll(Iterable<String> required) {
        for (String entitlement : required) {
            if (!holds(entitlement)) {
                return false;
            }
        }
        return true;
    }
}
