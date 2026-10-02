package com.example.brokerage.domain;

import java.security.SecureRandom;

/**
 * Identifier minting. Three properties, each of which a model-facing identifier needs.
 *
 * <p>They are prefixed, so a misrouted identifier fails at the schema instead of in the handler:
 * {@code acct_} can never be mistaken for {@code ord_}, and the input schema's pattern says which
 * one a parameter wants. They use Crockford base32, which has no {@code I}, {@code L}, {@code O}
 * or {@code U}, so an identifier a customer reads aloud and an agent types back survives the round
 * trip. And they are opaque: nothing downstream may parse one for a date, a branch code or a
 * customer number, because the day somebody does is the day the format can never change.
 *
 * <p>The seeded identifiers are derived from an index rather than from a counter, and that detail
 * matters more than it looks. A counter advances with every object ever created in the process, so
 * the demonstration customer's first account would have a different identifier in the second test
 * of a run than in the first. Deriving from the index means the four demonstration accounts have
 * the same four identifiers on every machine, in every test, and in every figure printed in the
 * book — which is what makes a recorded evaluation cassette replayable at all.
 */
public final class Ids {

    private static final String ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ";
    private static final SecureRandom RANDOM = new SecureRandom();

    private Ids() {
    }

    /** A genuinely random identifier, for anything created while the service is running. */
    public static String random(String prefix, int length) {
        StringBuilder out = new StringBuilder(prefix);
        for (int i = 0; i < length; i++) {
            out.append(ALPHABET.charAt(RANDOM.nextInt(ALPHABET.length())));
        }
        return out.toString();
    }

    /**
     * A repeatable identifier for the seeded book of business. Derived from the kind and the index
     * only, so it is stable across runs, machines and JVM restarts.
     */
    public static String seeded(String prefix, int index, int length) {
        StringBuilder out = new StringBuilder(prefix);
        long seed = (prefix.hashCode() * 0x9E3779B97F4A7C15L) ^ ((index + 1L) * 0x165667B19E3779F9L);
        for (int i = 0; i < length; i++) {
            long mixed = seed ^ ((i + 1L) * 0xBF58476D1CE4E5B9L);
            mixed ^= mixed >>> 29;
            mixed *= 0x94D049BB133111EBL;
            mixed ^= mixed >>> 32;
            out.append(ALPHABET.charAt((int) Math.floorMod(mixed, 32L)));
        }
        return out.toString();
    }

    public static String account(int index) {
        return seeded("acct_", index, 16);
    }

    public static String lot(int index) {
        return seeded("lot_", index, 12);
    }

    /** A seeded order, for the working orders the demonstration account starts with. */
    public static String seededOrder(int index) {
        return seeded("ord_", index, 16);
    }

    public static String order() {
        return random("ord_", 16);
    }

    public static String execution() {
        return random("exec_", 16);
    }
}
