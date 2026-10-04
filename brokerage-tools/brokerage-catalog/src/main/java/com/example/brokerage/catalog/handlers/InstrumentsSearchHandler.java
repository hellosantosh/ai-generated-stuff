package com.example.brokerage.catalog.handlers;

import java.util.List;
import java.util.Locale;

import com.example.brokerage.domain.Brokerage;
import com.example.brokerage.domain.Instrument;
import com.example.brokerage.tooling.contract.Invocation;
import com.example.brokerage.tooling.contract.ToolHandler;

import org.springframework.stereotype.Component;

/**
 * {@code brokerage_instruments_search}.
 *
 * <p>This handler's whole design is one decision: it does not choose. It returns candidates and an
 * {@code exactMatch} flag, and leaves the choosing to the agent, which will put it to the customer.
 *
 * <p>The temptation to choose is strong, because 90 percent of queries have an obvious answer and
 * returning one row makes the agent's job easier. The 10 percent is the problem. "Buy me some
 * Apple" has two plausible tickers, "buy Google" has two share classes, and an order is not
 * reversible. A tool that silently resolves ambiguity has moved a decision from the customer to a
 * ranking function, and nobody reviewed the ranking function.
 */
@Component
public class InstrumentsSearchHandler implements ToolHandler {

    record Candidate(String symbol, String name, String exchange, String type, String sector,
                     boolean marginEligible) {
    }

    record Payload(boolean exactMatch, List<Candidate> candidates) {
    }

    private final Brokerage brokerage;

    public InstrumentsSearchHandler(Brokerage brokerage) {
        this.brokerage = brokerage;
    }

    @Override
    public String name() {
        return "brokerage_instruments_search";
    }

    @Override
    public Object handle(Invocation invocation) {
        String query = invocation.string("query").trim();
        int limit = invocation.integer("limit", 5);
        List<Instrument> found = brokerage.instruments().search(query, limit);
        boolean exact = found.size() == 1
                && found.getFirst().symbol().equals(query.toUpperCase(Locale.ROOT));
        return new Payload(exact, found.stream().map(InstrumentsSearchHandler::candidate).toList());
    }

    private static Candidate candidate(Instrument instrument) {
        return new Candidate(instrument.symbol(), instrument.name(), instrument.exchange(),
                instrument.type().name(), instrument.sector(), instrument.marginEligible());
    }
}
