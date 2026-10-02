package com.example.brokerage.catalog.handlers;

import java.time.LocalDate;
import java.util.List;

import com.example.brokerage.domain.Accounts;
import com.example.brokerage.domain.Brokerage;
import com.example.brokerage.tooling.contract.Invocation;
import com.example.brokerage.tooling.contract.ToolHandler;

import org.springframework.stereotype.Component;

/**
 * {@code brokerage_accounts_list}.
 *
 * <p>The shortest handler in the catalog, and worth reading first for that reason. It takes the
 * customer from the principal — never from an argument — maps the domain's accounts into the shape
 * the output schema promises, and stops.
 *
 * <p>The one judgment it makes is to project {@code restriction} into a {@code tradeable} boolean
 * as well as passing the restriction itself. The model is perfectly capable of working out that
 * {@code FROZEN} means it cannot trade, but "perfectly capable" is a per-call probability, and a
 * boolean is a certainty. Where a conclusion matters and is cheap to compute, compute it.
 */
@Component
public class AccountsListHandler implements ToolHandler {

    record AccountView(String accountId, String nickname, String type, String currency,
                       LocalDate openedOn, boolean tradeable, String restriction) {
    }

    record Payload(List<AccountView> accounts) {
    }

    private final Brokerage brokerage;

    public AccountsListHandler(Brokerage brokerage) {
        this.brokerage = brokerage;
    }

    @Override
    public String name() {
        return "brokerage_accounts_list";
    }

    @Override
    public Object handle(Invocation invocation) {
        List<AccountView> accounts =
                brokerage.accounts().forCustomer(invocation.principal().customerId()).stream()
                        .map(AccountsListHandler::view)
                        .toList();
        return new Payload(accounts);
    }

    private static AccountView view(Accounts.Account account) {
        return new AccountView(account.accountId(), account.nickname(), account.type().name(),
                account.currency(), account.openedOn(), account.tradeable(),
                account.restriction() == null ? null : account.restriction().name());
    }
}
