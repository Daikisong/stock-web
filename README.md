# stock-web

Assistant-readable FinanceData/marcap OHLC atlas for E2R historical calibration.

This repo commits compact plain-text artifacts generated from FinanceData/marcap. Raw reference rows and calibration-safe tradable rows are separated.

## What To Read First

1. `atlas/manifest.json` for source, date range, row quality counts, and shard roots.
2. `diagnostics/chatgpt_bundle.txt` or `.json` for a compact ChatGPT verification bundle.
3. `atlas/research_packs/smoke/smoke_005930_000660_298040_267260_086520.json` for a ready E2R smoke pack.

## Price Shards

- Calibration-safe tradable rows: `atlas/ohlcv_tradable_by_symbol_year/{prefix}/{code}/{year}.csv`
- Raw reference rows with `row_status`: `atlas/ohlcv_raw_by_symbol_year/{prefix}/{code}/{year}.csv`
- Backward-compatible tradable copy: `atlas/ohlcv_min_by_symbol_year/{prefix}/{code}/{year}.csv`

## Example: Samsung Electronics

- Profile: `atlas/symbol_profiles/005/005930.json`
- 2024 tradable OHLC shard: `atlas/ohlcv_tradable_by_symbol_year/005/005930/2024.csv`
- 2024 raw OHLC shard: `atlas/ohlcv_raw_by_symbol_year/005/005930/2024.csv`
- Code prefix index: `atlas/index/by_code_prefix/005.json`

## Source Caveat

- Source: FinanceData/marcap
- Source repo: https://github.com/FinanceData/marcap
- Price adjustment status: raw_unadjusted_marcap
- Caveat: Raw/unadjusted OHLC from FinanceData/marcap. Corporate actions are not adjusted unless explicitly added later.
- Zero-volume and zero-OHLC rows are excluded from calibration shards.
- Corporate-action-contaminated windows are blocked from calibration by default.

This is a collector-generated research data access layer, not investment advice.
