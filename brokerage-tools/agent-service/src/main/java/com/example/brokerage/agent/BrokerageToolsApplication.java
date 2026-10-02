package com.example.brokerage.agent;

import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;
import org.springframework.context.annotation.ComponentScan;

/**
 * The agent service: the registry, the pipeline, the Claude-backed agent, the HTTP surface the
 * Angular console talks to, and the console itself once it has been built into
 * {@code src/main/resources/static}.
 *
 * <pre>
 *   export ANTHROPIC_API_KEY=sk-ant-...
 *   ./mvnw -pl agent-service spring-boot:run
 *   open http://localhost:8080/
 * </pre>
 *
 * <p>Without an API key everything except the chat endpoint still works: the catalog, the
 * ontology, the producer graph, the generated briefing and direct tool invocation are all
 * model-free. That is on purpose. A tool catalog you can only exercise through a language model is
 * a catalog you cannot debug.
 */
@SpringBootApplication
@ComponentScan(basePackages = "com.example.brokerage")
public class BrokerageToolsApplication {

    public static void main(String[] args) {
        SpringApplication.run(BrokerageToolsApplication.class, args);
    }
}
