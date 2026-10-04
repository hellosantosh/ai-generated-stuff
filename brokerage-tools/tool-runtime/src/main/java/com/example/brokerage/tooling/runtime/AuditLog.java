package com.example.brokerage.tooling.runtime;

import java.time.Instant;
import java.util.ArrayDeque;
import java.util.ArrayList;
import java.util.Deque;
import java.util.List;

import com.example.brokerage.tooling.contract.ErrorCode;
import com.example.brokerage.tooling.contract.Governance;

/**
 * The audit record for every invocation, successful or not.
 *
 * <p>A record is written before the handler runs and completed after, so that a call which hangs
 * or crashes the process still leaves evidence it was attempted. The alternative — logging on the
 * way out — loses exactly the invocations an incident review cares about.
 *
 * <p>What is deliberately absent is as important as what is present. Arguments are recorded as a
 * hash and a list of field names, never as values. The reasoning: the record has to prove which
 * call was made and let a reviewer match it against an approval, and a hash does both. Storing the
 * values themselves would put account identifiers, order sizes and whatever the customer typed
 * into a log with a six-year retention and a much wider readership than the account itself.
 *
 * <p>In production this writes to the firm's immutable store. Here it keeps the last few thousand
 * in memory so the console can show them and the tests can assert on them.
 */
public class AuditLog {

    /**
     * @param outcome  OK, or the error code returned
     * @param approval the approval reference, when a human had to agree
     */
    public record Record(String requestId, Instant at, String tool, String toolVersion,
                         int riskTier, String auditEvent, String retention, String subject,
                         String sessionId, List<String> argumentFields, String argumentHash,
                         String outcome, Long latencyMs, String approval,
                         Governance.ApprovalMode approvalMode) {
    }

    private static final int CAPACITY = 5000;

    private final Deque<Record> records = new ArrayDeque<>();

    public synchronized void write(Record record) {
        records.addLast(record);
        while (records.size() > CAPACITY) {
            records.removeFirst();
        }
    }

    public synchronized List<Record> recent(int limit) {
        List<Record> out = new ArrayList<>(records);
        java.util.Collections.reverse(out);
        return out.subList(0, Math.min(limit, out.size()));
    }

    public synchronized List<Record> forSession(String sessionId) {
        return records.stream().filter(record -> sessionId.equals(record.sessionId())).toList();
    }

    public synchronized long count(ErrorCode code) {
        return records.stream().filter(record -> code.name().equals(record.outcome())).count();
    }

    public synchronized void clear() {
        records.clear();
    }
}
