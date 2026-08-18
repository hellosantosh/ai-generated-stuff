/**
 * Trading API examples: stock orders and options orders.
 *
 * Point this at the sandbox. `BrokerageClient` defaults to the sandbox base
 * URL; switching to production is a deliberate edit, not an env accident.
 *
 *     node trading-examples.mjs
 */

import { ApiError, BrokerageClient, Dec } from './brokerage-client.mjs';

// ---------------------------------------------------------------------------
// Instrument resolution -- you need an instrumentId before you can trade
// ---------------------------------------------------------------------------

export async function resolveEquity(client, symbol) {
  const { items } = await client.get('/instruments', {
    params: { query: symbol, type: 'EQUITY' },
  });
  const match = items.find((i) => i.symbol.toUpperCase() === symbol.toUpperCase());
  if (!match) throw new Error(`No tradable equity matching '${symbol}'.`);
  return match;
}

/**
 * Find one option contract in a chain.
 *
 * Always narrow by expiration. An unfiltered chain on a liquid underlying is
 * thousands of contracts and will be slow for both sides.
 */
export async function resolveOption(client, underlying, expiration, strike, optionType) {
  const chain = await client.get(`/instruments/${underlying}/option-chain`, {
    params: { expiration, optionType },
  });

  const match = chain.contracts.find((c) => Dec.cmp(c.option.strike, strike) === 0);
  if (!match) {
    const strikes = [...new Set(chain.contracts.map((c) => c.option.strike))].slice(0, 12);
    throw new Error(
      `No ${underlying} ${expiration} ${strike}${optionType[0]}. Strikes: ${strikes.join(', ')}`,
    );
  }
  return match;
}

// ---------------------------------------------------------------------------
// Preview -- run this before every order you show a human
// ---------------------------------------------------------------------------

/**
 * Cost, fees, buying-power impact, and warnings, without routing anything.
 *
 * `valid: false` means at least one ERROR-severity warning; submitting anyway
 * will be rejected. Surface the warnings rather than retrying.
 */
export async function preview(client, account, order) {
  const result = await client.post(`${client.link(account, 'self')}/orders/preview`, order);

  console.log('  Preview');
  if (result.estimatedCost) console.log(`    est. cost        ${Dec.format(result.estimatedCost)}`);
  if (result.estimatedProceeds) console.log(`    est. proceeds    ${Dec.format(result.estimatedProceeds)}`);
  console.log(`    commission       ${Dec.format(result.commission)}`);
  for (const fee of result.fees ?? []) {
    console.log(`    ${fee.type.toLowerCase().padEnd(16)} ${Dec.format(fee.amount)}`);
  }
  if (result.buyingPowerAfter != null) {
    console.log(`    BP after         ${Dec.format(result.buyingPowerAfter)}`);
  }
  if (result.maxLoss != null) console.log(`    max loss         ${Dec.format(result.maxLoss)}`);

  for (const warning of result.warnings ?? []) {
    console.log(`    [${warning.severity}] ${warning.message}`);
  }

  return result;
}

// ---------------------------------------------------------------------------
// Placing orders
// ---------------------------------------------------------------------------

/**
 * Preview, then place.
 *
 * The idempotency key is generated once, outside the client's retry loop, and
 * reused on every attempt. That is the entire point: a socket timeout on a
 * market order is indistinguishable from a rejection, and the natural retry
 * would otherwise double the position.
 */
export async function place(client, account, order, { dryRun = true } = {}) {
  const result = await preview(client, account, order);
  if (!result.valid) {
    console.log('  Preview invalid; not submitting.');
    return null;
  }
  if (dryRun) {
    console.log('  [dry run] not submitting.');
    return null;
  }

  const idempotencyKey = client.newIdempotencyKey();
  const placed = await client.post(`${client.link(account, 'self')}/orders`, order, {
    idempotencyKey,
  });

  console.log(`  Placed ${placed.orderId}  status=${placed.status}`);
  // A 201 acknowledges routing, not a fill. Terminal state arrives from the
  // event stream or a poll.
  return placed;
}

// --- stock -----------------------------------------------------------------

/** Buy 300 MSFT, limit 412.50, day. */
export async function stockLimitBuy(client, account, dryRun = true) {
  console.log('\nStock: limit buy');
  const instrument = await resolveEquity(client, 'MSFT');
  return place(client, account, {
    assetClass: 'EQUITY',
    clientOrderId: 'node-example-limit-buy',
    side: 'BUY',
    instrumentId: instrument.instrumentId,
    quantity: '300',
    orderType: 'LIMIT',
    limitPrice: '412.50',
    timeInForce: 'DAY',
    extendedHours: false,
  }, { dryRun });
}

/**
 * Flatten an existing position at market.
 *
 * Quantity comes from the position, not a constant -- selling more than you
 * hold turns a close into an unintended short.
 */
export async function stockMarketSell(client, account, dryRun = true) {
  console.log('\nStock: market sell to flatten');
  const positions = await client.collect(client.link(account, 'positions'), {
    params: { instrumentType: 'EQUITY' },
  });
  if (positions.length === 0) {
    console.log('  No equity positions to close.');
    return null;
  }

  const position = positions[0];
  return place(client, account, {
    assetClass: 'EQUITY',
    clientOrderId: `node-flatten-${position.positionId.slice(-8)}`,
    side: position.side === 'LONG' ? 'SELL' : 'BUY_TO_COVER',
    instrumentId: position.instrument.instrumentId,
    quantity: Dec.abs(position.quantity),
    orderType: 'MARKET',
    timeInForce: 'DAY',
  }, { dryRun });
}

/**
 * Entry with an attached profit target and stop.
 *
 * The exits are released only when the entry fills and are OCO: whichever
 * triggers first cancels the other, so a filled target cannot leave a live
 * stop behind.
 */
export async function stockBracket(client, account, dryRun = true) {
  console.log('\nStock: bracket order');
  const instrument = await resolveEquity(client, 'MSFT');
  return place(client, account, {
    assetClass: 'EQUITY',
    clientOrderId: 'node-example-bracket',
    side: 'BUY',
    instrumentId: instrument.instrumentId,
    quantity: '100',
    orderType: 'LIMIT',
    limitPrice: '410.00',
    timeInForce: 'DAY',
    bracket: {
      takeProfit: { orderType: 'LIMIT', limitPrice: '430.00' },
      stopLoss: { orderType: 'STOP', stopPrice: '398.00' },
    },
  }, { dryRun });
}

/** Trailing stop 5% below the high-water mark, good till cancelled. */
export async function stockTrailingStop(client, account, dryRun = true) {
  console.log('\nStock: trailing stop');
  const instrument = await resolveEquity(client, 'NVDA');
  return place(client, account, {
    assetClass: 'EQUITY',
    clientOrderId: 'node-example-trail',
    side: 'SELL',
    instrumentId: instrument.instrumentId,
    quantity: '50',
    orderType: 'TRAILING_STOP',
    trailingPercent: '5.0',
    timeInForce: 'GTC',
  }, { dryRun });
}

// --- options ---------------------------------------------------------------

/**
 * Buy 5 AAPL Dec-26 250 calls to open, limit 12.40 per contract.
 *
 * `limitPrice` is per contract; the cash outlay is price x quantity x
 * contractMultiplier -- 12.40 x 5 x 100 = 6,200. Requires LEVEL_2.
 */
export async function optionSingleLeg(client, account, dryRun = true) {
  console.log('\nOption: single-leg long call');
  const contract = await resolveOption(client, 'AAPL', '2026-12-18', '250.00', 'CALL');
  return place(client, account, {
    assetClass: 'OPTION',
    strategy: 'SINGLE',
    clientOrderId: 'node-example-long-call',
    legs: [{ instrumentId: contract.instrumentId, side: 'BUY_TO_OPEN', ratio: 1 }],
    quantity: '5',
    orderType: 'LIMIT',
    limitPrice: '12.40',
    priceEffect: 'DEBIT',
    timeInForce: 'DAY',
  }, { dryRun });
}

/**
 * 10 AAPL Dec-26 250/260 call debit spreads at a net 3.15.
 *
 * Two points that trip people up:
 *
 *   - `quantity` is strategy units, not contracts. 10 means 10 spreads, so 20
 *     contracts total.
 *   - Declaring `strategy: VERTICAL` is what gets spread margin applied. The
 *     same legs sent as CUSTOM are margined leg by leg, and the short call
 *     alone would demand far more buying power -- often the difference between
 *     accepted and rejected.
 */
export async function optionVerticalSpread(client, account, dryRun = true) {
  console.log('\nOption: vertical debit spread');
  const [longLeg, shortLeg] = await Promise.all([
    resolveOption(client, 'AAPL', '2026-12-18', '250.00', 'CALL'),
    resolveOption(client, 'AAPL', '2026-12-18', '260.00', 'CALL'),
  ]);

  return place(client, account, {
    assetClass: 'OPTION',
    strategy: 'VERTICAL',
    clientOrderId: 'node-example-vertical',
    legs: [
      { instrumentId: longLeg.instrumentId, side: 'BUY_TO_OPEN', ratio: 1 },
      { instrumentId: shortLeg.instrumentId, side: 'SELL_TO_OPEN', ratio: 1 },
    ],
    quantity: '10',
    orderType: 'NET_DEBIT',
    netPrice: '3.15',        // always positive; direction lives in priceEffect
    priceEffect: 'DEBIT',
    timeInForce: 'DAY',
  }, { dryRun });
}

/**
 * Four-leg iron condor for a net credit. Requires LEVEL_3.
 *
 * Leg order is irrelevant to the matching engine, but strike order keeps the
 * intent legible to whoever reads this next.
 */
export async function optionIronCondor(client, account, dryRun = true) {
  console.log('\nOption: iron condor');
  const spec = [
    ['520.00', 'PUT', 'BUY_TO_OPEN'],
    ['540.00', 'PUT', 'SELL_TO_OPEN'],
    ['600.00', 'CALL', 'SELL_TO_OPEN'],
    ['620.00', 'CALL', 'BUY_TO_OPEN'],
  ];

  const legs = await Promise.all(
    spec.map(async ([strike, kind, side]) => ({
      instrumentId: (await resolveOption(client, 'SPY', '2026-11-20', strike, kind)).instrumentId,
      side,
      ratio: 1,
    })),
  );

  return place(client, account, {
    assetClass: 'OPTION',
    strategy: 'IRON_CONDOR',
    clientOrderId: 'node-example-condor',
    legs,
    quantity: '2',
    orderType: 'NET_CREDIT',
    netPrice: '1.85',
    priceEffect: 'CREDIT',
    timeInForce: 'DAY',
  }, { dryRun });
}

/**
 * Sell calls against stock you already own. LEVEL_1, the lowest tier.
 *
 * The covered-call check is on shares held, not on the order: 100 shares per
 * contract. Selling more contracts than your share count covers makes the
 * excess naked, which needs LEVEL_4 and far more margin.
 */
export async function optionCoveredCall(client, account, dryRun = true) {
  console.log('\nOption: covered call');
  const positions = await client.collect(client.link(account, 'positions'), {
    params: { symbol: 'AAPL', instrumentType: 'EQUITY' },
  });
  if (positions.length === 0) {
    console.log('  No AAPL shares held; nothing to cover.');
    return null;
  }

  const shares = Dec.abs(positions[0].quantity);
  const contracts = Math.floor(Number(shares.split('.')[0]) / 100);
  if (contracts < 1) {
    console.log(`  Only ${shares} shares; need 100 per contract.`);
    return null;
  }

  const contract = await resolveOption(client, 'AAPL', '2026-12-18', '250.00', 'CALL');
  return place(client, account, {
    assetClass: 'OPTION',
    strategy: 'COVERED_CALL',
    clientOrderId: 'node-example-covered-call',
    legs: [{ instrumentId: contract.instrumentId, side: 'SELL_TO_OPEN', ratio: 1 }],
    quantity: String(contracts),
    orderType: 'LIMIT',
    limitPrice: '9.85',
    priceEffect: 'CREDIT',
    timeInForce: 'DAY',
  }, { dryRun });
}

// ---------------------------------------------------------------------------
// Amending and cancelling
// ---------------------------------------------------------------------------

/**
 * Reprice a working order using optimistic concurrency.
 *
 * The read/decide/write cycle is racing the market. `If-Match` is what turns
 * "the order filled while I was deciding" from a silent mis-amendment into a
 * 412 you can actually handle.
 */
export async function amendOrder(client, order, newPrice) {
  const selfUrl = client.link(order, 'self');
  const { body: fresh, headers } = await client.getWithHeaders(selfUrl);

  if (!client.can(fresh, 'replace')) {
    console.log(`  ${fresh.orderId} is ${fresh.status}; not amendable.`);
    return fresh;
  }

  try {
    const amended = await client.patch(selfUrl, { limitPrice: newPrice }, {
      ifMatch: headers.get('ETag'),
    });
    console.log(`  Repriced to ${newPrice}; status=${amended.status}`);
    return amended;
  } catch (err) {
    if (err instanceof ApiError && err.status === 412) {
      // Re-read and decide again -- do not blindly retry with a new ETag, or
      // you will amend an order whose state you never evaluated.
      const current = await client.get(selfUrl);
      console.log(`  Stale ETag; order is now ${current.status}. Re-evaluate before retrying.`);
      return current;
    }
    throw err;
  }
}

/**
 * Request cancellation.
 *
 * Cancellation is a request, not a guarantee. The order moves to
 * PENDING_CANCEL and reaches CANCELLED only when the venue acknowledges; a
 * fill already in flight can still land. Confirm terminal state separately.
 */
export async function cancelOrder(client, order) {
  const selfUrl = client.link(order, 'self');
  const { body: fresh, headers } = await client.getWithHeaders(selfUrl);

  if (!client.can(fresh, 'cancel')) {
    console.log(`  ${fresh.orderId} is ${fresh.status}; nothing to cancel.`);
    return fresh;
  }

  const cancelled = await client.delete(selfUrl, { ifMatch: headers.get('ETag') });
  console.log(`  Cancel requested; status=${cancelled.status}`);
  return cancelled;
}

/** Flatten the working order book. A useful panic button. */
export async function cancelAllWorking(client, account) {
  const { items: working } = await client.get(client.link(account, 'orders'));
  console.log(`\nCancelling ${working.length} working order(s)`);

  for (const order of working) {
    try {
      await cancelOrder(client, order);
    } catch (err) {
      // 409 here just means it reached a terminal state first -- benign.
      if (!(err instanceof ApiError)) throw err;
      console.log(`  ${order.orderId}: ${err.title}`);
    }
  }
}

// ---------------------------------------------------------------------------

async function main() {
  const client = new BrokerageClient();
  const { items } = await client.get('/accounts');
  const account = items[0];

  console.log(`Account ${account.accountId}  options=${account.optionsLevel}`);
  if (account.status !== 'ACTIVE') {
    console.log(`Account is ${account.status}; trading operations will be rejected.`);
    return;
  }

  // dryRun previews without routing. Flip it deliberately, sandbox only.
  const dryRun = true;

  try {
    await stockLimitBuy(client, account, dryRun);
    await stockBracket(client, account, dryRun);
    await stockTrailingStop(client, account, dryRun);

    if (['LEVEL_2', 'LEVEL_3', 'LEVEL_4'].includes(account.optionsLevel)) {
      await optionSingleLeg(client, account, dryRun);
    }
    if (['LEVEL_3', 'LEVEL_4'].includes(account.optionsLevel)) {
      await optionVerticalSpread(client, account, dryRun);
      await optionIronCondor(client, account, dryRun);
    }
    await optionCoveredCall(client, account, dryRun);
  } catch (err) {
    if (!(err instanceof ApiError)) throw err;
    console.error(`\nAPI error: ${err.message}`);
    if (err.type.endsWith('/insufficient-buying-power')) {
      console.error(
        `  needs ${err.problem.required?.amount} ${err.problem.required?.currency}, ` +
          `has ${err.problem.available?.amount}`,
      );
    } else if (err.type.endsWith('/options-level-insufficient')) {
      console.error(`  needs ${err.problem.requiredLevel}, account is ${err.problem.currentLevel}`);
    }
    process.exitCode = 1;
  }
}

if (import.meta.url === `file://${process.argv[1]}`) await main();
