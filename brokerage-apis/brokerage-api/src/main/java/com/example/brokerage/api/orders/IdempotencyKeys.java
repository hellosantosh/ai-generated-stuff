package com.example.brokerage.api.orders;

import java.time.Clock;
import java.time.Duration;
import java.time.Instant;
import java.util.Map;
import java.util.Optional;
import java.util.concurrent.ConcurrentHashMap;

import com.example.brokerage.api.platform.ApiException;
import com.example.brokerage.api.platform.ProblemType;

import org.springframework.stereotype.Component;

/**
 * Remembers which order each Idempotency-Key created, for 24 hours.
 *
 * <p>A network timeout leaves a client unsure whether its order was placed. With a key,
 * retrying is always safe: the same key and the same request return the original order
 * instead of buying twice. The same key with a different request is a client bug, and is
 * refused rather than guessed at.
 */
@Component
public class IdempotencyKeys {

    static final Duration RETENTION = Duration.ofHours(24);

    private record Entry(String fingerprint, String orderId, Instant createdAt) {
    }

    private final Map<String, Entry> entries = new ConcurrentHashMap<>();
    private final Clock clock;

    public IdempotencyKeys(Clock clock) {
        this.clock = clock;
    }

    /** The order already created with this key, if any. */
    Optional<String> existingOrder(String scope, String key, String fingerprint) {
        Instant now = clock.instant();
        entries.values().removeIf(entry -> entry.createdAt().plus(RETENTION).isBefore(now));
        Entry entry = entries.get(scope + "|" + key);
        if (entry == null) {
            return Optional.empty();
        }
        if (!entry.fingerprint().equals(fingerprint)) {
            throw new ApiException(ProblemType.IDEMPOTENCY_KEY_REUSED,
                    "Idempotency-Key " + key + " was already used for a different order request");
        }
        return Optional.of(entry.orderId());
    }

    void remember(String scope, String key, String fingerprint, String orderId) {
        entries.put(scope + "|" + key, new Entry(fingerprint, orderId, clock.instant()));
    }
}
