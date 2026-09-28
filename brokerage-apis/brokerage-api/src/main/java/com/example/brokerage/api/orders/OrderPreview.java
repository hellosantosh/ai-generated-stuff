package com.example.brokerage.api.orders;

import java.math.BigDecimal;
import java.net.URI;
import java.util.List;

/**
 * What an order would cost and whether it would be accepted, without placing it. A preview
 * that fails a business rule is still a successful response: "acceptable" is false and each
 * issue carries the same problem type URI that placing the order would return.
 *
 * @param estimatedTotal for a purchase, the cash needed; for a sale, the expected proceeds
 */
public record OrderPreview(String accountId, String instrumentId, String symbol, Order.Side side,
        Order.Type type, BigDecimal quantity, BigDecimal estimatedPrice, BigDecimal estimatedNotional,
        BigDecimal estimatedCommission, BigDecimal estimatedTotal, BigDecimal buyingPower,
        boolean acceptable, List<Issue> issues) {

    /** A reason the order would be refused. */
    public record Issue(URI type, String detail) {
    }
}
