package com.example.brokerage.api.platform;

import java.nio.charset.StandardCharsets;
import java.time.Instant;
import java.util.Base64;
import java.util.Comparator;
import java.util.List;
import java.util.function.Function;

/**
 * One page of a newest-first list, with an opaque cursor for the next page.
 *
 * <p>The cursor encodes the sort key (timestamp and ID) of the last item returned, so a
 * page never skips or repeats items when new ones are inserted at the top: something
 * offset-based paging ("page=3") cannot promise. Clients never parse cursors; they
 * follow the "next" link that carries one.
 */
public record CursorPage<T>(List<T> items, String nextCursor) {

    public static final int DEFAULT_LIMIT = 25;
    public static final int MAX_LIMIT = 100;

    public boolean hasNext() {
        return nextCursor != null;
    }

    public static <T> CursorPage<T> of(List<T> all, Function<T, Instant> time, Function<T, String> id,
            String cursor, Integer requestedLimit) {
        int limit = requestedLimit == null ? DEFAULT_LIMIT : requestedLimit;
        if (limit < 1 || limit > MAX_LIMIT) {
            throw new ApiException(ProblemType.INVALID_REQUEST,
                    "limit must be between 1 and " + MAX_LIMIT);
        }
        Comparator<T> newestFirst = Comparator.comparing(time).thenComparing(id).reversed();
        List<T> sorted = all.stream().sorted(newestFirst).toList();

        int start = 0;
        if (cursor != null) {
            Key after = Key.decode(cursor);
            while (start < sorted.size() && !isOlder(sorted.get(start), after, time, id)) {
                start++;
            }
        }
        int end = Math.min(start + limit, sorted.size());
        List<T> items = sorted.subList(start, end);
        String next = end < sorted.size()
                ? new Key(time.apply(items.getLast()), id.apply(items.getLast())).encode()
                : null;
        return new CursorPage<>(items, next);
    }

    private static <T> boolean isOlder(T item, Key after, Function<T, Instant> time, Function<T, String> id) {
        int byTime = time.apply(item).compareTo(after.time());
        return byTime < 0 || (byTime == 0 && id.apply(item).compareTo(after.id()) < 0);
    }

    record Key(Instant time, String id) {

        String encode() {
            String raw = time.toEpochMilli() + ":" + id;
            return Base64.getUrlEncoder().withoutPadding()
                    .encodeToString(raw.getBytes(StandardCharsets.UTF_8));
        }

        static Key decode(String cursor) {
            try {
                String raw = new String(Base64.getUrlDecoder().decode(cursor), StandardCharsets.UTF_8);
                int colon = raw.indexOf(':');
                return new Key(Instant.ofEpochMilli(Long.parseLong(raw.substring(0, colon))),
                        raw.substring(colon + 1));
            } catch (RuntimeException ex) {
                throw new ApiException(ProblemType.INVALID_REQUEST, "The cursor is malformed or expired");
            }
        }
    }
}
