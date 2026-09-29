package com.example.brokerage.api.platform;

import java.math.BigDecimal;
import java.util.Set;

import com.example.brokerage.api.orders.Order;
import com.example.brokerage.api.orders.OrderRequest;

import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.hateoas.LinkRelation;
import org.springframework.hateoas.UriTemplate;
import org.springframework.hateoas.config.EnableHypermediaSupport;
import org.springframework.hateoas.config.EnableHypermediaSupport.HypermediaType;
import org.springframework.hateoas.mediatype.hal.CurieProvider;
import org.springframework.hateoas.mediatype.hal.DefaultCurieProvider;
import org.springframework.hateoas.mediatype.hal.HalLinkRelation;
import org.springframework.hateoas.mediatype.hal.forms.HalFormsConfiguration;
import org.springframework.hateoas.mediatype.hal.forms.HalFormsOptions;

/**
 * Hypermedia formats and link relations.
 *
 * <p>HAL (application/hal+json) is the default. Clients that ask for HAL-FORMS
 * (application/prs.hal-forms+json) also receive "_templates": machine-readable
 * descriptions of the state transitions available right now, such as placing,
 * changing or canceling an order.
 */
@Configuration(proxyBeanMethods = false)
@EnableHypermediaSupport(type = { HypermediaType.HAL, HypermediaType.HAL_FORMS })
public class HypermediaConfig {

    /** Documentation for every custom link relation lives at this URI template. */
    public static final String RELS = "https://docs.brokerage.example/rels/{rel}";

    /**
     * Relations that are not registered with IANA (like "orders") are custom, and HAL
     * requires custom relations to be URIs. A CURIE keeps them short on the wire:
     * "bk:orders" expands to https://docs.brokerage.example/rels/orders.
     */
    @Bean
    CurieProvider curieProvider() {
        return new DefaultCurieProvider("bk", UriTemplate.of(RELS)) {
            @Override
            public HalLinkRelation getNamespacedRelFor(LinkRelation relation) {
                // Registered by RFC 8631, but missing from Spring HATEOAS's list of IANA relations.
                return RFC_8631.contains(relation.value()) ? HalLinkRelation.uncuried(relation.value())
                        : super.getNamespacedRelFor(relation);
            }
        };
    }

    private static final Set<String> RFC_8631 = Set.of("service-desc", "service-doc", "service-meta");

    /** Offer the allowed values of enum fields inside HAL-FORMS templates. */
    @Bean
    HalFormsConfiguration halFormsConfiguration() {
        return new HalFormsConfiguration()
                // Decimals travel as strings: describe them as text that must look like a number.
                .withPattern(BigDecimal.class, "^[0-9]+(\\.[0-9]+)?$")
                .withOptions(OrderRequest.class, "side",
                        metadata -> HalFormsOptions.inline(Order.Side.values()))
                .withOptions(OrderRequest.class, "type",
                        metadata -> HalFormsOptions.inline(Order.Type.values()))
                .withOptions(OrderRequest.class, "timeInForce",
                        metadata -> HalFormsOptions.inline(Order.TimeInForce.values()));
    }
}
