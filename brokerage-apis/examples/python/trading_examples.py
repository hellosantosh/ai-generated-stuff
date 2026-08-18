"""
Trading API examples: stock orders and options orders.

Point this at the sandbox. `BrokerageClient` defaults to the sandbox base URL;
switching to production is a deliberate edit, not an environment accident.

    python3 trading_examples.py
"""

from __future__ import annotations

from decimal import Decimal
from typing import Optional

from brokerage_client import ApiError, BrokerageClient


# ---------------------------------------------------------------------------
# Instrument resolution -- you need an instrumentId before you can trade
# ---------------------------------------------------------------------------


def resolve_equity(client: BrokerageClient, symbol: str) -> dict:
    matches = client.get("/instruments", params={"query": symbol, "type": "EQUITY"})["items"]
    for instrument in matches:
        if instrument["symbol"].upper() == symbol.upper():
            return instrument
    raise LookupError(f"No tradable equity matching {symbol!r}.")


def resolve_option(
    client: BrokerageClient, underlying: str, expiration: str, strike: str, option_type: str
) -> dict:
    """Find one option contract in a chain.

    Always narrow by expiration. An unfiltered chain on a liquid underlying is
    thousands of contracts and will be slow for both sides.
    """
    chain = client.get(
        f"/instruments/{underlying}/option-chain",
        params={"expiration": expiration, "optionType": option_type},
    )
    target = Decimal(strike)
    for contract in chain["contracts"]:
        if Decimal(contract["option"]["strike"]) == target:
            return contract
    available = sorted({str(Decimal(c["option"]["strike"])) for c in chain["contracts"]})
    raise LookupError(f"No {underlying} {expiration} {strike}{option_type[0]}. Strikes: {available[:12]}")


# ---------------------------------------------------------------------------
# Preview -- run this before every order you show a human
# ---------------------------------------------------------------------------


def preview(client: BrokerageClient, account: dict, order: dict) -> dict:
    """Cost, fees, buying-power impact, and warnings, without routing anything.

    `valid: false` means at least one ERROR-severity warning; submitting anyway
    will be rejected. Surface the warnings instead of retrying.
    """
    result = client.post(f"{client.link(account, 'self')}/orders/preview", order)

    print("  Preview")
    if result.get("estimatedCost"):
        print(f"    est. cost        {Decimal(result['estimatedCost']):>12,.2f}")
    if result.get("estimatedProceeds"):
        print(f"    est. proceeds    {Decimal(result['estimatedProceeds']):>12,.2f}")
    print(f"    commission       {Decimal(result['commission']):>12,.2f}")
    for fee in result.get("fees", []):
        print(f"    {fee['type'].lower():<16} {Decimal(fee['amount']):>12,.2f}")
    if result.get("buyingPowerAfter") is not None:
        print(f"    BP after         {Decimal(result['buyingPowerAfter']):>12,.2f}")
    if result.get("maxLoss") is not None:
        print(f"    max loss         {Decimal(result['maxLoss']):>12,.2f}")

    for warning in result.get("warnings", []):
        print(f"    [{warning['severity']}] {warning['message']}")

    return result


# ---------------------------------------------------------------------------
# Placing orders
# ---------------------------------------------------------------------------


def place(client: BrokerageClient, account: dict, order: dict, *, dry_run: bool = True) -> Optional[dict]:
    """Preview, then place.

    The idempotency key is generated once, outside the client's retry loop, and
    reused on every attempt. That is the whole point: a socket timeout on a
    market order is indistinguishable from a rejection, and the natural retry
    would otherwise double the position.
    """
    result = preview(client, account, order)
    if not result["valid"]:
        print("  Preview invalid; not submitting.")
        return None

    if dry_run:
        print("  [dry run] not submitting.")
        return None

    key = client.new_idempotency_key()
    placed = client.post(
        f"{client.link(account, 'self')}/orders", order, idempotency_key=key
    )

    print(f"  Placed {placed['orderId']}  status={placed['status']}")
    # A 201 is an acknowledgement of routing, not a fill. Terminal state comes
    # from the event stream or a poll.
    return placed


# --- stock -----------------------------------------------------------------


def stock_limit_buy(client: BrokerageClient, account: dict, dry_run: bool = True):
    """Buy 300 MSFT, limit 412.50, day."""
    print("\nStock: limit buy")
    instrument = resolve_equity(client, "MSFT")
    return place(client, account, {
        "assetClass": "EQUITY",
        "clientOrderId": "py-example-limit-buy",
        "side": "BUY",
        "instrumentId": instrument["instrumentId"],
        "quantity": "300",
        "orderType": "LIMIT",
        "limitPrice": "412.50",
        "timeInForce": "DAY",
        "extendedHours": False,
    }, dry_run=dry_run)


def stock_market_sell(client: BrokerageClient, account: dict, dry_run: bool = True):
    """Flatten an existing position at market.

    Quantity comes from the position rather than a constant -- selling more
    than you hold turns a close into an unintended short.
    """
    print("\nStock: market sell to flatten")
    positions = list(client.paginate(client.link(account, "positions"),
                                     params={"instrumentType": "EQUITY"}))
    if not positions:
        print("  No equity positions to close.")
        return None

    position = positions[0]
    quantity = abs(Decimal(position["quantity"]))

    return place(client, account, {
        "assetClass": "EQUITY",
        "clientOrderId": f"py-flatten-{position['positionId'][-8:]}",
        "side": "SELL" if position["side"] == "LONG" else "BUY_TO_COVER",
        "instrumentId": position["instrument"]["instrumentId"],
        "quantity": str(quantity),
        "orderType": "MARKET",
        "timeInForce": "DAY",
    }, dry_run=dry_run)


def stock_bracket(client: BrokerageClient, account: dict, dry_run: bool = True):
    """Entry with an attached profit target and stop.

    The two exits are released only when the entry fills and are OCO: whichever
    triggers first cancels the other, so a filled target cannot leave a live
    stop behind.
    """
    print("\nStock: bracket order")
    instrument = resolve_equity(client, "MSFT")
    return place(client, account, {
        "assetClass": "EQUITY",
        "clientOrderId": "py-example-bracket",
        "side": "BUY",
        "instrumentId": instrument["instrumentId"],
        "quantity": "100",
        "orderType": "LIMIT",
        "limitPrice": "410.00",
        "timeInForce": "DAY",
        "bracket": {
            "takeProfit": {"orderType": "LIMIT", "limitPrice": "430.00"},
            "stopLoss": {"orderType": "STOP", "stopPrice": "398.00"},
        },
    }, dry_run=dry_run)


def stock_trailing_stop(client: BrokerageClient, account: dict, dry_run: bool = True):
    """Trailing stop 5% below the high-water mark, good till cancelled."""
    print("\nStock: trailing stop")
    instrument = resolve_equity(client, "NVDA")
    return place(client, account, {
        "assetClass": "EQUITY",
        "clientOrderId": "py-example-trail",
        "side": "SELL",
        "instrumentId": instrument["instrumentId"],
        "quantity": "50",
        "orderType": "TRAILING_STOP",
        "trailingPercent": "5.0",
        "timeInForce": "GTC",
    }, dry_run=dry_run)


# --- options ---------------------------------------------------------------


def option_single_leg(client: BrokerageClient, account: dict, dry_run: bool = True):
    """Buy 5 AAPL Dec-26 250 calls to open, limit 12.40 per contract.

    `limitPrice` is per contract; the cash outlay is price x quantity x
    contractMultiplier -- 12.40 x 5 x 100 = 6,200. Requires LEVEL_2 approval.
    """
    print("\nOption: single-leg long call")
    contract = resolve_option(client, "AAPL", "2026-12-18", "250.00", "CALL")
    return place(client, account, {
        "assetClass": "OPTION",
        "strategy": "SINGLE",
        "clientOrderId": "py-example-long-call",
        "legs": [{"instrumentId": contract["instrumentId"], "side": "BUY_TO_OPEN", "ratio": 1}],
        "quantity": "5",
        "orderType": "LIMIT",
        "limitPrice": "12.40",
        "priceEffect": "DEBIT",
        "timeInForce": "DAY",
    }, dry_run=dry_run)


def option_vertical_spread(client: BrokerageClient, account: dict, dry_run: bool = True):
    """10 AAPL Dec-26 250/260 call debit spreads at a net 3.15.

    Two points that trip people up:

      * `quantity` is strategy units, not contracts. 10 here means 10 spreads,
        so 20 contracts total.
      * Declaring `strategy: VERTICAL` is what gets spread margin applied. The
        same legs sent as CUSTOM are margined leg by leg, and the short call
        alone would demand far more buying power -- often the difference
        between accepted and rejected.
    """
    print("\nOption: vertical debit spread")
    long_leg = resolve_option(client, "AAPL", "2026-12-18", "250.00", "CALL")
    short_leg = resolve_option(client, "AAPL", "2026-12-18", "260.00", "CALL")

    return place(client, account, {
        "assetClass": "OPTION",
        "strategy": "VERTICAL",
        "clientOrderId": "py-example-vertical",
        "legs": [
            {"instrumentId": long_leg["instrumentId"], "side": "BUY_TO_OPEN", "ratio": 1},
            {"instrumentId": short_leg["instrumentId"], "side": "SELL_TO_OPEN", "ratio": 1},
        ],
        "quantity": "10",
        "orderType": "NET_DEBIT",
        "netPrice": "3.15",       # always positive; direction lives in priceEffect
        "priceEffect": "DEBIT",
        "timeInForce": "DAY",
    }, dry_run=dry_run)


def option_iron_condor(client: BrokerageClient, account: dict, dry_run: bool = True):
    """Four-leg iron condor for a net credit. Requires LEVEL_3 approval.

    Leg order does not matter to the matching engine, but keeping them in
    strike order makes the intent legible to whoever reads this next.
    """
    print("\nOption: iron condor")
    legs_spec = [
        ("520.00", "PUT", "BUY_TO_OPEN"),
        ("540.00", "PUT", "SELL_TO_OPEN"),
        ("600.00", "CALL", "SELL_TO_OPEN"),
        ("620.00", "CALL", "BUY_TO_OPEN"),
    ]
    legs = [
        {
            "instrumentId": resolve_option(client, "SPY", "2026-11-20", strike, kind)["instrumentId"],
            "side": side,
            "ratio": 1,
        }
        for strike, kind, side in legs_spec
    ]

    return place(client, account, {
        "assetClass": "OPTION",
        "strategy": "IRON_CONDOR",
        "clientOrderId": "py-example-condor",
        "legs": legs,
        "quantity": "2",
        "orderType": "NET_CREDIT",
        "netPrice": "1.85",
        "priceEffect": "CREDIT",
        "timeInForce": "DAY",
    }, dry_run=dry_run)


def option_covered_call(client: BrokerageClient, account: dict, dry_run: bool = True):
    """Sell calls against stock you already own. LEVEL_1, the lowest tier.

    The covered-call check is on shares held, not on the order: 100 shares per
    contract. Selling more contracts than your share count covers makes the
    excess naked, which requires LEVEL_4 and far more margin.
    """
    print("\nOption: covered call")
    positions = list(client.paginate(client.link(account, "positions"),
                                     params={"symbol": "AAPL", "instrumentType": "EQUITY"}))
    if not positions:
        print("  No AAPL shares held; nothing to cover.")
        return None

    shares = abs(Decimal(positions[0]["quantity"]))
    contracts = int(shares // 100)
    if contracts < 1:
        print(f"  Only {shares:g} shares; need 100 per contract.")
        return None

    contract = resolve_option(client, "AAPL", "2026-12-18", "250.00", "CALL")
    return place(client, account, {
        "assetClass": "OPTION",
        "strategy": "COVERED_CALL",
        "clientOrderId": "py-example-covered-call",
        "legs": [{"instrumentId": contract["instrumentId"], "side": "SELL_TO_OPEN", "ratio": 1}],
        "quantity": str(contracts),
        "orderType": "LIMIT",
        "limitPrice": "9.85",
        "priceEffect": "CREDIT",
        "timeInForce": "DAY",
    }, dry_run=dry_run)


# ---------------------------------------------------------------------------
# Amending and cancelling
# ---------------------------------------------------------------------------


def amend_order(client: BrokerageClient, order: dict, new_price: str) -> dict:
    """Reprice a working order using optimistic concurrency.

    The read/decide/write cycle is racing the market. `If-Match` is what turns
    "the order filled while I was deciding" from a silent mis-amendment into a
    412 you can actually handle.
    """
    self_url = client.link(order, "self")
    fresh, headers = client.get_with_headers(self_url)
    etag = headers.get("ETag")

    if not client.can(fresh, "replace"):
        print(f"  {fresh['orderId']} is {fresh['status']}; not amendable.")
        return fresh

    try:
        amended = client.patch(self_url, {"limitPrice": new_price}, if_match=etag)
        print(f"  Repriced to {new_price}; status={amended['status']}")
        return amended
    except ApiError as exc:
        if exc.status == 412:
            # Re-read and decide again -- do not blindly retry with a new ETag,
            # or you will amend an order whose state you never evaluated.
            current = client.get(self_url)
            print(f"  Stale ETag; order is now {current['status']}. Re-evaluate before retrying.")
            return current
        raise


def cancel_order(client: BrokerageClient, order: dict) -> dict:
    """Request cancellation.

    Cancellation is a request, not a guarantee. The order goes to
    PENDING_CANCEL and only reaches CANCELLED when the venue acknowledges; a
    fill already in flight can still land. Confirm terminal state separately.
    """
    self_url = client.link(order, "self")
    fresh, headers = client.get_with_headers(self_url)

    if not client.can(fresh, "cancel"):
        print(f"  {fresh['orderId']} is {fresh['status']}; nothing to cancel.")
        return fresh

    cancelled = client.delete(self_url, if_match=headers.get("ETag"))
    print(f"  Cancel requested; status={cancelled['status']}")
    return cancelled


def cancel_all_working(client: BrokerageClient, account: dict) -> None:
    """Flatten the working order book. A useful panic button."""
    working = client.get(client.link(account, "orders"))["items"]
    print(f"\nCancelling {len(working)} working order(s)")
    for order in working:
        try:
            cancel_order(client, order)
        except ApiError as exc:
            # 409 here just means it reached a terminal state first -- benign.
            print(f"  {order['orderId']}: {exc.title}")


# ---------------------------------------------------------------------------


def main() -> None:
    client = BrokerageClient()
    account = client.get("/accounts")["items"][0]

    print(f"Account {account['accountId']}  options={account['optionsLevel']}")
    if account["status"] != "ACTIVE":
        print(f"Account is {account['status']}; trading operations will be rejected.")
        return

    # dry_run=True previews without routing. Flip it deliberately, and only
    # against the sandbox.
    dry_run = True

    try:
        stock_limit_buy(client, account, dry_run)
        stock_bracket(client, account, dry_run)
        stock_trailing_stop(client, account, dry_run)

        if account["optionsLevel"] in ("LEVEL_2", "LEVEL_3", "LEVEL_4"):
            option_single_leg(client, account, dry_run)
        if account["optionsLevel"] in ("LEVEL_3", "LEVEL_4"):
            option_vertical_spread(client, account, dry_run)
            option_iron_condor(client, account, dry_run)
        option_covered_call(client, account, dry_run)

    except ApiError as exc:
        print(f"\nAPI error: {exc}")
        if exc.type.endswith("/insufficient-buying-power"):
            required = exc.problem.get("required", {})
            available = exc.problem.get("available", {})
            print(f"  needs {required.get('amount')} {required.get('currency')}, "
                  f"has {available.get('amount')}")
        elif exc.type.endswith("/options-level-insufficient"):
            print(f"  needs {exc.problem.get('requiredLevel')}, "
                  f"account is {exc.problem.get('currentLevel')}")
        raise SystemExit(1)
    except LookupError as exc:
        print(f"\nInstrument lookup failed: {exc}")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
