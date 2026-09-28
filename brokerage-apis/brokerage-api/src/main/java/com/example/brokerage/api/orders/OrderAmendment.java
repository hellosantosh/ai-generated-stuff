package com.example.brokerage.api.orders;

import java.math.BigDecimal;

import jakarta.validation.constraints.Digits;
import jakarta.validation.constraints.Positive;

import org.springframework.hateoas.InputType;

/**
 * The body of PATCH .../orders/{orderId}, sent as application/merge-patch+json (RFC 7396):
 * only the fields present are changed. The instrument, side and type of a working order
 * cannot change; cancel it and place a new one instead.
 */
public class OrderAmendment {

    @InputType("text") @Positive @Digits(integer = 9, fraction = 6)
    private BigDecimal quantity;
    @InputType("text") @Positive @Digits(integer = 9, fraction = 4)
    private BigDecimal limitPrice;
    @InputType("text") @Positive @Digits(integer = 9, fraction = 4)
    private BigDecimal stopPrice;
    private Order.TimeInForce timeInForce;

    public BigDecimal getQuantity() { return quantity; }
    public void setQuantity(BigDecimal quantity) { this.quantity = quantity; }
    public BigDecimal getLimitPrice() { return limitPrice; }
    public void setLimitPrice(BigDecimal limitPrice) { this.limitPrice = limitPrice; }
    public BigDecimal getStopPrice() { return stopPrice; }
    public void setStopPrice(BigDecimal stopPrice) { this.stopPrice = stopPrice; }
    public Order.TimeInForce getTimeInForce() { return timeInForce; }
    public void setTimeInForce(Order.TimeInForce timeInForce) { this.timeInForce = timeInForce; }
}
