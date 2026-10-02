package com.example.brokerage.catalog.handlers;

import java.nio.charset.StandardCharsets;
import java.util.Base64;

import com.example.brokerage.tooling.contract.ToolFailure;

/**
 * Opaque forward cursors.
 *
 * <p>This implementation encodes an offset, which is honest about what it is: a demonstration. The
 * encoding is the point, not the content. Because the cursor is opaque, the day this service moves
 * to a keyset cursor over {@code (placedAt, orderId)} is a day no caller has to change, no
 * descriptor has to be re-reviewed, and no model has to be re-evaluated. Publish an integer page
 * number instead and you have published a contract you did not mean to.
 *
 * <p>A malformed cursor is {@code INVALID_ARGUMENT} rather than a silent reset to the first page.
 * Silently starting over means an agent paging through a long list can loop forever, each time
 * reporting the same first page as new.
 */
final class Cursors {

    private static final String PREFIX = "o:";

    private Cursors() {
    }

    static String encode(int offset) {
        return Base64.getUrlEncoder().withoutPadding()
                .encodeToString((PREFIX + offset).getBytes(StandardCharsets.UTF_8));
    }

    static int offset(String cursor) {
        if (cursor == null || cursor.isBlank()) {
            return 0;
        }
        try {
            String decoded = new String(Base64.getUrlDecoder().decode(cursor),
                    StandardCharsets.UTF_8);
            if (!decoded.startsWith(PREFIX)) {
                throw new IllegalArgumentException("bad prefix");
            }
            return Integer.parseInt(decoded.substring(PREFIX.length()));
        } catch (RuntimeException e) {
            throw ToolFailure.invalidArgument(
                    "That cursor was not issued by this tool.",
                    "Drop the cursor and call again from the start, or pass back a nextCursor "
                            + "exactly as it was returned.");
        }
    }
}
