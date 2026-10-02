package com.example.brokerage.domain;

import java.math.BigDecimal;
import java.math.RoundingMode;

/**
 * Money, as the only two representations a brokerage may use: {@link BigDecimal} inside the
 * process and a decimal <em>string</em> at every boundary.
 *
 * <p>There is no third option. A JSON number for money is a defect waiting for a volume large
 * enough to show it: IEEE-754 binary doubles cannot represent 0.1, so a thousand-share order at
 * $10.10 can total $10099.999999999998, and the model will dutifully read that back to a
 * customer. Every amount this domain hands to a tool goes through {@link #string}.
 */
public final class Money {

    public static final int SCALE = 2;
    public static final String USD = "USD";

    private Money() {
    }

    public static BigDecimal of(String amount) {
        return new BigDecimal(amount);
    }

    public static BigDecimal of(double amount) {
        return BigDecimal.valueOf(amount).setScale(SCALE, RoundingMode.HALF_UP);
    }

    /** Round half-up to cents, the convention a customer statement uses. */
    public static BigDecimal cents(BigDecimal amount) {
        return amount.setScale(SCALE, RoundingMode.HALF_UP);
    }

    /** The wire form: a plain decimal string, never scientific notation, never a float. */
    public static String string(BigDecimal amount) {
        return cents(amount).toPlainString();
    }

    /** The wire form at a wider scale, for per-share prices quoted in sub-cents. */
    public static String price(BigDecimal amount) {
        return amount.setScale(4, RoundingMode.HALF_UP).stripTrailingZeros().toPlainString();
    }

    public static BigDecimal times(BigDecimal amount, long quantity) {
        return amount.multiply(BigDecimal.valueOf(quantity));
    }
}
