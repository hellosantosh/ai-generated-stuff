package com.example.brokerage.api.platform;

import java.math.BigDecimal;

import com.fasterxml.jackson.annotation.JsonFormat;

import org.springframework.boot.jackson.autoconfigure.JsonMapperBuilderCustomizer;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;

/**
 * JSON conventions that apply to every representation.
 */
@Configuration(proxyBeanMethods = false)
public class JsonConfig {

    /**
     * Money, prices and quantities are written as decimal strings ("187.50"), never as
     * JSON numbers. Many JSON parsers read numbers into binary floating point, where
     * 0.1 + 0.2 is not 0.3; a string survives every parser intact.
     */
    @Bean
    JsonMapperBuilderCustomizer decimalsAsStrings() {
        return builder -> builder.withConfigOverride(BigDecimal.class,
                override -> override.setFormat(JsonFormat.Value.forShape(JsonFormat.Shape.STRING)));
    }
}
