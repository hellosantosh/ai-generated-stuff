package com.example.brokerage.api.accounts;

import java.math.BigDecimal;

/**
 * What open orders have already claimed. Implemented by the order service; declared here
 * so that the accounts package does not depend on the orders package.
 */
public interface Reservations {

    /** Cash held back for open buy orders. */
    BigDecimal reservedCash(String accountId);

    /** Units of an instrument already promised to open sell orders. */
    BigDecimal reservedQuantity(String accountId, String instrumentId);
}
