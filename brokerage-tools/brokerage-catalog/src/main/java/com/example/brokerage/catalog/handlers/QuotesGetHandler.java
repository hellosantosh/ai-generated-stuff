package com.example.brokerage.catalog.handlers;

import java.time.Instant;
import java.util.ArrayList;
import java.util.List;

import com.example.brokerage.domain.Brokerage;
import com.example.brokerage.domain.MarketData;
import com.example.brokerage.domain.Money;
import com.example.brokerage.tooling.contract.Invocation;
import com.example.brokerage.tooling.contract.ToolHandler;

import org.springframework.stereotype.Component;
import tools.jackson.databind.JsonNode;

/**
 * {@code brokerage_quotes_get}.
 *
 * <p>Plural by design. An agent comparing three holdings will call a singular quote tool three
 * times, and three round trips cost three times the latency, three audit records and three
 * opportunities for one of them to fail on its own. Batching is the single cheapest performance
 * decision in a tool catalog and it is made in the descriptor, not in the handler.
 *
 * <p>The {@code unresolved} array matters more than it looks. A tool that quietly drops the
 * symbols it could not price leaves the agent to notice that it asked for three and got two, and
 * it will not notice. Naming the failures makes the agent's next move obvious.
 */
@Component
public class QuotesGetHandler implements ToolHandler {

    record QuoteView(String symbol, String last, String bid, String ask, long bidSize,
                     long askSize, String previousClose, String change, String changePercent,
                     long volume, int spreadBasisPoints, String currency, Instant asOf) {
    }

    record Payload(String session, List<QuoteView> quotes, List<String> unresolved) {
    }

    private final Brokerage brokerage;

    public QuotesGetHandler(Brokerage brokerage) {
        this.brokerage = brokerage;
    }

    @Override
    public String name() {
        return "brokerage_quotes_get";
    }

    @Override
    public Object handle(Invocation invocation) {
        List<QuoteView> quotes = new ArrayList<>();
        List<String> unresolved = new ArrayList<>();
        for (JsonNode node : invocation.node("symbols").values()) {
            String symbol = node.asString();
            if (brokerage.instruments().find(symbol).isEmpty()) {
                unresolved.add(symbol);
                continue;
            }
            quotes.add(view(brokerage.marketData().quote(symbol)));
        }
        return new Payload(brokerage.clock().session().name(), quotes, unresolved);
    }

    private static QuoteView view(MarketData.Quote quote) {
        return new QuoteView(quote.symbol(), Money.price(quote.last()), Money.price(quote.bid()),
                Money.price(quote.ask()), quote.bidSize(), quote.askSize(),
                Money.price(quote.previousClose()), Money.string(quote.change()),
                Money.string(quote.changePercent()), quote.volume(), quote.spreadBasisPoints(),
                Money.USD, quote.asOf());
    }
}
