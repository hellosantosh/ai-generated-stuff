# REQUIREMENTS.MD
# Quantitative Stock Selection & DCA Backtesting Application

## 1. Document Purpose

This document defines the requirements for a Python application that automatically evaluates publicly traded U.S. equities, ranks companies within sectors, selects stocks using a deterministic quantitative scoring model, backtests the strategy against IVV/S&P 500, and produces a weekly portfolio recommendation.

The application is intended to be used as a research and decision-support system. It must NOT place live trades in the initial version.

The application should be designed so that a future version can support paper trading and, only after explicit user approval, broker integration.

The primary objective is to answer:

> "If I invested $1,000 every week using a systematic stock-selection strategy, would the strategy have historically outperformed investing the same $1,000 every week in IVV?"

The system must avoid look-ahead bias, survivorship bias where practical, and accidental use of information that was not available at the historical decision date.

---

# 2. Primary User Goals

The application shall allow the user to:

1. Download and maintain historical market and fundamental data.
2. Define a stock universe, initially the S&P 500.
3. Classify stocks by sector.
4. Calculate quantitative factors for every eligible stock.
5. Generate a composite score.
6. Select the top three stocks in each sector.
7. Optionally select a separate portfolio of high-growth/momentum stocks.
8. Simulate investing $1,000 every week.
9. Compare the resulting portfolio with IVV.
10. Analyze returns, drawdowns, volatility, turnover, and other risk metrics.
11. Run the strategy historically for approximately the most recent 360 weeks.
12. Generate a weekly report showing current selections and changes from the previous week.
13. Export all results to CSV and Excel.
14. Produce charts suitable for human review.
15. Allow all important strategy parameters to be configured without modifying source code.

---

# 3. Non-Goals for Version 1

Version 1 shall NOT:

- Automatically place real-money trades.
- Provide personalized financial advice.
- Guarantee investment returns.
- Predict future stock prices.
- Use an LLM as the primary stock-selection mechanism.
- Automatically override the quantitative model based on subjective opinions.
- Use future information in historical backtests.
- Select historical winners retrospectively and represent them as an investable strategy.

Future broker integration may be considered after the research/backtesting system is validated.

---

# 4. Core Investment Strategy

The initial portfolio design is:

| Component | Weekly Allocation |
|---|---:|
| IVV core | $500 |
| Sector-leader portfolio | $300 |
| High-growth/momentum portfolio | $200 |
| Total | $1,000 |

The application must allow these allocations to be changed through configuration.

The user must also be able to run alternative experiments, including:

- 100% IVV
- 100% sector leaders
- 100% momentum
- 100% growth
- custom percentage allocations

The default number of weekly contributions for the historical experiment is:

**360 weeks**

Total contributions:

**360 × $1,000 = $360,000**

---

# 5. Investment Timing

The default contribution frequency is weekly.

Default contribution day:

**Friday**

Default execution price:

**Closing adjusted price or closing market price, depending on the backtest methodology.**

The application must make the execution convention configurable.

Recommended default:

- Generate selections after the Friday market close.
- Execute the modeled contribution using the following trading day's opening price, OR
- Generate selections using data available before the selected execution point.

The implementation must document exactly which information was available at the decision time.

The system must never use Friday's closing price to calculate a signal and then pretend that the portfolio purchased at Friday's opening price.

---

# 6. Data Sources

The architecture must support multiple data providers.

## 6.1 Primary Market Data

Preferred initial provider:

**Alpha Vantage**

Use it for:

- Daily prices
- Weekly adjusted prices
- Volume
- Dividends
- Stock splits
- Historical market data
- Technical indicators where useful

The API key must be supplied through an environment variable.

Example:

```text
ALPHAVANTAGE_API_KEY
```

Never hard-code API keys in source code.

The application must cache downloaded data locally.

---

## 6.2 SEC Data

Use SEC EDGAR/company XBRL data where practical for fundamental information.

Potential metrics:

- Revenue
- Net income
- EPS where available
- Operating income
- Gross profit
- Operating cash flow
- Capital expenditures
- Free cash flow
- Assets
- Liabilities
- Equity
- Debt
- Shares outstanding

The system should prefer primary SEC data over scraped financial websites for fundamental calculations when practical.

SEC request requirements, including appropriate User-Agent identification and rate limits, must be respected.

---

## 6.3 Benchmark Data

The application must support:

- IVV
- S&P 500 index
- Optional SPY
- Optional Nasdaq-100/QQQ benchmark

IVV is the default investment benchmark.

The application should distinguish:

- Price return
- Total return
- Adjusted-price return

Dividends should be included in total-return calculations where the source data supports them.

---

# 7. Stock Universe

Initial universe:

**S&P 500 constituents**

The application must maintain historical constituent membership where possible.

This is important because using today's S&P 500 constituents for a 2019 backtest introduces survivorship bias.

The system should support:

```text
Historical universe:
Date -> list of stocks that were actually members of the S&P 500 at that date
```

If historical membership data is unavailable, the application must clearly label the backtest as survivorship-biased.

The architecture should make it possible to add:

- S&P 500
- S&P 400
- S&P 600
- Russell 1000
- Nasdaq-100
- Custom ticker list

---

# 8. Sector Classification

Use the GICS 11-sector framework initially:

1. Information Technology
2. Health Care
3. Financials
4. Consumer Discretionary
5. Communication Services
6. Industrials
7. Consumer Staples
8. Energy
9. Utilities
10. Real Estate
11. Materials

Each stock must have a sector identifier.

The sector classification must be date-aware if historical sector classifications are available.

The application must not silently use today's classification for all historical periods.

---

# 9. Fundamental Factors

The application should calculate the following where data is available.

## Growth

- Revenue growth YoY
- Revenue CAGR, 3 years
- Revenue CAGR, 5 years
- EPS growth YoY
- EPS CAGR, 3 years
- EPS CAGR, 5 years
- Free cash flow growth
- Earnings acceleration
- Revenue acceleration

## Profitability

- Gross margin
- Operating margin
- Net margin
- ROE
- ROIC
- FCF margin

## Balance Sheet

- Debt/equity
- Net debt/EBITDA
- Current ratio
- Interest coverage
- Cash/debt ratio

## Cash Flow

- Operating cash flow
- Free cash flow
- FCF margin
- FCF growth

Fundamental metrics must be calculated using information that was publicly available as of the backtest decision date.

The system must maintain a `data_as_of` or `filing_date` field.

---

# 10. Technical and Momentum Factors

Calculate:

- 1-month return
- 3-month return
- 6-month return
- 12-month return
- 18-month return
- 24-month return
- 52-week high distance
- 52-week low distance
- 50-day moving average
- 200-day moving average
- Price / 50-day MA
- Price / 200-day MA
- 50-day MA / 200-day MA
- Relative strength versus S&P 500
- Relative strength versus sector
- Volatility
- Maximum drawdown
- Average true range if daily data supports it

The default momentum score should emphasize 12-month, 6-month, and 3-month momentum.

---

# 11. Valuation Factors

Where data is available:

- Forward P/E
- Trailing P/E
- PEG
- Price/Sales
- Price/Free Cash Flow
- EV/EBITDA
- EV/Sales
- Free cash flow yield
- Earnings yield

Valuation should not automatically penalize high-growth companies simply because they have high P/E ratios.

The scoring system should normalize valuation metrics within sector.

For example, a technology company's P/E should preferably be compared with other technology companies rather than directly with utilities.

---

# 12. Risk Factors

Calculate:

- Annualized volatility
- Beta versus S&P 500
- Maximum drawdown
- Downside volatility
- Sharpe ratio where sufficient history exists
- Sortino ratio where practical
- 52-week drawdown

Risk should be incorporated as a penalty rather than used as an absolute exclusion unless a configurable risk rule is triggered.

---

# 13. Composite Scoring Model

Default score:

```text
Total Score =
    30% Growth
  + 30% Momentum
  + 20% Quality
  + 10% Valuation
  + 10% Risk
```

The application must allow the weights to be changed.

Example configuration:

```yaml
weights:
  growth: 0.30
  momentum: 0.30
  quality: 0.20
  valuation: 0.10
  risk: 0.10
```

Weights must sum to 1.0.

The system must validate this.

---

# 14. Factor Normalization

Raw financial metrics must not be combined directly.

The application should normalize factors using one of:

- Percentile ranking
- Z-score
- Sector-relative percentile
- Winsorized z-score

Preferred initial approach:

**Sector-relative percentile ranking**

Example:

```text
Revenue CAGR:
stock = 35%

Within technology:
percentile = 94

Normalized score = 94
```

Extreme outliers should be winsorized before normalization.

---

# 15. Sector Selection Strategy

For each sector:

1. Filter eligible stocks.
2. Calculate factor scores.
3. Calculate composite score.
4. Rank stocks descending.
5. Select top three.

Example:

```text
Technology

1. NVDA
2. AVGO
3. MSFT
```

The system must output:

- ticker
- company name
- sector
- total score
- individual factor scores
- rank
- previous week's rank
- rank change

---

# 16. Eligibility Rules

A stock should be excluded if:

- It lacks sufficient price history.
- It lacks required fundamental information for the selected model.
- It is delisted before the decision date.
- It has unusable/corrupt price data.
- It is suspended or otherwise untradeable according to the configured rules.

Minimum recommended price history:

**52 weeks**

For 3-year and 5-year fundamental growth calculations, missing historical data should be handled explicitly rather than silently converted to zero.

---

# 17. High-Growth/Momentum Portfolio

In addition to the sector-leader portfolio, create a separate cross-sector ranking.

Default selection:

**Top 10 stocks across all sectors**

based on a configurable high-growth score.

Suggested high-growth score:

```text
35% Growth
35% Momentum
15% Quality
10% Relative Strength
5% Risk
```

The user should be able to change the number of holdings.

Default allocation:

**$200/week**

Equal-weight the selected holdings unless another weighting method is configured.

---

# 18. Portfolio Weighting

Default:

**Equal weight**

For 3 stocks per sector:

```text
$300 sector allocation
÷ 11 sectors
÷ 3 stocks
= approximately $9.09 per stock per week
```

The application must support:

- Equal weighting
- Score weighting
- Market-cap weighting
- Volatility-adjusted weighting
- Custom weighting

---

# 19. IVV Benchmark

The system must maintain an equivalent $1,000/week IVV portfolio.

The benchmark must use:

- identical contribution dates
- identical contribution amounts
- identical cash-flow assumptions
- dividend reinvestment
- fractional shares where supported by the model

This creates a direct apples-to-apples comparison.

---

# 20. Dollar-Cost Averaging Engine

The DCA engine must support:

```text
weekly_contribution = $1,000
number_of_weeks = 360
```

For each contribution:

```text
shares_purchased =
    contribution_amount / execution_price
```

Portfolio value:

```text
portfolio_value =
    sum(shares_owned × current_price)
    + cash_balance
```

Dividends must be reinvested in total-return mode.

The system must support fractional shares.

---

# 21. Rebalancing

Default recommendation:

- Generate rankings weekly.
- Review rankings weekly.
- Do not necessarily trade every week.
- Rebalance the portfolio monthly or quarterly.

This must be configurable.

Supported rebalance frequencies:

- Weekly
- Monthly
- Quarterly
- Semiannual
- Annual
- Threshold-based

Threshold-based example:

```text
Rebalance if:
absolute weight drift > 25%
OR
stock drops below rank 10
OR
stock violates risk rule
```

---

# 22. Backtesting Requirements

The backtester must be strictly point-in-time.

For each historical decision date, the system must only use:

- Price information available before the trade
- Financial information publicly available before the trade
- Sector classification available before the trade
- Universe membership available before the trade

It must NOT use:

- Future earnings
- Future prices
- Future index membership
- Future stock splits
- Future sector classifications
- Future analyst estimates
- Future company filings

The backtester should include an optional "strict mode" that fails the run if point-in-time requirements cannot be verified.

---

# 23. Historical Test Period

Default:

**360 weekly observations**

Approximately seven years.

The application must also support arbitrary dates:

```yaml
backtest:
  start_date: 2019-10-18
  end_date: 2026-09-25
  frequency: weekly
```

The user must be able to specify:

- Start date
- End date
- Contribution amount
- Frequency
- Rebalance frequency
- Strategy
- Benchmark

---

# 24. Performance Metrics

For every portfolio calculate:

### Return

- Total contributions
- Ending value
- Total gain
- Total return on contributions
- Time-weighted return
- Money-weighted return / XIRR
- CAGR

### Risk

- Maximum drawdown
- Maximum drawdown duration
- Volatility
- Downside volatility
- Sharpe ratio
- Sortino ratio
- Beta
- Tracking error

### Trading

- Number of trades
- Turnover
- Number of holdings
- Average holding period
- Entry/exit count

### Benchmark Comparison

- Excess return versus IVV
- Rolling 12-month alpha
- Rolling 36-month alpha
- Percentage of weeks outperforming IVV
- Percentage of months outperforming IVV

---

# 25. Drawdown Analysis

The application must identify:

- Largest drawdown
- Date drawdown began
- Trough date
- Recovery date
- Duration to trough
- Duration to recovery

Special periods must be reported:

- 2020 COVID crash
- 2022 bear market
- 2025 correction
- Any future bear market automatically detected

Example output:

```text
Maximum Drawdown
----------------
Peak:       2021-12-31
Trough:     2022-10-14
Drawdown:   -31.4%
Recovery:   2023-08-25
Recovery:   315 days
```

---

# 26. Required Reports

## Weekly Report

The weekly report must contain:

### Market Summary

- S&P 500 return
- IVV return
- Market volatility
- Current drawdown

### Sector Rankings

For every sector:

```text
Rank | Ticker | Company | Score | Previous Rank | Change
```

### Recommended Portfolio

```text
Ticker
Sector
Score
Allocation
Reason for selection
Previous week
Action
```

Actions:

- BUY
- HOLD
- SELL
- NEW
- EXIT
- NO CHANGE

The action is a model output, not a trade instruction.

---

# 27. Historical Backtest Report

Generate:

1. Executive summary
2. Portfolio comparison table
3. Cumulative portfolio chart
4. Drawdown chart
5. Rolling 12-month returns
6. Rolling 36-month returns
7. Year-by-year performance
8. Monthly returns
9. Sector contribution
10. Individual stock contribution
11. Turnover
12. Risk metrics
13. IVV comparison
14. Worst periods
15. Best periods

---

# 28. Required Comparison Table

At minimum:

| Metric | IVV | Sector Leaders | High Growth | Combined |
|---|---:|---:|---:|---:|
| Weekly contribution | $1,000 | $300 | $200 | $1,000 |
| Total contributions | | | | |
| Ending balance | | | | |
| Gain | | | | |
| CAGR | | | | |
| Maximum drawdown | | | | |
| Volatility | | | | |
| Sharpe | | | | |
| Sortino | | | | |
| Turnover | | | | |

---

# 29. Data Storage

Use a local database.

Preferred:

**SQLite**

Potential future option:

**PostgreSQL**

Suggested tables:

```text
stocks
sectors
historical_prices
fundamentals
financial_statements
corporate_actions
universe_membership
factor_scores
rankings
portfolio_positions
portfolio_transactions
portfolio_values
backtest_runs
backtest_metrics
```

Every data record should include an appropriate timestamp/date.

For fundamental data, distinguish:

```text
period_end_date
filing_date
data_available_date
```

This is essential for point-in-time backtesting.

---

# 30. Caching

The application must cache API results.

Requirements:

- Never download the same historical data repeatedly unless forced.
- Support incremental updates.
- Store raw responses when practical.
- Record provider and retrieval timestamp.
- Detect missing periods.
- Detect duplicate records.

Example:

```text
data/raw/alphavantage/
data/raw/sec/
data/processed/
data/cache/
```

---

# 31. API Reliability

The application must handle:

- API rate limits
- HTTP errors
- timeouts
- malformed responses
- missing data
- temporary provider outages
- provider schema changes

Implement:

- retries
- exponential backoff
- logging
- validation
- graceful failure

The application must never silently continue using stale data without reporting it.

---

# 32. Configuration

All important parameters must be configurable in YAML/TOML.

Example:

```yaml
strategy:
  weekly_contribution: 1000
  ivv_allocation: 0.50
  sector_allocation: 0.30
  growth_allocation: 0.20

sector_strategy:
  stocks_per_sector: 3
  rebalance_frequency: quarterly

growth_strategy:
  number_of_stocks: 10
  rebalance_frequency: monthly

backtest:
  start_date: 2019-10-18
  end_date: 2026-09-25
  frequency: weekly

scoring:
  growth: 0.30
  momentum: 0.30
  quality: 0.20
  valuation: 0.10
  risk: 0.10
```

---

# 33. Command-Line Interface

Provide a CLI.

Examples:

```bash
python main.py update-data
```

```bash
python main.py rank
```

```bash
python main.py weekly-report
```

```bash
python main.py backtest
```

```bash
python main.py backtest --start 2019-10-18 --end 2026-09-25
```

```bash
python main.py export
```

```bash
python main.py validate-data
```

---

# 34. Output Files

The application should generate:

```text
reports/
    weekly_report_YYYY-MM-DD.md
    weekly_report_YYYY-MM-DD.html
    backtest_summary.csv
    backtest_summary.xlsx
    portfolio_history.csv
    transactions.csv
    stock_rankings.csv
    factor_scores.csv
    drawdowns.csv
```

---

# 35. Excel Output

The Excel workbook should contain sheets:

1. Summary
2. Portfolio
3. IVV Benchmark
4. Transactions
5. Stock Rankings
6. Factor Scores
7. Sector Rankings
8. Drawdowns
9. Monthly Returns
10. Annual Returns
11. Configuration

---

# 36. Visualization Requirements

Generate:

1. Portfolio value over time
2. Portfolio versus IVV
3. Drawdown over time
4. Rolling 12-month return
5. Rolling 36-month return
6. Sector contribution
7. Top winners
8. Top losers
9. Portfolio allocation
10. Turnover

Use matplotlib initially.

Charts must be saved as PNG files.

---

# 37. Testing

The application must have automated tests.

Minimum tests:

### Data tests

- Price data has no duplicate dates.
- Prices are positive.
- Dates are ordered.
- Missing data is detected.
- Corporate actions are handled.

### Financial tests

- Revenue growth calculation.
- CAGR calculation.
- Margin calculation.
- ROIC calculation.
- FCF calculation.

### Scoring tests

- Factor normalization.
- Weight validation.
- Composite score.
- Ranking.

### Portfolio tests

- $1,000 contribution.
- Fractional shares.
- Dividend reinvestment.
- Portfolio valuation.
- Rebalancing.

### Backtest tests

- No future information leakage.
- Correct contribution count.
- Correct transaction dates.
- Correct benchmark comparison.
- Correct drawdown calculations.

---

# 38. Backtest Integrity Tests

This is one of the highest-priority requirements.

The system should include a test that intentionally introduces a future data point and verifies that the historical selection does not change.

Example:

```text
Decision date:
2021-06-30

Company filing:
2021-07-15

The July 15 filing must NOT affect the June 30 decision.
```

The system must use the filing date, not merely the fiscal period end date.

---

# 39. Reproducibility

Every backtest must save:

- Configuration
- Data-provider versions
- Data retrieval timestamps
- Universe
- Strategy version
- Scoring weights
- Start/end dates
- Software version
- Python version

Each backtest should generate a unique run ID.

Example:

```text
BACKTEST-20260928-001
```

The same configuration and same data snapshot should produce the same result.

---

# 40. Logging

Use Python's `logging` module.

Log:

- API calls
- Data downloads
- Missing data
- Calculation errors
- Ranking changes
- Portfolio transactions
- Backtest progress
- Warnings
- Exceptions

Logs should be written to:

```text
logs/app.log
```

---

# 41. Error Handling

The application must fail loudly for critical errors.

Examples:

- Missing API key
- Invalid data
- Corrupt database
- Incomplete price history
- Impossible financial values
- Missing sector classification

Do not replace missing values with zero unless zero is economically correct.

---

# 42. Security

API keys must be stored in:

```text
.env
```

or environment variables.

Never commit:

- API keys
- Passwords
- Broker credentials
- Account numbers
- Tokens

Provide:

```text
.env.example
```

---

# 43. Environment

Target environment:

- macOS
- Python 3.11+
- Virtual environment or Conda
- Git
- SQLite

The code should also work on Linux.

Optional Windows support is desirable.

---

# 44. Python Libraries

Suggested initial dependencies:

```text
pandas
numpy
requests
python-dotenv
scipy
scikit-learn
matplotlib
openpyxl
sqlalchemy
pyyaml
pytest
yfinance
```

`yfinance` may be used as a secondary/research data source, but the architecture must not depend on it as the sole source of truth.

Prefer official/primary sources where practical.

---

# 45. Optional Web Dashboard

Version 1 may remain CLI/report based.

A future version should support a local dashboard using:

- Streamlit, or
- FastAPI + React

Dashboard features:

- Current rankings
- Portfolio
- Historical performance
- Backtest configuration
- Factor charts
- Drawdown chart
- Stock detail
- Sector comparison

---

# 46. Optional Notifications

Future version:

Send weekly report through:

- Email
- Slack
- Telegram
- Push notification

Notification should contain:

```text
Weekly Quant Report

Market:
S&P 500: +X.XX%

New selections:
NVDA
JPM
LLY

Exits:
XYZ

Portfolio:
IVV       $500
Leaders   $300
Growth    $200
```

No trade should be executed merely because a notification was generated.

---

# 47. Optional Broker Integration

Do NOT implement live trading initially.

Future architecture should allow:

```text
Strategy Engine
      ↓
Portfolio Engine
      ↓
Trade Proposal
      ↓
Human Approval
      ↓
Broker API
```

Possible future brokers:

- Interactive Brokers
- Alpaca
- Fidelity if API access is available
- Schwab if API access is available

The system should initially support paper trading only.

---

# 48. Human Approval

Before any future live trade:

```text
SYSTEM:
Recommended transaction:
BUY 2.31 shares NVDA
Estimated value: $350

USER:
APPROVE
```

Only explicit approval should permit live execution.

---

# 49. Strategy Versioning

The strategy must be versioned.

Example:

```text
Strategy v1.0
Growth 30%
Momentum 30%
Quality 20%
Valuation 10%
Risk 10%
```

If the scoring model changes:

```text
Strategy v1.1
```

Backtests must retain the strategy version used to produce the result.

---

# 50. Avoiding Overfitting

The application must support:

- In-sample period
- Validation period
- Out-of-sample period

Example:

```text
2016–2022 = training
2023–2024 = validation
2025–2026 = out-of-sample
```

Do not optimize factor weights on the entire historical period and then report that same period as unbiased performance.

The system should provide warnings if the user performs excessive parameter optimization.

---

# 51. Walk-Forward Testing

Preferred advanced backtesting approach:

```text
Train/define parameters
       ↓
Test next period
       ↓
Advance window
       ↓
Recalculate
       ↓
Test next period
```

This should be supported in a future version.

---

# 52. Transaction Costs

The backtester must support:

```yaml
transaction_cost:
  commission_per_trade: 0
  slippage_bps: 5
```

Default slippage:

**5 basis points**

The user must be able to change it.

---

# 53. Taxes

Version 1 should report pre-tax results.

Future versions may model:

- Taxable brokerage account
- Short-term capital gains
- Long-term capital gains
- Qualified dividends
- Tax-loss harvesting

Do not include taxes in the default backtest.

---

# 54. Portfolio Concentration Limits

The system should support configurable limits:

```yaml
limits:
  max_single_stock_weight: 0.05
  max_sector_weight: 0.20
  max_positions: 40
```

The strategy must report when limits conflict with the desired selection.

---

# 55. Risk Controls

Optional rules:

- Do not purchase stocks below 200-day moving average.
- Reduce allocation when market is below 200-day moving average.
- Stop adding to stocks with severe fundamental deterioration.
- Maximum individual-stock allocation.
- Maximum sector allocation.
- Minimum liquidity requirement.

These should be disabled by default unless explicitly enabled.

The backtest must clearly show whether risk controls are active.

---

# 56. Weekly Decision Process

Every week:

```text
1. Update data
2. Validate data
3. Update universe
4. Update fundamentals
5. Calculate technical factors
6. Calculate fundamental factors
7. Normalize factors
8. Calculate composite scores
9. Rank stocks within sectors
10. Select top 3 per sector
11. Rank high-growth stocks
12. Calculate recommended allocations
13. Compare with current portfolio
14. Generate proposed transactions
15. Generate report
16. Save all results
```

---

# 57. Current-Week Report Example

```text
==================================================
QUANT WEEKLY STOCK REPORT
Date: 2026-09-25
==================================================

CORE
IVV                         $500

SECTOR LEADERS              $300

Information Technology
  1. NVDA
  2. AVGO
  3. MSFT

Financials
  1. JPM
  2. V
  3. BRK.B

...

HIGH-GROWTH PORTFOLIO       $200

  1. NVDA
  2. ...
  3. ...

==================================================
CHANGES FROM LAST WEEK
==================================================

NEW:
...

EXITED:
...

UPGRADED:
...

DOWNGRADED:
...

==================================================
RISK
==================================================

S&P 500 drawdown:
...

Portfolio estimated volatility:
...

Largest individual position:
...

==================================================
```

---

# 58. Explainability

Every selected stock must have an explanation generated from deterministic metrics.

Example:

```text
NVDA

Overall Score: 91.4

Growth:       97
Momentum:     99
Quality:      92
Valuation:    55
Risk:         73

Primary strengths:
- Strong revenue growth
- Strong earnings growth
- Positive relative strength
- Above long-term moving averages

Primary risks:
- High valuation
- High historical volatility
```

The application should not fabricate qualitative reasons.

---

# 59. Data Quality Dashboard

The system should report:

- Percentage of stocks with complete price data
- Percentage with complete fundamental data
- Missing filings
- Stale prices
- API failures
- Data-provider inconsistencies
- Corporate-action issues

Example:

```text
Data Quality
------------

S&P 500 stocks:          503
Price data complete:     498
Fundamental data:        476
Missing data:             27
Warnings:                  5
Critical errors:           0
```

---

# 60. Acceptance Criteria

The application is considered Version 1 complete when:

1. It can download historical price data.
2. It can build the S&P 500 universe.
3. It can classify stocks by sector.
4. It can calculate momentum factors.
5. It can calculate available fundamental factors.
6. It can calculate a composite score.
7. It can select three stocks per sector.
8. It can simulate $1,000 weekly investments.
9. It can simulate IVV using the same contributions.
10. It can run a 360-week backtest.
11. It can calculate ending balance and gains.
12. It can calculate maximum drawdown.
13. It can calculate CAGR/XIRR.
14. It can export CSV.
15. It can export Excel.
16. It can generate charts.
17. It can generate a weekly report.
18. It prevents obvious look-ahead bias.
19. It stores configuration and strategy version.
20. It has automated tests.
21. It can reproduce a backtest from the saved configuration.
22. It does not place live trades.

---

# 61. Recommended Development Phases

## Phase 1 — Data

Build:

- API clients
- Local cache
- SQLite database
- Data validation
- S&P 500 universe
- Sector classification

## Phase 2 — Factors

Build:

- Momentum
- Growth
- Quality
- Valuation
- Risk

## Phase 3 — Ranking

Build:

- Factor normalization
- Composite score
- Sector rankings
- Top-3 selection
- High-growth selection

## Phase 4 — Backtesting

Build:

- DCA engine
- IVV benchmark
- Portfolio engine
- Rebalancing
- Dividends
- Transaction costs
- Drawdowns

## Phase 5 — Reporting

Build:

- CSV
- Excel
- Markdown
- Charts
- Weekly report

## Phase 6 — Validation

Build:

- Unit tests
- Look-ahead tests
- Data-quality tests
- Walk-forward testing

## Phase 7 — Dashboard

Optional:

- Streamlit/FastAPI
- Interactive charts
- Portfolio monitoring

## Phase 8 — Paper Trading

Only after extensive validation.

---

# 62. Recommended Project Structure

```text
quant-stock-selector/
│
├── README.md
├── REQUIREMENTS.md
├── LICENSE
├── pyproject.toml
├── .env.example
├── .gitignore
│
├── config/
│   ├── settings.yaml
│   ├── scoring.yaml
│   └── sectors.yaml
│
├── data/
│   ├── raw/
│   ├── processed/
│   ├── cache/
│   └── database/
│
├── src/
│   ├── __init__.py
│   ├── config.py
│   ├── database.py
│   ├── logging_config.py
│   │
│   ├── data/
│   │   ├── alphavantage.py
│   │   ├── sec.py
│   │   ├── universe.py
│   │   └── validator.py
│   │
│   ├── factors/
│   │   ├── growth.py
│   │   ├── momentum.py
│   │   ├── quality.py
│   │   ├── valuation.py
│   │   └── risk.py
│   │
│   ├── ranking/
│   │   ├── normalization.py
│   │   ├── scoring.py
│   │   └── sector_ranker.py
│   │
│   ├── portfolio/
│   │   ├── dca.py
│   │   ├── portfolio.py
│   │   ├── rebalance.py
│   │   └── transactions.py
│   │
│   ├── backtest/
│   │   ├── engine.py
│   │   ├── metrics.py
│   │   ├── drawdown.py
│   │   └── validation.py
│   │
│   └── reports/
│       ├── weekly.py
│       ├── excel.py
│       └── charts.py
│
├── tests/
│   ├── test_data.py
│   ├── test_factors.py
│   ├── test_scoring.py
│   ├── test_portfolio.py
│   ├── test_backtest.py
│   └── test_lookahead.py
│
├── reports/
│
└── main.py
```

---

# 63. Frontier-Model Development Instructions

When giving this requirements document to a coding model, the model should:

1. Build the application incrementally.
2. Do not create a giant untested script.
3. Use modular Python packages.
4. Write tests alongside each feature.
5. Explain architectural decisions.
6. Never hard-code API keys.
7. Never silently discard missing data.
8. Never use future data in historical backtests.
9. Preserve reproducibility.
10. Document all assumptions.
11. Prefer official/primary data sources.
12. Clearly distinguish verified historical results from estimates.
13. Provide complete files rather than isolated snippets when implementing changes.
14. Run tests after each major implementation stage.
15. Keep the application usable from the command line.
16. Make configuration external and user-editable.
17. Do not implement live trading until explicitly requested.

---

# 64. Important Financial-Research Principle

The application is a research and backtesting system.

Historical outperformance does not establish that the strategy will outperform in the future.

The application must therefore report:

- Historical results
- Model assumptions
- Data limitations
- Survivorship-bias status
- Look-ahead-bias status
- Transaction-cost assumptions
- Parameter choices
- Out-of-sample results where available

The system must never describe a backtested strategy as guaranteed, safe, or certain to outperform.

---

# 65. Future Enhancements

Potential future versions:

- Earnings-call transcript analysis
- News sentiment
- Analyst estimate revisions
- Insider transactions
- Institutional ownership
- Options-implied volatility
- Short interest
- Alternative data
- Machine-learning ranking models
- XGBoost/LightGBM factor model
- Walk-forward optimization
- Regime detection
- Market breadth
- Sector rotation
- Tax-aware optimization
- Tax-loss harvesting
- Broker paper trading
- Broker live trading with explicit human approval
- Web dashboard
- Mobile notifications

These are not required for Version 1.

---

# 66. Final Design Principle

The application should prioritize:

**Data integrity > backtest integrity > reproducibility > explainability > complexity.**

A simple model using clean point-in-time data is preferable to a sophisticated model that accidentally uses future information.

The primary objective is not to find the stocks that performed best in hindsight.

The objective is to determine whether a systematic process could have identified strong stocks using only information that was actually available at the time.

