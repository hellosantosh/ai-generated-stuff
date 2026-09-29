package com.example.brokerage.api.orders;

import static org.springframework.hateoas.server.mvc.WebMvcLinkBuilder.afford;
import static org.springframework.hateoas.server.mvc.WebMvcLinkBuilder.linkTo;
import static org.springframework.hateoas.server.mvc.WebMvcLinkBuilder.methodOn;

import com.example.brokerage.api.accounts.AccountController;
import com.example.brokerage.api.market.InstrumentController;
import com.example.brokerage.api.platform.Caller;

import org.springframework.hateoas.EntityModel;
import org.springframework.hateoas.IanaLinkRelations;
import org.springframework.hateoas.Link;
import org.springframework.stereotype.Component;

/**
 * Builds an order's representation. This is where hypermedia earns its keep: the links
 * an order carries depend on its state and on the caller's token.
 *
 * <ul>
 *   <li>A working order, seen by a token with orders:write, offers "edit" (change it with
 *       PATCH) and "bk:cancel"; in HAL-FORMS both come with a template describing the
 *       request.</li>
 *   <li>A filled or canceled order offers neither. A client that shows buttons only for
 *       links that are present can never offer an action the server would refuse.</li>
 * </ul>
 */
@Component
public class OrderModelAssembler {

    public EntityModel<Order> toModel(Order order, Caller caller) {
        String accountId = order.accountId();
        String orderId = order.id();
        boolean changeable = order.status().isWorking() && caller.canTrade();

        Link self = linkTo(methodOn(OrderController.class).order(accountId, orderId, null, null))
                .withSelfRel();
        if (changeable) {
            self = self.andAffordance(afford(methodOn(OrderController.class)
                    .replaceOrder(accountId, orderId, null, null, null)));
        }
        EntityModel<Order> model = EntityModel.of(order, self,
                linkTo(methodOn(AccountController.class).account(accountId, null)).withRel("account"),
                linkTo(methodOn(InstrumentController.class).instrument(order.instrumentId()))
                        .withRel("instrument"));

        if (changeable) {
            model.add(linkTo(methodOn(OrderController.class).order(accountId, orderId, null, null))
                    .withRel(IanaLinkRelations.EDIT));
            model.add(linkTo(methodOn(OrderController.class).cancelOrder(accountId, orderId, null))
                    .withRel("cancel")
                    .andAffordance(
                            afford(methodOn(OrderController.class).cancelOrder(accountId, orderId, null))));
        }
        return model;
    }
}
