package com.example.brokerage.api.accounts;

import static org.springframework.hateoas.server.mvc.WebMvcLinkBuilder.afford;
import static org.springframework.hateoas.server.mvc.WebMvcLinkBuilder.linkTo;
import static org.springframework.hateoas.server.mvc.WebMvcLinkBuilder.methodOn;

import java.time.LocalDate;
import java.time.ZoneOffset;
import java.util.List;

import com.example.brokerage.api.accounts.Holdings.Balances;
import com.example.brokerage.api.accounts.Holdings.Position;
import com.example.brokerage.api.market.InstrumentController;
import com.example.brokerage.api.orders.OrderController;
import com.example.brokerage.api.orders.OrderEventController;
import com.example.brokerage.api.platform.Caller;
import com.example.brokerage.api.platform.CursorPage;

import org.springframework.format.annotation.DateTimeFormat;
import org.springframework.hateoas.CollectionModel;
import org.springframework.hateoas.EntityModel;
import org.springframework.hateoas.Link;
import org.springframework.security.core.annotation.AuthenticationPrincipal;
import org.springframework.security.oauth2.jwt.Jwt;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

/**
 * Accounts and everything read-only beneath them. Scope: accounts:read.
 */
@RestController
public class AccountController {

    private final Ledger ledger;
    private final Portfolio portfolio;

    public AccountController(Ledger ledger, Portfolio portfolio) {
        this.ledger = ledger;
        this.portfolio = portfolio;
    }

    @GetMapping("/accounts")
    public CollectionModel<EntityModel<Account>> accounts(@AuthenticationPrincipal Jwt jwt) {
        Caller caller = Caller.of(jwt);
        List<EntityModel<Account>> accounts = ledger.accounts(caller.customerId()).stream()
                .map(account -> toModel(account, caller))
                .toList();
        return CollectionModel.of(accounts,
                linkTo(methodOn(AccountController.class).accounts(null)).withSelfRel());
    }

    @GetMapping("/accounts/{accountId}")
    public EntityModel<Account> account(@PathVariable String accountId,
            @AuthenticationPrincipal Jwt jwt) {
        Caller caller = Caller.of(jwt);
        return toModel(ledger.account(caller.customerId(), accountId), caller);
    }

    @GetMapping("/accounts/{accountId}/balances")
    public EntityModel<Balances> balances(@PathVariable String accountId,
            @AuthenticationPrincipal Jwt jwt) {
        Account account = ledger.account(Caller.of(jwt).customerId(), accountId);
        return EntityModel.of(portfolio.balances(account),
                linkTo(methodOn(AccountController.class).balances(accountId, null)).withSelfRel(),
                linkTo(methodOn(AccountController.class).account(accountId, null)).withRel("account"),
                linkTo(methodOn(AccountController.class).positions(accountId, null)).withRel("positions"));
    }

    @GetMapping("/accounts/{accountId}/positions")
    public CollectionModel<EntityModel<Position>> positions(@PathVariable String accountId,
            @AuthenticationPrincipal Jwt jwt) {
        Account account = ledger.account(Caller.of(jwt).customerId(), accountId);
        List<EntityModel<Position>> positions = portfolio.positions(account).stream()
                .map(position -> toModel(accountId, position))
                .toList();
        return CollectionModel.of(positions,
                linkTo(methodOn(AccountController.class).positions(accountId, null)).withSelfRel(),
                linkTo(methodOn(AccountController.class).account(accountId, null)).withRel("account"));
    }

    @GetMapping("/accounts/{accountId}/positions/{instrumentId}")
    public EntityModel<Position> position(@PathVariable String accountId,
            @PathVariable String instrumentId, @AuthenticationPrincipal Jwt jwt) {
        Account account = ledger.account(Caller.of(jwt).customerId(), accountId);
        return toModel(accountId, portfolio.position(account, instrumentId));
    }

    /**
     * Transaction history, newest first, filterable by type and by an inclusive date range
     * (UTC), paged with an opaque cursor.
     */
    @GetMapping("/accounts/{accountId}/transactions")
    public CollectionModel<EntityModel<Transaction>> transactions(@PathVariable String accountId,
            @RequestParam(required = false) Transaction.Type type,
            @RequestParam(required = false) @DateTimeFormat(iso = DateTimeFormat.ISO.DATE) LocalDate from,
            @RequestParam(required = false) @DateTimeFormat(iso = DateTimeFormat.ISO.DATE) LocalDate to,
            @RequestParam(required = false) String cursor, @RequestParam(required = false) Integer limit,
            @AuthenticationPrincipal Jwt jwt) {
        Account account = ledger.account(Caller.of(jwt).customerId(), accountId);
        List<Transaction> matching = ledger.transactions(account.id()).stream()
                .filter(t -> type == null || t.type() == type)
                .filter(t -> from == null || !utcDate(t).isBefore(from))
                .filter(t -> to == null || !utcDate(t).isAfter(to))
                .toList();
        CursorPage<Transaction> page = CursorPage.of(matching, Transaction::occurredAt, Transaction::id,
                cursor, limit);

        CollectionModel<EntityModel<Transaction>> model = CollectionModel.of(
                page.items().stream().map(EntityModel::of).toList(),
                linkTo(methodOn(AccountController.class)
                        .transactions(accountId, type, from, to, cursor, limit, null))
                        .withSelfRel().expand(),
                linkTo(methodOn(AccountController.class).account(accountId, null)).withRel("account"));
        if (page.hasNext()) {
            // expand(): "next" is a concrete URL, not a template with the unused filters left open
            model.add(linkTo(methodOn(AccountController.class)
                    .transactions(accountId, type, from, to, page.nextCursor(), limit, null))
                    .withRel("next").expand());
        }
        return model;
    }

    private static LocalDate utcDate(Transaction transaction) {
        return transaction.occurredAt().atZone(ZoneOffset.UTC).toLocalDate();
    }

    // ---------------------------------------------------------------- representations

    /**
     * An account's links depend on what the caller may do: a token without orders:read is
     * not shown the orders at all, and only a token with orders:write is offered the
     * "placeOrder" affordance. Clients discover their capabilities instead of hard-coding them.
     */
    EntityModel<Account> toModel(Account account, Caller caller) {
        String id = account.id();
        EntityModel<Account> model = EntityModel.of(account,
                linkTo(methodOn(AccountController.class).account(id, null)).withSelfRel(),
                linkTo(methodOn(AccountController.class).balances(id, null)).withRel("balances"),
                linkTo(methodOn(AccountController.class).positions(id, null)).withRel("positions"),
                linkTo(methodOn(AccountController.class).transactions(id, null, null, null, null, null, null))
                        .withRel("transactions"));
        if (caller.canReadOrders()) {
            Link orders = linkTo(methodOn(OrderController.class).orders(id, null, null, null, null, null))
                    .withRel("orders");
            if (caller.canTrade()) {
                orders = orders.andAffordance(
                        afford(methodOn(OrderController.class).placeOrder(id, null, null, null)));
            }
            model.add(orders,
                    linkTo(methodOn(OrderController.class).previewOrder(id, null, null))
                            .withRel("order-previews"),
                    linkTo(methodOn(OrderEventController.class).events(id, null, null))
                            .withRel("order-events"));
        }
        return model;
    }

    private EntityModel<Position> toModel(String accountId, Position position) {
        return EntityModel.of(position,
                linkTo(methodOn(AccountController.class).position(accountId, position.instrumentId(), null))
                        .withSelfRel(),
                linkTo(methodOn(InstrumentController.class).instrument(position.instrumentId()))
                        .withRel("instrument"),
                linkTo(methodOn(InstrumentController.class).quote(position.instrumentId(), null))
                        .withRel("quote"));
    }
}
