# FinanceData/marcap OHLC Atlas

This atlas contains collector-generated Korean stock OHLC artifacts for E2R historical calibration.

Raw FinanceData/marcap files are not committed directly because they are source/cache artifacts. This repo commits assistant-readable plain text shards instead.

## How ChatGPT Should Read It

1. For whole-market historical research, open `atlas/research_daily/README_LLM.md` first.
2. For a trading date D, open `atlas/research_daily/access/YYYY/MM/YYYYMMDD.json`.
3. Before BLIND sealing, read only the `blind_snapshot_path` from that access JSON.
4. After sealing, read the `outcome_snapshot_path`.
5. For single-stock calibration, open `atlas/manifest.json`, then use the first 3 code digits as prefix.
6. Use `atlas/ohlcv_tradable_by_symbol_year/{prefix}/{code}/{year}.csv` for calibration.
7. Use `atlas/ohlcv_raw_by_symbol_year/{prefix}/{code}/{year}.csv` only to inspect excluded raw rows and row_status.
8. Prefer generated research pack JSON/MD for E2R trigger calibration.

## Example Paths

- `atlas/manifest.json`
- `atlas/index/by_code_prefix/005.json`
- `atlas/symbol_profiles/005/005930.json`
- `atlas/ohlcv_tradable_by_symbol_year/005/005930/2024.csv`
- `atlas/ohlcv_raw_by_symbol_year/086/086520/2024.csv`
- `atlas/corporate_actions/corporate_action_candidates.csv`
- `atlas/research_daily/README_LLM.md`
- `atlas/research_daily/access/2026/06/20260622.json`
- `atlas/research_daily/snapshots/2026/06/20260622.csv`
- `atlas/research_packs/smoke/smoke_005930_000660_298040_267260_086520.json`

## Caveat

- Source: FinanceData/marcap
- Source repo: https://github.com/FinanceData/marcap
- Price adjustment status: raw_unadjusted_marcap
- Raw/unadjusted OHLC. Corporate actions are not adjusted unless explicitly added later.
- No high/low repair is applied for calibration.
- Zero-volume and zero-OHLC rows are excluded from calibration shards.
- Corporate-action-contaminated windows are blocked from calibration by default.

Use only `calibration_usable=true` rows for E2R calibration. Reject cases without 180 forward tradable days. Do not use narrative-only rows for weight changes.
