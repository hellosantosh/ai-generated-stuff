package com.example.brokerage.api.market;

import java.math.BigDecimal;
import java.time.LocalDate;

import com.fasterxml.jackson.annotation.JsonIgnore;

import org.springframework.hateoas.server.core.Relation;

/**
 * Something that can be traded: a stock, an ETF, or an option contract. The ID is
 * opaque: clients store it and send it back, but never take it apart.
 *
 * @param multiplier shares controlled by one unit: 1 for stocks, 100 for a US equity option
 * @param option     present only for option contracts
 */
@Relation(collectionRelation = "instruments", itemRelation = "instrument")
public record Instrument(String id, String symbol, String name, Type type, String exchange,
        String currency, boolean tradable, boolean fractionable, BigDecimal multiplier, OptionTerms option) {

    public enum Type { EQUITY, ETF, OPTION }

    public enum Right { CALL, PUT }

    /** The contract terms of an option. */
    public record OptionTerms(String underlyingId, String underlyingSymbol, Right right,
            BigDecimal strike, LocalDate expiration, String style) {
    }

    @JsonIgnore // otherwise Jackson would also write a boolean "option" property
    public boolean isOption() {
        return type == Type.OPTION;
    }
}
