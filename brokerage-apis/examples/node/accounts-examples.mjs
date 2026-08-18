/**
 * Accounts API examples: list, balances, positions, order status, transactions.
 *
 *     node accounts-examples.mjs
 */

import { ApiError, BrokerageClient, Dec } from './brokerage-client.mjs';

const pad = (s, n) => String(s).padEnd(n);
const rpad = (s, n) => String(s).padStart(n);

// ---------------------------------------------------------------------------
// GET /accounts
// ---------------------------------------------------------------------------

/**
 * Every account reachable with the current grant.
 *
 * `accountId` is the opaque handle used everywhere else. `accountNumber` is
 * the customer-facing value and arrives masked unless the grant carries
 * `accounts:read:full` -- do not key anything on it.
 */
export async function listAccounts(client) {
  const { items: accounts } = await client.get('/accounts');

  for (const account of accounts) {
    console.log(
      `${account.accountId}  ${rpad(account.accountNumber, 10)}  ` +
        `${pad(account.type, 7)} ${pad(account.registration, 16)} ` +
        `${pad(account.status, 10)} options=${account.optionsLevel}`,
    );

    // Affordances are state-dependent: a RESTRICTED account carries no
    // place-order link, so a UI can disable the trade button without knowing
    // anything about compliance rules.
    if (!client.can(account, 'place-order')) {
      console.log('    (not trade-enabled with this grant)');
    }
  }

  return accounts;
}

// ---------------------------------------------------------------------------
// GET /accounts/{accountId}/balances
// ---------------------------------------------------------------------------

/**
 * Cash, buying power, and margin. Reached by following the `balances` link
 * rather than building the URL, so the client survives a path change.
 */
export async function showBalances(client, account) {
  const balances = await client.follow(account, 'balances');

  console.log(`\nBalances as of ${balances.asOf} (${balances.currency})`);
  console.log(`  Net liquidation : ${rpad(Dec.format(balances.netLiquidation), 14)}`);
  console.log(`  Cash (settled)  : ${rpad(Dec.format(balances.cash.settled), 14)}`);
  console.log(`  Cash (total)    : ${rpad(Dec.format(balances.cash.total), 14)}`);
  console.log(`  Withdrawable    : ${rpad(Dec.format(balances.cash.withdrawable), 14)}`);

  // Stock and option buying power differ materially in a margin account --
  // sizing an option order off stock buying power gets it rejected.
  console.log('  Buying power');
  console.log(`    stock         : ${rpad(Dec.format(balances.buyingPower.stock), 14)}`);
  console.log(`    option        : ${rpad(Dec.format(balances.buyingPower.option), 14)}`);
  if (balances.buyingPower.dayTrading) {
    console.log(`    day trading   : ${rpad(Dec.format(balances.buyingPower.dayTrading), 14)}`);
  }

  if (balances.margin) {
    console.log(`  Excess liquidity: ${rpad(Dec.format(balances.margin.excessLiquidity), 14)}`);
    const call = balances.margin.marginCall;
    if (call) {
      // A margin call is the one balance field worth alerting on.
      console.log(`  ** ${call.type}: ${Dec.format(call.amount)} due ${call.dueDate} **`);
    }
  }

  if (balances.patternDayTrader) {
    console.log(`  Day trades left : ${balances.dayTradesRemaining}`);
  }

  return balances;
}

// ---------------------------------------------------------------------------
// GET /accounts/{accountId}/positions
// ---------------------------------------------------------------------------

export async function showPositions(client, account) {
  const positions = await client.collect(client.link(account, 'positions'));

  if (positions.length === 0) {
    console.log('\nNo open positions.');
    return positions;
  }

  console.log(
    `\n${pad('Symbol', 24)} ${pad('Side', 6)} ${rpad('Qty', 8)} ` +
      `${rpad('Avg cost', 11)} ${rpad('Market value', 14)} ${rpad('Unrealized', 13)}`,
  );
  console.log('-'.repeat(80));

  let totalUnrealized = '0';
  for (const position of positions) {
    const { instrument } = position;

    // Option positions carry the 21-character OSI symbol, which is unreadable
    // in a list. Build something human-facing from the option detail instead.
    const label =
      instrument.type === 'OPTION'
        ? `${instrument.option.underlyingSymbol} ${instrument.option.expiration.slice(2)} ` +
          `${Dec.format(instrument.option.strike, 0)}${instrument.option.optionType[0]}`
        : instrument.symbol;

    totalUnrealized = Dec.add(totalUnrealized, position.unrealizedPnl);

    console.log(
      `${pad(label, 24)} ${pad(position.side, 6)} ` +
        `${rpad(Dec.format(position.quantity, 0), 8)} ` +
        `${rpad(Dec.format(position.averageCost, 4), 11)} ` +
        `${rpad(Dec.format(position.marketValue), 14)} ` +
        `${rpad(Dec.format(position.unrealizedPnl), 13)}`,
    );
  }

  console.log('-'.repeat(80));
  console.log(`${pad('Total unrealized', 57)}${rpad(Dec.format(totalUnrealized), 13)}`);
  return positions;
}

// ---------------------------------------------------------------------------
// GET /accounts/{accountId}/orders
// ---------------------------------------------------------------------------

/**
 * Working orders, then a wider historical view.
 *
 * With no `status` filter the endpoint returns working orders only -- the set
 * a trading screen needs on load. Pass `status=ALL` to widen it.
 */
export async function showOrderStatus(client, account) {
  const ordersUrl = client.link(account, 'orders');

  const { items: working } = await client.get(ordersUrl);
  console.log(`\nWorking orders: ${working.length}`);

  for (const order of working) {
    const symbol = order.instrument?.symbol ?? order.strategy ?? '?';
    console.log(
      `  ${order.orderId}  ${pad(order.status, 17)} ${pad(order.side ?? '', 12)} ` +
        `${pad(symbol, 10)} ${Dec.format(order.filledQuantity, 0)}/` +
        `${Dec.format(order.quantity, 0)} @ ${order.limitPrice ?? 'MKT'}`,
    );

    // Presence of the link is the authority on what you may do next.
    const actions = ['cancel', 'replace'].filter((rel) => client.can(order, rel));
    console.log(`      available: ${actions.join(', ') || 'none (terminal)'}`);
  }

  // Everything from the last 7 days, terminal states included.
  const weekAgo = new Date(Date.now() - 7 * 864e5).toISOString();
  const recent = await client.collect(ordersUrl, { params: { status: ['ALL'], from: weekAgo } });
  console.log(`\nOrders in the last 7 days: ${recent.length}`);

  const byStatus = new Map();
  for (const order of recent) byStatus.set(order.status, (byStatus.get(order.status) ?? 0) + 1);
  for (const [status, count] of [...byStatus].sort((a, b) => b[1] - a[1])) {
    console.log(`  ${pad(status, 18)} ${count}`);
  }

  return working;
}

/**
 * Poll one order to a terminal state using conditional requests.
 *
 * `If-None-Match` makes an unchanged poll a 304 with no body, which is what
 * keeps a 1-second cadence inside the rate limit. For anything
 * latency-sensitive use the SSE stream -- this is for batch reconciliation.
 */
export async function pollOrderUntilTerminal(client, order, { timeoutMs = 60_000 } = {}) {
  const terminal = new Set(['FILLED', 'CANCELLED', 'REJECTED', 'EXPIRED', 'REPLACED']);
  const url = client.link(order, 'self');
  const deadline = Date.now() + timeoutMs;
  let etag;
  let current = order;

  while (Date.now() < deadline) {
    const { body, headers } = await client.getWithHeaders(url, {
      headers: etag ? { 'If-None-Match': etag } : {},
    });

    if (body) {
      current = body;
      etag = headers.get('ETag');
      console.log(`  -> ${current.status} filled=${current.filledQuantity}/${current.quantity}`);
      if (terminal.has(current.status)) return current;
    }

    await new Promise((r) => setTimeout(r, 1000));
  }

  console.log(`  timed out; order still ${current.status}`);
  return current;
}

/** Watch order events live instead of polling. */
export async function watchOrderEvents(client, account, { durationMs = 30_000 } = {}) {
  console.log(`\nWatching order events for ${durationMs / 1000}s...`);
  const controller = new AbortController();
  setTimeout(() => controller.abort(), durationMs);

  const seen = new Set();
  try {
    for await (const event of client.streamOrderEvents(account, { signal: controller.signal })) {
      // Delivery is at-least-once; dedupe on eventId.
      if (seen.has(event.eventId)) continue;
      seen.add(event.eventId);

      if (event.eventType === 'execution') {
        const fill = event.execution;
        console.log(
          `  fill ${event.orderId}: ${fill.quantity} @ ${fill.price} on ${fill.venue} ` +
            `-> ${event.status}`,
        );
      } else {
        console.log(`  ${event.eventType} ${event.orderId} -> ${event.status}`);
      }
    }
  } catch (err) {
    if (err.name !== 'AbortError') throw err;
  }
}

// ---------------------------------------------------------------------------
// GET /accounts/{accountId}/transactions
// ---------------------------------------------------------------------------

/**
 * Transaction history over a date window.
 *
 * Settlement can back-date entries, so a transaction may appear in a page you
 * already read. Reconcile on `transactionId`, which is permanent and unique --
 * hence the dedupe below rather than a blind sum.
 */
export async function showTransactions(client, account, { days = 30 } = {}) {
  const today = new Date();
  const params = {
    from: new Date(today.getTime() - days * 864e5).toISOString().slice(0, 10),
    to: today.toISOString().slice(0, 10),
  };

  const seen = new Set();
  const transactions = [];
  let netCash = '0';

  for await (const txn of client.paginate(client.link(account, 'transactions'), { params })) {
    if (seen.has(txn.transactionId)) continue;
    seen.add(txn.transactionId);
    transactions.push(txn);
    netCash = Dec.add(netCash, txn.netAmount);
  }

  console.log(`\nTransactions, last ${days} days: ${transactions.length}`);
  console.log(`${pad('Date', 12)} ${pad('Type', 16)} ${pad('Description', 44)} ${rpad('Net', 13)}`);
  console.log('-'.repeat(88));

  for (const txn of transactions.slice(0, 20)) {
    console.log(
      `${pad(txn.tradeDate, 12)} ${pad(txn.type, 16)} ` +
        `${pad((txn.description ?? '').slice(0, 44), 44)} ` +
        `${rpad(Dec.format(txn.netAmount), 13)}`,
    );
  }
  if (transactions.length > 20) console.log(`... and ${transactions.length - 20} more`);

  console.log('-'.repeat(88));
  console.log(`${pad('Net cash movement', 73)}${rpad(Dec.format(netCash), 13)}`);

  // Fee totals are a common reconciliation need and are not exposed as a
  // summary field, so roll them up from the fee arrays.
  const feesByType = new Map();
  for (const txn of transactions) {
    for (const fee of txn.fees ?? []) {
      feesByType.set(fee.type, Dec.add(feesByType.get(fee.type) ?? '0', fee.amount));
    }
  }
  if (feesByType.size) {
    console.log('\nFees by type:');
    for (const [type, amount] of [...feesByType].sort()) {
      console.log(`  ${pad(type, 14)} ${rpad(Dec.format(amount), 10)}`);
    }
  }

  return transactions;
}

// ---------------------------------------------------------------------------

async function main() {
  const client = new BrokerageClient();

  try {
    const accounts = await listAccounts(client);
    if (accounts.length === 0) {
      console.log('No accounts available under this grant.');
      return;
    }

    const account = accounts[0];
    console.log(`\n${'='.repeat(88)}`);
    console.log(`Account ${account.accountId} (${account.nickname ?? account.type})`);
    console.log('='.repeat(88));

    await showBalances(client, account);
    await showPositions(client, account);
    await showOrderStatus(client, account);
    await showTransactions(client, account);
  } catch (err) {
    if (!(err instanceof ApiError)) throw err;
    console.error(`\nAPI error: ${err.message}`);
    if (err.type.endsWith('/insufficient-scope')) {
      console.error(`  Re-authorize with the ${err.problem.requiredScope} scope.`);
    }
    for (const detail of err.errors) console.error(`  ${detail.pointer}: ${detail.detail}`);
    process.exitCode = 1;
  }
}

if (import.meta.url === `file://${process.argv[1]}`) await main();
