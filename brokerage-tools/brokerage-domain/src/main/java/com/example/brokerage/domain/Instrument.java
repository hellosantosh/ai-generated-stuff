package com.example.brokerage.domain;

import java.math.BigDecimal;

/**
 * A tradeable instrument. The {@code name} field is the one that causes trouble: a customer says
 * "buy some Apple" and three listed companies have Apple in their name. That is why the search
 * tool exists and why it is allowed to refuse.
 */
public record Instrument(String symbol, String name, String exchange, Type type, String sector,
                         BigDecimal basePrice, boolean marginEligible, boolean fractionable) {

    public enum Type { EQUITY, ETF, ADR }
}
