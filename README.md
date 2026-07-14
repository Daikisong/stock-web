# stock-web

Assistant-readable FinanceData/marcap OHLC atlas for E2R historical calibration.

This repo commits compact plain-text artifacts generated from FinanceData/marcap. Raw reference rows and calibration-safe tradable rows are separated.

## What To Read First

1. `atlas/research_daily/README_LLM.md` for historical market-wide BLIND/outcome research.
2. `atlas/research_daily/access/YYYY/MM/YYYYMMDD.json` for the safe files for a trade date.
3. `atlas/manifest.json` for source, date range, row quality counts, and shard roots.
4. `diagnostics/chatgpt_bundle.txt` or `.json` for a compact ChatGPT verification bundle.
5. `atlas/research_packs/smoke/smoke_005930_000660_298040_267260_086520.json` for a ready E2R smoke pack.

## Date-Centered Research Daily Layer

Use this when ChatGPT needs the whole KOSPI/KOSDAQ/KOSDAQ GLOBAL market for one historical trading date without opening thousands of symbol files.

Example for `2026-06-22`:

- Access manifest: `atlas/research_daily/access/2026/06/20260622.json`
- BLIND snapshot before prediction sealing: `atlas/research_daily/snapshots/2026/06/20260619.csv`
- OUTCOME snapshot after sealing: `atlas/research_daily/snapshots/2026/06/20260622.csv`

Raw URL examples:

- https://raw.githubusercontent.com/Daikisong/stock-web/refs/heads/main/atlas/research_daily/access/2026/06/20260622.json
- https://raw.githubusercontent.com/Daikisong/stock-web/refs/heads/main/atlas/research_daily/snapshots/2026/06/20260619.csv
- https://raw.githubusercontent.com/Daikisong/stock-web/refs/heads/main/atlas/research_daily/snapshots/2026/06/20260622.csv

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

## Rebuild Commands

```bash
python scripts/build_price_atlas.py
python scripts/validate_price_atlas.py
python scripts/build_research_daily.py --incremental --validate
python scripts/validate_research_daily.py --full
pytest -q
```

## Temporary current-run NSLAB media transport

- [Current prompt media URL](https://media.githubusercontent.com/media/Daikisong/new_bot/main/docs/research_prompt.md)
- [Current CSV media URL](https://media.githubusercontent.com/media/Daikisong/new_bot/main/docs/csv/news_20180620.csv)
- [Current example media URL](https://media.githubusercontent.com/media/Daikisong/new_bot/main/docs/example2.md)
