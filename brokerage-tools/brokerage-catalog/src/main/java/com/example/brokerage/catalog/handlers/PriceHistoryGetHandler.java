package com.example.brokerage.catalog.handlers;

import java.math.BigDecimal;
import java.math.RoundingMode;
import java.time.LocalDate;
import java.util.Comparator;
import java.util.List;

import com.example.brokerage.domain.Brokerage;
import com.example.brokerage.domain.MarketData;
import com.example.brokerage.domain.Money;
import com.example.brokerage.tooling.contract.ErrorCode;
import com.example.brokerage.tooling.contract.Invocation;
import com.example.brokerage.tooling.contract.ToolFailure;
import com.example.brokerage.tooling.contract.ToolHandler;

import org.springframework.stereotype.Component;

/**
 * {@code brokerage_price_history_get}.
 *
 * <p>The interesting work here is the three summary fields. A model given 120 rows of OHLCV will
 * read some of them, do arithmetic in its head, and occasionally get it wrong — and the arithmetic
 * it is most likely to get wrong is the one it is most likely to be asked for, the period return.
 * Computing the answer in the handler costs nothing and removes a whole class of error.
 *
 * <p>This is a general rule worth stating plainly: <em>if a model will have to compute something
 * from your payload, and the computation is deterministic, compute it yourself.</em> Payload size
 * and arithmetic error go down together.
 */
@Component
public class PriceHistoryGetHandler implements ToolHandler {

    record BarView(LocalDate date, String open, String high, String low, String close,
                   long volume) {
    }

    record Payload(String symbol, String currency, LocalDate from, LocalDate to,
                   String periodHigh, String periodLow, String totalReturnPercent,
                   List<BarView> bars) {
    }

    private final Brokerage brokerage;

    public PriceHistoryGetHandler(Brokerage brokerage) {
        this.brokerage = brokerage;
    }

    @Override
    public String name() {
        return "brokerage_price_history_get";
    }

    @Override
    public Object handle(Invocation invocation) {
        String symbol = invocation.string("symbol");
        if (brokerage.instruments().find(symbol).isEmpty()) {
            throw ToolFailure.notFound(symbol,
                    "Call brokerage_instruments_search with what the customer said to find the "
                            + "right ticker, then try again.");
        }
        List<MarketData.Bar> bars = brokerage.marketData().history(symbol,
                invocation.integer("days", 30));
        if (bars.isEmpty()) {
            throw ToolFailure.of(ErrorCode.PRECONDITION_FAILED,
                    "There are no trading days in that window.",
                    "Ask for a longer window, or tell the customer the market has not been open.");
        }
        BigDecimal first = bars.getFirst().close();
        BigDecimal last = bars.getLast().close();
        BigDecimal high = bars.stream().map(MarketData.Bar::close).max(Comparator.naturalOrder())
                .orElse(last);
        BigDecimal low = bars.stream().map(MarketData.Bar::close).min(Comparator.naturalOrder())
                .orElse(last);
        BigDecimal totalReturn = first.signum() == 0 ? BigDecimal.ZERO
                : last.subtract(first).multiply(BigDecimal.valueOf(100))
                        .divide(first, 2, RoundingMode.HALF_UP);
        return new Payload(symbol, Money.USD, bars.getFirst().date(), bars.getLast().date(),
                Money.price(high), Money.price(low), totalReturn.toPlainString(),
                bars.stream().map(PriceHistoryGetHandler::view).toList());
    }

    private static BarView view(MarketData.Bar bar) {
        return new BarView(bar.date(), Money.price(bar.open()), Money.price(bar.high()),
                Money.price(bar.low()), Money.price(bar.close()), bar.volume());
    }
}
