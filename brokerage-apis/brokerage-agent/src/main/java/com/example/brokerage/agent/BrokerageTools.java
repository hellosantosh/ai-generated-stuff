package com.example.brokerage.agent;

import java.math.BigDecimal;
import java.util.List;

import com.example.brokerage.agent.PendingActions.Proposal;
import com.example.brokerage.client.BrokerageClient;
import com.example.brokerage.client.Model.Account;
import com.example.brokerage.client.Model.Balances;
import com.example.brokerage.client.Model.Order;
import com.example.brokerage.client.Model.OrderPreview;
import com.example.brokerage.client.Model.OrderTicket;
import com.example.brokerage.client.Model.Position;
import com.example.brokerage.client.Model.Quote;

import org.springframework.ai.tool.annotation.Tool;
import org.springframework.ai.tool.annotation.ToolParam;
import org.springframework.stereotype.Component;

/**
 * The agent's tools. Each is a thin wrapper over the hypermedia client, so the model sees
 * exactly what the API allows, and every rule is still enforced by the API itself.
 * Read tools act immediately; tools that would change anything only create proposals.
 */
@Component
public class BrokerageTools {

    private final BrokerageClient brokerage;
    private final PendingActions pending;

    public BrokerageTools(BrokerageClient brokerage, PendingActions pending) {
        this.brokerage = brokerage;
        this.pending = pending;
    }

    @Tool(description = "List the customer's brokerage accounts: ID, nickname, type and currency.")
    public List<Account> listAccounts() {
        return brokerage.accounts();
    }

    @Tool(description = "Cash, cash reserved for open orders, buying power and total equity of one account.")
    public Balances getBalances(@ToolParam(description = "Account ID, such as ACC-1001") String accountId) {
        return brokerage.balances(accountId);
    }

    @Tool(description = "Holdings of one account with quantity, average cost, market value and "
            + "unrealized P&L.")
    public List<Position> getPositions(@ToolParam(description = "Account ID") String accountId) {
        return brokerage.positions(accountId);
    }

    @Tool(description = "The latest quote for a stock or ETF: last price, bid, ask and change on the day.")
    public Quote getQuote(@ToolParam(description = "Ticker symbol, such as AAPL") String symbol) {
        return brokerage.quote(symbol);
    }

    @Tool(description = "Orders in one account that are still working (open or partially filled).")
    public List<Order> getOpenOrders(@ToolParam(description = "Account ID") String accountId) {
        return brokerage.openOrders(accountId);
    }

    @Tool(description = """
            Propose an order for the customer to confirm. The brokerage checks it first and the
            estimate is returned. Nothing is placed until the customer confirms the proposal.""")
    public Object proposeOrder(@ToolParam(description = "Account ID") String accountId,
            @ToolParam(description = "Ticker symbol") String symbol,
            @ToolParam(description = "BUY or SELL") String side,
            @ToolParam(description = "Number of shares, as a decimal string such as \"10\"") String quantity,
            @ToolParam(description = "Limit price as a decimal string; omit for a market order",
                    required = false) String limitPrice) {
        String instrumentId = brokerage.instrument(symbol).id();
        BigDecimal shares = new BigDecimal(quantity);
        OrderTicket ticket = limitPrice == null || limitPrice.isBlank()
                ? OrderTicket.market(instrumentId, side, shares)
                : OrderTicket.limit(instrumentId, side, shares, new BigDecimal(limitPrice));
        OrderPreview preview = brokerage.preview(accountId, ticket);
        if (!preview.acceptable()) {
            return preview; // the issues explain why; nothing to confirm
        }
        String price = ticket.limitPrice() == null ? "at market" : "limit " + ticket.limitPrice();
        String summary = "%s %s %s in %s, %s, estimated total %s USD".formatted(side, quantity, symbol,
                accountId, price, preview.estimatedTotal());
        return pending.propose(summary, idempotencyKey -> {
            Order order = brokerage.place(accountId, ticket, idempotencyKey);
            return "Placed order " + order.id() + ": " + order.status();
        });
    }

    @Tool(description = "Propose canceling a working order. "
            + "Nothing is canceled until the customer confirms.")
    public Proposal proposeCancellation(@ToolParam(description = "Account ID") String accountId,
            @ToolParam(description = "Order ID, such as ORD-100002") String orderId) {
        Order order = brokerage.openOrders(accountId).stream()
                .filter(open -> open.id().equals(orderId))
                .findFirst()
                .orElseThrow(() -> new IllegalArgumentException(
                        "No working order " + orderId + " in " + accountId));
        String summary = "Cancel " + order.side() + " " + order.quantity() + " " + order.symbol()
                + " (" + orderId + ")";
        return pending.propose(summary,
                ignored -> "Cancellation requested: " + brokerage.cancel(order).status());
    }
}
