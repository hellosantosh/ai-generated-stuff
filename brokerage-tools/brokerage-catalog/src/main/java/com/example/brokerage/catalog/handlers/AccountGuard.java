package com.example.brokerage.catalog.handlers;

import com.example.brokerage.domain.Accounts;
import com.example.brokerage.domain.Brokerage;
import com.example.brokerage.tooling.contract.Invocation;
import com.example.brokerage.tooling.contract.ToolFailure;

import org.springframework.stereotype.Component;

/**
 * Resolves the {@code accountId} argument and proves the session is entitled to it.
 *
 * <p>Every handler that takes an account calls this first. The pipeline has already checked that
 * the principal holds the right scope, but a scope says "this session may read accounts", not
 * "this session may read <em>that</em> account". The second check cannot be done generically,
 * because only the domain knows who owns what, so it is done here — once, in one place, by every
 * handler that needs it.
 *
 * <p>The error is {@link com.example.brokerage.tooling.contract.ErrorCode#NOT_FOUND} and not
 * {@code NOT_ENTITLED}, which is the detail most worth copying. An authorization error tells the
 * caller the account exists and belongs to somebody else. A not-found error tells them nothing.
 * Since an agent cannot act on either, there is no cost to the honest-sounding answer and a real
 * cost to the informative one: an enumeration oracle, driven by a model that will happily try a
 * hundred identifiers if a prompt suggests it.
 */
@Component
public class AccountGuard {

    private final Brokerage brokerage;

    public AccountGuard(Brokerage brokerage) {
        this.brokerage = brokerage;
    }

    public Accounts.Account require(Invocation invocation) {
        return require(invocation, invocation.string("accountId"));
    }

    public Accounts.Account require(Invocation invocation, String accountId) {
        Accounts.Account account = brokerage.accounts().find(accountId)
                .filter(a -> a.customerId().equals(invocation.principal().customerId()))
                .orElseThrow(() -> ToolFailure.notFound("Account " + accountId,
                        "Call brokerage_accounts_list to get the accounts this customer actually "
                                + "holds, and use an accountId from that result."));
        return account;
    }

    /** The same resolution, plus a refusal when the account cannot trade at all. */
    public Accounts.Account requireTradeable(Invocation invocation) {
        Accounts.Account account = require(invocation);
        if (!account.tradeable()) {
            throw ToolFailure.precondition(
                    "Account " + account.nickname() + " is " + account.restriction()
                            + " and cannot place or change orders.",
                    "Tell the customer the account is restricted and that their service team can "
                            + "explain it. Do not try another account on their behalf.");
        }
        return account;
    }
}
