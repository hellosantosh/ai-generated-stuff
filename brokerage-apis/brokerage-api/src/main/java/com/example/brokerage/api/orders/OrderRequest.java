package com.example.brokerage.api.orders;

import java.math.BigDecimal;

import jakarta.validation.constraints.Digits;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotNull;
import jakarta.validation.constraints.Pattern;
import jakarta.validation.constraints.Positive;
import jakarta.validation.constraints.Size;

import org.springframework.hateoas.InputType;

/**
 * The body of POST .../orders and POST .../order-previews. Field-level rules are declared
 * here and checked by Bean Validation; rules that involve several fields or the account
 * (a limit order needs a limit price, a sale needs shares to sell) live in OrderService.
 *
 * <p>This is a JavaBean rather than a record on purpose: Spring HATEOAS derives HAL-FORMS
 * templates from bean properties, and marks a property without a setter as read-only.
 */
public class OrderRequest {

    @NotBlank
    private String instrumentId;
    @NotNull
    private Order.Side side;
    @NotNull
    private Order.Type type;
    private Order.TimeInForce timeInForce;
    @InputType("text") @NotNull @Positive @Digits(integer = 9, fraction = 6)
    private BigDecimal quantity;
    @InputType("text") @Positive @Digits(integer = 9, fraction = 4)
    private BigDecimal limitPrice;
    @InputType("text") @Positive @Digits(integer = 9, fraction = 4)
    private BigDecimal stopPrice;
    /** The caller's own reference for the order, echoed back unchanged. */
    @InputType("text") @Size(max = 64) @Pattern(regexp = "[A-Za-z0-9._-]*")
    private String clientOrderId;

    public OrderRequest() {
    }

    public OrderRequest(String instrumentId, Order.Side side, Order.Type type, Order.TimeInForce timeInForce,
            BigDecimal quantity, BigDecimal limitPrice, BigDecimal stopPrice, String clientOrderId) {
        this.instrumentId = instrumentId;
        this.side = side;
        this.type = type;
        this.timeInForce = timeInForce;
        this.quantity = quantity;
        this.limitPrice = limitPrice;
        this.stopPrice = stopPrice;
        this.clientOrderId = clientOrderId;
    }

    public Order.TimeInForce timeInForceOrDefault() {
        return timeInForce == null ? Order.TimeInForce.DAY : timeInForce;
    }

    /** A stable fingerprint, so a retried request can be recognized as the same request. */
    String fingerprint() {
        return String.join("|", instrumentId, side.name(), type.name(), timeInForceOrDefault().name(),
                plain(quantity), plain(limitPrice), plain(stopPrice), String.valueOf(clientOrderId));
    }

    private static String plain(BigDecimal value) {
        return value == null ? "" : value.stripTrailingZeros().toPlainString();
    }

    // ---------------------------------------------------------------- accessors

    public String getInstrumentId() { return instrumentId; }
    public void setInstrumentId(String instrumentId) { this.instrumentId = instrumentId; }
    public Order.Side getSide() { return side; }
    public void setSide(Order.Side side) { this.side = side; }
    public Order.Type getType() { return type; }
    public void setType(Order.Type type) { this.type = type; }
    public Order.TimeInForce getTimeInForce() { return timeInForce; }
    public void setTimeInForce(Order.TimeInForce timeInForce) { this.timeInForce = timeInForce; }
    public BigDecimal getQuantity() { return quantity; }
    public void setQuantity(BigDecimal quantity) { this.quantity = quantity; }
    public BigDecimal getLimitPrice() { return limitPrice; }
    public void setLimitPrice(BigDecimal limitPrice) { this.limitPrice = limitPrice; }
    public BigDecimal getStopPrice() { return stopPrice; }
    public void setStopPrice(BigDecimal stopPrice) { this.stopPrice = stopPrice; }
    public String getClientOrderId() { return clientOrderId; }
    public void setClientOrderId(String clientOrderId) { this.clientOrderId = clientOrderId; }
}
