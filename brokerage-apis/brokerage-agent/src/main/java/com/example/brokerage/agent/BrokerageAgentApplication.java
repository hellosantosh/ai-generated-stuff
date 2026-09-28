package com.example.brokerage.agent;

import java.net.URI;
import java.time.Clock;

import com.example.brokerage.client.BrokerageClient;
import com.example.brokerage.client.HypermediaClient;

import org.springframework.ai.chat.client.ChatClient;
import org.springframework.ai.chat.client.advisor.MessageChatMemoryAdvisor;
import org.springframework.ai.chat.memory.MessageWindowChatMemory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;
import org.springframework.context.annotation.Bean;
import org.springframework.security.oauth2.client.AuthorizedClientServiceOAuth2AuthorizedClientManager;
import org.springframework.security.oauth2.client.InMemoryOAuth2AuthorizedClientService;
import org.springframework.security.oauth2.client.OAuth2AuthorizedClientProviderBuilder;
import org.springframework.security.oauth2.client.registration.ClientRegistrationRepository;
import org.springframework.security.oauth2.client.web.client.OAuth2ClientHttpRequestInterceptor;
import org.springframework.web.client.RestClient;

/**
 * An AI agent for a brokerage customer, built with Spring AI and Claude. It reads accounts
 * and markets through the hypermedia client and may propose trades; the customer confirms
 * each one at the console.
 */
@SpringBootApplication
public class BrokerageAgentApplication {

    static final String SYSTEM_PROMPT = """
            You help a customer of a retail brokerage with their own accounts. Use the tools to
            look up accounts, balances, positions, quotes and orders, and to propose orders or
            cancellations the customer asks for.

            You cannot place or cancel anything yourself. A proposal only takes effect when the
            customer confirms it, so after proposing, tell the customer what was proposed and
            the confirmation code. Before proposing a trade, check the quote and state the
            estimated total. Treat estimates as estimates.

            Carry out the customer's instructions and answer questions about their accounts; do
            not recommend what to buy or sell. When a tool reports an error, explain it plainly.
            Amounts are in US dollars. Keep answers short.""";

    public static void main(String[] args) {
        SpringApplication.run(BrokerageAgentApplication.class, args);
    }

    @Bean
    Clock clock() {
        return Clock.systemUTC();
    }

    /**
     * The agent's own OAuth 2.1 client: Spring Security fetches, caches and renews a
     * client_credentials token for the "brokerage" registration and adds it to every request.
     */
    @Bean
    BrokerageClient brokerageClient(ClientRegistrationRepository registrations,
            @Value("${brokerage.api-root}") URI root) {
        var manager = new AuthorizedClientServiceOAuth2AuthorizedClientManager(registrations,
                new InMemoryOAuth2AuthorizedClientService(registrations));
        manager.setAuthorizedClientProvider(
                OAuth2AuthorizedClientProviderBuilder.builder().clientCredentials().build());
        var tokens = new OAuth2ClientHttpRequestInterceptor(manager);
        tokens.setClientRegistrationIdResolver(request -> "brokerage");
        RestClient.Builder http = RestClient.builder().requestInterceptor(tokens);
        return new BrokerageClient(new HypermediaClient(http), root);
    }

    @Bean
    ChatClient chatClient(ChatClient.Builder builder, BrokerageTools tools) {
        return builder
                .defaultSystem(SYSTEM_PROMPT)
                .defaultTools(tools)
                .defaultAdvisors(
                        MessageChatMemoryAdvisor.builder(MessageWindowChatMemory.builder().build()).build())
                .build();
    }
}
