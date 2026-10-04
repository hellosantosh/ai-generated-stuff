package com.example.brokerage.domain;

import java.util.Comparator;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Optional;

import org.springframework.stereotype.Component;

/**
 * The instrument master, small enough to read and large enough to be ambiguous on purpose.
 *
 * <p>Several entries exist only to make the agent behave correctly. {@code APLE} and {@code AAPL}
 * are both real tickers and both match the word "apple", which is the case that separates an agent
 * that asks from an agent that guesses. {@code GOOGL} and {@code GOOG} are the same company with
 * different voting rights, so "buy Google" has no single right answer. And {@code BRK.B} has a
 * dot in it, which is how you find out whether a tool's symbol pattern was written from the
 * specification or from the first five symbols the author happened to try.
 */
@Component
public class Instruments {

    private final Map<String, Instrument> bySymbol = new LinkedHashMap<>();

    public Instruments() {
        add("AAPL", "Apple Inc.", "NASDAQ", Instrument.Type.EQUITY, "Information Technology",
                "234.50", true, true);
        add("APLE", "Apple Hospitality REIT, Inc.", "NYSE", Instrument.Type.EQUITY, "Real Estate",
                "13.82", true, false);
        add("MSFT", "Microsoft Corporation", "NASDAQ", Instrument.Type.EQUITY,
                "Information Technology", "512.18", true, true);
        add("GOOGL", "Alphabet Inc. Class A", "NASDAQ", Instrument.Type.EQUITY,
                "Communication Services", "248.90", true, true);
        add("GOOG", "Alphabet Inc. Class C", "NASDAQ", Instrument.Type.EQUITY,
                "Communication Services", "250.11", true, true);
        add("AMZN", "Amazon.com, Inc.", "NASDAQ", Instrument.Type.EQUITY, "Consumer Discretionary",
                "231.44", true, true);
        add("NVDA", "NVIDIA Corporation", "NASDAQ", Instrument.Type.EQUITY,
                "Information Technology", "184.62", true, true);
        add("TSLA", "Tesla, Inc.", "NASDAQ", Instrument.Type.EQUITY, "Consumer Discretionary",
                "412.07", true, true);
        add("JPM", "JPMorgan Chase & Co.", "NYSE", Instrument.Type.EQUITY, "Financials",
                "312.55", true, false);
        add("KO", "The Coca-Cola Company", "NYSE", Instrument.Type.EQUITY, "Consumer Staples",
                "71.20", true, false);
        add("XOM", "Exxon Mobil Corporation", "NYSE", Instrument.Type.EQUITY, "Energy",
                "118.73", true, false);
        add("BRK.B", "Berkshire Hathaway Inc. Class B", "NYSE", Instrument.Type.EQUITY,
                "Financials", "498.30", true, false);
        add("VOO", "Vanguard S&P 500 ETF", "NYSEARCA", Instrument.Type.ETF, "Broad Market",
                "604.15", true, true);
        add("QQQM", "Invesco NASDAQ 100 ETF", "NASDAQ", Instrument.Type.ETF, "Broad Market",
                "248.03", true, true);
        add("SCHD", "Schwab US Dividend Equity ETF", "NYSEARCA", Instrument.Type.ETF, "Dividend",
                "27.94", true, true);
        add("GME", "GameStop Corp.", "NYSE", Instrument.Type.EQUITY, "Consumer Discretionary",
                "22.41", false, false);
        add("TSM", "Taiwan Semiconductor Manufacturing ADR", "NYSE", Instrument.Type.ADR,
                "Information Technology", "288.60", true, false);
    }

    private void add(String symbol, String name, String exchange, Instrument.Type type,
                     String sector, String price, boolean marginEligible, boolean fractionable) {
        bySymbol.put(symbol, new Instrument(symbol, name, exchange, type, sector,
                Money.of(price), marginEligible, fractionable));
    }

    public Optional<Instrument> find(String symbol) {
        return Optional.ofNullable(bySymbol.get(symbol == null ? "" : symbol.toUpperCase(Locale.ROOT)));
    }

    public Instrument require(String symbol) {
        return find(symbol).orElseThrow(() ->
                new IllegalArgumentException("no instrument " + symbol));
    }

    public List<Instrument> all() {
        return List.copyOf(bySymbol.values());
    }

    /**
     * Candidates for a free-text query, best first. The score is deliberately crude — an exact
     * ticker beats a name prefix, which beats a name containing the words — because the tool's job
     * is not to pick but to hand the agent a short list with enough on each row to ask a sensible
     * question.
     */
    public List<Instrument> search(String query, int limit) {
        String q = query == null ? "" : query.trim().toUpperCase(Locale.ROOT);
        if (q.isEmpty()) {
            return List.of();
        }
        return bySymbol.values().stream()
                .map(instrument -> Map.entry(instrument, score(instrument, q)))
                .filter(entry -> entry.getValue() > 0)
                .sorted(Comparator.<Map.Entry<Instrument, Integer>>comparingInt(Map.Entry::getValue)
                        .reversed()
                        .thenComparing(entry -> entry.getKey().symbol()))
                .limit(limit)
                .map(Map.Entry::getKey)
                .toList();
    }

    private static int score(Instrument instrument, String query) {
        String name = instrument.name().toUpperCase(Locale.ROOT);
        if (instrument.symbol().equals(query)) {
            return 100;
        }
        if (name.startsWith(query)) {
            return 60;
        }
        if (instrument.symbol().startsWith(query)) {
            return 50;
        }
        if (name.contains(query)) {
            return 40;
        }
        String firstWord = query.split("\\s+")[0];
        return firstWord.length() >= 3 && name.contains(firstWord) ? 20 : 0;
    }
}
