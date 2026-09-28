package com.example.brokerage.api;

import java.time.Clock;

import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;
import org.springframework.boot.context.properties.ConfigurationPropertiesScan;
import org.springframework.context.annotation.Bean;
import org.springframework.scheduling.annotation.EnableScheduling;

/**
 * The brokerage REST API: accounts, positions, transactions, instruments, quotes and
 * orders, served as HAL (and HAL-FORMS) hypermedia and protected by OAuth 2.1 scopes.
 */
@SpringBootApplication
@EnableScheduling
@ConfigurationPropertiesScan
public class BrokerageApiApplication {

    public static void main(String[] args) {
        SpringApplication.run(BrokerageApiApplication.class, args);
    }

    /** One clock for the whole application, so time can be controlled in tests. */
    @Bean
    Clock clock() {
        return Clock.systemUTC();
    }
}
