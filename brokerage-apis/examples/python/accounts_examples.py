"""
Accounts API examples: list, balances, positions, order status, transactions.

    python3 accounts_examples.py
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

from brokerage_client import ApiError, BrokerageClient


# ---------------------------------------------------------------------------
# GET /accounts
# ---------------------------------------------------------------------------


def list_accounts(client: BrokerageClient) -> list[dict]:
    """Every account reachable with the current grant.

    `accountId` is the opaque handle used everywhere else in the API.
    `accountNumber` is the customer-facing value and arrives masked unless the
    grant carries `accounts:read:full` -- do not use it as a key.
    """
    response = client.get("/accounts")
    accounts = response["items"]

    for account in accounts:
        print(f"{account['accountId']}  {account['accountNumber']:>10}  "
              f"{account['type']:<7} {account['registration']:<16} "
              f"{account['status']:<10} options={account['optionsLevel']}")

        # Affordances are state-dependent: a RESTRICTED account has no
        # place-order link at all, so a UI can grey out the trade button
        # without knowing anything about compliance rules.
        if not client.can(account, "place-order"):
            print("    (not trade-enabled with this grant)")

    return accounts


# ---------------------------------------------------------------------------
# GET /accounts/{accountId}/balances
# ---------------------------------------------------------------------------


def show_balances(client: BrokerageClient, account: dict) -> dict:
    """Cash, buying power, and margin.

    Reached by following the `balances` link rather than building the URL, so
    the client keeps working if the path layout ever changes.
    """
    balances = client.follow(account, "balances")

    print(f"\nBalances as of {balances['asOf']} ({balances['currency']})")
    print(f"  Net liquidation : {Decimal(balances['netLiquidation']):>14,.2f}")
    print(f"  Cash (settled)  : {Decimal(balances['cash']['settled']):>14,.2f}")
    print(f"  Cash (total)    : {Decimal(balances['cash']['total']):>14,.2f}")
    print(f"  Withdrawable    : {Decimal(balances['cash']['withdrawable']):>14,.2f}")

    # Stock and option buying power differ materially in a margin account --
    # sizing an option order off stock buying power will get it rejected.
    buying_power = balances["buyingPower"]
    print(f"  Buying power")
    print(f"    stock         : {Decimal(buying_power['stock']):>14,.2f}")
    print(f"    option        : {Decimal(buying_power['option']):>14,.2f}")
    if "dayTrading" in buying_power:
        print(f"    day trading   : {Decimal(buying_power['dayTrading']):>14,.2f}")

    margin = balances.get("margin")
    if margin:
        print(f"  Excess liquidity: {Decimal(margin['excessLiquidity']):>14,.2f}")
        call = margin.get("marginCall")
        if call:
            # A margin call is the one balance field worth alerting on.
            print(f"  ** {call['type']}: {Decimal(call['amount']):,.2f} due {call['dueDate']} **")

    if balances.get("patternDayTrader"):
        print(f"  Day trades left : {balances['dayTradesRemaining']}")

    return balances


# ---------------------------------------------------------------------------
# GET /accounts/{accountId}/positions
# ---------------------------------------------------------------------------


def show_positions(client: BrokerageClient, account: dict) -> list[dict]:
    """Open positions with cost basis and unrealized P&L."""
    positions = list(client.paginate(client.link(account, "positions")))

    if not positions:
        print("\nNo open positions.")
        return positions

    print(f"\n{'Symbol':<24} {'Side':<6} {'Qty':>8} {'Avg cost':>11} "
          f"{'Market value':>14} {'Unrealized':>13}")
    print("-" * 80)

    total_unrealized = Decimal("0")
    for position in positions:
        instrument = position["instrument"]

        # Option positions are quantified in contracts; the display symbol is
        # the 21-character OSI string, which is unreadable in a list. Build
        # something human-facing from the option detail instead.
        if instrument["type"] == "OPTION":
            option = instrument["option"]
            label = (f"{option['underlyingSymbol']} {option['expiration'][2:]} "
                     f"{Decimal(option['strike']):g}{option['optionType'][0]}")
        else:
            label = instrument["symbol"]

        unrealized = Decimal(position["unrealizedPnl"])
        total_unrealized += unrealized

        print(f"{label:<24} {position['side']:<6} "
              f"{Decimal(position['quantity']):>8,g} "
              f"{Decimal(position['averageCost']):>11,.4f} "
              f"{Decimal(position['marketValue']):>14,.2f} "
              f"{unrealized:>+13,.2f}")

    print("-" * 80)
    print(f"{'Total unrealized':<57}{total_unrealized:>+13,.2f}")
    return positions


# ---------------------------------------------------------------------------
# GET /accounts/{accountId}/orders
# ---------------------------------------------------------------------------


def show_order_status(client: BrokerageClient, account: dict) -> list[dict]:
    """Working orders, then a wider historical view.

    With no `status` filter the endpoint returns working orders only -- the set
    a trading screen needs on load. Pass `status=ALL` to widen it.
    """
    orders_url = client.link(account, "orders")

    working = client.get(orders_url)["items"]
    print(f"\nWorking orders: {len(working)}")
    for order in working:
        filled = Decimal(order["filledQuantity"])
        total = Decimal(order["quantity"])
        symbol = order.get("instrument", {}).get("symbol", order.get("strategy", "?"))
        print(f"  {order['orderId']}  {order['status']:<17} {order.get('side', ''):<12} "
              f"{symbol:<10} {filled:g}/{total:g} @ {order.get('limitPrice', 'MKT')}")

        # Presence of the link is the authority on what you may do next.
        actions = [rel for rel in ("cancel", "replace") if client.can(order, rel)]
        print(f"      available: {', '.join(actions) or 'none (terminal)'}")

    # Everything from the last 7 days, terminal states included.
    week_ago = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=7)).isoformat()
    recent = list(client.paginate(orders_url, params={"status": ["ALL"], "from": week_ago}))
    print(f"\nOrders in the last 7 days: {len(recent)}")

    by_status: dict[str, int] = {}
    for order in recent:
        by_status[order["status"]] = by_status.get(order["status"], 0) + 1
    for status, count in sorted(by_status.items(), key=lambda kv: -kv[1]):
        print(f"  {status:<18} {count}")

    return working


def poll_order_until_terminal(client: BrokerageClient, order: dict, timeout: int = 60) -> dict:
    """Poll one order to a terminal state using conditional requests.

    `If-None-Match` makes an unchanged poll a 304 with no body, which is what
    keeps a 1-second poll inside the rate limit. For anything latency-sensitive
    use the SSE stream instead -- this exists for batch reconciliation.
    """
    import time

    terminal = {"FILLED", "CANCELLED", "REJECTED", "EXPIRED", "REPLACED"}
    url = client.link(order, "self")
    etag = None
    deadline = time.time() + timeout

    while time.time() < deadline:
        headers = {"If-None-Match": etag} if etag else {}
        body, response_headers = client.get_with_headers(url, headers=headers)

        if body is not None:          # 200: state changed
            order = body
            etag = response_headers.get("ETag")
            print(f"  -> {order['status']} filled={order['filledQuantity']}/{order['quantity']}")
            if order["status"] in terminal:
                return order

        time.sleep(1)

    print(f"  timed out after {timeout}s; order still {order['status']}")
    return order


# ---------------------------------------------------------------------------
# GET /accounts/{accountId}/transactions
# ---------------------------------------------------------------------------


def show_transactions(client: BrokerageClient, account: dict, days: int = 30) -> list[dict]:
    """Transaction history over a date window.

    Settlement can back-date entries, so a transaction may appear in a page you
    already read. Reconcile on `transactionId`, which is permanent and unique --
    that is why the running total below dedupes rather than summing blindly.
    """
    today = dt.date.today()
    params = {"from": (today - dt.timedelta(days=days)).isoformat(), "to": today.isoformat()}

    seen: set[str] = set()
    transactions: list[dict] = []
    net_cash = Decimal("0")

    for txn in client.paginate(client.link(account, "transactions"), params=params):
        if txn["transactionId"] in seen:
            continue
        seen.add(txn["transactionId"])
        transactions.append(txn)
        net_cash += Decimal(txn["netAmount"])

    print(f"\nTransactions, last {days} days: {len(transactions)}")
    print(f"{'Date':<12} {'Type':<16} {'Description':<44} {'Net':>13}")
    print("-" * 88)
    for txn in transactions[:20]:
        print(f"{txn['tradeDate']:<12} {txn['type']:<16} "
              f"{txn.get('description', '')[:44]:<44} "
              f"{Decimal(txn['netAmount']):>+13,.2f}")
    if len(transactions) > 20:
        print(f"... and {len(transactions) - 20} more")
    print("-" * 88)
    print(f"{'Net cash movement':<73}{net_cash:>+13,.2f}")

    # Fee totals are a common reconciliation need and are not exposed as a
    # summary field, so roll them up from the fee arrays.
    fees_by_type: dict[str, Decimal] = {}
    for txn in transactions:
        for fee in txn.get("fees", []):
            fees_by_type[fee["type"]] = fees_by_type.get(fee["type"], Decimal("0")) + Decimal(fee["amount"])
    if fees_by_type:
        print("\nFees by type:")
        for fee_type, amount in sorted(fees_by_type.items()):
            print(f"  {fee_type:<14} {amount:>10,.2f}")

    return transactions


# ---------------------------------------------------------------------------


def main() -> None:
    client = BrokerageClient()

    try:
        accounts = list_accounts(client)
        if not accounts:
            print("No accounts available under this grant.")
            return

        account = accounts[0]
        print(f"\n{'=' * 88}\nAccount {account['accountId']} ({account.get('nickname') or account['type']})\n{'=' * 88}")

        show_balances(client, account)
        show_positions(client, account)
        show_order_status(client, account)
        show_transactions(client, account)

    except ApiError as exc:
        print(f"\nAPI error: {exc}")
        if exc.type.endswith("/insufficient-scope"):
            print(f"  Re-authorize with the {exc.problem.get('requiredScope')} scope.")
        for error in exc.errors:
            print(f"  {error['pointer']}: {error['detail']}")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
