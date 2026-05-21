# stock-web

Assistant-readable FinanceData/marcap OHLC atlas for E2R historical calibration.

This repo commits compact plain-text artifacts generated from FinanceData/marcap. It does not commit the original raw source checkout, DuckDB cache, one giant CSV, or any paid/external market API output.

## What To Read First

1. `atlas/manifest.json` for source, date range, row count, and branch strategy.
2. `diagnostics/chatgpt_bundle.txt` or `.json` for a compact ChatGPT verification bundle.
3. `atlas/research_packs/smoke/smoke_005930_000660_298040_267260_086520.json` for a ready E2R smoke pack.

## Example: Samsung Electronics

- Profile: `atlas/symbol_profiles/005/005930.json`
- 2024 OHLC shard: `atlas/ohlcv_min_by_symbol_year/005/005930/2024.csv`
- Code prefix index: `atlas/index/by_code_prefix/005.json`

## Source Caveat

- Source: FinanceData/marcap
- Source repo: https://github.com/FinanceData/marcap
- Price adjustment status: raw_unadjusted_marcap
- Caveat: Raw/unadjusted OHLC from FinanceData/marcap. Corporate actions are not adjusted unless explicitly added later.
- Atlas normalization: one row per code/date, with high/low consistency enforced for browser-readable backtests.

This is a collector-generated research data access layer, not investment advice.
