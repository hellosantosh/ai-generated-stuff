package com.example.brokerage.api.orders;

import com.example.brokerage.api.market.MarketData;

import org.springframework.boot.autoconfigure.condition.ConditionalOnBooleanProperty;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;

/**
 * Drives the simulated market: every tick, prices move and open orders are matched.
 * Tests switch this off (brokerage.simulator.enabled=false) and call match() directly,
 * so that every price is known in advance.
 */
@Component
@ConditionalOnBooleanProperty(name = "brokerage.simulator.enabled", matchIfMissing = true)
class TradingSession {

    private final MarketData market;
    private final OrderService orders;

    TradingSession(MarketData market, OrderService orders) {
        this.market = market;
        this.orders = orders;
    }

    @Scheduled(fixedDelayString = "${brokerage.simulator.tick-millis:1000}")
    void tick() {
        market.move();
        orders.match();
    }
}
