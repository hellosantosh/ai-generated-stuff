/* Quant Stock Selector — weekly decision UI.
 *
 * Plain ES, no build step and no framework: the whole UI is a thin view over
 * the JSON API, and keeping it dependency-free means it works from a checkout
 * with nothing but `pip install`.
 *
 * The Sunday run drives the layout. Steps 1-3 are the ritual; everything else
 * is a tab you visit when you want to check the model's homework. Nothing on
 * page load triggers a heavy data load, so the page opens instantly and the
 * half-minute scoring pass only happens when you press a button that needs it.
 */
'use strict';

const API = '/api';
const $  = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

const DAY_MS = 86400000;
/* Past this many days the cached prices are too old to trade on. */
const STALE_DAYS = 3;
const VERY_STALE_DAYS = 7;

const state = {
  status: null,
  weekly: null,
  plan: null,
  holdings: null,
  activeJob: null,
  jobPollTimer: null,
  buildAfterUpdate: false,
  updatedThisSession: false,
  appliedThisSession: false,
  lastFinishedJob: null,
  rankingsLoaded: false,
  verdict: null,
};

/* ---------- helpers ---------- */
async function api(path, options = {}) {
  const response = await fetch(API + path, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
  });
  const text = await response.text();
  let body = null;
  try { body = text ? JSON.parse(text) : null; } catch { body = { detail: text }; }
  if (!response.ok) throw new Error(body?.detail || `HTTP ${response.status}`);
  return body;
}

const pct = (v, d = 2) => (v === null || v === undefined || Number.isNaN(v)) ? '—' : `${(v * 100).toFixed(d)}%`;
const money = (v, d = 2) => (v === null || v === undefined || Number.isNaN(v)) ? '—'
  : v.toLocaleString(undefined, { style: 'currency', currency: 'USD', minimumFractionDigits: d, maximumFractionDigits: d });
const num = (v, d = 2) => (v === null || v === undefined || Number.isNaN(v)) ? '—' : Number(v).toFixed(d);
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, c => (
  { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

/* Dates from the API are plain ISO days; render them the way a person reads a
   calendar, not as a timestamp. */
function prettyDate(iso) {
  if (!iso) return '—';
  const date = new Date(`${String(iso).slice(0, 10)}T12:00:00`);
  if (Number.isNaN(date.getTime())) return String(iso);
  return date.toLocaleDateString(undefined, { weekday: 'short', month: 'short', day: 'numeric' });
}
function daysSince(iso) {
  if (!iso) return null;
  const then = new Date(iso);
  if (Number.isNaN(then.getTime())) return null;
  return Math.floor((Date.now() - then.getTime()) / DAY_MS);
}
const plural = (n, word) => `${n} ${word}${n === 1 ? '' : 's'}`;

function toast(message, isError = false) {
  const el = document.createElement('div');
  el.className = 'toast' + (isError ? ' err' : '');
  el.textContent = message;
  document.body.appendChild(el);
  setTimeout(() => el.remove(), isError ? 6500 : 3200);
}

function copyText(text, label) {
  navigator.clipboard.writeText(text)
    .then(() => toast(`${label} copied to the clipboard`))
    .catch(() => toast('Could not copy to clipboard', true));
}

function statCard(label, value, meta = '', tone = '') {
  return `<div class="card stat">
    <div class="label">${esc(label)}</div>
    <div class="value ${tone}">${value}</div>
    ${meta ? `<div class="meta">${meta}</div>` : ''}
  </div>`;
}

function rowsOrEmpty(tbody, rows, colspan, message) {
  tbody.innerHTML = rows.length ? rows.join('')
    : `<tr><td colspan="${colspan}" class="empty">${esc(message)}</td></tr>`;
}

/* ---------- vocabulary ---------- */
/* The API speaks the model's language; the UI speaks the investor's. */
const SLEEVE_LABEL = {
  'core': 'index core',
  'sector': 'sector leader',
  'growth': 'high growth',
  'sector + growth': 'both sleeves',
};
const SLEEVE_TITLE = {
  'core': 'The benchmark ETF that anchors the portfolio; not a stock pick.',
  'sector': 'One of the top-scoring names in its GICS sector.',
  'growth': 'One of the highest-growth names across every sector.',
  'sector + growth': 'Picked by both sleeves, so it carries the sum of both weights.',
};
function sleevePill(sleeve) {
  if (!sleeve) return '';
  const key = String(sleeve);
  const cls = key.includes('+') ? 'growth' : key;
  return `<span class="pill ${esc(cls)}" title="${esc(SLEEVE_TITLE[key] || '')}">${esc(SLEEVE_LABEL[key] || key)}</span>`;
}

/* The benchmark ETF spans every sector and anything held outside the index has no
   classification at all. Both are shown as "—" rather than bucketed somewhere. */
function sectorCell(sector, sleeve) {
  if (sector) return `<span class="sector">${esc(sector)}</span>`;
  const why = sleeve === 'core'
    ? 'An index ETF: it holds every sector, so it belongs to none.'
    : 'No sector classification for this ticker.';
  return `<span class="muted" title="${esc(why)}">—</span>`;
}

const ACTION = {
  NEW:   { label: 'Buy new',     cls: 'new',   title: 'You do not own this yet.' },
  BUY:   { label: 'Buy more',    cls: 'buy',   title: 'You own less than the target weight.' },
  SELL:  { label: 'Trim',        cls: 'sell',  title: 'You own more than the target weight; sell part of it.' },
  EXIT:  { label: 'Sell all',    cls: 'exit',  title: 'No longer picked by the model; close the position.' },
  HOLD:  { label: 'Hold',        cls: 'hold',  title: 'Within tolerance of the target; do nothing.' },
  DRIFT: { label: 'Keep for now', cls: 'drift', title: 'Dropped by the model, but buy-only weeks never sell.' },
};
const actionPill = (action) => {
  const spec = ACTION[action] || { label: action, cls: '', title: '' };
  return `<span class="pill ${esc(spec.cls)}" title="${esc(spec.title)}">${esc(spec.label)}</span>`;
};
const isBuy  = (action) => action === 'BUY' || action === 'NEW';
const isSell = (action) => action === 'SELL' || action === 'EXIT';

/* ---------- step chrome ---------- */
function setStep(id, stateName, text) {
  const step = $(`#step${id}`);
  if (!step) return;
  step.classList.toggle('done', stateName === 'done');
  step.classList.toggle('active', stateName === 'active');
  $(`#step${id}State`).innerHTML = text || '';
}

/* ---------- banners ---------- */
function renderBanners() {
  const box = $('#banners');
  const out = [];
  const w = state.weekly, s = state.status;

  if (s?.synthetic_mode || w?.synthetic) {
    out.push(`<div class="banner danger"><span class="icon">⚠</span><div>
      <strong>Synthetic data.</strong> ${esc(w?.synthetic_banner || 'This server was started with the generated-data provider.')}
      Nothing on this page reflects real markets, and no trade plan will be produced.</div></div>`);
  }
  if (w?.partial_session) {
    out.push(`<div class="banner warn"><span class="icon">◐</span><div>
      <strong>Today's session may still be open.</strong> The newest price is from today,
      so it could be a partial bar. Re-run step 1 after the close before you trade.</div></div>`);
  }
  (w?.warnings || []).forEach(warning => {
    out.push(`<div class="banner warn"><span class="icon">!</span><div>
      <strong>Model warning.</strong> ${esc(warning)}</div></div>`);
  });
  if (w && !w.sector_classification_point_in_time) {
    out.push(`<div class="banner info"><span class="icon">i</span><div>
      Sector labels are today's; historical sector changes are not modeled.</div></div>`);
  }
  const v = state.verdict;
  if (v) {
    const behind = v.ivvEnd - v.mixEnd;
    out.push(behind > 0
      ? `<div class="banner warn"><span class="icon">⚠</span><div>
          <strong>Your own backtest says this strategy lost to the benchmark.</strong>
          Contributing the same money from ${esc(v.start)} to ${esc(v.end)}, the combined
          portfolio ended at ${money(v.mixEnd, 0)} (${pct(v.mixCagr, 2)} a year) against
          ${money(v.ivvEnd, 0)} (${pct(v.ivvCagr, 2)} a year) for buying only the benchmark —
          <strong>${money(behind, 0)} behind</strong>. Read the
          <em>Track record</em> tab before you commit money to the stock sleeves.</div></div>`
      : `<div class="banner info"><span class="icon">i</span><div>
          Your last backtest (${esc(v.start)} to ${esc(v.end)}) has the combined portfolio ahead
          of the benchmark, ${money(v.mixEnd, 0)} against ${money(v.ivvEnd, 0)}. That is one
          historical path, not a forecast.</div></div>`);
  }
  if (s && !s.latest_run) {
    out.push(`<div class="banner info"><span class="icon">i</span><div>
      <strong>No backtest has been run on this machine.</strong> There is no evidence yet
      that picking these stocks beats simply buying the benchmark. Run one on the
      <em>Track record</em> tab before committing money.</div></div>`);
  }
  box.innerHTML = out.join('');
}

/* ---------- freshness / step 1 ---------- */
function renderFreshness() {
  const s = state.status;
  const bar = $('#freshBar');
  if (!s) return;

  const age = daysSince(s.cache_updated);
  const has = s.cache_files > 0 && age !== null;
  const through = state.weekly
    ? ` Companies were scored on the close of <strong>${esc(prettyDate(state.weekly.as_of))}</strong>.`
    : '';

  let tone, text, stepState, stepText, hint;
  if (!has) {
    tone = 'bad';
    text = '<strong>No market data downloaded yet.</strong> Start with step 1.';
    stepState = 'active';
    stepText = 'Nothing is cached yet. The first download fetches prices and SEC filings for '
      + 'the whole index and takes a few minutes. After that, weekly top-ups are quick.';
    hint = 'first run: a few minutes';
  } else if (age >= VERY_STALE_DAYS) {
    tone = 'bad';
    text = `<strong>Prices are ${plural(age, 'day')} old</strong> (downloaded ${esc(prettyDate(s.cache_updated))}).
      Too stale to trade on — run step 1.` + through;
    stepState = 'active';
    stepText = `The cache was last refreshed on ${prettyDate(s.cache_updated)}. Refresh it before building this week's basket.`;
    hint = 'usually under a minute from cache';
  } else if (age >= STALE_DAYS) {
    tone = 'stale';
    text = `Prices downloaded ${esc(prettyDate(s.cache_updated))} (${plural(age, 'day')} ago).
      Refresh to pick up Friday's close.` + through;
    stepState = 'active';
    stepText = 'A weekly refresh adds the sessions since the last download and any filings that landed during the week.';
    hint = 'usually under a minute';
  } else {
    tone = 'ok';
    text = `<strong>Data is current</strong> — downloaded ${esc(prettyDate(s.cache_updated))}.` + through;
    stepState = 'done';
    stepText = 'Nothing to do. You can refresh again if you want, but the cache already covers the latest close.';
    hint = '';
  }

  bar.className = `freshbar ${tone}`;
  /* One span per flex item: loose text nodes beside a <strong> would each become
     their own item and pick up the container's gap as stray whitespace. */
  bar.innerHTML = `<span>${text}</span>
    <span class="muted" style="margin-left:auto">${esc(String(s.provider))} prices · ${esc(String(s.fundamentals_provider))} fundamentals</span>`;

  setStep(1, stepState, stepState === 'done' ? 'data is current' : 'needs a refresh');
  $('#step1Text').textContent = stepText;
  $('#step1Hint').textContent = hint;
}

/* ---------- step 2: the plan ---------- */
function selectedMode() {
  return ($$('input[name="mode"]').find(r => r.checked) || {}).value || '';
}

async function buildPlan() {
  const tbody = $('#planTable tbody');
  const contribution = parseFloat($('#contribution').value) || 0;
  if (contribution <= 0) { toast('Enter an amount greater than zero', true); return; }

  setStep(2, 'active', 'building…');
  tbody.innerHTML = `<tr><td colspan="10" class="empty"><span class="spinner"></span>
    Loading prices and scoring the index… the first build of the session takes about half a minute.</td></tr>`;
  $('#buildPlan').disabled = true;
  try {
    const query = new URLSearchParams({ contribution: String(contribution) });
    const mode = selectedMode();
    if (mode) query.set('mode', mode);
    state.plan = await api(`/trade-plan?${query}`);
  } catch (err) {
    rowsOrEmpty(tbody, [], 11, err.message);
    setStep(2, 'active', 'failed');
    toast(err.message, true);
    return;
  } finally {
    $('#buildPlan').disabled = false;
  }
  renderPlan();
  /* The service has the week cached by now, so this is instant and gives us the
     target basket plus any model warnings. */
  loadWeekly().catch(() => {});
}

function renderPlan() {
  const plan = state.plan;
  if (!plan) return;
  const meta = plan.meta || {};
  const buys = plan.trades.filter(t => isBuy(t.action));
  const sells = plan.trades.filter(t => isSell(t.action));

  setStep(2, 'done', `plan for ${money(plan.contribution, 0)}`);
  setStep(3, state.appliedThisSession ? 'done' : 'active',
    `${plural(buys.length + sells.length, 'order')} to place`);

  $('#modeSummary').textContent = plan.mode === 'rebalance'
    ? '— this week: full rebalance' : '— this week: buy only';

  /* Say in one sentence which mode ran and why, so the radio group never has to
     be understood to use the app. */
  const first = !meta.last_rebalance;
  const modeNote = plan.mode === 'rebalance'
    ? (first
        ? `<strong>Building the basket from scratch.</strong> Nothing has been recorded as traded
           yet, so every line below is a fresh buy and there is nothing to sell.`
        : `<strong>Rebalance week.</strong> ${esc(meta.rebalance_reason || '')}. Your whole
           portfolio plus this week's cash is traded back to the target weights, so this
           list can include trims and sells.`)
    : `<strong>Buy-only week.</strong> ${esc(meta.rebalance_reason || '')}. This week's cash goes
       to the names furthest below their target; nothing is sold.`;

  const notes = [`<div class="banner ${plan.mode === 'rebalance' ? 'warn' : 'info'}">
    <span class="icon">${plan.mode === 'rebalance' ? '⟳' : '＋'}</span><div>${modeNote}</div></div>`];
  (plan.notes || []).forEach(n => notes.push(
    `<div class="banner"><span class="icon">·</span><div>${esc(n)}</div></div>`));
  notes.push(`<div class="banner"><span class="icon">✋</span><div>${esc(plan.disclaimer)}</div></div>`);
  $('#planMeta').innerHTML = notes.join('');

  $('#planKpis').innerHTML = [
    statCard('Portfolio value now', money(plan.portfolio_value_before, 0),
      plan.portfolio_value_before > 0
        ? 'your recorded positions at their last close'
        : 'nothing recorded on the My portfolio tab'),
    statCard('Cash going in', money(plan.contribution, 0), 'the amount you entered in step 2'),
    statCard('Buying', money(plan.total_buys, 0), plural(buys.length, 'order'), 'pos'),
    statCard('Selling', money(plan.total_sells, 0),
      sells.length ? plural(sells.length, 'order') : 'nothing sold this week',
      plan.total_sells > 0 ? 'neg' : ''),
    statCard('Portfolio after', money(plan.portfolio_value_after, 0),
      'value now + your cash (trades only move money between positions)'),
  ].join('');

  const rows = plan.trades.map(t => `<tr>
    <td>${actionPill(t.action)}</td>
    <td class="ticker">${esc(t.ticker)}</td>
    <td>${sleevePill(t.sleeve)}</td>
    <td>${sectorCell(t.sector, t.sleeve)}</td>
    <td class="num">${t.action === 'HOLD' ? '—' : money(t.amount)}</td>
    <td class="num">${t.action === 'HOLD' ? '—' : num(t.shares, 3)}</td>
    <td class="num">${money(t.price)}</td>
    <td class="num detail">${pct(t.current_weight, 2)}</td>
    <td class="num detail">${pct(t.target_weight, 2)}</td>
    <td class="num detail">${t.score === null || t.score === undefined ? '—' : num(t.score, 1)}</td>
    <td class="muted">${esc(t.reason)}</td>
  </tr>`);
  if (buys.length || sells.length) {
    rows.push(`<tr class="total">
      <td>Total</td><td></td><td></td><td></td>
      <td class="num">${money(plan.total_buys - plan.total_sells, 0)} net</td>
      <td class="num"></td><td class="num"></td>
      <td class="num detail"></td><td class="num detail"></td><td class="num detail"></td>
      <td class="muted">${esc(money(plan.total_buys, 0))} of buys, ${esc(money(plan.total_sells, 0))} of sells</td></tr>`);
  }
  rowsOrEmpty($('#planTable tbody'), rows, 11,
    'No orders proposed — everything is already within tolerance of the target.');
  $('#applyPlan').disabled = !(buys.length || sells.length);
}

function planCsv() {
  const lines = ['action,ticker,sleeve,sector,amount_usd,shares,price,current_weight_pct,target_weight_pct,reason'];
  (state.plan?.trades || []).forEach(t => lines.push([
    t.action, t.ticker, t.sleeve, `"${t.sector || ''}"`, t.amount, t.shares, t.price,
    (t.current_weight * 100).toFixed(4), (t.target_weight * 100).toFixed(4),
    `"${String(t.reason).replace(/"/g, '""')}"`,
  ].join(',')));
  return lines.join('\n');
}

async function applyPlan() {
  const plan = state.plan;
  if (!plan) return;
  const buys = plan.trades.filter(t => isBuy(t.action)).length;
  const sells = plan.trades.filter(t => isSell(t.action)).length;
  const summary =
    `Record ${buys} buy(s) and ${sells} sell(s) in your portfolio?\n\n`
    + 'This does NOT place any orders. It updates data/holdings.json so that next week the '
    + 'app knows what you own and can tell you what to sell.\n\n'
    + 'Only do this after your broker has actually filled the orders.';
  if (!confirm(summary)) return;
  try {
    const result = await api('/trade-plan/apply', {
      method: 'POST',
      body: JSON.stringify({ contribution: plan.contribution, mode: plan.mode, confirm: true }),
    });
    state.appliedThisSession = true;
    setStep(3, 'done', `${plural(result.recorded, 'trade')} recorded`);
    toast(`Recorded ${plural(result.recorded, 'trade')} in your portfolio`);
    await loadStatus();
    await loadHoldings();
    await buildPlan();
  } catch (err) { toast(err.message, true); }
}

/* ---------- the target basket ---------- */
async function loadWeekly(refresh = false) {
  state.weekly = await api(`/weekly${refresh ? '?refresh=true' : ''}`);
  renderBasket();
  renderFreshness();
  renderBanners();
}

function renderBasket() {
  const w = state.weekly;
  if (!w) return;
  const contribution = parseFloat($('#contribution').value) || 0;
  const stocks = w.basket.filter(b => b.sleeve !== 'core');

  $('#basketMeta').textContent =
    `${w.basket.length} positions (${stocks.length} stocks + the benchmark core) · `
    + `scored on ${prettyDate(w.as_of)}'s close`
    + (w.execution_date ? ` · meant to be filled ${prettyDate(w.execution_date)}` : '');

  const rows = w.basket.map(b => `<tr>
    <td class="ticker">${esc(b.ticker)}</td>
    <td>${sleevePill(b.sleeve)}</td>
    <td>${sectorCell(b.sector, b.sleeve)}</td>
    <td class="num">${pct(b.weight, 2)}</td>
    <td class="num scorecell">${b.score === null ? '—'
      : `<div class="bar" style="width:${Math.max(0, Math.min(100, b.score))}%"></div><span>${num(b.score, 1)}</span>`}</td>
    <td class="num">${money(b.weight * contribution)}</td>
  </tr>`);
  const total = w.basket.reduce((sum, b) => sum + b.weight, 0);
  if (rows.length) {
    rows.push(`<tr class="total"><td>Total</td><td></td><td></td>
      <td class="num">${pct(total, 2)}</td><td></td>
      <td class="num">${money(total * contribution)}</td></tr>`);
  }
  rowsOrEmpty($('#basketTable tbody'), rows, 6, 'No basket available.');
}

function basketCsv() {
  const contribution = parseFloat($('#contribution').value) || 0;
  const lines = ['ticker,weight_pct,sleeve,sector,score,amount_usd'];
  (state.weekly?.basket || []).forEach(b => lines.push([
    b.ticker, (b.weight * 100).toFixed(4), b.sleeve, `"${b.sector || ''}"`, b.score ?? '',
    (b.weight * contribution).toFixed(2),
  ].join(',')));
  return lines.join('\n');
}

/* ---------- rankings ---------- */
async function loadRankings() {
  const tbody = $('#rankTable tbody');
  state.rankingsLoaded = true;
  tbody.innerHTML = `<tr><td colspan="10" class="empty"><span class="spinner"></span> Scoring the index…</td></tr>`;
  try {
    const sleeve = $('#rankSleeve').value;
    const limit = $('#rankLimit').value;
    const data = await api(`/rankings?sleeve=${sleeve}&limit=${limit}`);
    let lastSector = null;
    const rows = [];
    data.rows.forEach(r => {
      if (sleeve === 'sector_leaders' && r.sector !== lastSector) {
        lastSector = r.sector;
        rows.push(`<tr class="total"><td colspan="10">${esc(r.sector)}</td></tr>`);
      }
      rows.push(`<tr>
        <td class="num">${r.rank}${r.selected ? ' ✓' : ''}</td>
        <td class="ticker">${esc(r.ticker)}</td>
        <td>${esc(r.company_name || '')}</td>
        <td class="muted">${esc(r.sector || '')}</td>
        <td class="num scorecell"><div class="bar" style="width:${Math.max(0, Math.min(100, r.total_score || 0))}%"></div><span>${num(r.total_score, 1)}</span></td>
        <td class="num">${num(r.growth, 0)}</td><td class="num">${num(r.momentum, 0)}</td>
        <td class="num">${num(r.quality, 0)}</td><td class="num">${num(r.valuation, 0)}</td>
        <td class="num">${num(r.risk, 0)}</td>
      </tr>`);
    });
    rowsOrEmpty(tbody, rows, 10, 'No rankings available.');
  } catch (err) {
    rowsOrEmpty(tbody, [], 10, err.message);
  }
}

/* ---------- holdings ---------- */
async function loadHoldings() {
  const tbody = $('#holdingsTable tbody');
  try {
    state.holdings = await api('/holdings');
  } catch (err) {
    rowsOrEmpty(tbody, [], 8, err.message);
    return;
  }
  const h = state.holdings;
  $('#holdingsMeta').textContent = h.positions.length
    ? `${plural(h.positions.length, 'position')} · ${money(h.total_value, 0)} · saved ${h.updated_at || 'never'}`
    : 'nothing recorded yet';

  const rows = h.positions.map((p, i) => `<tr data-index="${i}">
    <td><input class="h-ticker" value="${esc(p.ticker)}" style="width:80px"></td>
    <td class="num"><input class="h-shares" type="number" step="any" min="0" value="${p.shares}" style="width:110px"></td>
    <td class="num"><input class="h-basis" type="number" step="any" min="0" value="${p.cost_basis}" style="width:110px"></td>
    <td class="num">${money(p.price)}</td>
    <td class="num">${money(p.value, 0)}</td>
    <td class="num">${pct(p.weight, 2)}</td>
    <td class="num">${money(p.unrealized, 0)}</td>
    <td><button class="h-remove" title="Remove this row">✕</button></td>
  </tr>`);
  rowsOrEmpty(tbody, rows, 8,
    'Nothing recorded. Add a position, import a broker CSV, or just run your first Sunday '
    + 'run and press “I placed these orders”.');
  if (h.unpriced?.length) {
    toast(`No price found for ${h.unpriced.join(', ')} — check the ticker`, true);
  }
}

function addPositionRow() {
  const tbody = $('#holdingsTable tbody');
  if ($('.empty', tbody)) tbody.innerHTML = '';
  const tr = document.createElement('tr');
  tr.innerHTML = `
    <td><input class="h-ticker" value="" placeholder="AAPL" style="width:80px"></td>
    <td class="num"><input class="h-shares" type="number" step="any" min="0" value="0" style="width:110px"></td>
    <td class="num"><input class="h-basis" type="number" step="any" min="0" value="0" style="width:110px"></td>
    <td class="num">—</td><td class="num">—</td><td class="num">—</td><td class="num">—</td>
    <td><button class="h-remove" title="Remove this row">✕</button></td>`;
  tbody.appendChild(tr);
  $('.h-ticker', tr).focus();
}

async function saveHoldings() {
  const positions = $$('#holdingsTable tbody tr').map(tr => {
    const ticker = $('.h-ticker', tr)?.value?.trim().toUpperCase();
    const shares = parseFloat($('.h-shares', tr)?.value || '0');
    const basis = parseFloat($('.h-basis', tr)?.value || '0');
    return (ticker && shares > 0) ? { ticker, shares, cost_basis: basis || 0 } : null;
  }).filter(Boolean);

  try {
    await api('/holdings', {
      method: 'PUT',
      body: JSON.stringify({
        positions, cash: 0,
        last_rebalance: state.holdings?.last_rebalance || null,
      }),
    });
    toast(`Saved ${plural(positions.length, 'position')}`);
    await loadHoldings();
    await loadStatus();
  } catch (err) { toast(err.message, true); }
}

async function importCsv(file) {
  const form = new FormData();
  form.append('file', file);
  try {
    const response = await fetch(`${API}/holdings/import`, { method: 'POST', body: form });
    const body = await response.json();
    if (!response.ok) throw new Error(body.detail || 'import failed');
    toast(`Imported ${plural(body.imported, 'position')}`);
    await loadHoldings();
  } catch (err) { toast(err.message, true); }
}

/* ---------- backtest ---------- */
const METRIC_ROWS = [
  ['weekly_contribution', 'Weekly contribution', 'money'],
  ['total_contributions', 'Total contributions', 'money'],
  ['ending_value', 'Ending balance', 'money'],
  ['total_gain', 'Gain', 'money'],
  ['return_on_contributions', 'Return on contributions', 'pct'],
  ['time_weighted_return', 'Time-weighted return', 'pct'],
  ['cagr_twr', 'CAGR (time-weighted)', 'pct'],
  ['xirr', 'XIRR (money-weighted)', 'pct'],
  ['max_drawdown', 'Maximum drawdown', 'pct'],
  ['volatility', 'Volatility', 'pct'],
  ['sharpe', 'Sharpe', 'num'],
  ['sortino', 'Sortino', 'num'],
  ['beta', 'Beta vs IVV', 'num'],
  ['tracking_error', 'Tracking error', 'pct'],
  ['turnover', 'Turnover (annual)', 'pct'],
  ['num_trades', 'Trades', 'int'],
  ['weeks_outperforming', 'Weeks outperforming IVV', 'pct'],
  ['months_outperforming', 'Months outperforming IVV', 'pct'],
];
const VARIANT_ORDER = ['ivv', 'sector_leaders', 'high_growth', 'combined'];
const VARIANT_LABEL = {
  ivv: 'IVV only', sector_leaders: 'Sector leaders',
  high_growth: 'High growth', combined: 'Combined',
};

async function loadRuns() {
  try {
    const data = await api('/runs');
    const select = $('#runSelect');
    select.innerHTML = data.runs.length
      ? data.runs.map(r => `<option value="${esc(r.run_id)}">${esc(r.run_id)} — ${esc(r.start_date)} to ${esc(r.end_date)}</option>`).join('')
      : '<option value="">no runs stored</option>';
    if (data.runs.length) await loadRun();
  } catch (err) { toast(err.message, true); }
}

async function loadRun() {
  const runId = $('#runSelect').value;
  if (!runId) return;
  try {
    const data = await api(`/runs/${encodeURIComponent(runId)}`);
    const variants = VARIANT_ORDER.filter(v => data.metrics[v]);
    $('#runTable thead').innerHTML =
      `<tr><th>Metric</th>${variants.map(v => `<th class="num">${esc(VARIANT_LABEL[v] || v)}</th>`).join('')}</tr>`;

    const format = (value, kind) => {
      if (value === null || value === undefined) return '—';
      if (kind === 'money') return money(value, 0);
      if (kind === 'pct') return pct(value, 2);
      if (kind === 'int') return Number(value).toLocaleString();
      return num(value, 3);
    };
    const rows = METRIC_ROWS.map(([key, label, kind]) => `<tr>
      <td>${esc(label)}</td>
      ${variants.map(v => `<td class="num">${format(data.metrics[v]?.[key], kind)}</td>`).join('')}
    </tr>`);
    rowsOrEmpty($('#runTable tbody'), rows, variants.length + 1, 'No metrics.');

    const universe = data.provenance?.universe || {};
    $('#runBias').innerHTML = universe.bias_note
      ? `<div class="banner ${universe.survivorship_free ? 'info' : 'warn'}">
           <span class="icon">${universe.survivorship_free ? 'i' : '⚠'}</span>
           <div><strong>Universe.</strong> ${esc(universe.bias_note)}</div></div>`
      : '';

    const charts = ['portfolio_value', 'portfolio_vs_ivv', 'drawdown', 'rolling_12m',
                    'sector_contribution', 'top_winners'];
    $('#chartGrid').innerHTML = charts.map(name =>
      `<img loading="lazy" src="${API}/charts/${name}.png" alt="${esc(name.replace(/_/g, ' '))}"
            onerror="this.style.display='none'">`).join('');
  } catch (err) { toast(err.message, true); }
}

/* ---------- jobs ---------- */
async function startJob(path, body, options = {}) {
  try {
    const job = await api(path, { method: 'POST', body: JSON.stringify(body || {}) });
    state.activeJob = job.id;
    state.buildAfterUpdate = Boolean(options.thenBuildPlan);
    toast(options.toast || `Started ${job.name}`);
    if (options.showOnStep1) $('#step1Log').hidden = false;
    pollJobs();
  } catch (err) { toast(err.message, true); }
}

async function pollJobs() {
  let data;
  try { data = await api('/jobs'); } catch { return; }

  $('#jobStatus').innerHTML = data.busy ? '<span class="spinner"></span> a job is running' : 'idle';
  ['#startUpdate', '#startBacktest', '#updateData', '#runEverything'].forEach(sel => {
    const el = $(sel);
    if (el) el.disabled = data.busy;
  });

  $('#jobList').innerHTML = data.jobs.length ? data.jobs.map(j => `
    <div class="row" style="margin-bottom:.4rem">
      <button data-job="${esc(j.id)}" class="job-pick">${esc(j.name)}</button>
      <span class="pill ${j.status === 'failed' ? 'sell' : j.status === 'succeeded' ? 'buy' : 'hold'}">${esc(j.status)}</span>
      <span class="muted">${j.duration_seconds ? j.duration_seconds + 's' : ''}</span>
    </div>`).join('') : '<span class="muted">Nothing has run yet.</span>';

  if (!state.activeJob && data.jobs.length) state.activeJob = data.jobs[0].id;
  let job = null;
  if (state.activeJob) {
    try { job = await api(`/jobs/${state.activeJob}`); } catch { /* gone */ }
  }
  if (job) {
    const text = job.lines.length ? job.lines.join('\n') : '(no output yet)';
    $('#jobLog').textContent = text;
    $('#jobLog').scrollTop = $('#jobLog').scrollHeight;
    const stepLog = $('#step1Log');
    if (job.name === 'update-data') {
      stepLog.hidden = false;
      stepLog.textContent = text;
      stepLog.scrollTop = stepLog.scrollHeight;
      setStep(1, job.status === 'succeeded' ? 'done' : 'active',
        job.status === 'running' ? '<span class="spinner"></span> downloading…' : esc(job.status));
    }
    if (job.status === 'succeeded' && job.id !== state.lastFinishedJob) {
      state.lastFinishedJob = job.id;
      if (job.name === 'update-data') {
        state.updatedThisSession = true;
        await loadStatus();
        if (state.buildAfterUpdate) {
          state.buildAfterUpdate = false;
          await buildPlan();
        }
      }
      if (job.name === 'backtest') await loadRuns();
    }
  }

  clearTimeout(state.jobPollTimer);
  if (data.busy) state.jobPollTimer = setTimeout(pollJobs, 2500);
}

async function loadStatus() {
  try { state.status = await api('/status'); } catch { return; }
  const s = state.status;
  $('#brandSub').textContent =
    `${s.provider} prices · ${s.fundamentals_provider} fundamentals · strategy v${s.strategy_version}`;
  $('#statusKpis').innerHTML = [
    statCard('Price provider', esc(s.provider), `fundamentals: ${esc(s.fundamentals_provider)}`),
    statCard('Cached series', String(s.cache_files),
      s.cache_updated ? `updated ${esc(s.cache_updated.slice(0, 16))}` : 'never downloaded'),
    statCard('Recorded positions', String(s.holdings.positions),
      s.holdings.last_rebalance ? `last rebalance ${esc(s.holdings.last_rebalance)}` : 'no rebalance recorded'),
    statCard('Latest backtest', s.latest_run ? esc(s.latest_run.run_id) : '—',
      s.latest_run ? `${esc(s.latest_run.start_date)} → ${esc(s.latest_run.end_date)}` : 'none stored'),
  ].join('');
  renderFreshness();
  renderBanners();
  loadVerdict();
}

/* The one number that should decide whether to use this thing at all: did the
   strategy beat the benchmark in the person's own backtest? */
async function loadVerdict() {
  const run = state.status?.latest_run;
  if (!run || state.verdict?.runId === run.run_id) return;
  try {
    const data = await api(`/runs/${encodeURIComponent(run.run_id)}`);
    const ivv = data.metrics?.ivv;
    const mix = data.metrics?.combined;
    if (!ivv || !mix) return;
    state.verdict = {
      runId: run.run_id,
      start: run.start_date, end: run.end_date,
      ivvEnd: ivv.ending_value, mixEnd: mix.ending_value,
      ivvCagr: ivv.cagr_twr, mixCagr: mix.cagr_twr,
    };
    renderBanners();
  } catch { /* the verdict is a courtesy; never block the page on it */ }
}

async function loadReports() {
  try {
    const data = await api('/reports');
    const rows = data.files.map(f => `<tr>
      <td><a href="${API}/reports/${encodeURIComponent(f.name)}" target="_blank" rel="noopener">${esc(f.name)}</a></td>
      <td class="num">${(f.size / 1024).toFixed(0)} KB</td>
      <td class="muted">${esc(f.modified.replace('T', ' ').slice(0, 16))}</td>
    </tr>`);
    rowsOrEmpty($('#reportsTable tbody'), rows, 3, 'Nothing generated yet.');
  } catch { /* non-fatal */ }
}

/* ---------- tabs and wiring ---------- */
const PANELS = ['sunday', 'portfolio', 'picks', 'record', 'data'];

function switchTab(name) {
  if (!PANELS.includes(name)) name = 'sunday';
  $$('.tab').forEach(t => t.setAttribute('aria-selected', String(t.dataset.panel === name)));
  $$('.panel').forEach(p => { p.hidden = p.id !== `panel-${name}`; });
  location.hash = name;
  if (name === 'portfolio') loadHoldings();
  if (name === 'record') loadRuns();
  if (name === 'data') { pollJobs(); loadReports(); }
  if (name === 'picks' && !state.rankingsLoaded) loadRankings();
}

function initTheme() {
  const saved = localStorage.getItem('quant-theme');
  if (saved) document.documentElement.setAttribute('data-theme', saved);
  $('#themeToggle').addEventListener('click', () => {
    const current = document.documentElement.getAttribute('data-theme')
      || (matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light');
    const next = current === 'dark' ? 'light' : 'dark';
    document.documentElement.setAttribute('data-theme', next);
    try { localStorage.setItem('quant-theme', next); } catch { /* private mode */ }
  });
}

function init() {
  initTheme();
  $('#tabs').addEventListener('click', e => {
    const tab = e.target.closest('.tab');
    if (tab) switchTab(tab.dataset.panel);
  });

  /* Sunday run */
  $('#runEverything').addEventListener('click', () => startJob('/jobs/update-data', { force: false }, {
    thenBuildPlan: true, showOnStep1: true,
    toast: 'Downloading this week’s data — your orders will be built as soon as it finishes',
  }));
  $('#updateData').addEventListener('click', () => startJob('/jobs/update-data', { force: false }, {
    showOnStep1: true, toast: 'Downloading this week’s data',
  }));
  $('#buildPlan').addEventListener('click', buildPlan);
  $('#contribution').addEventListener('input', renderBasket);
  $('#contribution').addEventListener('keydown', e => { if (e.key === 'Enter') buildPlan(); });
  $$('input[name="mode"]').forEach(radio => radio.addEventListener('change', () => {
    if (state.plan) buildPlan();
  }));
  $('#showDetail').addEventListener('change', e =>
    document.body.classList.toggle('show-detail', e.target.checked));
  $('#copyPlan').addEventListener('click', () => copyText(planCsv(), 'Order list'));
  $('#copyBasket').addEventListener('click', () => copyText(basketCsv(), 'Target basket'));
  $('#applyPlan').addEventListener('click', applyPlan);

  /* Portfolio */
  $('#addPosition').addEventListener('click', addPositionRow);
  $('#saveHoldings').addEventListener('click', saveHoldings);
  $('#holdingsTable').addEventListener('click', e => {
    if (e.target.classList.contains('h-remove')) e.target.closest('tr').remove();
  });
  $('#importCsv').addEventListener('change', e => {
    if (e.target.files?.[0]) importCsv(e.target.files[0]);
    e.target.value = '';
  });

  /* Picks, record, data */
  $('#loadRankings').addEventListener('click', loadRankings);
  $('#rankSleeve').addEventListener('change', loadRankings);
  $('#runSelect').addEventListener('change', loadRun);
  $('#startBacktest').addEventListener('click', () => startJob('/jobs/backtest',
    { weeks: parseInt($('#btWeeks').value, 10) || 360 }, { toast: 'Backtest started' }));
  $('#startUpdate').addEventListener('click', () => startJob('/jobs/update-data', { force: false }));
  $('#refreshJobs').addEventListener('click', pollJobs);
  $('#jobList').addEventListener('click', e => {
    const button = e.target.closest('.job-pick');
    if (button) { state.activeJob = button.dataset.job; pollJobs(); }
  });

  setStep(2, 'active', '');
  setStep(3, '', 'waiting on step 2');
  switchTab((location.hash || '#sunday').slice(1));
  loadStatus();
  pollJobs();
}

document.addEventListener('DOMContentLoaded', init);
