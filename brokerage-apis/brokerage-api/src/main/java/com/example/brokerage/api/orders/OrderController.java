package com.example.brokerage.api.orders;

import static org.springframework.hateoas.server.mvc.WebMvcLinkBuilder.linkTo;
import static org.springframework.hateoas.server.mvc.WebMvcLinkBuilder.methodOn;

import java.util.List;

import jakarta.validation.Valid;

import com.example.brokerage.api.accounts.AccountController;
import com.example.brokerage.api.platform.Caller;
import com.example.brokerage.api.platform.CursorPage;

import org.springframework.hateoas.CollectionModel;
import org.springframework.hateoas.EntityModel;
import org.springframework.hateoas.IanaLinkRelations;
import org.springframework.http.HttpHeaders;
import org.springframework.http.ResponseEntity;
import org.springframework.security.core.annotation.AuthenticationPrincipal;
import org.springframework.security.oauth2.jwt.Jwt;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PatchMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestHeader;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.context.request.WebRequest;

/**
 * Orders. Reading needs orders:read; placing, changing and canceling need orders:write.
 */
@RestController
public class OrderController {

    public static final String IDEMPOTENCY_KEY = "Idempotency-Key";
    public static final String MERGE_PATCH_JSON = "application/merge-patch+json";

    private final OrderService service;
    private final OrderModelAssembler assembler;

    public OrderController(OrderService service, OrderModelAssembler assembler) {
        this.service = service;
        this.assembler = assembler;
    }

    /** Orders, newest first, optionally filtered by status (repeatable) and symbol. */
    @GetMapping("/accounts/{accountId}/orders")
    public CollectionModel<EntityModel<Order>> orders(@PathVariable String accountId,
            @RequestParam(required = false) List<Order.Status> status,
            @RequestParam(required = false) String symbol,
            @RequestParam(required = false) String cursor, @RequestParam(required = false) Integer limit,
            @AuthenticationPrincipal Jwt jwt) {
        Caller caller = Caller.of(jwt);
        CursorPage<Order> page = service.orders(caller, accountId, status, symbol, cursor, limit);
        CollectionModel<EntityModel<Order>> model = CollectionModel.of(
                page.items().stream().map(order -> assembler.toModel(order, caller)).toList(),
                linkTo(methodOn(OrderController.class).orders(accountId, status, symbol, cursor, limit, null))
                        .withSelfRel().expand(),
                linkTo(methodOn(AccountController.class).account(accountId, null)).withRel("account"));
        if (page.hasNext()) {
            model.add(linkTo(methodOn(OrderController.class)
                    .orders(accountId, status, symbol, page.nextCursor(), limit, null))
                    .withRel(IanaLinkRelations.NEXT).expand());
        }
        return model;
    }

    /**
     * Places an order: 201 Created with a Location header and the order's ETag. Retrying
     * with the same Idempotency-Key returns the original order, marked Idempotent-Replayed.
     */
    @PostMapping("/accounts/{accountId}/orders")
    public ResponseEntity<EntityModel<Order>> placeOrder(@PathVariable String accountId,
            @RequestHeader(name = IDEMPOTENCY_KEY, required = false) String idempotencyKey,
            @Valid @RequestBody OrderRequest request, @AuthenticationPrincipal Jwt jwt) {
        Caller caller = Caller.of(jwt);
        OrderService.Placement placement = service.place(caller, accountId, request, idempotencyKey);
        EntityModel<Order> model = assembler.toModel(placement.order(), caller);
        return ResponseEntity.created(model.getRequiredLink(IanaLinkRelations.SELF).toUri())
                .eTag(placement.order().etag())
                .header("Idempotent-Replayed", Boolean.toString(placement.replayed()))
                .body(model);
    }

    /** One order. Its ETag supports cheap polling (If-None-Match) and safe edits (If-Match). */
    @GetMapping("/accounts/{accountId}/orders/{orderId}")
    public ResponseEntity<EntityModel<Order>> order(@PathVariable String accountId,
            @PathVariable String orderId, WebRequest request, @AuthenticationPrincipal Jwt jwt) {
        Caller caller = Caller.of(jwt);
        Order order = service.order(caller, accountId, orderId);
        if (request.checkNotModified(order.etag())) {
            return null;
        }
        return ResponseEntity.ok().eTag(order.etag()).body(assembler.toModel(order, caller));
    }

    /** Changes a working order's quantity, prices or time in force. Requires If-Match. */
    @PatchMapping(path = "/accounts/{accountId}/orders/{orderId}",
            consumes = { MERGE_PATCH_JSON, "application/json" })
    public ResponseEntity<EntityModel<Order>> replaceOrder(@PathVariable String accountId,
            @PathVariable String orderId,
            @RequestHeader(name = HttpHeaders.IF_MATCH, required = false) String ifMatch,
            @Valid @RequestBody OrderAmendment change, @AuthenticationPrincipal Jwt jwt) {
        Caller caller = Caller.of(jwt);
        Order order = service.amend(caller, accountId, orderId, change, ifMatch);
        return ResponseEntity.ok().eTag(order.etag()).body(assembler.toModel(order, caller));
    }

    /**
     * Requests cancellation. 202 Accepted: the cancel is in progress, and the Location
     * header points at the order, whose status will become CANCELLED.
     */
    @PostMapping("/accounts/{accountId}/orders/{orderId}/cancellation")
    public ResponseEntity<EntityModel<Order>> cancelOrder(@PathVariable String accountId,
            @PathVariable String orderId, @AuthenticationPrincipal Jwt jwt) {
        Caller caller = Caller.of(jwt);
        Order order = service.cancel(caller, accountId, orderId);
        EntityModel<Order> model = assembler.toModel(order, caller);
        return ResponseEntity.accepted()
                .location(linkTo(methodOn(OrderController.class).order(accountId, orderId, null, null))
                        .toUri())
                .eTag(order.etag())
                .body(model);
    }

    /**
     * Estimates an order without placing it. Safe to call as often as a user edits a
     * ticket; when the order would be accepted, a link to place it is included.
     */
    @PostMapping("/accounts/{accountId}/order-previews")
    public EntityModel<OrderPreview> previewOrder(@PathVariable String accountId,
            @Valid @RequestBody OrderRequest request, @AuthenticationPrincipal Jwt jwt) {
        Caller caller = Caller.of(jwt);
        OrderPreview preview = service.preview(caller, accountId, request);
        EntityModel<OrderPreview> model = EntityModel.of(preview,
                linkTo(methodOn(AccountController.class).account(accountId, null)).withRel("account"));
        if (preview.acceptable() && caller.canTrade()) {
            // expand(): this link is a POST target, so the listing's query parameters do not apply
            model.add(linkTo(methodOn(OrderController.class).orders(accountId, null, null, null, null, null))
                    .withRel("place-order").expand());
        }
        return model;
    }
}
