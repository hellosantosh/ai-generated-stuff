package com.example.brokerage.api;

import static org.springframework.hateoas.server.mvc.WebMvcLinkBuilder.linkTo;
import static org.springframework.hateoas.server.mvc.WebMvcLinkBuilder.methodOn;

import java.time.Duration;

import com.example.brokerage.api.accounts.AccountController;
import com.example.brokerage.api.market.InstrumentController;

import org.springframework.core.io.ClassPathResource;
import org.springframework.core.io.Resource;
import org.springframework.hateoas.Link;
import org.springframework.hateoas.RepresentationModel;
import org.springframework.http.CacheControl;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.servlet.support.ServletUriComponentsBuilder;

/**
 * The API root: the one URL a client needs to know. Everything else is discovered by
 * following links from here, so the server is free to reorganize every other URI.
 */
@RestController
public class RootController {

    /** The root's own properties, beside its links. */
    public static class ApiRoot extends RepresentationModel<ApiRoot> {

        public final String name = "Brokerage API";
        public final String description = "Accounts, positions, market data and trading, as hypermedia";
    }

    @GetMapping("/")
    public ApiRoot root() {
        String base = ServletUriComponentsBuilder.fromCurrentContextPath().toUriString();
        return new ApiRoot()
                .add(linkTo(methodOn(RootController.class).root()).withSelfRel())
                .add(linkTo(methodOn(AccountController.class).accounts(null)).withRel("accounts"))
                .add(linkTo(methodOn(InstrumentController.class).search(null, null)).withRel("instruments"))
                // RFC 8631: the machine-readable description of this API ...
                .add(Link.of(base + "/openapi.yaml", "service-desc").withType("application/openapi+yaml"))
                // ... and its human-readable documentation.
                .add(Link.of("https://docs.brokerage.example", "service-doc").withType("text/html"))
                // RFC 9728: where clients learn which authorization server issues tokens for this API.
                .add(Link.of(base + "/.well-known/oauth-protected-resource", "oauth-protected-resource"));
    }

    /** The OpenAPI contract, served by the application it describes so the two ship together. */
    @GetMapping(path = "/openapi.yaml", produces = "application/openapi+yaml")
    public ResponseEntity<Resource> contract() {
        return ResponseEntity.ok()
                .cacheControl(CacheControl.maxAge(Duration.ofHours(1)))
                .body(new ClassPathResource("contract/brokerage-api.yaml"));
    }
}
