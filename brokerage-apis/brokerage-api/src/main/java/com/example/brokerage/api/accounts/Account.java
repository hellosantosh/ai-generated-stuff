package com.example.brokerage.api.accounts;

import java.time.LocalDate;

import com.fasterxml.jackson.annotation.JsonIgnore;

import org.springframework.hateoas.server.core.Relation;

/**
 * A brokerage account. A customer can own several (an individual margin account and a
 * retirement account, say); every other resource hangs off one of them.
 *
 * @param number the account number, masked: only the last four digits are ever sent
 */
@Relation(collectionRelation = "accounts", itemRelation = "account")
public record Account(String id, @JsonIgnore String customerId, String number, String nickname, Type type,
        Registration registration, String currency, LocalDate openedOn) {

    /** How purchases are funded: settled cash only, or cash plus a margin loan. */
    public enum Type { CASH, MARGIN }

    /** The legal form of the account. */
    public enum Registration { INDIVIDUAL, JOINT, ROTH_IRA, TRADITIONAL_IRA }
}
