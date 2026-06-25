from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from collections import defaultdict, deque
from pathlib import Path
from typing import Any

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.research_daily_utils import (
    ACCESS_ROOT,
    CALENDAR_COLUMNS,
    CALENDAR_PATH,
    CAVEAT,
    DEFAULT_RESEARCH_START_DATE,
    PRICE_ADJUSTMENT_STATUS,
    RESEARCH_DAILY_ROOT,
    RESEARCH_DAILY_VERSION,
    SNAPSHOT_COLUMNS,
    SNAPSHOT_ROOT,
    SOURCE_NAME,
    SOURCE_REPO_URL,
    atomic_write_json,
    atomic_write_text,
    compute_limit_up_price,
    csv_value,
    days_between,
    finalize_snapshot_rows,
    int_price,
    json_text,
    load_corporate_action_dates,
    load_name_history,
    parse_markets,
    pct,
    prepare_daily_source_frame,
    preserve_generated_at_if_stable,
    ratio_pct,
    rel_path,
    research_access_path,
    research_snapshot_path,
    rows_to_csv_text,
    sha256_file,
    source_files_for_range,
    utc_now,
)
from scripts.atlas_utils import file_year, read_marcap_file

SOURCE_REPO_PATH = ROOT / ".cache" / "marcap"
ATLAS_ROOT = ROOT / "atlas"
DIAGNOSTICS_ROOT = ROOT / "diagnostics"


def git_commit_hash(repo: Path) -> str:
    try:
        return subprocess.check_output(["git", "-C", str(repo), "rev-parse", "HEAD"], text=True).strip()
    except Exception:
        return ""


def load_atlas_manifest() -> dict[str, Any]:
    path = ATLAS_ROOT / "manifest.json"
    if not path.exists():
        raise FileNotFoundError("atlas/manifest.json is required before building research_daily")
    return json.loads(path.read_text(encoding="utf-8"))


def collect_market_trade_dates(files: list[Path], markets: list[str], start_date: str, end_date: str) -> tuple[list[str], str | None]:
    all_dates: set[str] = set()
    for path in files:
        frame = prepare_daily_source_frame(read_marcap_file(path), markets)
        if frame.empty:
            continue
        dates = frame.loc[frame["date"] <= end_date, "date"].dropna().unique().tolist()
        all_dates.update(str(item) for item in dates)
        print(f"calendar scan {path.name}: tradable_market_dates={len(set(dates))}")
    sorted_dates = sorted(all_dates)
    research_dates = [item for item in sorted_dates if start_date <= item <= end_date]
    if not research_dates:
        raise RuntimeError(f"no market trade dates found between {start_date} and {end_date}")
    seed_candidates = [item for item in sorted_dates if item < research_dates[0]]
    seed_date = seed_candidates[-1] if seed_candidates else None
    return research_dates, seed_date


def state_seed() -> dict[str, Any]:
    return {"history": deque(maxlen=21), "recent": deque(maxlen=4)}


def build_snapshot_row(
    source_row: dict[str, Any],
    state: dict[str, Any],
    market_previous_date: str | None,
    corporate_dates: set[str],
) -> dict[str, Any]:
    trade_date = str(source_row["date"])
    code = str(source_row["code"]).zfill(6)
    market = str(source_row.get("market") or "")
    open_price = int_price(source_row.get("open"))
    high = int_price(source_row.get("high"))
    low = int_price(source_row.get("low"))
    close = int_price(source_row.get("close"))
    volume = int_price(source_row.get("volume"))
    amount = int_price(source_row.get("amount"))
    marcap = int_price(source_row.get("marcap"))
    stocks = int_price(source_row.get("stocks"))
    history = state["history"]
    previous = history[-1] if history else None
    prev_date = previous["date"] if previous else None
    prev_close = previous["close"] if previous else None
    corp_warning = any(prev_date < item <= trade_date if prev_date else item == trade_date for item in corporate_dates)
    new_or_no_ref = previous is None

    if corp_warning:
        data_quality_status = "blocked_by_corporate_action"
        upper_status = "blocked_corporate_action"
    elif new_or_no_ref:
        data_quality_status = "blocked_no_reference"
        upper_status = "blocked_new_listing"
    elif market not in {"KOSPI", "KOSDAQ", "KOSDAQ GLOBAL"}:
        data_quality_status = "blocked_unsupported_market_rule"
        upper_status = "unsupported_market_rule"
    else:
        data_quality_status = "clean"
        upper_status = "verified_normal_day"

    returns_allowed = data_quality_status == "clean"
    open_gap_pct = pct(open_price, prev_close) if returns_allowed else None
    high_return_pct = pct(high, prev_close) if returns_allowed else None
    low_return_pct = pct(low, prev_close) if returns_allowed else None
    close_return_pct = pct(close, prev_close) if returns_allowed else None
    turnover_pct = ratio_pct(volume, stocks)
    trailing_returns: dict[int, float | None] = {}
    for window in [3, 5, 10, 20]:
        ref_close = history[-window]["close"] if len(history) >= window else None
        trailing_returns[window] = pct(close, ref_close) if returns_allowed else None

    limit_up_price = None
    upper_limit_touched = None
    upper_limit_closed = None
    upper_limit_released = None
    one_price_upper_limit = None
    if upper_status == "verified_normal_day":
        limit_up_price = compute_limit_up_price(prev_close, trade_date, market)
        if limit_up_price is None:
            upper_status = "unsupported_market_rule"
            data_quality_status = "blocked_unsupported_market_rule"
        else:
            upper_limit_touched = high == limit_up_price
            upper_limit_closed = close == limit_up_price
            upper_limit_released = upper_limit_touched and not upper_limit_closed
            one_price_upper_limit = open_price == high == low == close == limit_up_price

    current_recent = {
        "upper_limit_touched": upper_limit_touched,
        "upper_limit_closed": upper_limit_closed,
        "high_return_pct": high_return_pct,
    }
    recent_window = list(state["recent"]) + [current_recent]
    touch_count_5d = sum(1 for item in recent_window if item.get("upper_limit_touched") is True)
    close_count_5d = sum(1 for item in recent_window if item.get("upper_limit_closed") is True)
    high_ge_10_5d = sum(1 for item in recent_window if item.get("high_return_pct") is not None and item["high_return_pct"] >= 10)
    high_ge_20_5d = sum(1 for item in recent_window if item.get("high_return_pct") is not None and item["high_return_pct"] >= 20)

    return {
        "snapshot_date": trade_date,
        "previous_market_trade_date": market_previous_date,
        "code": code,
        "name": source_row.get("resolved_name"),
        "name_resolution_status": source_row.get("name_resolution_status"),
        "name_candidates": source_row.get("name_candidates"),
        "market": market,
        "prev_symbol_trade_date": prev_date,
        "days_since_prev_symbol_trade": days_between(prev_date, trade_date),
        "prev_close": prev_close,
        "open": open_price,
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
        "amount": amount,
        "market_cap": marcap,
        "listed_shares": stocks,
        "open_gap_pct": open_gap_pct,
        "high_return_pct": high_return_pct,
        "low_return_pct": low_return_pct,
        "close_return_pct": close_return_pct,
        "turnover_pct": turnover_pct,
        "return_3d_pct": trailing_returns[3],
        "return_5d_pct": trailing_returns[5],
        "return_10d_pct": trailing_returns[10],
        "return_20d_pct": trailing_returns[20],
        "amount_rank": None,
        "turnover_rank": None,
        "market_cap_rank": None,
        "high_return_rank": None,
        "close_return_rank": None,
        "limit_up_price": limit_up_price,
        "upper_limit_touched": upper_limit_touched,
        "upper_limit_closed": upper_limit_closed,
        "upper_limit_released": upper_limit_released,
        "one_price_upper_limit": one_price_upper_limit,
        "upper_limit_label_status": upper_status,
        "upper_limit_touch_count_5d": touch_count_5d,
        "upper_limit_close_count_5d": close_count_5d,
        "high_return_ge_10_count_5d": high_ge_10_5d,
        "high_return_ge_20_count_5d": high_ge_20_5d,
        "corporate_action_warning": corp_warning,
        "new_listing_or_no_reference": new_or_no_ref,
        "data_quality_status": data_quality_status,
        "max_source_date": trade_date,
    }


def update_symbol_state(source_row: dict[str, Any], state: dict[str, Any], snapshot_row: dict[str, Any]) -> None:
    state["history"].append(
        {
            "date": str(source_row["date"]),
            "close": int_price(source_row.get("close")),
        }
    )
    state["recent"].append(
        {
            "upper_limit_touched": snapshot_row.get("upper_limit_touched"),
            "upper_limit_closed": snapshot_row.get("upper_limit_closed"),
            "high_return_pct": snapshot_row.get("high_return_pct"),
        }
    )


def write_snapshot(path: Path, rows: list[dict[str, Any]], overwrite: bool) -> dict[str, Any]:
    final_rows = finalize_snapshot_rows(rows)
    text = rows_to_csv_text(final_rows, SNAPSHOT_COLUMNS)
    changed = atomic_write_text(ROOT / path, text, overwrite=overwrite)
    abs_path = ROOT / path
    return {
        "path": str(path).replace("\\", "/"),
        "row_count": len(final_rows),
        "bytes": abs_path.stat().st_size,
        "sha256": sha256_file(abs_path),
        "changed": changed,
    }


def build_schema(markets: list[str]) -> dict[str, Any]:
    return {
        "schema_version": "stock_web.research_daily_snapshot.v1",
        "encoding": "UTF-8, LF, no BOM",
        "canonical_format": "plain CSV",
        "markets": markets,
        "columns": SNAPSHOT_COLUMNS,
        "column_types": {
            "snapshot_date": "date YYYY-MM-DD",
            "previous_market_trade_date": "date YYYY-MM-DD or null",
            "code": "zero-padded 6 character string",
            "name": "string or null",
            "name_resolution_status": "exact_source_row|unique_history_match|ambiguous_history_match|unresolved",
            "name_candidates": "pipe-separated string or null",
            "market": "KOSPI|KOSDAQ|KOSDAQ GLOBAL by default",
            "prev_symbol_trade_date": "date YYYY-MM-DD or null",
            "days_since_prev_symbol_trade": "integer calendar days or null",
            "prev_close": "KRW integer or null",
            "open": "KRW integer",
            "high": "KRW integer",
            "low": "KRW integer",
            "close": "KRW integer",
            "volume": "integer shares",
            "amount": "KRW integer",
            "market_cap": "KRW integer",
            "listed_shares": "integer shares",
            "open_gap_pct": "percent, max 6 decimals, null when reference is blocked",
            "high_return_pct": "percent, max 6 decimals, null when reference is blocked",
            "low_return_pct": "percent, max 6 decimals, null when reference is blocked",
            "close_return_pct": "percent, max 6 decimals, null when reference is blocked",
            "turnover_pct": "volume / listed_shares * 100",
            "return_3d_pct": "current close / close 3 symbol tradable rows earlier - 1",
            "return_5d_pct": "current close / close 5 symbol tradable rows earlier - 1",
            "return_10d_pct": "current close / close 10 symbol tradable rows earlier - 1",
            "return_20d_pct": "current close / close 20 symbol tradable rows earlier - 1",
            "amount_rank": "ordinal rank among snapshot rows, descending amount, code tie-break",
            "turnover_rank": "ordinal rank among snapshot rows, descending turnover_pct, code tie-break",
            "market_cap_rank": "ordinal rank among snapshot rows, descending market_cap, code tie-break",
            "high_return_rank": "ordinal rank among snapshot rows, descending high_return_pct, code tie-break",
            "close_return_rank": "ordinal rank among snapshot rows, descending close_return_pct, code tie-break",
            "limit_up_price": "KRW integer or null",
            "upper_limit_touched": "true|false|null",
            "upper_limit_closed": "true|false|null",
            "upper_limit_released": "true|false|null",
            "one_price_upper_limit": "true|false|null",
            "upper_limit_label_status": "verified_normal_day|blocked_corporate_action|blocked_new_listing|blocked_no_reference_price|blocked_ambiguous_reference|unsupported_market_rule",
            "upper_limit_touch_count_5d": "integer count over latest 5 symbol tradable rows including snapshot date",
            "upper_limit_close_count_5d": "integer count over latest 5 symbol tradable rows including snapshot date",
            "high_return_ge_10_count_5d": "integer count over latest 5 symbol tradable rows including snapshot date",
            "high_return_ge_20_count_5d": "integer count over latest 5 symbol tradable rows including snapshot date",
            "corporate_action_warning": "true when a candidate discontinuity affects current reference interval",
            "new_listing_or_no_reference": "true when no previous symbol tradable row exists",
            "data_quality_status": "clean|usable_with_caveat|blocked_by_corporate_action|blocked_no_reference|blocked_invalid_ohlc|blocked_unsupported_market_rule",
            "max_source_date": "latest source date used by this row, equal to snapshot_date",
        },
        "null_policy": "Unknown values are empty CSV fields. Numeric zero is never used as null.",
        "rank_policy": "Ranks are deterministic ordinal ranks starting at 1; null values receive no rank; ties sort by code ascending.",
        "name_resolution_policy": [
            "Use FinanceData/marcap Name from the source row first.",
            "If missing, use atlas/universe/name_history.csv only when exactly one historical name covers snapshot_date.",
            "Ambiguous historical names leave name empty and list candidates.",
            "current_or_latest_name is never backfilled into historical snapshots.",
        ],
        "limit_up_policy": [
            "KOSPI/KOSDAQ daily price limit is modeled as +30% from previous symbol close for 2016+ normal days.",
            "The gateway floors the +30% candidate to the applicable KRX-style tick size for the trade date and market.",
            "Tick size tables distinguish pre-2023-01-25 and post-2023-01-25 stock tick regimes.",
            "Corporate-action and new-listing/reference-ambiguous rows block the label instead of returning false.",
        ],
        "corporate_action_policy": "If atlas/corporate_actions/corporate_action_candidates.csv has a candidate in the previous-symbol-date to snapshot-date interval, return fields are blocked and corporate_action_warning=true.",
        "data_quality_policy": "Snapshots include only tradable atlas rows: volume>0, positive OHLC, and OHLC consistency. Zero-volume and zero-OHLC rows are excluded before snapshot generation.",
        "price_data_source": SOURCE_NAME,
        "source_repo_url": SOURCE_REPO_URL,
        "price_adjustment_status": PRICE_ADJUSTMENT_STATUS,
        "caveat": CAVEAT,
    }


def write_readmes() -> None:
    readme = """# Research Daily Atlas

Date-centered plain-text snapshots for GPT historical market research.

Each trading date has one immutable market snapshot under `snapshots/YYYY/MM/YYYYMMDD.csv`.
The matching access manifest under `access/YYYY/MM/YYYYMMDD.json` tells a researcher which file is safe before prediction sealing and which file is the outcome file.

Example for trade date `2026-06-22`:

- BLIND before sealing: `atlas/research_daily/snapshots/2026/06/20260619.csv`
- POSTMORTEM after sealing: `atlas/research_daily/snapshots/2026/06/20260622.csv`

Default markets are KOSPI, KOSDAQ, and KOSDAQ GLOBAL. KONEX is excluded unless the builder is run with `--include-konex`.

## Build

```bash
python scripts/build_research_daily.py --start-date 2016-01-01 --validate
python scripts/build_research_daily.py --incremental --validate
python scripts/validate_research_daily.py --full
```

The canonical GPT files are plain CSV and JSON. No parquet, zip, gzip, or Git LFS is required to read this layer.
"""
    llm = """# Research Daily LLM Usage Guide

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
"""
    atomic_write_text(RESEARCH_DAILY_ROOT / "README.md", readme)
    atomic_write_text(RESEARCH_DAILY_ROOT / "README_LLM.md", llm)


def write_access_and_calendar(
    research_dates: list[str],
    seed_date: str | None,
    snapshot_meta: dict[str, dict[str, Any]],
    source_manifest_sha256: str,
    overwrite: bool,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    calendar_rows: list[dict[str, Any]] = []
    access_changed = 0
    all_snapshot_dates = ([seed_date] if seed_date else []) + research_dates
    previous_by_date = {trade_date: all_snapshot_dates[index - 1] if index > 0 else "" for index, trade_date in enumerate(all_snapshot_dates)}
    for index, trade_date in enumerate(research_dates):
        previous_trade_date = previous_by_date[trade_date]
        next_trade_date = research_dates[index + 1] if index + 1 < len(research_dates) else ""
        blind_meta = snapshot_meta[previous_trade_date]
        outcome_meta = snapshot_meta[trade_date]
        access_rel = research_access_path(trade_date)
        payload = {
            "schema_version": "stock_web.research_daily_access.v1",
            "trade_date": trade_date,
            "previous_trade_date": previous_trade_date,
            "next_trade_date": next_trade_date,
            "blind_snapshot_date": previous_trade_date,
            "blind_snapshot_path": blind_meta["path"],
            "outcome_snapshot_date": trade_date,
            "outcome_snapshot_path": outcome_meta["path"],
            "blind_snapshot_sha256": blind_meta["sha256"],
            "outcome_snapshot_sha256": outcome_meta["sha256"],
            "blind_snapshot_row_count": blind_meta["row_count"],
            "outcome_snapshot_row_count": outcome_meta["row_count"],
            "blind_snapshot_bytes": blind_meta["bytes"],
            "outcome_snapshot_bytes": outcome_meta["bytes"],
            "blind_max_source_date": previous_trade_date,
            "outcome_max_source_date": trade_date,
            "source_manifest_sha256": source_manifest_sha256,
            "build_status": "complete",
        }
        if atomic_write_json(ROOT / access_rel, payload, overwrite=overwrite):
            access_changed += 1
        calendar_rows.append(
            {
                "trade_date": trade_date,
                "previous_trade_date": previous_trade_date,
                "next_trade_date": next_trade_date,
                "blind_snapshot_date": previous_trade_date,
                "blind_snapshot_path": blind_meta["path"],
                "outcome_snapshot_date": trade_date,
                "outcome_snapshot_path": outcome_meta["path"],
                "access_manifest_path": str(access_rel).replace("\\", "/"),
                "blind_snapshot_sha256": blind_meta["sha256"],
                "outcome_snapshot_sha256": outcome_meta["sha256"],
                "blind_snapshot_row_count": blind_meta["row_count"],
                "outcome_snapshot_row_count": outcome_meta["row_count"],
                "blind_snapshot_bytes": blind_meta["bytes"],
                "outcome_snapshot_bytes": outcome_meta["bytes"],
                "blind_max_source_date": previous_trade_date,
                "outcome_max_source_date": trade_date,
                "source_manifest_sha256": source_manifest_sha256,
                "build_status": "complete",
            }
        )
    atomic_write_text(ROOT / CALENDAR_PATH, rows_to_csv_text(calendar_rows, CALENDAR_COLUMNS), overwrite=overwrite)
    return calendar_rows, {"access_changed": access_changed}


def build_research_daily(args: argparse.Namespace) -> dict[str, Any]:
    atlas_manifest = load_atlas_manifest()
    start_date = args.start_date
    end_date = args.end_date or atlas_manifest["max_date"]
    markets = parse_markets(args.markets, include_konex=args.include_konex)
    source_start_year = int(start_date[:4]) - 1
    source_end_year = int(end_date[:4])
    source_files = source_files_for_range(SOURCE_REPO_PATH, source_start_year, source_end_year)
    if not source_files:
        raise FileNotFoundError(f"no FinanceData/marcap source files found under {SOURCE_REPO_PATH / 'data'}")

    if args.overwrite and RESEARCH_DAILY_ROOT.exists():
        shutil.rmtree(RESEARCH_DAILY_ROOT)

    source_manifest_path = ATLAS_ROOT / "source_manifest.json"
    source_manifest_sha256 = sha256_file(source_manifest_path) if source_manifest_path.exists() else ""
    research_dates, seed_date = collect_market_trade_dates(source_files, markets, start_date, end_date)
    snapshot_dates = set(research_dates)
    if seed_date:
        snapshot_dates.add(seed_date)

    name_history = load_name_history(ATLAS_ROOT)
    corp_dates = load_corporate_action_dates(ATLAS_ROOT)
    states: dict[str, dict[str, Any]] = defaultdict(state_seed)
    snapshot_meta: dict[str, dict[str, Any]] = {}
    counters = {
        "total_snapshot_rows": 0,
        "ambiguous_name_count": 0,
        "unresolved_name_count": 0,
        "corporate_action_blocked_rows": 0,
        "new_listing_blocked_rows": 0,
        "unsupported_limit_rows": 0,
        "upper_limit_verified_rows": 0,
        "upper_limit_touched_rows": 0,
        "upper_limit_closed_rows": 0,
        "one_price_upper_limit_rows": 0,
        "snapshot_changed": 0,
    }
    previous_market_date: str | None = None

    print(
        f"building research_daily start={start_date} end={end_date} seed={seed_date or ''} "
        f"research_trade_dates={len(research_dates)} markets={','.join(markets)}"
    )

    for path in source_files:
        frame = prepare_daily_source_frame(read_marcap_file(path), markets)
        frame = frame[frame["date"] <= end_date].copy()
        if frame.empty:
            continue
        has_source_name = frame["name"].notna() & frame["name"].astype("string").ne("")
        frame["resolved_name"] = frame["name"].where(has_source_name, None)
        frame["name_resolution_status"] = "exact_source_row"
        frame.loc[~has_source_name, "name_resolution_status"] = None
        frame["name_candidates"] = None
        for index, row in frame.loc[~has_source_name].iterrows():
            name, status, candidates = resolve_source_name(row.get("name"), row["code"], row["date"], name_history)
            frame.at[index, "resolved_name"] = name
            frame.at[index, "name_resolution_status"] = status
            frame.at[index, "name_candidates"] = candidates
        print(f"snapshot pass {path.name}: tradable_market_rows={len(frame)}")
        for trade_date, group in frame.groupby("date", sort=True):
            trade_date = str(trade_date)
            rows: list[dict[str, Any]] = []
            for source_row in group.to_dict("records"):
                code = str(source_row["code"]).zfill(6)
                state = states[code]
                snapshot_row = build_snapshot_row(source_row, state, previous_market_date, corp_dates.get(code, set()))
                if trade_date in snapshot_dates:
                    rows.append(snapshot_row)
                update_symbol_state(source_row, state, snapshot_row)
            if trade_date in snapshot_dates:
                meta = write_snapshot(research_snapshot_path(trade_date), rows, overwrite=args.overwrite)
                snapshot_meta[trade_date] = meta
                counters["snapshot_changed"] += int(meta["changed"])
                counters["total_snapshot_rows"] += meta["row_count"]
                for row in rows:
                    counters["ambiguous_name_count"] += int(row["name_resolution_status"] == "ambiguous_history_match")
                    counters["unresolved_name_count"] += int(row["name_resolution_status"] == "unresolved")
                    counters["corporate_action_blocked_rows"] += int(row["data_quality_status"] == "blocked_by_corporate_action")
                    counters["new_listing_blocked_rows"] += int(row["data_quality_status"] == "blocked_no_reference")
                    counters["unsupported_limit_rows"] += int(row["upper_limit_label_status"] == "unsupported_market_rule")
                    counters["upper_limit_verified_rows"] += int(row["upper_limit_label_status"] == "verified_normal_day")
                    counters["upper_limit_touched_rows"] += int(row["upper_limit_touched"] is True)
                    counters["upper_limit_closed_rows"] += int(row["upper_limit_closed"] is True)
                    counters["one_price_upper_limit_rows"] += int(row["one_price_upper_limit"] is True)
            previous_market_date = trade_date

    missing_snapshots = sorted(snapshot_dates - set(snapshot_meta))
    if missing_snapshots:
        raise RuntimeError(f"snapshot generation missed dates: {missing_snapshots[:10]}")

    calendar_rows, access_stats = write_access_and_calendar(research_dates, seed_date, snapshot_meta, source_manifest_sha256, args.overwrite)
    schema = build_schema(markets)
    atomic_write_json(RESEARCH_DAILY_ROOT / "schema.json", schema, overwrite=args.overwrite)
    write_readmes()

    size_files = [path for path in RESEARCH_DAILY_ROOT.rglob("*") if path.is_file()]
    total_size = sum(path.stat().st_size for path in size_files)
    largest_file = max(size_files, key=lambda item: item.stat().st_size) if size_files else None
    manifest = {
        "research_daily_version": RESEARCH_DAILY_VERSION,
        "generated_at": utc_now(),
        "source_atlas_version": atlas_manifest.get("atlas_version"),
        "source_atlas_generated_at": atlas_manifest.get("generated_at"),
        "source_manifest_sha256": source_manifest_sha256,
        "source_name": SOURCE_NAME,
        "source_repo_url": SOURCE_REPO_URL,
        "source_commit_hash": git_commit_hash(SOURCE_REPO_PATH),
        "price_adjustment_status": PRICE_ADJUSTMENT_STATUS,
        "research_start_date": start_date,
        "first_research_trade_date": research_dates[0],
        "seed_snapshot_date": seed_date,
        "max_trade_date": research_dates[-1],
        "markets": markets,
        "snapshot_count": len(snapshot_meta),
        "access_manifest_count": len(research_dates),
        "total_snapshot_rows": counters["total_snapshot_rows"],
        "snapshot_root": str(SNAPSHOT_ROOT).replace("\\", "/"),
        "access_root": str(ACCESS_ROOT).replace("\\", "/"),
        "calendar_path": str(CALENDAR_PATH).replace("\\", "/"),
        "schema_path": "atlas/research_daily/schema.json",
        "full_backfill_complete": True,
        "validation_passed": False,
        "counters": counters,
        "total_size_bytes": total_size,
        "total_size_mb": round(total_size / 1024 / 1024, 2),
        "largest_file_path": rel_path(largest_file, ROOT) if largest_file else "",
        "largest_file_bytes": largest_file.stat().st_size if largest_file else 0,
        "caveat": CAVEAT,
    }
    manifest = preserve_generated_at_if_stable(
        RESEARCH_DAILY_ROOT / "manifest.json",
        manifest,
        [
            "source_manifest_sha256",
            "research_start_date",
            "first_research_trade_date",
            "seed_snapshot_date",
            "max_trade_date",
            "markets",
            "snapshot_count",
            "access_manifest_count",
            "total_snapshot_rows",
        ],
    )
    atomic_write_json(RESEARCH_DAILY_ROOT / "manifest.json", manifest, overwrite=args.overwrite)

    atlas_manifest["research_daily"] = {
        "root": "atlas/research_daily",
        "manifest_path": "atlas/research_daily/manifest.json",
        "calendar_path": "atlas/research_daily/trading_calendar.csv",
        "first_research_trade_date": research_dates[0],
        "max_trade_date": research_dates[-1],
        "snapshot_count": len(snapshot_meta),
        "access_manifest_count": len(research_dates),
        "validation_passed": False,
    }
    atomic_write_json(ATLAS_ROOT / "manifest.json", atlas_manifest)

    build_report = {
        "generated_at": utc_now(),
        "start_date": start_date,
        "end_date": end_date,
        "seed_snapshot_date": seed_date,
        "trade_date_count": len(research_dates),
        "snapshot_count": len(snapshot_meta),
        "access_manifest_count": len(research_dates),
        "total_snapshot_rows": counters["total_snapshot_rows"],
        "total_size_bytes": total_size,
        "total_size_mb": round(total_size / 1024 / 1024, 2),
        "largest_file_path": manifest["largest_file_path"],
        "largest_file_bytes": manifest["largest_file_bytes"],
        "ambiguous_name_count": counters["ambiguous_name_count"],
        "unresolved_name_count": counters["unresolved_name_count"],
        "corporate_action_blocked_rows": counters["corporate_action_blocked_rows"],
        "upper_limit_verified_rows": counters["upper_limit_verified_rows"],
        "upper_limit_blocked_rows": counters["corporate_action_blocked_rows"] + counters["new_listing_blocked_rows"] + counters["unsupported_limit_rows"],
        "missing_dates": [],
        "missing_files": [],
        "validation_failures": [],
        "snapshot_changed": counters["snapshot_changed"],
        "access_changed": access_stats["access_changed"],
    }
    DIAGNOSTICS_ROOT.mkdir(exist_ok=True)
    atomic_write_json(DIAGNOSTICS_ROOT / "research_daily_build_report.json", build_report)
    atomic_write_text(
        DIAGNOSTICS_ROOT / "research_daily_build_report.md",
        "\n".join(
            [
                "# Research Daily Build Report",
                "",
                f"- start_date: {start_date}",
                f"- end_date: {end_date}",
                f"- seed_snapshot_date: {seed_date or ''}",
                f"- trade_date_count: {len(research_dates)}",
                f"- snapshot_count: {len(snapshot_meta)}",
                f"- access_manifest_count: {len(research_dates)}",
                f"- total_snapshot_rows: {counters['total_snapshot_rows']}",
                f"- total_size_mb: {round(total_size / 1024 / 1024, 2)}",
                f"- largest_file_path: {manifest['largest_file_path']}",
                f"- largest_file_bytes: {manifest['largest_file_bytes']}",
                f"- ambiguous_name_count: {counters['ambiguous_name_count']}",
                f"- corporate_action_blocked_rows: {counters['corporate_action_blocked_rows']}",
                f"- upper_limit_verified_rows: {counters['upper_limit_verified_rows']}",
                f"- upper_limit_blocked_rows: {build_report['upper_limit_blocked_rows']}",
                f"- snapshot_changed: {counters['snapshot_changed']}",
                f"- access_changed: {access_stats['access_changed']}",
                "",
            ]
        ),
    )
    atomic_write_json(
        DIAGNOSTICS_ROOT / "research_daily_size_report.json",
        {
            "generated_at": utc_now(),
            "total_size_bytes": total_size,
            "total_size_mb": round(total_size / 1024 / 1024, 2),
            "file_count": len(size_files),
            "largest_file_path": manifest["largest_file_path"],
            "largest_file_bytes": manifest["largest_file_bytes"],
            "files_over_50_mib": [rel_path(path, ROOT) for path in size_files if path.stat().st_size > 50 * 1024 * 1024],
            "files_over_100_mib": [rel_path(path, ROOT) for path in size_files if path.stat().st_size > 100 * 1024 * 1024],
        },
    )

    if args.validate:
        from scripts.validate_research_daily import validate_research_daily

        validation = validate_research_daily(full=True)
        manifest["validation_passed"] = validation["ok"]
        atomic_write_json(RESEARCH_DAILY_ROOT / "manifest.json", manifest, overwrite=True)
        atlas_manifest["research_daily"]["validation_passed"] = validation["ok"]
        atomic_write_json(ATLAS_ROOT / "manifest.json", atlas_manifest, overwrite=True)
        if not validation["ok"]:
            raise SystemExit(1)

    print(json.dumps({**build_report, "manifest_path": "atlas/research_daily/manifest.json"}, ensure_ascii=False, indent=2))
    return manifest


def resolve_source_name(source_name: Any, code: str, snapshot_date: str, name_history: dict[str, list[dict[str, str]]]) -> tuple[str | None, str, str | None]:
    from scripts.research_daily_utils import resolve_name

    return resolve_name(source_name, code, snapshot_date, name_history)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build date-centered GPT research snapshots from the local marcap atlas/source cache.")
    parser.add_argument("--start-date", default=DEFAULT_RESEARCH_START_DATE)
    parser.add_argument("--end-date", default=None)
    parser.add_argument("--markets", default=None, help="Comma-separated markets. Default: KOSPI,KOSDAQ,KOSDAQ GLOBAL")
    parser.add_argument("--include-konex", action="store_true")
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--incremental", action="store_true", help="Skip rewriting unchanged files. This is the default behavior.")
    parser.add_argument("--resume", action="store_true", help="Accepted for resumable atomic builds; completed files are reused when unchanged.")
    parser.add_argument("--validate", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    build_research_daily(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
