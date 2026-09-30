/* Quant Stock Selector dashboard.
 *
 * Plain ES modules, no build step and no framework: the whole UI is a thin
 * view over the JSON API, and keeping it dependency-free means it works from
 * a checkout with nothing but `pip install`.
 */
'use strict';

const API = '/api';
const $  = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

const state = {
  weekly: null,
  plan: null,
  holdings: null,
  status: null,
  jobPollTimer: null,
  activeJob: null,
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

const pct  = (v, d = 2) => (v === null || v === undefined || Number.isNaN(v)) ? '—' : `${(v * 100).toFixed(d)}%`;
const money = (v, d = 2) => (v === null || v === undefined || Number.isNaN(v)) ? '—'
  : v.toLocaleString(undefined, { style: 'currency', currency: 'USD', minimumFractionDigits: d, maximumFractionDigits: d });
const num = (v, d = 2) => (v === null || v === undefined || Number.isNaN(v)) ? '—' : Number(v).toFixed(d);
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, c => (
  { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

function toast(message, isError = false) {
  const el = document.createElement('div');
  el.className = 'toast' + (isError ? ' err' : '');
  el.textContent = message;
  document.body.appendChild(el);
  setTimeout(() => el.remove(), isError ? 6500 : 3200);
}

function copyText(text, label) {
  navigator.clipboard.writeText(text)
    .then(() => toast(`${label} copied`))
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

function sleevePill(sleeve) {
  if (!sleeve) return '';
  const key = sleeve.includes('+') ? 'growth' : sleeve.replace(/\s/g, '');
  return `<span class="pill ${esc(key)}">${esc(sleeve)}</span>`;
}

/* ---------- banners ---------- */
function renderBanners() {
  const box = $('#banners');
  const out = [];
  const w = state.weekly, s = state.status;

  if (w?.synthetic) {
    out.push(`<div class="banner danger"><span class="icon">⚠</span><div>
      <strong>Synthetic data.</strong> ${esc(w.synthetic_banner || '')}
      Nothing on this page reflects real markets.</div></div>`);
  }
  if (w?.partial_session) {
    out.push(`<div class="banner warn"><span class="icon">◐</span><div>
      <strong>The market may still be open.</strong> The decision date is today, so the
      last bar could be a partial session. Re-run after the close before trading.</div></div>`);
  }
  if (w && w.stale_days > 7) {
    out.push(`<div class="banner warn"><span class="icon">⌛</span><div>
      <strong>Data is ${w.stale_days} days old.</strong> Run “Update market data” on the
      Data &amp; jobs tab.</div></div>`);
  }
  (w?.warnings || []).forEach(warning => {
    out.push(`<div class="banner warn"><span class="icon">!</span><div>
      <strong>Model warning.</strong> ${esc(warning)}</div></div>`);
  });
  if (w && !w.sector_classification_point_in_time) {
    out.push(`<div class="banner info"><span class="icon">i</span><div>
      Sector classification is current-only; historical sector changes are not modeled.</div></div>`);
  }
  if (s && !s.latest_run) {
    out.push(`<div class="banner info"><span class="icon">i</span><div>
      <strong>No backtest has been run.</strong> There is no evidence yet that this
      strategy beats simply buying the benchmark. Run one on the Backtest tab.</div></div>`);
  }
  box.innerHTML = out.join('');
}

/* ---------- dashboard ---------- */
async function loadWeekly(refresh = false) {
  const tbody = $('#basketTable tbody');
  tbody.innerHTML = `<tr><td colspan="5" class="empty"><span class="spinner"></span> Computing rankings…</td></tr>`;
  try {
    state.weekly = await api(`/weekly${refresh ? '?refresh=true' : ''}`);
  } catch (err) {
    rowsOrEmpty(tbody, [], 5, err.message);
    toast(err.message, true);
    return;
  }
  renderWeekly();
  renderBanners();
}

function renderWeekly() {
  const w = state.weekly;
  if (!w) return;
  const stocks = w.basket.filter(b => b.sleeve !== 'core');
  const top = stocks.length ? stocks.reduce((a, b) => (b.weight > a.weight ? b : a)) : null;

  $('#dashKpis').innerHTML = [
    statCard('Decision date', esc(w.as_of),
      w.execution_date ? `fill ${esc(w.execution_date)} at the open` : 'no next session loaded'),
    statCard('Positions', String(w.basket.length),
      `${stocks.length} stocks + benchmark core`),
    statCard('Universe', `${w.eligible}`, `of ${w.universe_size} index members eligible`),
    statCard('Largest stock', top ? esc(top.ticker) : '—', top ? pct(top.weight, 2) : ''),
  ].join('');

  $('#basketMeta').textContent = `${w.basket.length} positions · ${esc(w.universe_status)}`;
  renderBasketRows();
}

function renderBasketRows() {
  const w = state.weekly;
  const contribution = parseFloat($('#dashContribution').value) || 0;
  const rows = (w?.basket || []).map(b => `<tr>
    <td class="ticker">${esc(b.ticker)}</td>
    <td>${sleevePill(b.sleeve)}</td>
    <td class="num">${pct(b.weight, 3)}</td>
    <td class="num scorecell">${b.score === null ? '—'
      : `<div class="bar" style="width:${Math.max(0, Math.min(100, b.score))}%"></div><span>${num(b.score, 1)}</span>`}</td>
    <td class="num">${money(b.weight * contribution)}</td>
  </tr>`);
  const total = (w?.basket || []).reduce((sum, b) => sum + b.weight, 0);
  if (rows.length) {
    rows.push(`<tr class="total"><td>Total</td><td></td>
      <td class="num">${pct(total, 2)}</td><td></td>
      <td class="num">${money(total * contribution)}</td></tr>`);
  }
  rowsOrEmpty($('#basketTable tbody'), rows, 5, 'No basket available.');
}

function basketCsv() {
  const contribution = parseFloat($('#dashContribution').value) || 0;
  const lines = ['ticker,weight_pct,sleeve,score,amount'];
  (state.weekly?.basket || []).forEach(b => {
    lines.push([b.ticker, (b.weight * 100).toFixed(4), b.sleeve,
      b.score ?? '', (b.weight * contribution).toFixed(2)].join(','));
  });
  return lines.join('\n');
}

/* ---------- trade plan ---------- */
async function buildPlan() {
  const tbody = $('#planTable tbody');
  tbody.innerHTML = `<tr><td colspan="9" class="empty"><span class="spinner"></span> Building…</td></tr>`;
  const contribution = parseFloat($('#planContribution').value) || 0;
  const mode = $('#planMode').value;
  try {
    const query = new URLSearchParams({ contribution: String(contribution) });
    if (mode) query.set('mode', mode);
    state.plan = await api(`/trade-plan?${query}`);
  } catch (err) {
    rowsOrEmpty(tbody, [], 9, err.message);
    toast(err.message, true);
    return;
  }
  renderPlan();
}

function renderPlan() {
  const plan = state.plan;
  if (!plan) return;
  const meta = plan.meta || {};

  const notes = [];
  notes.push(`<div class="banner ${meta.rebalance_due ? 'warn' : 'info'}">
    <span class="icon">${meta.rebalance_due ? '⟳' : 'i'}</span><div>
    <strong>${plan.mode === 'rebalance' ? 'Rebalance week' : 'Contribution only'}.</strong>
    ${esc(meta.rebalance_reason || '')}
    ${plan.mode === 'contribute'
      ? ' New money is deployed toward target; nothing is sold.'
      : ' The whole portfolio is traded toward target weights.'}</div></div>`);
  (plan.notes || []).forEach(n => notes.push(
    `<div class="banner"><span class="icon">·</span><div>${esc(n)}</div></div>`));
  notes.push(`<div class="banner"><span class="icon">✓</span><div>${esc(plan.disclaimer)}</div></div>`);
  $('#planMeta').innerHTML = notes.join('');

  $('#planKpis').innerHTML = [
    statCard('Portfolio before', money(plan.portfolio_value_before, 0)),
    statCard('To buy', money(plan.total_buys, 0), `${plan.trades.filter(t => ['BUY','NEW'].includes(t.action)).length} orders`, 'pos'),
    statCard('To sell', money(plan.total_sells, 0), `${plan.trades.filter(t => ['SELL','EXIT'].includes(t.action)).length} orders`, plan.total_sells > 0 ? 'neg' : ''),
    statCard('Portfolio after', money(plan.portfolio_value_after, 0), `contribution ${money(plan.contribution, 0)}`),
  ].join('');

  const rows = plan.trades.map(t => `<tr>
    <td><span class="pill ${esc(t.action.toLowerCase())}">${esc(t.action)}</span></td>
    <td class="ticker">${esc(t.ticker)}</td>
    <td>${sleevePill(t.sleeve)}</td>
    <td class="num">${t.action === 'HOLD' ? '—' : money(t.amount)}</td>
    <td class="num">${t.action === 'HOLD' ? '—' : num(t.shares, 4)}</td>
    <td class="num">${money(t.price)}</td>
    <td class="num">${pct(t.current_weight, 2)}</td>
    <td class="num">${pct(t.target_weight, 2)}</td>
    <td class="muted">${esc(t.reason)}</td>
  </tr>`);
  rowsOrEmpty($('#planTable tbody'), rows, 9, 'No trades proposed.');
  $('#applyPlan').disabled = !(plan.buys?.length || plan.trades.some(t => ['BUY','NEW','SELL','EXIT'].includes(t.action)));
}

function planCsv() {
  const lines = ['action,ticker,amount,shares,price,current_weight_pct,target_weight_pct,reason'];
  (state.plan?.trades || []).forEach(t => lines.push([
    t.action, t.ticker, t.amount, t.shares, t.price,
    (t.current_weight * 100).toFixed(4), (t.target_weight * 100).toFixed(4),
    `"${String(t.reason).replace(/"/g, '""')}"`,
  ].join(',')));
  return lines.join('\n');
}

async function applyPlan() {
  const plan = state.plan;
  if (!plan) return;
  const summary = `Record ${plan.buys.length} buy(s) and ${plan.sells.length} sell(s) in your holdings?\n\n`
    + 'This updates the holdings file to reflect trades you have made. It does NOT place any orders.';
  if (!confirm(summary)) return;
  try {
    const result = await api('/trade-plan/apply', {
      method: 'POST',
      body: JSON.stringify({
        contribution: plan.contribution,
        mode: plan.mode,
        confirm: true,
      }),
    });
    toast(`Recorded ${result.recorded} trade(s)`);
    await loadHoldings();
    await buildPlan();
  } catch (err) { toast(err.message, true); }
}

/* ---------- rankings ---------- */
async function loadRankings() {
  const tbody = $('#rankTable tbody');
  tbody.innerHTML = `<tr><td colspan="10" class="empty"><span class="spinner"></span> Loading…</td></tr>`;
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
    ? `${h.positions.length} positions · ${money(h.total_value, 0)} · updated ${h.updated_at || 'never'}`
    : 'no positions recorded';

  const rows = h.positions.map((p, i) => `<tr data-index="${i}">
    <td><input class="h-ticker" value="${esc(p.ticker)}" style="width:80px"></td>
    <td class="num"><input class="h-shares" type="number" step="any" min="0" value="${p.shares}" style="width:110px"></td>
    <td class="num"><input class="h-basis" type="number" step="any" min="0" value="${p.cost_basis}" style="width:110px"></td>
    <td class="num">${money(p.price)}</td>
    <td class="num">${money(p.value, 0)}</td>
    <td class="num">${pct(p.weight, 2)}</td>
    <td class="num ${p.unrealized > 0 ? '' : ''}">${money(p.unrealized, 0)}</td>
    <td><button class="h-remove" title="Remove">✕</button></td>
  </tr>`);
  rowsOrEmpty(tbody, rows, 8, 'No positions yet. Add one, or import a broker CSV.');
  if (h.unpriced?.length) {
    toast(`No price for ${h.unpriced.join(', ')} — check the ticker`, true);
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
    <td><button class="h-remove" title="Remove">✕</button></td>`;
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
        positions,
        cash: 0,
        last_rebalance: state.holdings?.last_rebalance || null,
      }),
    });
    toast(`Saved ${positions.length} position(s)`);
    await loadHoldings();
  } catch (err) { toast(err.message, true); }
}

async function importCsv(file) {
  const form = new FormData();
  form.append('file', file);
  try {
    const response = await fetch(`${API}/holdings/import`, { method: 'POST', body: form });
    const body = await response.json();
    if (!response.ok) throw new Error(body.detail || 'import failed');
    toast(`Imported ${body.imported} position(s)`);
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
    const thead = $('#runTable thead');
    thead.innerHTML = `<tr><th>Metric</th>${variants.map(v => `<th class="num">${esc(VARIANT_LABEL[v] || v)}</th>`).join('')}</tr>`;

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
            onerror="this.closest('img').style.display='none'">`).join('');
  } catch (err) { toast(err.message, true); }
}

/* ---------- jobs ---------- */
async function startJob(path, body) {
  try {
    const job = await api(path, { method: 'POST', body: JSON.stringify(body || {}) });
    toast(`Started ${job.name}`);
    state.activeJob = job.id;
    switchTab('jobs');
    pollJobs();
  } catch (err) { toast(err.message, true); }
}

async function pollJobs() {
  try {
    const data = await api('/jobs');
    $('#jobStatus').innerHTML = data.busy
      ? '<span class="spinner"></span> a job is running'
      : 'idle';
    $('#startUpdate').disabled = data.busy;
    $('#startBacktest').disabled = data.busy;

    $('#jobList').innerHTML = data.jobs.length ? data.jobs.map(j => `
      <div class="row" style="margin-bottom:.4rem">
        <button data-job="${esc(j.id)}" class="job-pick">${esc(j.name)}</button>
        <span class="pill ${j.status === 'failed' ? 'sell' : j.status === 'succeeded' ? 'buy' : 'hold'}">${esc(j.status)}</span>
        <span class="muted">${j.duration_seconds ? j.duration_seconds + 's' : ''}</span>
      </div>`).join('') : '<span class="muted">No jobs yet.</span>';

    if (!state.activeJob && data.jobs.length) state.activeJob = data.jobs[0].id;
    if (state.activeJob) {
      const job = await api(`/jobs/${state.activeJob}`);
      $('#jobLog').textContent = job.lines.length ? job.lines.join('\n') : '(no output yet)';
      $('#jobLog').scrollTop = $('#jobLog').scrollHeight;
      if (job.status === 'succeeded' && job.name === 'backtest') {
        await loadRuns();
      }
    }

    clearTimeout(state.jobPollTimer);
    if (data.busy) state.jobPollTimer = setTimeout(pollJobs, 2500);
  } catch (err) { /* the server may be restarting; retry on the next tick */ }
}

async function loadStatus() {
  try {
    state.status = await api('/status');
  } catch { return; }
  const s = state.status;
  $('#brandSub').textContent =
    `${s.provider} prices · ${s.fundamentals_provider} fundamentals · strategy v${s.strategy_version}`;
  $('#statusKpis').innerHTML = [
    statCard('Price provider', esc(s.provider), `fundamentals: ${esc(s.fundamentals_provider)}`),
    statCard('Cached series', String(s.cache_files), s.cache_updated ? `updated ${esc(s.cache_updated.slice(0, 16))}` : 'never'),
    statCard('Holdings', String(s.holdings.positions), s.holdings.last_rebalance ? `rebalanced ${esc(s.holdings.last_rebalance)}` : 'no rebalance recorded'),
    statCard('Latest run', s.latest_run ? esc(s.latest_run.run_id) : '—', s.latest_run ? `${esc(s.latest_run.start_date)} → ${esc(s.latest_run.end_date)}` : 'none stored'),
  ].join('');
  renderBanners();
}

async function loadReports() {
  try {
    const data = await api('/reports');
    const rows = data.files.map(f => `<tr>
      <td><a href="${API}/reports/${encodeURIComponent(f.name)}" target="_blank" rel="noopener">${esc(f.name)}</a></td>
      <td class="num">${(f.size / 1024).toFixed(0)} KB</td>
      <td class="muted">${esc(f.modified.replace('T', ' ').slice(0, 16))}</td>
    </tr>`);
    rowsOrEmpty($('#reportsTable tbody'), rows, 3, 'No report files yet.');
  } catch { /* non-fatal */ }
}

/* ---------- tabs and wiring ---------- */
function switchTab(name) {
  $$('.tab').forEach(t => t.setAttribute('aria-selected', String(t.dataset.panel === name)));
  $$('.panel').forEach(p => { p.hidden = p.id !== `panel-${name}`; });
  location.hash = name;
  if (name === 'holdings') loadHoldings();
  if (name === 'backtest') loadRuns();
  if (name === 'jobs') { pollJobs(); loadReports(); }
  if (name === 'rankings' && $('#rankTable tbody').textContent.includes('Press Load')) loadRankings();
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

  $('#refreshWeekly').addEventListener('click', () => loadWeekly(true));
  $('#dashContribution').addEventListener('input', renderBasketRows);
  $('#copyBasket').addEventListener('click', () => copyText(basketCsv(), 'Basket'));

  $('#runPlan').addEventListener('click', buildPlan);
  $('#copyPlan').addEventListener('click', () => copyText(planCsv(), 'Trade plan'));
  $('#applyPlan').addEventListener('click', applyPlan);

  $('#loadRankings').addEventListener('click', loadRankings);
  $('#rankSleeve').addEventListener('change', loadRankings);

  $('#addPosition').addEventListener('click', addPositionRow);
  $('#saveHoldings').addEventListener('click', saveHoldings);
  $('#holdingsTable').addEventListener('click', e => {
    if (e.target.classList.contains('h-remove')) e.target.closest('tr').remove();
  });
  $('#importCsv').addEventListener('change', e => {
    if (e.target.files?.[0]) importCsv(e.target.files[0]);
    e.target.value = '';
  });

  $('#loadRun').addEventListener('click', loadRun);
  $('#runSelect').addEventListener('change', loadRun);
  $('#startBacktest').addEventListener('click', () =>
    startJob('/jobs/backtest', { weeks: parseInt($('#btWeeks').value, 10) || 360 }));

  $('#startUpdate').addEventListener('click', () => startJob('/jobs/update-data', { force: false }));
  $('#refreshJobs').addEventListener('click', pollJobs);
  $('#jobList').addEventListener('click', e => {
    const button = e.target.closest('.job-pick');
    if (button) { state.activeJob = button.dataset.job; pollJobs(); }
  });

  switchTab((location.hash || '#dashboard').slice(1));
  loadStatus();
  loadWeekly();
  pollJobs();
}

document.addEventListener('DOMContentLoaded', init);
