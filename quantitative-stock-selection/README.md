# Quantitative Stock Selection & DCA Backtesting

A research and decision-support system that scores US equities on a deterministic
factor model, picks the top names per GICS sector plus a cross-sector high-growth
sleeve, simulates investing $1,000 every week, and compares the result against the
same $1,000/week put into IVV.

It answers one question:

> If I had invested $1,000 every week using a systematic stock-selection strategy,
> would that have historically beaten investing the same $1,000 every week in IVV?

**This software places no trades and gives no investment advice.** It is a
backtesting and reporting tool. Historical results do not establish that a strategy
will perform similarly in future. See [Honest limitations](#honest-limitations)
before you read any number it produces.

---

## Quick start

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env               # then fill in SEC_USER_AGENT (see below)
```

### Weekly use

`backtest.end_date` is `null` by default, meaning "the latest session available",
so a Sunday-night run picks up Friday's close without anyone editing config:

```bash
python main.py weekly-report        # writes reports/weekly_report_<date>.md and .html
```

The report ends with a **Basket** table: every position with its weight as a share
of the contribution, so the same basket works whether you put in $500 or $2,000
that week. A ticker selected by both sleeves appears once carrying the sum of its
weights. Run it after the market closes — the CLI warns if the last bar is from a
session that may still be open, or if the data is more than a week stale.

Note what the report is and is not: it is the **target portfolio** for new money,
not a trade list. It has no knowledge of what you already hold, so it cannot tell
you what to sell. See [Honest limitations](#honest-limitations).

### Web UI

```bash
python main.py serve            # http://127.0.0.1:8000
```

A dashboard over the same service layer the CLI uses: this week's basket, a trade
plan diffed against your positions, rankings, holdings editing, backtest results
with charts, and a job runner for the long operations. The API is documented at
`/docs`. It binds to loopback and has **no authentication** — do not expose it to a
network without putting auth in front of it. There is no trading endpoint.

### Tracking positions

The weekly report can only say SELL if it knows what you own.

```bash
python main.py holdings set --ticker AAPL --shares 25 --cost-basis 5200
python main.py holdings import --file broker_export.csv
python main.py trade-plan --contribution 1000
```

`trade-plan` diffs your holdings against the target basket. Between rebalances it
proposes buys only and flags holdings that have dropped out of the selection as
DRIFT; on a rebalance week it proposes the full set of buys and sells to reach
target. Every line is a proposal — nothing is ordered.

### First run

Run the whole pipeline offline first, on generated data, to confirm the install:

```bash
python main.py backtest --provider synthetic --weeks 100
```

That writes `reports/backtest_report.html`, ten charts, an Excel workbook and the
CSV set — with a loud banner saying the data is synthetic. Then run it for real:

```bash
python main.py update-data              # download and cache prices + filings
python main.py validate-data            # data-quality checks
python main.py backtest                 # the 360-week backtest
python main.py weekly-report            # this week's recommendation
```

A full 360-week run over the whole S&P 500 universe takes roughly 10–20 minutes,
most of it in the first `update-data`. Subsequent runs are served from the cache.

---

## What it does

```
universe  →  factors  →  normalize  →  score  →  rank  →  allocate  →  simulate  →  report
```

**Universe.** S&P 500 membership reconstructed *point-in-time*. The Wikipedia
constituents article dropped its "Selected changes" table in mid-2026, so the
provider walks the article's revision history for the newest revision that still
carries it (currently 2026-07-21, 406 change rows), reconstructs membership
backwards from that revision, then reconciles forward to today. The result knows
that TWTR was in the index in 2020 and gone by 2024 — 862 tickers were members at
some point in the window, against 503 today.

**Factors.** Momentum and risk from prices; growth, quality and valuation from SEC
EDGAR XBRL filings. A factor that cannot be computed is `None`, never `0`.

**Normalization.** Winsorize universe-wide, then rank into a 0–100 percentile
*within GICS sector*, so a software P/E is ranked against software rather than
against utilities. Sectors below `min_group_size` fall back to a universe-wide rank.

**Scoring.** `30% growth + 30% momentum + 20% quality + 10% valuation + 10% risk`,
all configurable and validated to sum to 1.0. Missing categories have their weight
**redistributed**, not filled with a neutral 50 — and if a whole category is missing
for most of the universe, the run says so loudly rather than quietly scoring a
different model.

**Selection.** Top 3 per sector (33 names) plus the top 10 on a growth-tilted score.

**Simulation.** $500 IVV / $300 sector leaders / $200 high growth each week,
fractional shares, dividends credited on the ex-date, 5 bps slippage, configurable
commission, quarterly/monthly rebalancing.

**Reporting.** Markdown + HTML reports, 10 charts, an 11-sheet Excel workbook, the
CSV set, and a provenance JSON that pins the exact inputs.

---

## How look-ahead bias is prevented

This is the part that decides whether any of the output means anything, so it is
enforced structurally rather than by convention.

**Two objects, two jobs.** Anything that *decides* — factors, scoring, ranking —
receives a `PointInTimeView` pinned to one date. It physically cannot return a bar
dated after that date, or a filing whose `data_available_date` is later. Anything
that *executes* uses `MarketData` with an explicit trade date. Trading after the
decision is legitimate; the two roles live in separate objects so the distinction
cannot blur by accident.

**Filing date, not period end.** Every fundamental record carries three dates:

```
period_end_date      the fiscal quarter the number describes
filing_date          the day it reached the SEC
data_available_date  the first day a backtest may use it
```

Only the third is ever filtered on. A quarter ending 30 June that was filed on
15 July is invisible to a 30 June decision.

**The fill is always after the signal.** `ContributionEvent` raises at construction
if `execution_date <= decision_date`. The default convention decides on Friday's
close and fills at Monday's open. The convention the requirements explicitly
forbid — signal from Friday's close, fill at Friday's open — is not representable.

**It is tested, not asserted.** `tests/test_lookahead.py` injects a spectacular
quarter filed two weeks *after* a decision date, re-runs the selection, and asserts
the ranking is byte-for-byte identical. A paired control test injects the same
filing before a decision date and asserts the ranking *does* change — otherwise the
first test would pass trivially.

```bash
pytest tests/test_lookahead.py -v
```

---

## Commands

| Command | What it does |
|---|---|
| `update-data` | Download and cache prices, filings and universe; write to SQLite |
| `validate-data` | Run the data-quality checks; `--strict` exits non-zero on critical issues |
| `data-quality` | The data-quality dashboard, written to CSV |
| `rank` | Rank the universe as of a date (`--as-of`, `--explain`) |
| `weekly-report` | The weekly decision report in Markdown and HTML |
| `backtest` | The historical backtest plus the full report set |
| `export` | Re-export a stored run from SQLite |
| `runs` | List stored backtest runs |
| `schedule` | Print the contribution schedule, to audit decision-vs-execution dates |

Useful flags:

```bash
python main.py backtest --start 2019-10-18 --end 2026-09-25
python main.py backtest --weeks 360 --variants ivv combined
python main.py backtest --weights growth=0.4,momentum=0.3,quality=0.2,valuation=0.05,risk=0.05
python main.py backtest --provider synthetic --max-tickers 60 --no-excel
python main.py rank --as-of 2024-03-15 --explain
```

---

## Configuration

Everything lives in `config/`, is validated on load, and fails loudly rather than
falling back to a silent default.

- **`settings.yaml`** — contributions, allocations, sleeve sizes, rebalance
  frequency, backtest window, execution convention, costs, limits, risk controls,
  eligibility, data providers, logging.
- **`scoring.yaml`** — category weights, per-factor weights within each category,
  normalization method, and the list of factors where lower is better.
- **`sectors.yaml`** — the GICS 11 and the provider aliases that map onto them.

Weights must sum to 1.0; the loader raises `ConfigError` if they do not. The
strategy version in `settings.yaml` is recorded with every run — bump it whenever
the economic meaning of the model changes, so results stay attributable.

### Data providers

| Role | Default | Alternatives |
|---|---|---|
| Prices | `yfinance` | `alphavantage` (needs `ALPHAVANTAGE_API_KEY`), `synthetic` |
| Fundamentals | `sec` (EDGAR XBRL) | `yfinance`, `synthetic`, `none` |
| Universe | `wikipedia` | `static`, `custom`, `synthetic` |

`yfinance` is the price default because it needs no key and makes the tool work
out of the box. It is one interchangeable implementation of the `PriceProvider`
protocol, not a hard dependency of the architecture.

**SEC EDGAR requires a contact address.** The SEC refuses requests without a
descriptive `User-Agent`, so set it in `.env`:

```
SEC_USER_AGENT=Your Name your.email@example.com
```

Without it, fundamentals fall back to whatever you configure instead — and see the
warning below about what that costs you.

### Secrets

API keys go in `.env`, which is git-ignored. `.env.example` documents the
variables. No key is ever read from a config file or hard-coded.

---

## Honest limitations

Read these before you read a return number. They are also printed in section 17 of
every backtest report, generated from the actual run rather than boilerplate.

**Survivorship bias is detected, not merely reconstructed.** Point-in-time
membership fixes half the problem. The other half is whether the price provider can
still serve the companies that left the index — and free providers usually cannot.
In a 60-ticker sample, 14 historical S&P 500 members (23%) had no price history
from Yahoo: ABK, ABMD, ACAS, ACE, ADS, AET, AGN, AKS, ALTR, ALXN, ANDV, ANR, ANSS,
ABS — all acquired or delisted, i.e. disproportionately the ones that did badly.
The pipeline measures this and **withdraws the survivorship-free claim** when more
than 2% of historical members cannot be priced, stating the number and the reason
in the report. To genuinely remove the bias you need a provider that serves
delisted securities (CRSP, Norgate, Sharadar).

**Dual share classes are counted twice.** GOOG and GOOGL are separate index
members, so both can be selected and the basket then holds roughly double the
intended weight in one company. The same applies to FOX/FOXA and NWS/NWSA. The
model has no concept of a corporate parent.

**Fundamentals history decides whether the model you configured is the model that
runs.** Yahoo serves roughly five quarters of statements. Year-over-year TTM growth
needs eight. With `--fundamentals yfinance`, the growth category — 30% of the
composite — is unscoreable for the entire universe, its weight is redistributed,
and the 5-factor model silently becomes a 4-factor one. The system now detects this
and prints:

> the 'growth' category could be scored for only 0% of the universe, so its 30%
> weight is being redistributed across the other categories. The composite is not
> the model configured in scoring.yaml.

Use SEC EDGAR, which carries years of filings, unless you have a reason not to.

**Sector classification is current-only.** Wikipedia publishes no GICS history, so a
company that changed sector is ranked against today's peers throughout. The
`SectorMap` records this as `point_in_time=False` and the reports say so. The data
model is date-aware and ready for a historical source.

**Forward-looking factors are absent by choice.** Forward P/E, analyst estimates and
revisions are reported as `None`. No point-in-time source is wired up, and
back-filling today's estimates onto a 2020 decision is precisely the leak this
system exists to prevent.

**Costs are modeled simply.** 5 bps slippage and a configurable per-trade
commission. Real spreads, market impact, partial fills and borrow costs are not
modeled. The sector sleeve places ~33 small trades a week; a broker without
free fractional trading would change the result materially.

**Dividends are reinvested at weekly granularity.** Credited as cash on the ex-date
and redeployed at the next contribution, not reinvested intraday.

**Taxes are not modeled.** All figures are pre-tax. The sector sleeve turns over
roughly 100% a year in backtest and the growth sleeve roughly 350%; in a taxable
account most of that is short-term gains taxed as ordinary income.

**Position tracking is a manual record, not a broker connection.** `holdings.json`
is whatever you last told it. It does not reconcile with your broker, model
corporate actions on your positions, or track tax lots. If you trade and forget to
record it, the next trade plan is wrong.

**SEC company facts are large.** A full 503-ticker fundamentals cache is about
2.8 GB under `data/raw/sec/`. It is git-ignored, and re-running is served from
disk.

**Drawdowns are measured on the time-weighted index, not the dollar balance.** In a
DCA strategy the balance keeps climbing on new contributions, so a value-based
drawdown badly understates losses — in March 2020 a weekly contributor's balance
could look nearly flat while the holdings fell a third. Both figures are available;
the headline uses the honest one.

**Two return measures, always both.** Time-weighted return removes contribution
timing and is what you compare to an index. Money-weighted return (XIRR) keeps it
and is what you actually earned. Quoting only one flatters or penalizes the strategy
depending on which way the market trended, so every report shows both.

**Overfitting.** The shipped weights were not fitted to this data. If you tune them
and re-run, read section 16 of the report — the in-sample / validation /
out-of-sample split — and not the headline.

---

## Project layout

```
config/            settings.yaml, scoring.yaml, sectors.yaml
src/quant/
  config.py        typed, validated configuration
  database.py      SQLite schema and helpers
  pipeline.py      provider wiring, loading, persistence
  data/            providers, cache, point-in-time store, validator, universe
  factors/         momentum, growth, quality, valuation, risk + TTM panel
  ranking/         normalization, composite scoring, sector ranker
  portfolio/       accounting, DCA schedule, rebalancing
  backtest/        engine, metrics, drawdown, integrity + provenance
  reports/         charts, Excel, CSV, weekly and backtest reports
tests/             193 tests, no network required
main.py            CLI
```

### Where the interesting decisions live

| Question | File |
|---|---|
| How is look-ahead prevented? | `src/quant/data/store.py` |
| How are the three fundamental dates used? | `src/quant/data/types.py`, `src/quant/data/sec.py` |
| How is historical index membership rebuilt? | `src/quant/data/universe.py` |
| Why is a missing factor not zero? | `src/quant/ranking/scoring.py` |
| What order do contributions, dividends and trades happen in? | `src/quant/backtest/engine.py` |
| How is turnover / TWR / XIRR defined? | `src/quant/backtest/metrics.py` |

---

## Output

`reports/` receives:

```
backtest_report.md / .html      the full historical report
weekly_report_YYYY-MM-DD.md/.html
backtest_summary.csv / .xlsx    11-sheet workbook
portfolio_history.csv           weekly values per variant
transactions.csv                every fill, with commission and slippage
stock_rankings.csv              weekly rankings, both sleeves
factor_scores.csv               raw factor values
drawdowns.csv                   every episode with peak/trough/recovery
charts/*.png                    10 charts
provenance_<run-id>.json        config, versions, data hash
```

Charts use one validated palette throughout. Every line series is directly labeled,
so identity never rests on color alone; the categorical slots were checked against
the light surface for lightness band, chroma, and colorblind separation.

---

## Reproducibility

Every run gets an ID (`BACKTEST-20260928-001`) and saves its configuration, the
strategy version, software and Python versions, platform, git commit, and a
`data_snapshot_hash` fingerprinting the loaded bars. Two runs with the same hash and
the same config produce identical numbers — asserted by
`tests/test_lookahead.py::test_backtest_is_reproducible`.

```bash
python main.py runs                        # list stored runs
python main.py export --run-id BACKTEST-20260928-001
```

---

## Testing

```bash
pytest                       # 193 tests, ~80 seconds, no network
pytest tests/test_lookahead.py -v          # the integrity suite
```

All tests run on the seeded synthetic provider, so a failure means a code change
rather than a market move or a provider outage.

---

## Measured results

A 360-week backtest to 2026-09-28, full S&P 500 universe, SEC fundamentals,
5 bps slippage (`BACKTEST-REAL-002`):

| | IVV only | Sector leaders | High growth | Combined |
|---|---:|---:|---:|---:|
| Ending balance | **$647,112** | $163,223 | $111,444 | $598,223 |
| CAGR (time-weighted) | **15.86%** | 10.75% | 12.38% | 13.75% |
| XIRR | **16.93%** | 11.93% | 12.62% | 14.66% |
| Max drawdown | **-33.59%** | -38.79% | -31.10% | -34.63% |
| Sharpe | **0.873** | 0.626 | 0.614 | 0.774 |
| Annual turnover | **0%** | 196% | 452% | 145% |
| Weeks beating IVV | — | 49.6% | 50.7% | 47.9% |

**The stock selection lost to the benchmark.** The combined portfolio ended
$48,889 behind buying IVV alone with the same contributions, with a slightly deeper
drawdown, a worse Sharpe ratio, and 145% annual turnover. It underperformed in all
three sub-periods (-9.3%, -3.3%, -5.1%) and in five of seven calendar years.

Two caveats both point the same way: 24% of historical index members could not be
priced, which removes failed companies and therefore *flatters* the strategy; and
taxes are not modeled, which would penalize a 145%-turnover strategy far more than
a 0%-turnover one. The real gap is probably wider than the table shows.

Reproduce with `python main.py backtest --weeks 360`.

## What this deliberately does not do

Version 1 does not place trades, paper-trade, connect to a broker, use an LLM to
pick stocks, or override the quantitative model with judgment. Section 47 of
`REQUIREMENTS.MD` sketches the path to broker integration; it runs through a human
approval step and is out of scope here.

---

## License

MIT — see `LICENSE`. The license carries an additional notice restating that this
is research software, not investment advice.
