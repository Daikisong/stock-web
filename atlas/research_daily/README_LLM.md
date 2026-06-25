# Research Daily LLM Usage Guide

Use this layer when researching a historical trading date without leaking future information.

## Trading Date D Procedure

1. Open `atlas/research_daily/access/YYYY/MM/YYYYMMDD.json`.
2. Before BLIND sealing, download only `blind_snapshot_path`.
3. Confirm `blind_snapshot_date` equals `previous_trade_date`.
4. Confirm every row in the blind snapshot has `max_source_date <= previous_trade_date`.
5. Save the pre-market prediction and seal its SHA-256.
6. Only after sealing, download `outcome_snapshot_path`.
7. Use the outcome snapshot for market-wide upper-limit, strong-rise, amount-rank, and breadth research.
8. Do not use `symbol_profiles`, `all_symbols`, or latest/current fields for historical BLIND research.

## Raw URL Examples

```text
https://raw.githubusercontent.com/Daikisong/stock-web/refs/heads/main/atlas/research_daily/access/2026/06/20260622.json
https://raw.githubusercontent.com/Daikisong/stock-web/refs/heads/main/atlas/research_daily/snapshots/2026/06/20260619.csv
https://raw.githubusercontent.com/Daikisong/stock-web/refs/heads/main/atlas/research_daily/snapshots/2026/06/20260622.csv
```

If a file is missing, do not infer a holiday from the missing path. Check `atlas/research_daily/trading_calendar.csv`.

The data is raw/unadjusted FinanceData/marcap OHLC. Corporate-action-warning rows are blocked for upper-limit and return labels.
