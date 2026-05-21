# marcap-price-gateway Goal

Build a minimal, public-read, research-only Korean stock OHLC gateway for E2R historical calibration.

The goal is not a beautiful UI. The goal is to expose FinanceData/marcap OHLC rows and trigger-level backtest summaries in a way that ChatGPT can open through a normal web browser/web fetch.

Repository name:

- `marcap-price-gateway`

Primary data source:

- FinanceData/marcap
- https://github.com/FinanceData/marcap

Important source facts:

- FinanceData/marcap contains KRX daily market-cap / OHLC data from 1995-05-02 to current.
- It includes columns such as Date, Code, Name, Open, High, Low, Close, Volume, Amount, Marcap, Stocks, MarketId, Market, Dept.
- The GitHub README says the data directory contains yearly compressed CSV files. Do not assume parquet only.
- Support `.csv.gz` as the primary input.
- Supporting parquet as an optional cache is allowed.
- This is for personal research/backtesting only.

Why this exists:

- A separate E2R historical calibration prompt requires:
- `actual_1D_OHLC_available = true`
- `minimum_forward_window_available = 180_trading_days`
- `price_source_is_usable_for_backtest = true`
- Actual `Date/Open/High/Low/Close/Volume` rows visible through a web route
- MFE / MAE / peak / drawdown computed from real OHLC, not from news/event-return summaries.

Build a simple FastAPI app that can be deployed publicly and accessed by ChatGPT.

## Critical Accessibility Requirements

1. Do not require login.
2. Do not require Basic Auth.
3. Do not require OAuth.
4. Do not require Authorization headers.
5. Do not require cookies.
6. Do not require JavaScript to see data.
7. Do not use CAPTCHA.
8. Do not put routes behind Cloudflare Access, Bot Fight Mode, Turnstile, WAF challenge, or any browser challenge.
9. Do not return 403 to unknown user agents.
10. Do not block common crawler/browser user agents.
11. API routes must work with plain curl and normal browser GET.
12. HTML routes must be server-rendered and contain visible text tables.
13. JSON routes must return normal `application/json`.
14. CSV routes must return `text/csv`.
15. Add CORS allow-all for this personal research gateway.
16. Add noindex headers, but do not block access.
17. Use a long random URL token for light obscurity, not real authentication.

## Security Model

- This is a read-only research gateway.
- No write API.
- No user accounts.
- No database mutation from public routes.
- No secrets exposed.
- The only protection is an optional `ACCESS_TOKEN` path segment.
- If `ACCESS_TOKEN` is set, routes should be available under `/g/{ACCESS_TOKEN}/...`.
- If `ACCESS_TOKEN` is empty or `ACCESS_TOKEN=dev`, routes should also work locally without token.
- Token check should be simple path-token comparison, not a header/cookie challenge.
- The app must still expose `/__ping` and `/__health` without auth for connectivity testing.

## Tech Stack

- Python 3.11+
- FastAPI
- Uvicorn
- Pandas
- DuckDB
- PyArrow optional
- Jinja2 for simple HTML templates
- pytest
- httpx or FastAPI TestClient for tests

## Non-Goals

- Do not over-engineer.
- Do not build a frontend framework.
- Do not use React/Next/Vue.
- Do not use a paid database.
- Do not use Redis.
- Do not use authentication middleware.
- Do not require external market API keys.

## Required Project Structure

```text
marcap-price-gateway/
  README.md
  pyproject.toml
  Dockerfile
  .env.example
  app/
    __init__.py
    main.py
    settings.py
    marcap_store.py
    backtest.py
    schemas.py
    templates/
      index.html
      price_path.html
      trigger_backtest.html
      event_window.html
  scripts/
    bootstrap_marcap.py
    build_duckdb_cache.py
    refresh_marcap.py
  tests/
    test_health.py
    test_backtest_math.py
    test_routes.py
    fixtures/
      sample_marcap.csv.gz
```

## Data Layout

```text
data/
  marcap/
    data/
      marcap-1995.csv.gz
      marcap-1996.csv.gz
      ...
  cache/
    marcap.duckdb
```

## Environment Variables

- `MARCAP_REPO_URL=https://github.com/FinanceData/marcap.git`
- `MARCAP_REPO_PATH=./data/marcap`
- `MARCAP_DUCKDB_PATH=./data/cache/marcap.duckdb`
- `ACCESS_TOKEN=dev`
- `DEFAULT_START=1995-05-02`
- `DEFAULT_MAX_ROWS=20000`
- `PUBLIC_BASE_URL=`

## Script Requirements

### `scripts/bootstrap_marcap.py`

- If `MARCAP_REPO_PATH` does not exist, git clone `--depth 1` FinanceData/marcap into it.
- If it exists and is a git repo, run `git pull --ff-only`.
- Print latest detected data file.
- Do not fail if git is unavailable when files already exist.

### `scripts/build_duckdb_cache.py`

- Read yearly marcap `.csv.gz` files from `MARCAP_REPO_PATH/data`.
- Build a DuckDB table named `daily_prices`.
- Normalize columns to lowercase snake_case:
- `date, rank, code, name, open, high, low, close, volume, amount, changes, change_code, changes_ratio, marcap, stocks, market_id, market, dept`
- Preserve `code` as zero-padded 6-character string.
- Parse `date` as `DATE`.
- Numeric columns should be numeric.
- Create indexes if DuckDB supports them usefully, otherwise skip without failing.
- Create a metadata table:
- `source_name`
- `source_repo_url`
- `built_at`
- `min_date`
- `max_date`
- `row_count`
- `year_files_loaded`
- `price_adjustment_status`
- `price_adjustment_status` must be `raw_unadjusted_marcap`.
- The app should be able to lazily build cache on startup if missing, but prefer explicit script build.

## `app/marcap_store.py`

Implement a `MarcapStore` class with:

- `health()`
- `search_symbols(q, limit=20)`
- `get_ohlcv(code, start, end, market=None, limit=None)`
- `get_universe(date, market=None, limit=None)`
- `get_latest_date()`
- `get_first_available_on_or_after(code, date)`
- `get_event_window(code, anchor_date, pre=10, post=10)`
- `get_forward_window(code, entry_date, max_window=504)`

Fallback:

- If DuckDB cache is missing, read directly from `.csv.gz` files for the requested year range.
- Direct CSV fallback can be slower but must work.

## `app/backtest.py`

Implement deterministic functions:

- `choose_entry_row(rows, trigger_date, entry_mode)`
- `compute_trigger_backtest(rows, trigger_date, entry_mode="next_trading_day_close", windows=[30,90,180,252,504])`
- `compute_path_summary(rows, entry_date, points=[1,2,3,5,10,20,30,60,90,180,252,504])`
- `compute_event_window(rows, anchor_date, pre=10, post=10)`

Entry modes:

1. `trigger_close`
- `entry_date = first trading day on or after trigger_date`
- `entry_price = close of that row`

2. `next_trading_day_close`
- Default
- `entry_date = next trading day after first trading day on or after trigger_date`
- `entry_price = close of that row`
- This is the default to reduce same-day hindsight bias.

3. `next_trading_day_open`
- `entry_date = next trading day after first trading day on or after trigger_date`
- `entry_price = open of that row`

Backtest math:

- Use trading-day rows only.
- Sort rows by date ascending.
- For each N:
- `window_N = first N trading rows from entry_date inclusive`
- `MFE_N_pct = (max(high in window_N) / entry_price - 1) * 100`
- `MAE_N_pct = (min(low in window_N) / entry_price - 1) * 100`
- `below_entry_price_flag_N = true if any close < entry_price in window_N after the entry row`
- `peak_price = max(high over observed forward window up to max_window)`
- `peak_date = first date where high == peak_price`
- `drawdown_after_peak_pct = (min(low after peak_date) / peak_price - 1) * 100`
- `forward_window_trading_days = number of rows after entry_date available, excluding the entry row`
- `calibration_usable = true only if`:
- `open/high/low/close/volume` are present
- entry row exists
- at least 180 forward trading days are available
- `MFE_30D/MFE_90D/MFE_180D` and `MAE_30D/MAE_90D/MAE_180D` were computed
- If a window is unavailable, return null for that window and include an explanatory warning.

Path summary:

- For points `D+1, D+2, D+3, D+5, D+10, D+20, D+30, D+60, D+90, D+180, D+252, D+504`:
- Use `entry_date` as `D+0`.
- `D+N` row is N trading days after entry row.
- `close_return_pct = (close_at_point / entry_price - 1) * 100`
- `high_to_date_return_pct = (max high from entry row through point row / entry_price - 1) * 100`
- `low_to_date_return_pct = (min low from entry row through point row / entry_price - 1) * 100`
- If point is unavailable, include `available=false`.

## Required API Routes

All must support token-prefixed and non-token local paths.

Connectivity:

### `GET /__ping`

- returns plain text: `ok`

### `GET /__health`

- returns JSON with:
- `status`
- `source_name`
- `source_repo_url`
- `min_date`
- `max_date`
- `row_count`
- `cache_path`
- `price_adjustment_status`
- `access_mode`
- `current_time`

Token-prefixed group:

- `GET /g/{token}/__ping`
- `GET /g/{token}/__health`

Main API:

### `GET /g/{token}/api/ohlcv`

Params:

- `code`: required, 6-digit string
- `start`: required `YYYY-MM-DD`
- `end`: required `YYYY-MM-DD`
- `market`: optional
- `format`: `json` or `csv`, default `json`
- `limit`: optional

Return:

- rows with `date, code, name, open, high, low, close, volume, amount, marcap, stocks, market, source, price_adjustment_status`
- CSV version must be downloadable and visible as plain CSV.

### `GET /g/{token}/api/trigger-backtest`

Params:

- `code`: required
- `trigger_date`: required `YYYY-MM-DD`
- `entry_mode`: optional, default `next_trading_day_close`
- `max_window`: optional, default `504`
- `windows`: optional comma-separated, default `30,90,180,252,504`

Return JSON:

- `code`
- `name`
- `trigger_date`
- `entry_mode`
- `entry_date`
- `entry_price`
- `price_data_source = "FinanceData/marcap"`
- `price_adjustment_status = "raw_unadjusted_marcap"`
- `calibration_usable`
- `forward_window_trading_days`
- `MFE_30D_pct`
- `MFE_90D_pct`
- `MFE_180D_pct`
- `MFE_1Y_pct`
- `MFE_2Y_pct`
- `MAE_30D_pct`
- `MAE_90D_pct`
- `MAE_180D_pct`
- `MAE_1Y_pct`
- `below_entry_price_flag_30D`
- `below_entry_price_flag_90D`
- `peak_date`
- `peak_price`
- `drawdown_after_peak_pct`
- `warnings`
- `source_notes`

### `GET /g/{token}/api/path-summary`

Params:

- `code`: required
- `entry_date`: required `YYYY-MM-DD`
- `entry_mode`: optional, default `trigger_close`
- `points`: optional comma-separated, default `1,2,3,5,10,20,30,60,90,180,252,504`

Return JSON:

- `code`
- `name`
- `entry_date`
- `entry_price`
- `points`:
- `label: "D+30"`
- `trading_day_offset: 30`
- `date`
- `close`
- `high_to_date`
- `low_to_date`
- `close_return_pct`
- `high_to_date_return_pct`
- `low_to_date_return_pct`
- `available`

### `GET /g/{token}/api/event-window`

Params:

- `code`: required
- `anchor_date`: required `YYYY-MM-DD`
- `pre`: optional default `10`
- `post`: optional default `10`
- `format`: `json` or `csv`

Return rows around an event date:

- `relative_day_index`
- `date`
- `open`
- `high`
- `low`
- `close`
- `volume`
- `amount`
- `marcap`
- `close_return_from_anchor_pct`

### `GET /g/{token}/api/symbol-search`

Params:

- `q`: required
- `limit`: optional default `20`

Search by code or Korean name.

Return JSON rows:

- `code`
- `name`
- `market`
- `first_date`
- `last_date`
- `row_count`

### `GET /g/{token}/api/universe`

Params:

- `date`: required `YYYY-MM-DD`
- `market`: optional `KOSPI/KOSDAQ/KONEX`
- `format`: `json` or `csv`

Return all symbols available on that date.

### `GET /g/{token}/api/research-pack`

Params:

- `items`: required
- format: `code:trigger_date,code:trigger_date,code:trigger_date`
- example: `005930:2024-01-02,000660:2024-01-02`
- `entry_mode`: optional default `next_trading_day_close`

Return JSON:

- `generated_at`
- `item_count`
- `results`
- each result contains:
- `code`
- `trigger_date`
- `trigger_backtest`
- `path_summary`
- `event_window_pre10_post10`

This is for feeding ChatGPT a compact price pack.

## HTML Routes

### `GET /g/{token}/`

- Simple index page.
- Show example links using the current token.
- Show latest date, source, row_count.
- No JavaScript required.

### `GET /g/{token}/price-path/{code}`

Params:

- `start`
- `end`

Requirements:

- Server-rendered HTML table with visible OHLCV rows.
- Include CSV link.
- Include source note.
- Include `raw_unadjusted_marcap` note.

### `GET /g/{token}/trigger/{code}`

Params:

- `trigger_date`
- `entry_mode` optional

Requirements:

- Server-rendered trigger backtest page.
- Show the JSON-like summary in visible text.
- Show MFE/MAE table.
- Show D+ path summary table.
- Show pre/post event window table.
- Include direct JSON API links.

## Response Formatting

- Percent values should be rounded to 2 decimals.
- Dates should be ISO `YYYY-MM-DD` strings.
- Do not return `NaN`. Use `null`.
- Preserve 6-digit stock codes.
- Return warnings explicitly.

## Data Source Notes

Every API response must include:

- `price_data_source: "FinanceData/marcap"`
- `source_repo_url: "https://github.com/FinanceData/marcap"`
- `price_adjustment_status: "raw_unadjusted_marcap"`
- `caveat: "FinanceData/marcap OHLC appears raw/unadjusted in this gateway. Corporate actions are not adjusted unless explicitly added later."`

Important caveat:

- Do not pretend the data is adjusted OHLC.
- Do not call it official audited data.
- Do not give investment advice.
- This gateway is a research data access layer only.

## Tests

Create pytest tests using `tests/fixtures/sample_marcap.csv.gz`.

The fixture should include at least:

- one code with 600 trading rows
- known open/high/low/close values
- at least one event date

Tests must verify:

1. `/__ping` returns `ok`
2. `/__health` returns `status`
3. symbol search works
4. ohlcv returns sorted rows
5. trigger-backtest computes MFE/MAE correctly
6. path-summary computes `D+1`, `D+30`, `D+180` correctly
7. `calibration_usable` is true only when 180 forward trading days exist
8. missing windows return null and warnings, not exceptions
9. token route works
10. non-JS HTML contains visible table text

## README.md

Include:

- What this is
- How to bootstrap marcap
- How to build cache
- How to run locally
- How to deploy
- Accessibility rules for ChatGPT
- Example URLs:
- `/__ping`
- `/__health`
- `/g/dev/api/symbol-search?q=삼성`
- `/g/dev/api/ohlcv?code=005930&start=2024-01-01&end=2024-12-31`
- `/g/dev/api/trigger-backtest?code=005930&trigger_date=2024-01-02`
- `/g/dev/api/path-summary?code=005930&entry_date=2024-01-03`
- `/g/dev/price-path/005930?start=2024-01-01&end=2024-12-31`
- `/g/dev/trigger/005930?trigger_date=2024-01-02`
- Deployment warning:
- Do not enable Cloudflare Access, CAPTCHA, Bot Fight Mode, WAF challenge, login, or auth headers if ChatGPT needs to read it.
- Privacy warning:
- The token path is only obscurity. Anyone with the URL can read the data.

## Dockerfile

- Python 3.11 slim
- install dependencies
- expose 8000
- command:

```bash
uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}
```

## Implementation Details

- Use DuckDB for fast filtering:

```sql
SELECT * FROM daily_prices WHERE code = ? AND date BETWEEN ? AND ? ORDER BY date
```

- If code is numeric input, zero-pad to 6 digits.
- Validate date inputs.
- Return HTTP 400 for invalid params.
- Return HTTP 404 if no rows for a code/date range.
- Return HTTP 200 with warnings for insufficient forward window.
- Add simple in-memory LRU cache for repeated code/date queries if easy, but do not overcomplicate.

## One-Shot Completion Requirement

When done, provide:

1. Files created
2. Commands to run locally
3. Commands to bootstrap data
4. Commands to build cache
5. Example working URLs
6. Test command and result
7. Any limitations

Local run commands expected:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
python scripts/bootstrap_marcap.py
python scripts/build_duckdb_cache.py
ACCESS_TOKEN=dev uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

Windows PowerShell equivalents:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
python scripts\bootstrap_marcap.py
python scripts\build_duckdb_cache.py
$env:ACCESS_TOKEN="dev"; uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

Do not stop after scaffolding. Implement the actual working app, tests, and README.
