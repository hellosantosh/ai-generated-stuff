package com.example.brokerage.client;

import java.math.BigDecimal;
import java.net.URI;
import java.util.UUID;

import com.example.brokerage.client.Model.Order;
import com.example.brokerage.client.Model.OrderTicket;

import org.springframework.web.client.RestClient;

/**
 * A guided tour of the API through the hypermedia client. Start the authorization server
 * and the API first, then run:
 *
 * <pre>
 *   ./mvnw -q -pl brokerage-client compile exec:java -Dexec.mainClass=com.example.brokerage.client.Demo
 * </pre>
 */
public final class Demo {

    public static void main(String[] args) throws InterruptedException {
        ClientCredentials tokens = new ClientCredentials(URI.create("http://localhost:9000/oauth2/token"),
                "brokerage-cli", "cli-secret", "accounts:read market-data:read orders:read orders:write");
        HypermediaClient http = new HypermediaClient(RestClient.builder()
                .requestInterceptor((request, body, execution) -> {
                    request.getHeaders().setBearerAuth(tokens.get());
                    return execution.execute(request, body);
                }));
        BrokerageClient brokerage = new BrokerageClient(http, URI.create("http://localhost:8080/"));

        System.out.println("Accounts");
        brokerage.accounts().forEach(account -> System.out.printf("  %-9s %-18s %s%n",
                account.id(), account.nickname(), account.type()));

        var balances = brokerage.balances("ACC-1001");
        System.out.printf("%nACC-1001: cash %s, buying power %s, equity %s%n",
                balances.cash(), balances.buyingPower(), balances.totalEquity());

        System.out.println("\nPositions");
        brokerage.positions("ACC-1001").forEach(p -> System.out.printf("  %-5s %6s @ %-9s P&L %s%n",
                p.symbol(), p.quantity(), p.lastPrice(), p.unrealizedPnl()));

        var quote = brokerage.quote("AAPL");
        System.out.printf("%nAAPL %s  bid %s  ask %s  (%s%%)%n",
                quote.last(), quote.bid().price(), quote.ask().price(), quote.changePercent());

        // A limit order well below the market, so that it rests and can be canceled.
        BigDecimal price = quote.last().multiply(new BigDecimal("0.90"))
                .setScale(2, java.math.RoundingMode.DOWN);
        OrderTicket ticket = OrderTicket.limit("EQ-AAPL", "BUY", new BigDecimal("5"), price);
        var preview = brokerage.preview("ACC-1001", ticket);
        System.out.printf("%nPreview: 5 AAPL at %s costs %s (acceptable: %s)%n",
                price, preview.estimatedTotal(), preview.acceptable());

        Order order = brokerage.place("ACC-1001", ticket, UUID.randomUUID().toString());
        System.out.printf("Placed %s: %s, cancelable: %s%n", order.id(), order.status(),
                order.isCancellable());

        order = brokerage.cancel(order);
        System.out.printf("Cancel requested: %s%n", order.status());
        while (order.isCancellable() || "PENDING_CANCEL".equals(order.status())) {
            Thread.sleep(500);
            order = brokerage.refresh(order);
        }
        System.out.printf("Now %s, cancelable: %s%n", order.status(), order.isCancellable());
    }

    private Demo() {
    }
}
