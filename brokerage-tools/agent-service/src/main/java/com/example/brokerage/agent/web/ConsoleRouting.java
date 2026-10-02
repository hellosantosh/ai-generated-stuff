package com.example.brokerage.agent.web;

import org.springframework.context.annotation.Configuration;
import org.springframework.web.servlet.config.annotation.ViewControllerRegistry;
import org.springframework.web.servlet.config.annotation.WebMvcConfigurer;

/**
 * Deep links into the console.
 *
 * <p>The console does its own routing, so {@code /ontology} is a path the browser understands and
 * the server has never heard of. Without this, opening the console at a page and refreshing gives
 * a 404 — which is the single most common complaint about a single-page application served from a
 * Spring Boot jar.
 *
 * <p>Only the four known paths are forwarded, rather than a catch-all. A catch-all would also
 * swallow a mistyped {@code /api} call and answer it with HTML, and debugging "why is my JSON an
 * HTML document" is a worse afternoon than adding a line here when a page is added.
 */
@Configuration
public class ConsoleRouting implements WebMvcConfigurer {

    @Override
    public void addViewControllers(ViewControllerRegistry registry) {
        for (String page : new String[] {"catalog", "ontology", "agent", "governance"}) {
            registry.addViewController("/" + page).setViewName("forward:/index.html");
        }
    }
}
