package com.example.brokerage.tooling.contract;

import java.util.Map;

/**
 * Supplies the values a confirmation template needs that the arguments do not carry.
 *
 * <p>This interface exists because of a bug worth describing in full, since it is easy to ship and
 * hard to notice. A confirmation template naturally wants to say things like
 * {@code Estimated cost {{estimatedCost}}} and {@code in {{accountNickname}}} — because those are
 * the facts a human needs in order to agree to anything. Neither is an argument. The arguments are
 * an account identifier, a ticker, a side and a quantity; the cost does not exist until something
 * prices it and the nickname lives in the account record.
 *
 * <p>Render the template from the arguments alone and the dialog reads: "Place this order? BUY 50
 * AAPL in (not given). Estimated cost (not given)." A customer will approve that, because the part
 * they recognize is correct. They will have approved an order whose cost they never saw.
 *
 * <p>So the runtime asks the domain. The implementation for this catalog resolves the nickname and
 * re-prices the order at the moment of the confirmation, which is also the right moment: the
 * number the customer agrees to should be the number as it is when they agree, not as it was when
 * the agent first asked.
 *
 * <p>Enrichment must be read-only and must not throw. A confirmation dialog that fails to render
 * is a trade that cannot be approved, and the fallback — showing the template's own placeholder
 * text — is better than an error page but worse than a number.
 */
public interface ApprovalContext {

    /** Extra placeholder values, keyed by the name inside the double braces. Never null. */
    Map<String, String> enrich(Invocation invocation);

    /** The null object, for a runtime with no domain enrichment wired in. */
    ApprovalContext NONE = invocation -> Map.of();
}
