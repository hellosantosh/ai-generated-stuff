package com.example.brokerage.agent;

import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.concurrent.ConcurrentHashMap;

import com.example.brokerage.domain.Accounts;
import com.example.brokerage.tooling.contract.Principal;

import org.springframework.ai.chat.messages.Message;
import org.springframework.stereotype.Component;

/**
 * Who is talking, and what has been said.
 *
 * <p>The entitlement set lives here, on the session, rather than being passed in with each request,
 * and that is the security property the whole runtime rests on. The console can ask to start a
 * session with a named role; it cannot ask for a scope that role does not carry, and nothing the
 * model emits reaches this class at all.
 *
 * <p>The three roles exist to make a point the book returns to repeatedly: the same catalog serves
 * all three, and each sees a different number of tools. Nothing is reconfigured and no tool is
 * rewritten; only the entitlements differ.
 */
@Component
public class Sessions {

    /** A role, as a named set of scopes. Real systems get these from a token; this one does not. */
    public enum Role {
        /** Market data and reference only. Cannot see a single customer figure. */
        RESEARCH(Set.of("brokerage:reference:read", "brokerage:market-data:read")),

        /** Everything readable about this customer. Cannot trade. */
        SERVICE(Set.of("brokerage:reference:read", "brokerage:market-data:read",
                "brokerage:accounts:read", "brokerage:balances:read",
                "brokerage:positions:read", "brokerage:orders:read")),

        /** The full catalog, including the four tools that change something. */
        TRADING(Set.of("brokerage:reference:read", "brokerage:market-data:read",
                "brokerage:accounts:read", "brokerage:balances:read",
                "brokerage:positions:read", "brokerage:orders:read",
                "brokerage:orders:write"));

        private final Set<String> entitlements;

        Role(Set<String> entitlements) {
            this.entitlements = entitlements;
        }

        public Set<String> entitlements() {
            return entitlements;
        }
    }

    public record Session(String sessionId, Principal principal, Role role,
                          List<Message> history) {
    }

    private final Map<String, Session> sessions = new ConcurrentHashMap<>();

    public Session open(String sessionId, Role role) {
        Principal principal = new Principal("user:demo", Accounts.DEMO_CUSTOMER,
                role.entitlements(), sessionId);
        Session session = new Session(sessionId, principal, role,
                java.util.Collections.synchronizedList(new ArrayList<>()));
        sessions.put(sessionId, session);
        return session;
    }

    public Session require(String sessionId, Role fallbackRole) {
        Session session = sessions.get(sessionId);
        return session != null ? session : open(sessionId, fallbackRole);
    }

    public void close(String sessionId) {
        sessions.remove(sessionId);
    }

    public List<Session> all() {
        return List.copyOf(sessions.values());
    }
}
