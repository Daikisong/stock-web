# FinanceData/marcap OHLC Atlas

This atlas contains collector-generated Korean stock OHLC artifacts for E2R historical calibration.

Raw FinanceData/marcap files are not committed directly because they are source/cache artifacts. This repo commits assistant-readable plain text shards instead.

## How ChatGPT Should Read It

1. Open `atlas/manifest.json`.
2. For a stock code, use the first 3 digits as prefix.
3. Open `atlas/index/by_code_prefix/{prefix}.json`.
4. Open `atlas/symbol_profiles/{prefix}/{code}.json`.
5. Use `atlas/ohlcv_tradable_by_symbol_year/{prefix}/{code}/{year}.csv` for calibration.
6. Use `atlas/ohlcv_raw_by_symbol_year/{prefix}/{code}/{year}.csv` only to inspect excluded raw rows and row_status.
7. Prefer generated research pack JSON/MD for calibration.

## Example Paths

- `atlas/manifest.json`
- `atlas/index/by_code_prefix/005.json`
- `atlas/symbol_profiles/005/005930.json`
- `atlas/ohlcv_tradable_by_symbol_year/005/005930/2024.csv`
- `atlas/ohlcv_raw_by_symbol_year/086/086520/2024.csv`
- `atlas/corporate_actions/corporate_action_candidates.csv`
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
