# marcap-price-gateway

Minimal public-read FastAPI gateway for FinanceData/marcap Korean stock OHLC rows and trigger-level research backtest summaries.

This is a research data access layer for E2R historical calibration. It is not investment advice, not official audited data, and does not pretend that the OHLC data is adjusted.

## Data Source

- Source: FinanceData/marcap
- Repository: https://github.com/FinanceData/marcap
- Input format: yearly `data/*.csv.gz` files are the primary source.
- Price adjustment status: `raw_unadjusted_marcap`

The gateway always returns this caveat:

```text
FinanceData/marcap OHLC appears raw/unadjusted in this gateway. Corporate actions are not adjusted unless explicitly added later.
```

## Install Locally

Run these commands from the `marcap-price-gateway` directory:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

Windows PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
```

## Bootstrap FinanceData/marcap

```bash
python scripts/bootstrap_marcap.py
```

This clones `https://github.com/FinanceData/marcap.git` into `./data/marcap` if missing. If the repo already exists, it runs `git pull --ff-only`.

## Build DuckDB Cache

```bash
python scripts/build_duckdb_cache.py
```

This reads `./data/marcap/data/*.csv.gz`, creates `./data/cache/marcap.duckdb`, and builds a `daily_prices` table plus metadata.

The app can lazily build the cache on startup if the cache is missing and CSV files exist, but the explicit build script is preferred.

## Run Locally

```bash
ACCESS_TOKEN=dev uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

Windows PowerShell:

```powershell
$env:ACCESS_TOKEN="dev"; uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

## Environment

Copy `.env.example` values into your deployment environment:

```text
MARCAP_REPO_URL=https://github.com/FinanceData/marcap.git
MARCAP_REPO_PATH=./data/marcap
MARCAP_DUCKDB_PATH=./data/cache/marcap.duckdb
ACCESS_TOKEN=dev
DEFAULT_START=1995-05-02
DEFAULT_MAX_ROWS=20000
PUBLIC_BASE_URL=
```

If `ACCESS_TOKEN=dev` or empty, local non-token routes such as `/api/ohlcv` also work. If you set a long random token, use `/g/{ACCESS_TOKEN}/...`.

## Example URLs

```text
/__ping
/__health
/g/dev/api/symbol-search?q=삼성
/g/dev/api/ohlcv?code=005930&start=2024-01-01&end=2024-12-31
/g/dev/api/trigger-backtest?code=005930&trigger_date=2024-01-02
/g/dev/api/path-summary?code=005930&entry_date=2024-01-03
/g/dev/price-path/005930?start=2024-01-01&end=2024-12-31
/g/dev/trigger/005930?trigger_date=2024-01-02
```

CSV examples:

```text
/g/dev/api/ohlcv?code=005930&start=2024-01-01&end=2024-12-31&format=csv
/g/dev/api/event-window?code=005930&anchor_date=2024-01-02&format=csv
```

## Backtest Meaning

Example: with `entry_mode=next_trading_day_close`, a trigger on `2024-01-02` enters at the next trading day's close. This reduces same-day hindsight bias.

MFE/MAE are computed from actual OHLC rows:

- `MFE_30D_pct`: max high in the first 30 trading rows from entry, divided by entry price.
- `MAE_30D_pct`: min low in the first 30 trading rows from entry, divided by entry price.
- `drawdown_after_peak_pct`: lowest low after the peak high, divided by the peak high.

## ChatGPT Accessibility Rules

If ChatGPT needs to fetch this gateway, do not enable:

- Login
- Basic Auth
- OAuth
- Authorization headers
- Required cookies
- JavaScript-only rendering
- CAPTCHA
- Cloudflare Access
- Bot Fight Mode
- Turnstile
- WAF/browser challenges
- User-agent blocking

Routes must work with plain `curl` and normal browser `GET`. HTML pages are server-rendered visible tables. JSON routes return `application/json`. CSV routes return `text/csv`.

The app sends `X-Robots-Tag: noindex, nofollow`, but this is not an access block.

## Deployment

Docker:

```bash
docker build -t marcap-price-gateway .
docker run --rm -p 8000:8000 \
  -e ACCESS_TOKEN=replace-with-a-long-random-token \
  -v "$PWD/data:/app/data" \
  marcap-price-gateway
```

Before deploying, run:

```bash
python scripts/bootstrap_marcap.py
python scripts/build_duckdb_cache.py
```

Deployment warning: do not enable Cloudflare Access, CAPTCHA, Bot Fight Mode, WAF challenge, login, or auth headers if ChatGPT needs to read it.

Privacy warning: the token path is only obscurity. Anyone with the URL can read the data.

## Tests

```bash
pytest
```

The tests use `tests/fixtures/sample_marcap.csv.gz`, which contains deterministic sample OHLC rows for math verification.
