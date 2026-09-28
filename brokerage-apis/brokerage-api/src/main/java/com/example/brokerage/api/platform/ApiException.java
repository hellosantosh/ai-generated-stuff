package com.example.brokerage.api.platform;

import java.util.LinkedHashMap;
import java.util.Map;

/**
 * An error the API reports on purpose. It carries its problem type, a detail message for
 * the developer, and optional extension members (for example the buying power available).
 */
public class ApiException extends RuntimeException {

    private final ProblemType type;
    private final Map<String, Object> extensions = new LinkedHashMap<>();

    public ApiException(ProblemType type, String detail) {
        super(detail);
        this.type = type;
    }

    public ApiException with(String name, Object value) {
        extensions.put(name, value);
        return this;
    }

    public ProblemType type() {
        return type;
    }

    public Map<String, Object> extensions() {
        return extensions;
    }

    public static ApiException notFound(String what, String id) {
        return new ApiException(ProblemType.NOT_FOUND, what + " " + id + " was not found");
    }
}
