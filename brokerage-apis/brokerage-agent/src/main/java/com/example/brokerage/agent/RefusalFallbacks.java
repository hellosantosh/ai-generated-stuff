package com.example.brokerage.agent;

import java.io.IOException;

import okhttp3.Interceptor;
import okhttp3.MediaType;
import okhttp3.Request;
import okhttp3.RequestBody;
import okhttp3.Response;
import okio.Buffer;
import tools.jackson.databind.json.JsonMapper;
import tools.jackson.databind.node.ObjectNode;

import org.springframework.ai.anthropic.http.okhttp.AnthropicHttpClientBuilderCustomizer;
import org.springframework.ai.anthropic.http.okhttp.SpringAiAnthropicHttpClient;
import org.springframework.stereotype.Component;

/**
 * Opts every Messages API request into server-side refusal fallbacks: if a safety
 * classifier declines a request, the API re-runs it on the fallback model Anthropic
 * recommends for that category instead of returning the refusal. Spring AI has no option
 * for this request field, so it is added at the HTTP layer.
 */
@Component
class RefusalFallbacks implements AnthropicHttpClientBuilderCustomizer, Interceptor {

    static final String BETA = "server-side-fallback-2026-07-01";
    private static final JsonMapper JSON = JsonMapper.builder().build();

    @Override
    public void customize(SpringAiAnthropicHttpClient.Builder builder) {
        builder.interceptor(this);
    }

    @Override
    public Response intercept(Chain chain) throws IOException {
        Request request = chain.request();
        if (!"POST".equals(request.method()) || !request.url().encodedPath().endsWith("/v1/messages")
                || request.body() == null) {
            return chain.proceed(request);
        }
        Buffer body = new Buffer();
        request.body().writeTo(body);
        MediaType type = request.body().contentType();
        String existing = request.header("anthropic-beta");
        return chain.proceed(request.newBuilder()
                .header("anthropic-beta", existing == null ? BETA : existing + "," + BETA)
                .post(RequestBody.create(withFallbacks(body.readUtf8()), type))
                .build());
    }

    /** The request body with "fallbacks": "default" added. */
    static String withFallbacks(String json) {
        ObjectNode request = (ObjectNode) JSON.readTree(json);
        request.put("fallbacks", "default");
        return JSON.writeValueAsString(request);
    }
}
