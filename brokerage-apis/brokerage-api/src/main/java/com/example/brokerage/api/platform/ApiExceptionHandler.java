package com.example.brokerage.api.platform;

import java.math.BigDecimal;
import java.net.URI;
import java.util.Arrays;
import java.util.Comparator;
import java.util.List;
import java.util.Map;
import java.util.stream.Collectors;

import jakarta.servlet.http.HttpServletRequest;

import org.springframework.http.HttpHeaders;
import org.springframework.http.HttpStatusCode;
import org.springframework.http.ProblemDetail;
import org.springframework.http.ResponseEntity;
import org.springframework.http.converter.HttpMessageNotReadableException;
import org.springframework.security.access.AccessDeniedException;
import org.springframework.security.core.AuthenticationException;
import org.springframework.web.accept.InvalidApiVersionException;
import org.springframework.validation.FieldError;
import org.springframework.web.bind.MethodArgumentNotValidException;
import org.springframework.web.bind.annotation.ExceptionHandler;
import org.springframework.web.bind.annotation.RestControllerAdvice;
import org.springframework.web.context.request.ServletWebRequest;
import org.springframework.web.context.request.WebRequest;
import org.springframework.web.servlet.mvc.method.annotation.ResponseEntityExceptionHandler;
import tools.jackson.databind.exc.MismatchedInputException;

/**
 * Turns every error into an RFC 9457 problem document (application/problem+json), with a
 * stable type URI, the request path as "instance", and the request ID for support.
 * Spring's own exceptions (bad JSON, 405, 406, unsupported versions...) are handled by the
 * base class; this class adds the API's catalog and a consistent shape for all of them.
 */
@RestControllerAdvice
public class ApiExceptionHandler extends ResponseEntityExceptionHandler {

    @ExceptionHandler(ApiException.class)
    ResponseEntity<ProblemDetail> handleApi(ApiException ex, HttpServletRequest request) {
        ProblemDetail problem = problem(ex.type(), ex.getMessage(), request);
        ex.extensions().forEach(problem::setProperty);
        HttpHeaders headers = new HttpHeaders();
        if (ex instanceof RateLimitedException limited) {
            headers.set(HttpHeaders.RETRY_AFTER, String.valueOf(limited.retryAfterSeconds()));
        }
        return ResponseEntity.status(ex.type().status()).headers(headers).body(problem);
    }

    /** Raised by the security filter chain when no valid token is present. */
    @ExceptionHandler(AuthenticationException.class)
    ResponseEntity<ProblemDetail> handleUnauthenticated(AuthenticationException ex,
            HttpServletRequest request) {
        return ResponseEntity.status(ProblemType.UNAUTHORIZED.status())
                .body(problem(ProblemType.UNAUTHORIZED, "A valid bearer token is required", request));
    }

    /** Raised when the token is valid but does not carry the scope the operation needs. */
    @ExceptionHandler(AccessDeniedException.class)
    ResponseEntity<ProblemDetail> handleForbidden(AccessDeniedException ex, HttpServletRequest request) {
        return ResponseEntity.status(ProblemType.INSUFFICIENT_SCOPE.status())
                .body(problem(ProblemType.INSUFFICIENT_SCOPE,
                        "The access token does not grant the scope this operation requires", request));
    }

    /** An API-Version header naming a version this server does not offer. */
    @ExceptionHandler(InvalidApiVersionException.class)
    ResponseEntity<ProblemDetail> handleVersion(InvalidApiVersionException ex, HttpServletRequest request) {
        ProblemDetail problem = problem(ProblemType.UNSUPPORTED_API_VERSION,
                "API version " + ex.getVersion() + " is not supported", request);
        problem.setProperty("supportedVersions", List.of("1", "2"));
        return ResponseEntity.badRequest().body(problem);
    }

    /** Bean Validation failures: one problem, with every invalid field listed. */
    @Override
    protected ResponseEntity<Object> handleMethodArgumentNotValid(MethodArgumentNotValidException ex,
            HttpHeaders headers, HttpStatusCode status, WebRequest request) {
        ProblemDetail problem = problem(ProblemType.INVALID_REQUEST, "One or more fields are invalid",
                ((ServletWebRequest) request).getRequest());
        List<Map<String, String>> errors = ex.getBindingResult().getFieldErrors().stream()
                .sorted(Comparator.comparing(FieldError::getField))
                .map(error -> Map.of("field", error.getField(),
                        "message", String.valueOf(error.getDefaultMessage())))
                .toList();
        problem.setProperty("errors", errors);
        return ResponseEntity.badRequest().body(problem);
    }

    /**
     * A body that cannot be read: malformed JSON, or a value of the wrong type such as an
     * unknown enum constant. A wrong value is reported in the same "errors" shape as a
     * Bean Validation failure, so clients handle one format for every invalid field.
     */
    @Override
    protected ResponseEntity<Object> handleHttpMessageNotReadable(HttpMessageNotReadableException ex,
            HttpHeaders headers, HttpStatusCode status, WebRequest request) {
        HttpServletRequest servletRequest = ((ServletWebRequest) request).getRequest();
        for (Throwable cause = ex; cause != null; cause = cause.getCause()) {
            if (cause instanceof MismatchedInputException mismatch && !mismatch.getPath().isEmpty()) {
                ProblemDetail problem = problem(ProblemType.INVALID_REQUEST, "One or more fields are invalid",
                        servletRequest);
                problem.setProperty("errors", List.of(Map.of("field", fieldPath(mismatch),
                        "message", expected(mismatch.getTargetType()))));
                return ResponseEntity.badRequest().body(problem);
            }
        }
        return ResponseEntity.badRequest().body(problem(ProblemType.INVALID_REQUEST,
                "The request body is not valid JSON", servletRequest));
    }

    private static String fieldPath(MismatchedInputException mismatch) {
        return mismatch.getPath().stream()
                .map(ref -> ref.getPropertyName() != null ? ref.getPropertyName()
                        : "[" + ref.getIndex() + "]")
                .collect(Collectors.joining("."));
    }

    private static String expected(Class<?> type) {
        if (type != null && type.isEnum()) {
            return "must be one of " + Arrays.stream(type.getEnumConstants()).map(Object::toString)
                    .collect(Collectors.joining(", "));
        }
        return type == BigDecimal.class ? "must be a decimal number written as a string, such as \"10.25\""
                : "has the wrong type";
    }

    /** Everything Spring raises itself gets the same instance and requestId members. */
    @Override
    protected ResponseEntity<Object> createResponseEntity(Object body, HttpHeaders headers,
            HttpStatusCode status, WebRequest request) {
        if (body instanceof ProblemDetail problem) {
            HttpServletRequest servletRequest = ((ServletWebRequest) request).getRequest();
            if (problem.getType() == null || "about:blank".equals(problem.getType().toString())) {
                problem.setType(URI.create(ProblemType.BASE + slugFor(status)));
            }
            problem.setInstance(URI.create(servletRequest.getRequestURI()));
            problem.setProperty("requestId", RequestIdFilter.currentId(servletRequest));
        }
        return super.createResponseEntity(body, headers, status, request);
    }

    static ProblemDetail problem(ProblemType type, String detail, HttpServletRequest request) {
        ProblemDetail problem = ProblemDetail.forStatusAndDetail(type.status(), detail);
        problem.setType(type.uri());
        problem.setTitle(type.title());
        problem.setInstance(URI.create(request.getRequestURI()));
        problem.setProperty("requestId", RequestIdFilter.currentId(request));
        return problem;
    }

    private static String slugFor(HttpStatusCode status) {
        return switch (status.value()) {
            case 400 -> "invalid-request";
            case 404 -> "not-found";
            case 405 -> "method-not-allowed";
            case 406 -> "not-acceptable";
            case 415 -> "unsupported-media-type";
            default -> "http-" + status.value();
        };
    }
}
