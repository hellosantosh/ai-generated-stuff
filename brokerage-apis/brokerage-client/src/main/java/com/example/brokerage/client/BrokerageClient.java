package com.example.brokerage.client;

import java.net.URI;
import java.time.Duration;
import java.util.List;
import java.util.Map;

import com.example.brokerage.client.Model.Account;
import com.example.brokerage.client.Model.Balances;
import com.example.brokerage.client.Model.Instrument;
import com.example.brokerage.client.Model.Order;
import com.example.brokerage.client.Model.OrderPreview;
import com.example.brokerage.client.Model.OrderTicket;
import com.example.brokerage.client.Model.Position;
import com.example.brokerage.client.Model.Quote;

import org.springframework.hateoas.Link;
import org.springframework.web.client.ResourceAccessException;

/**
 * The brokerage, as a Java API. Every method starts from the API root and follows link
 * relations; not one URL is built here. If the server moves a resource, this class keeps
 * working, and if the server withholds a link, the corresponding action is unavailable.
 */
public class BrokerageClient {

    private static final int ATTEMPTS = 3;

    private final HypermediaClient http;
    private final URI root;

    public BrokerageClient(HypermediaClient http, URI root) {
        this.http = http;
        this.root = root;
    }

    // ---------------------------------------------------------------- accounts

    public List<Account> accounts() {
        return accountResources().stream().map(account -> account.as(Account.class)).toList();
    }

    public Balances balances(String accountId) {
        return follow(account(accountId), "bk:balances").as(Balances.class);
    }

    public List<Position> positions(String accountId) {
        return follow(account(accountId), "bk:positions").embedded("bk:positions").stream()
                .map(position -> position.as(Position.class)).toList();
    }

    // ---------------------------------------------------------------- market data

    public Instrument instrument(String symbol) {
        return instrumentResource(symbol).as(Instrument.class);
    }

    /** The latest quote, in the version 2 shape that the client pins on every request. */
    public Quote quote(String symbol) {
        Link quote = instrumentResource(symbol).requiredLink("bk:quote");
        return http.get(quote.toUri()).as(Quote.class);
    }

    // ---------------------------------------------------------------- trading

    public OrderPreview preview(String accountId, OrderTicket ticket) {
        Link previews = account(accountId).requiredLink("bk:order-previews");
        return http.post(previews, ticket, headers -> { }).as(OrderPreview.class);
    }

    /**
     * Places an order. On a timeout or a transient error the request is retried with the
     * same Idempotency-Key, so a retry can never place a second order.
     */
    public Order place(String accountId, OrderTicket ticket, String idempotencyKey) {
        Link orders = account(accountId).requiredLink("bk:orders").expand();
        for (int attempt = 1; ; attempt++) {
            try {
                return http.post(orders, ticket, headers -> headers.set("Idempotency-Key", idempotencyKey))
                        .as(Order.class);
            } catch (ResourceAccessException | ApiProblem failure) {
                boolean retryable = !(failure instanceof ApiProblem problem) || problem.isTransient();
                if (!retryable || attempt == ATTEMPTS) {
                    throw failure;
                }
                long seconds = failure instanceof ApiProblem problem
                        ? problem.retryAfterSeconds().orElse(1L) : attempt;
                pause(seconds);
            }
        }
    }

    public List<Order> openOrders(String accountId) {
        Link orders = account(accountId).requiredLink("bk:orders");
        URI open = orders.expand(Map.of("status", List.of("OPEN", "PARTIALLY_FILLED"))).toUri();
        return http.get(open).embedded("bk:orders").stream().map(order -> order.as(Order.class)).toList();
    }

    /** Re-reads an order through its own self link. */
    public Order refresh(Order order) {
        return http.get(order.self()).as(Order.class);
    }

    /** Cancels an order, if and only if the server still offers the cancel link. */
    public Order cancel(Order order) {
        URI cancel = order.link("bk:cancel").orElseThrow(() -> new IllegalStateException(
                "Order " + order.id() + " is " + order.status() + " and can no longer be canceled"));
        return http.post(Link.of(cancel.toString()), null, headers -> { }).as(Order.class);
    }

    // ---------------------------------------------------------------- navigation

    private List<HalResource> accountResources() {
        HalResource start = http.get(root);
        return follow(start, "bk:accounts").embedded("bk:accounts");
    }

    private HalResource account(String accountId) {
        return accountResources().stream()
                .filter(account -> accountId.equals(account.body().path("id").asString()))
                .findFirst()
                .orElseThrow(() -> new IllegalArgumentException(
                        "No account " + accountId + " for this customer"));
    }

    private HalResource instrumentResource(String symbol) {
        Link search = http.get(root).requiredLink("bk:instruments");
        return http.get(search.expand(Map.of("symbol", symbol)).toUri()).embedded("bk:instruments").stream()
                .findFirst()
                .orElseThrow(() -> new IllegalArgumentException("Unknown symbol " + symbol));
    }

    private HalResource follow(HalResource from, String rel) {
        return http.get(from.requiredLink(rel).expand().toUri());
    }

    private static void pause(long seconds) {
        try {
            Thread.sleep(Duration.ofSeconds(Math.min(seconds, 5)));
        } catch (InterruptedException interrupted) {
            Thread.currentThread().interrupt();
            throw new IllegalStateException(interrupted);
        }
    }
}
