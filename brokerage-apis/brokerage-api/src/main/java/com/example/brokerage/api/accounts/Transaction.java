package com.example.brokerage.api.accounts;

import java.math.BigDecimal;
import java.time.Instant;
import java.time.LocalDate;

import org.springframework.hateoas.server.core.Relation;

/**
 * One entry in an account's history. The amount is signed: money into the account is
 * positive, money out is negative, so a client can sum a statement without a lookup table.
 *
 * @param settlesOn when the cash actually moves: US equity trades settle one business day
 *                  after the trade (T+1)
 */
@Relation(collectionRelation = "transactions", itemRelation = "transaction")
public record Transaction(String id, Type type, String description, BigDecimal amount, String instrumentId,
        String symbol, BigDecimal quantity, BigDecimal price, String orderId, Instant occurredAt,
        LocalDate settlesOn) {

    public enum Type { DEPOSIT, WITHDRAWAL, TRADE, DIVIDEND, FEE }
}
