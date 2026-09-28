package com.example.brokerage.api.market;

import static org.springframework.hateoas.server.mvc.WebMvcLinkBuilder.linkTo;
import static org.springframework.hateoas.server.mvc.WebMvcLinkBuilder.methodOn;

import java.time.LocalDate;
import java.util.List;
import java.util.concurrent.TimeUnit;

import com.example.brokerage.api.market.Instrument.Type;
import com.example.brokerage.api.market.Quotes.Quote;
import com.example.brokerage.api.market.Quotes.QuoteV1;

import org.springframework.format.annotation.DateTimeFormat;
import org.springframework.hateoas.CollectionModel;
import org.springframework.hateoas.EntityModel;
import org.springframework.http.CacheControl;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.context.request.WebRequest;

/**
 * Instruments, quotes and option chains. Scope: market-data:read.
 */
@RestController
public class InstrumentController {

    private final MarketData market;

    public InstrumentController(MarketData market) {
        this.market = market;
    }

    @GetMapping("/instruments")
    public CollectionModel<EntityModel<Instrument>> search(
            @RequestParam(required = false) String symbol, @RequestParam(required = false) Type type) {
        List<EntityModel<Instrument>> found = market.search(symbol, type).stream()
                .map(this::toModel)
                .toList();
        return CollectionModel.of(found,
                linkTo(methodOn(InstrumentController.class).search(symbol, type)).withSelfRel().expand());
    }

    @GetMapping("/instruments/{instrumentId}")
    public EntityModel<Instrument> instrument(@PathVariable String instrumentId) {
        return toModel(market.instrument(instrumentId));
    }

    /** Version 1 of a quote (deprecated; see WebConfig). */
    @GetMapping(path = "/instruments/{instrumentId}/quote", version = "1")
    public ResponseEntity<EntityModel<QuoteV1>> quoteV1(@PathVariable String instrumentId,
            WebRequest request) {
        Instrument instrument = market.instrument(instrumentId);
        MarketData.Tick tick = market.tick(instrumentId);
        return quoteResponse(instrument, tick, QuoteV1.of(instrument, tick), request);
    }

    @GetMapping(path = "/instruments/{instrumentId}/quote", version = "2")
    public ResponseEntity<EntityModel<Quote>> quote(@PathVariable String instrumentId,
            WebRequest request) {
        Instrument instrument = market.instrument(instrumentId);
        MarketData.Tick tick = market.tick(instrumentId);
        return quoteResponse(instrument, tick, Quote.of(instrument, tick), request);
    }

    /**
     * Quotes change every second, so they may be cached only briefly and only privately
     * (market data is licensed per user). The ETag lets a client poll cheaply: an unchanged
     * quote costs a 304 with no body.
     */
    private <T> ResponseEntity<EntityModel<T>> quoteResponse(Instrument instrument, MarketData.Tick tick,
            T quote, WebRequest request) {
        String etag = "\"q" + tick.sequence() + "\"";
        if (request.checkNotModified(etag)) {
            return null;
        }
        EntityModel<T> model = EntityModel.of(quote,
                linkTo(methodOn(InstrumentController.class).quote(instrument.id(), null)).withSelfRel(),
                linkTo(methodOn(InstrumentController.class).instrument(instrument.id()))
                        .withRel("instrument"));
        return ResponseEntity.ok()
                .cacheControl(CacheControl.maxAge(1, TimeUnit.SECONDS).cachePrivate())
                .eTag(etag)
                .body(model);
    }

    /**
     * The option chain for one expiration. Every available expiration is offered as a
     * link with the same relation, told apart by its "name": the client picks one instead
     * of guessing dates.
     */
    @GetMapping("/instruments/{instrumentId}/option-chain")
    public CollectionModel<EntityModel<Instrument>> optionChain(@PathVariable String instrumentId,
            @RequestParam(required = false) @DateTimeFormat(iso = DateTimeFormat.ISO.DATE)
            LocalDate expiration) {
        Instrument underlying = market.instrument(instrumentId);
        List<LocalDate> expirations = market.expirations(underlying.id());
        LocalDate chosen = expiration != null ? expiration
                : expirations.isEmpty() ? null : expirations.getFirst();
        List<EntityModel<Instrument>> contracts = chosen == null ? List.of()
                : market.optionChain(underlying.id(), chosen).stream().map(this::toModel).toList();

        CollectionModel<EntityModel<Instrument>> chain = CollectionModel.of(contracts,
                linkTo(methodOn(InstrumentController.class).optionChain(underlying.id(), chosen))
                        .withSelfRel(),
                linkTo(methodOn(InstrumentController.class).instrument(underlying.id()))
                        .withRel("underlying"));
        for (LocalDate date : expirations) {
            chain.add(linkTo(methodOn(InstrumentController.class).optionChain(underlying.id(), date))
                    .withRel("expiration").withName(date.toString()));
        }
        return chain;
    }

    private EntityModel<Instrument> toModel(Instrument instrument) {
        EntityModel<Instrument> model = EntityModel.of(instrument,
                linkTo(methodOn(InstrumentController.class).instrument(instrument.id())).withSelfRel(),
                linkTo(methodOn(InstrumentController.class).quote(instrument.id(), null))
                        .withRel("quote"));
        if (instrument.isOption()) {
            String underlyingId = instrument.option().underlyingId();
            model.add(linkTo(methodOn(InstrumentController.class).instrument(underlyingId))
                    .withRel("underlying"));
        } else if (!market.expirations(instrument.id()).isEmpty()) {
            model.add(linkTo(methodOn(InstrumentController.class).optionChain(instrument.id(), null))
                    .withRel("option-chain"));
        }
        return model;
    }
}
