from __future__ import annotations

import json
import shutil
import subprocess
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.atlas_utils import (
    ATLAS_VERSION,
    CAVEAT,
    PRICE_ADJUSTMENT_STATUS,
    SOURCE_NAME,
    SOURCE_REPO_URL,
    compute_path_summary,
    compute_trigger_backtest,
    data_files,
    file_year,
    load_symbol_rows,
    read_marcap_file,
    safe_json,
    utc_now,
    write_csv,
    write_json,
)
from scripts.build_research_pack import build_pack, write_pack_md

SOURCE_REPO_PATH = ROOT / ".cache" / "marcap"
ATLAS_ROOT = ROOT / "atlas"
DIAGNOSTICS_ROOT = ROOT / "diagnostics"
SMOKE_ITEMS = [
    ("005930", "2024-01-02"),
    ("000660", "2024-01-02"),
    ("298040", "2024-01-02"),
    ("267260", "2024-01-02"),
    ("086520", "2024-01-02"),
]
SAMPLE_CODES = [code for code, _ in SMOKE_ITEMS]


def clean_outputs() -> None:
    for path in [ATLAS_ROOT, DIAGNOSTICS_ROOT]:
        if path.exists():
            shutil.rmtree(path)
    (ATLAS_ROOT / "research_packs" / "custom").mkdir(parents=True, exist_ok=True)
    (ATLAS_ROOT / "research_packs" / "custom" / ".gitkeep").write_text("", encoding="utf-8")
    DIAGNOSTICS_ROOT.mkdir(parents=True, exist_ok=True)


def git_commit_hash(repo: Path) -> str:
    try:
        return subprocess.check_output(["git", "-C", str(repo), "rev-parse", "HEAD"], text=True).strip()
    except Exception:
        return ""


def update_minmax(current_min: str | None, current_max: str | None, first: str, last: str) -> tuple[str, str]:
    new_min = first if current_min is None or first < current_min else current_min
    new_max = last if current_max is None or last > current_max else current_max
    return new_min, new_max


def infer_status(last_date: str, global_max_date: str) -> str:
    return "active_like" if last_date == global_max_date else "inactive_or_delisted_like"


def prepare_year_frame(frame: pd.DataFrame) -> tuple[pd.DataFrame, int, int, int]:
    """Make one assistant-readable code/date row per trading day.

    FinanceData/marcap is the source of truth, but some source rows can contain
    duplicate code/date records or OHLC inconsistencies. The atlas must be easy
    for ChatGPT to consume as one daily price path, so the repair is explicit
    and recorded in the manifests.
    """
    frame = frame.copy()
    for column in ["open", "high", "low", "close", "volume", "amount", "marcap", "stocks"]:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")

    invalid_required_rows = int((frame["close"].isna() | frame["volume"].isna()).sum())
    if invalid_required_rows:
        frame = frame[frame["close"].notna() & frame["volume"].notna()].copy()

    high_before = frame["high"].copy()
    low_before = frame["low"].copy()
    repaired_high = frame[["high", "open", "close"]].max(axis=1, skipna=True)
    repaired_low = frame[["low", "open", "close"]].min(axis=1, skipna=True)
    repaired_rows = int(
        (
            (high_before.notna() & repaired_high.notna() & high_before.ne(repaired_high))
            | (low_before.notna() & repaired_low.notna() & low_before.ne(repaired_low))
            | (high_before.isna() & repaired_high.notna())
            | (low_before.isna() & repaired_low.notna())
        ).sum()
    )
    frame["high"] = repaired_high
    frame["low"] = repaired_low

    duplicate_rows = int(frame.duplicated(["code", "date"]).sum())
    sort_columns = [column for column in ["code", "date", "marcap", "amount", "volume", "name", "market"] if column in frame.columns]
    frame = (
        frame.sort_values(sort_columns, kind="mergesort", na_position="first")
        .drop_duplicates(["code", "date"], keep="last")
        .sort_values(["code", "date"], kind="mergesort")
        .reset_index(drop=True)
    )
    return frame, duplicate_rows, repaired_rows, invalid_required_rows


def build_atlas() -> dict[str, Any]:
    clean_outputs()
    files = data_files(SOURCE_REPO_PATH)
    if not files:
        raise FileNotFoundError(f"no marcap data files under {SOURCE_REPO_PATH / 'data'}")

    profiles: dict[str, dict[str, Any]] = {}
    name_groups: dict[tuple[str, str], list[Any]] = defaultdict(lambda: [None, None])
    market_groups: dict[tuple[str, str], list[Any]] = defaultdict(lambda: [None, None])
    source_files = []
    market_coverage = []
    yearly_counts: dict[int, int] = {}
    global_min_date: str | None = None
    global_max_date: str | None = None
    total_rows = 0
    source_raw_row_count = 0
    duplicate_code_date_rows_dropped = 0
    ohlc_consistency_repaired_rows = 0
    invalid_required_ohlcv_rows_dropped = 0

    for path in files:
        year = file_year(path)
        frame = read_marcap_file(path)
        source_raw_row_count += len(frame)
        frame, duplicate_rows, repaired_rows, invalid_required_rows = prepare_year_frame(frame)
        duplicate_code_date_rows_dropped += duplicate_rows
        ohlc_consistency_repaired_rows += repaired_rows
        invalid_required_ohlcv_rows_dropped += invalid_required_rows
        row_count = len(frame)
        yearly_counts[year] = row_count
        total_rows += row_count
        year_min = str(frame["date"].min())
        year_max = str(frame["date"].max())
        global_min_date, global_max_date = update_minmax(global_min_date, global_max_date, year_min, year_max)
        source_files.append(str(path.relative_to(SOURCE_REPO_PATH)))

        for market, group in frame.groupby("market", dropna=False):
            market_coverage.append(
                {
                    "year": year,
                    "market": "" if pd.isna(market) else market,
                    "row_count": len(group),
                    "symbol_count": group["code"].nunique(),
                    "first_date": group["date"].min(),
                    "last_date": group["date"].max(),
                }
            )

        name_agg = frame.groupby(["code", "name"], dropna=False)["date"].agg(["min", "max"]).reset_index()
        for row in name_agg.to_dict("records"):
            key = (row["code"], "" if pd.isna(row["name"]) else row["name"])
            first, last = name_groups[key]
            name_groups[key] = [row["min"] if first is None or row["min"] < first else first, row["max"] if last is None or row["max"] > last else last]

        market_agg = frame.groupby(["code", "market"], dropna=False)["date"].agg(["min", "max"]).reset_index()
        for row in market_agg.to_dict("records"):
            key = (row["code"], "" if pd.isna(row["market"]) else row["market"])
            first, last = market_groups[key]
            market_groups[key] = [row["min"] if first is None or row["min"] < first else first, row["max"] if last is None or row["max"] > last else last]

        for code, group in frame.groupby("code", sort=True):
            prefix = code[:3]
            out_path = ATLAS_ROOT / "ohlcv_min_by_symbol_year" / prefix / code / f"{year}.csv"
            mini = group[["date", "open", "high", "low", "close", "volume", "amount", "marcap", "stocks", "market"]].rename(
                columns={"date": "d", "open": "o", "high": "h", "low": "l", "close": "c", "volume": "v", "amount": "a", "marcap": "mc", "stocks": "s", "market": "m"}
            )
            out_path.parent.mkdir(parents=True, exist_ok=True)
            mini.to_csv(out_path, index=False, na_rep="")

            last_row = group.iloc[-1]
            first_date = str(group["date"].min())
            last_date = str(group["date"].max())
            profile = profiles.setdefault(
                code,
                {
                    "code": code,
                    "current_or_latest_name": None,
                    "markets": set(),
                    "first_date": None,
                    "last_date": None,
                    "trading_day_count": 0,
                    "available_years": set(),
                    "year_files": [],
                    "latest_close": None,
                    "latest_marcap": None,
                    "latest_market": None,
                },
            )
            profile["first_date"], profile["last_date"] = update_minmax(profile["first_date"], profile["last_date"], first_date, last_date)
            profile["trading_day_count"] += len(group)
            profile["available_years"].add(year)
            profile["year_files"].append(str(out_path.relative_to(ROOT)))
            markets = [market for market in group["market"].dropna().unique().tolist() if market != ""]
            profile["markets"].update(markets)
            if last_date >= (profile.get("_latest_seen_date") or ""):
                profile["_latest_seen_date"] = last_date
                profile["current_or_latest_name"] = last_row.get("name")
                profile["latest_close"] = last_row.get("close")
                profile["latest_marcap"] = last_row.get("marcap")
                profile["latest_market"] = last_row.get("market")

        print(
            f"loaded {path.name}: raw_rows={row_count + duplicate_rows + invalid_required_rows} "
            f"atlas_rows={row_count} duplicate_rows_dropped={duplicate_rows} "
            f"invalid_required_ohlcv_rows_dropped={invalid_required_rows} ohlc_repaired_rows={repaired_rows}"
        )

    assert global_min_date is not None and global_max_date is not None

    name_history_by_code: dict[str, list[dict[str, str]]] = defaultdict(list)
    for (code, name), (first, last) in sorted(name_groups.items()):
        name_history_by_code[code].append({"name": name, "first_date": first, "last_date": last})

    market_history_by_code: dict[str, list[dict[str, str]]] = defaultdict(list)
    for (code, market), (first, last) in sorted(market_groups.items()):
        market_history_by_code[code].append({"market": market, "first_date": first, "last_date": last})

    all_symbol_rows = []
    name_history_rows = []
    symbol_span_rows = []
    by_prefix: dict[str, list[dict[str, Any]]] = {f"{i:03d}": [] for i in range(1000)}
    by_market_rows: dict[str, list[dict[str, Any]]] = defaultdict(list)
    profile_count = 0

    for code in sorted(profiles):
        raw = profiles[code]
        prefix = code[:3]
        available_years = sorted(raw["available_years"])
        status = infer_status(raw["last_date"], global_max_date)
        profile_path = f"atlas/symbol_profiles/{prefix}/{code}.json"
        profile = {
            "code": code,
            "current_or_latest_name": raw["current_or_latest_name"],
            "name_history": name_history_by_code[code],
            "markets": sorted(raw["markets"]),
            "market_history": market_history_by_code[code],
            "first_date": raw["first_date"],
            "last_date": raw["last_date"],
            "trading_day_count": raw["trading_day_count"],
            "available_years": available_years,
            "year_files": sorted(raw["year_files"]),
            "latest_close": raw["latest_close"],
            "latest_marcap": raw["latest_marcap"],
            "latest_market": raw["latest_market"],
            "status_inferred": status,
            "status_inference_note": "Inferred only from FinanceData/marcap row presence, not an official listing/delisting status.",
            "price_data_source": SOURCE_NAME,
            "source_repo_url": SOURCE_REPO_URL,
            "price_adjustment_status": PRICE_ADJUSTMENT_STATUS,
            "caveat": CAVEAT,
        }
        write_json(ROOT / profile_path, profile)
        profile_count += 1
        all_row = {
            "code": code,
            "current_or_latest_name": raw["current_or_latest_name"],
            "first_date": raw["first_date"],
            "last_date": raw["last_date"],
            "trading_day_count": raw["trading_day_count"],
            "markets": "|".join(sorted(raw["markets"])),
            "available_year_count": len(available_years),
            "latest_close": raw["latest_close"],
            "latest_marcap": raw["latest_marcap"],
            "status_inferred": status,
            "profile_path": profile_path,
        }
        all_symbol_rows.append(all_row)
        symbol_span_rows.append({**{key: all_row[key] for key in ["code", "current_or_latest_name", "first_date", "last_date", "trading_day_count", "profile_path"]}, "available_years": "|".join(map(str, available_years))})
        by_prefix[prefix].append(
            {
                "code": code,
                "name": raw["current_or_latest_name"],
                "profile_path": profile_path,
                "available_years": available_years,
                "first_date": raw["first_date"],
                "last_date": raw["last_date"],
                "status_inferred": status,
            }
        )
        for market in sorted(raw["markets"]):
            by_market_rows[market].append(
                {
                    "code": code,
                    "current_or_latest_name": raw["current_or_latest_name"],
                    "first_date": raw["first_date"],
                    "last_date": raw["last_date"],
                    "trading_day_count": raw["trading_day_count"],
                    "profile_path": profile_path,
                }
            )
        for item in name_history_by_code[code]:
            name_history_rows.append({"code": code, **item})

    current_rows = [row for row in all_symbol_rows if row["last_date"] == global_max_date]
    write_csv(ATLAS_ROOT / "universe" / "all_symbols.csv", all_symbol_rows, ["code", "current_or_latest_name", "first_date", "last_date", "trading_day_count", "markets", "available_year_count", "latest_close", "latest_marcap", "status_inferred", "profile_path"])
    write_csv(ATLAS_ROOT / "universe" / "current_symbols.csv", current_rows, ["code", "current_or_latest_name", "first_date", "last_date", "trading_day_count", "markets", "available_year_count", "latest_close", "latest_marcap", "status_inferred", "profile_path"])
    write_csv(ATLAS_ROOT / "universe" / "symbol_spans.csv", symbol_span_rows, ["code", "current_or_latest_name", "first_date", "last_date", "trading_day_count", "available_years", "profile_path"])
    write_csv(ATLAS_ROOT / "universe" / "name_history.csv", name_history_rows, ["code", "name", "first_date", "last_date"])
    write_csv(ATLAS_ROOT / "universe" / "market_coverage_by_year.csv", market_coverage, ["year", "market", "row_count", "symbol_count", "first_date", "last_date"])

    for prefix, codes in by_prefix.items():
        write_json(ATLAS_ROOT / "index" / "by_code_prefix" / f"{prefix}.json", {"prefix": prefix, "codes": codes})
    for market, rows in by_market_rows.items():
        write_csv(ATLAS_ROOT / "index" / "by_market" / f"{market or 'UNKNOWN'}.csv", rows, ["code", "current_or_latest_name", "first_date", "last_date", "trading_day_count", "profile_path"])
    name_search = [
        {
            "name": row["name"],
            "code": row["code"],
            "current_or_latest_name": profiles[row["code"]]["current_or_latest_name"],
            "first_date": row["first_date"],
            "last_date": row["last_date"],
            "markets": "|".join(sorted(profiles[row["code"]]["markets"])),
            "profile_path": f"atlas/symbol_profiles/{row['code'][:3]}/{row['code']}.json",
        }
        for row in name_history_rows
    ]
    write_csv(ATLAS_ROOT / "index" / "by_name" / "name_search.csv", name_search, ["name", "code", "current_or_latest_name", "first_date", "last_date", "markets", "profile_path"])

    markets = sorted({row["market"] for row in market_coverage if row["market"]})
    manifest = {
        "atlas_version": ATLAS_VERSION,
        "generated_at": utc_now(),
        "source_name": SOURCE_NAME,
        "source_repo_url": SOURCE_REPO_URL,
        "price_adjustment_status": PRICE_ADJUSTMENT_STATUS,
        "min_date": global_min_date,
        "max_date": global_max_date,
        "row_count": total_rows,
        "source_raw_row_count": source_raw_row_count,
        "duplicate_code_date_rows_dropped": duplicate_code_date_rows_dropped,
        "invalid_required_ohlcv_rows_dropped": invalid_required_ohlcv_rows_dropped,
        "ohlc_consistency_repair_applied": True,
        "ohlc_consistency_repaired_rows": ohlc_consistency_repaired_rows,
        "symbol_count": profile_count,
        "active_like_symbol_count": len(current_rows),
        "inactive_or_delisted_like_symbol_count": profile_count - len(current_rows),
        "markets": markets,
        "shard_type": "symbol_year_min_csv",
        "ohlcv_shard_root": "atlas/ohlcv_min_by_symbol_year",
        "schema_path": "atlas/schema.json",
        "universe_path": "atlas/universe/all_symbols.csv",
        "research_pack_generator": "scripts/build_research_pack.py",
        "full_ohlcv_atlas_committed_to_main": True,
        "full_ohlcv_atlas_branch": "",
        "data_branch_if_used": "",
        "notes": [
            "Raw/unadjusted OHLC. Corporate actions are not adjusted.",
            "Atlas enforces one code/date row and OHLC path consistency for browser-readable backtests.",
            "OHLC consistency repair: high=max(raw high, open, close), low=min(raw low, open, close).",
            "Rows missing close or volume are excluded because they are not usable 1D OHLCV rows.",
            "Original FinanceData/marcap data files are not committed directly.",
            "Assistant-readable files are compact text shards.",
            "Use generated research packs for E2R calibration whenever possible.",
        ],
    }
    write_json(ATLAS_ROOT / "manifest.json", manifest)
    write_json(
        ATLAS_ROOT / "source_manifest.json",
        {
            "source_name": SOURCE_NAME,
            "source_repo_url": SOURCE_REPO_URL,
            "source_commit_hash": git_commit_hash(SOURCE_REPO_PATH),
            "source_data_files_loaded": source_files,
            "loaded_years": sorted(yearly_counts),
            "generated_at": utc_now(),
            "row_count_by_year": yearly_counts,
            "min_date": global_min_date,
            "max_date": global_max_date,
            "source_raw_row_count": source_raw_row_count,
            "atlas_row_count": total_rows,
            "row_count": total_rows,
            "duplicate_code_date_rows_dropped": duplicate_code_date_rows_dropped,
            "invalid_required_ohlcv_rows_dropped": invalid_required_ohlcv_rows_dropped,
            "ohlc_consistency_repair_applied": True,
            "ohlc_consistency_repaired_rows": ohlc_consistency_repaired_rows,
            "ohlc_consistency_repair_rule": "high=max(raw high, open, close); low=min(raw low, open, close)",
        },
    )
    write_json(
        ATLAS_ROOT / "schema.json",
        {
            "shard_columns": {"d": "date", "o": "open", "h": "high", "l": "low", "c": "close", "v": "volume", "a": "amount", "mc": "marcap", "s": "stocks", "m": "market"},
            "price_adjustment_status": PRICE_ADJUSTMENT_STATUS,
            "caveat": CAVEAT,
            "normalization": [
                "Code is preserved as a zero-padded 6-digit string.",
                "Each shard has at most one row per code/date.",
                "Rows missing close or volume are excluded from OHLCV shards.",
                "If the source has duplicate code/date rows, the atlas keeps the deterministic row with the largest marcap/amount/volume sort order.",
                "OHLC consistency repair is applied: high=max(raw high, open, close), low=min(raw low, open, close).",
            ],
            "MFE_N_pct": "(max high from entry_date through N trading rows / entry_price - 1) * 100",
            "MAE_N_pct": "(min low from entry_date through N trading rows / entry_price - 1) * 100",
            "calibration_usable_rules": [
                "open/high/low/close/volume are present",
                "entry row exists",
                "at least 180 forward trading days are available",
                "MFE and MAE 30/90/180D are computed",
            ],
            "status_inferred_note": "active/inactive-like status is inferred only from row presence and is not official listing status.",
        },
    )
    return manifest


def write_readmes(manifest: dict[str, Any]) -> None:
    (ROOT / "README.md").write_text(
        f"""# stock-web

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

- Source: {SOURCE_NAME}
- Source repo: {SOURCE_REPO_URL}
- Price adjustment status: {PRICE_ADJUSTMENT_STATUS}
- Caveat: {CAVEAT}
- Atlas normalization: one row per code/date, with high/low consistency enforced for browser-readable backtests.

This is a collector-generated research data access layer, not investment advice.
""",
        encoding="utf-8",
    )
    (ATLAS_ROOT / "README.md").write_text(
        f"""# FinanceData/marcap OHLC Atlas

This atlas contains collector-generated Korean stock OHLC artifacts for E2R historical calibration.

Raw FinanceData/marcap files are not committed directly because they are source/cache artifacts. This repo commits assistant-readable plain text shards instead.

## How ChatGPT Should Read It

1. Open `atlas/manifest.json`.
2. For a stock code, use the first 3 digits as prefix.
3. Open `atlas/index/by_code_prefix/{{prefix}}.json`.
4. Open `atlas/symbol_profiles/{{prefix}}/{{code}}.json`.
5. Open `atlas/ohlcv_min_by_symbol_year/{{prefix}}/{{code}}/{{year}}.csv` only when raw OHLC rows are needed.
6. Prefer generated research pack JSON/MD for calibration.

## Example Paths

- `atlas/manifest.json`
- `atlas/index/by_code_prefix/005.json`
- `atlas/symbol_profiles/005/005930.json`
- `atlas/ohlcv_min_by_symbol_year/005/005930/2024.csv`
- `atlas/research_packs/smoke/smoke_005930_000660_298040_267260_086520.json`

## Caveat

- Source: {SOURCE_NAME}
- Source repo: {SOURCE_REPO_URL}
- Price adjustment status: {PRICE_ADJUSTMENT_STATUS}
- Raw/unadjusted OHLC. Corporate actions are not adjusted unless explicitly added later.
- Atlas normalization: one code/date row is kept, and high/low are repaired only when needed so high covers open/close and low covers open/close.

Use only `calibration_usable=true` rows for E2R calibration. Reject cases without 180 forward trading days. Do not use narrative-only rows for weight changes.
""",
        encoding="utf-8",
    )
    (ATLAS_ROOT / "research_packs" / "README.md").write_text("# Research Packs\n\nGenerated E2R-ready packs from atlas shards.\n", encoding="utf-8")


def write_diagnostics(manifest: dict[str, Any]) -> None:
    rows = load_symbol_rows(ATLAS_ROOT, "005930", "2024-01-01", "2024-12-31")
    sample_rows = rows[:5] + rows[-5:]
    trigger_rows = load_symbol_rows(ATLAS_ROOT, "005930")
    trigger = compute_trigger_backtest(trigger_rows, "2024-01-02")
    path = compute_path_summary(trigger_rows, trigger.get("entry_date") or "2024-01-03")
    lines = [
        "CHATGPT_MARCAP_BUNDLE",
        f"generated_at={utc_now()}",
        f"source_name={SOURCE_NAME}",
        f"source_repo_url={SOURCE_REPO_URL}",
        f"price_adjustment_status={PRICE_ADJUSTMENT_STATUS}",
        f"min_date={manifest['min_date']}",
        f"max_date={manifest['max_date']}",
        f"row_count={manifest['row_count']}",
        f"symbol_count={manifest['symbol_count']}",
        f"active_like_symbol_count={manifest['active_like_symbol_count']}",
        f"inactive_or_delisted_like_symbol_count={manifest['inactive_or_delisted_like_symbol_count']}",
    ]
    selftests = []
    for code, _trigger_date in SMOKE_ITEMS:
        symbol_rows = load_symbol_rows(ATLAS_ROOT, code)
        year_rows = load_symbol_rows(ATLAS_ROOT, code, "2024-01-01", "2024-12-31")
        t = compute_trigger_backtest(symbol_rows, "2024-01-02")
        p = compute_path_summary(symbol_rows, t.get("entry_date") or "2024-01-03")
        profile = json.loads((ATLAS_ROOT / "symbol_profiles" / code[:3] / f"{code}.json").read_text(encoding="utf-8"))
        d180 = any(point.get("trading_day_offset") == 180 and point.get("available") for point in p["points"])
        line = "|".join(
            [
                "SELFTEST",
                code,
                str(profile["current_or_latest_name"]),
                str(len(year_rows)),
                year_rows[0]["date"] if year_rows else "",
                year_rows[-1]["date"] if year_rows else "",
                "true" if year_rows else "false",
                "true" if t.get("calibration_usable") else "false",
                str(t.get("forward_window_trading_days")),
                str(t.get("MFE_30D_pct")),
                str(t.get("MFE_90D_pct")),
                str(t.get("MFE_180D_pct")),
                str(t.get("MAE_30D_pct")),
                str(t.get("MAE_90D_pct")),
                str(t.get("MAE_180D_pct")),
                "true" if d180 else "false",
                "ok",
                "; ".join(t.get("warnings", []) + p.get("warnings", [])),
            ]
        )
        lines.append(line)
        selftests.append(line)
    for row in sample_rows:
        lines.append("|".join(["OHLC_SAMPLE", "005930", str(row["date"]), str(row["open"]), str(row["high"]), str(row["low"]), str(row["close"]), str(row["volume"]), str(row["amount"]), str(row["marcap"]), str(row["market"])]))
    lines.append("|".join(["TRIGGER_SAMPLE", "005930", "2024-01-02", "next_trading_day_close", str(trigger.get("entry_date")), str(trigger.get("entry_price")), str(trigger.get("calibration_usable")).lower(), str(trigger.get("forward_window_trading_days")), str(trigger.get("MFE_30D_pct")), str(trigger.get("MFE_90D_pct")), str(trigger.get("MFE_180D_pct")), str(trigger.get("MAE_30D_pct")), str(trigger.get("MAE_90D_pct")), str(trigger.get("MAE_180D_pct")), str(trigger.get("peak_date")), str(trigger.get("peak_price")), str(trigger.get("drawdown_after_peak_pct") or "")]))
    for point in path["points"]:
        lines.append("|".join(["PATH_SAMPLE", "005930", str(path.get("entry_date")), str(point.get("label")), str(point.get("date")), str(point.get("close_return_pct")), str(point.get("high_to_date_return_pct")), str(point.get("low_to_date_return_pct")), str(point.get("available")).lower()]))
    text = "\n".join(lines) + "\n"
    (DIAGNOSTICS_ROOT / "chatgpt_bundle.txt").write_text(text, encoding="utf-8")
    (ATLAS_ROOT / "samples" / "chatgpt_bundle.txt").parent.mkdir(parents=True, exist_ok=True)
    (ATLAS_ROOT / "samples" / "chatgpt_bundle.txt").write_text(text, encoding="utf-8")
    bundle_json = {"manifest": manifest, "selftest_lines": selftests, "sample_005930_2024": sample_rows, "trigger_sample": trigger, "path_sample": path}
    write_json(DIAGNOSTICS_ROOT / "chatgpt_bundle.json", bundle_json)
    write_json(ATLAS_ROOT / "samples" / "chatgpt_bundle.json", bundle_json)


def size_report(manifest: dict[str, Any]) -> dict[str, Any]:
    files = [path for path in ATLAS_ROOT.rglob("*") if path.is_file()] + [path for path in DIAGNOSTICS_ROOT.rglob("*") if path.is_file()]
    total = sum(path.stat().st_size for path in files)
    largest = max((path.stat().st_size for path in files), default=0)
    report = {"atlas_total_size_mb": round(total / 1024 / 1024, 2), "largest_file_mb": round(largest / 1024 / 1024, 2), "file_count": len(files)}
    (DIAGNOSTICS_ROOT / "atlas_size_report.md").write_text(
        f"# Atlas Size Report\n\n- total_mb: {report['atlas_total_size_mb']}\n- largest_file_mb: {report['largest_file_mb']}\n- file_count: {report['file_count']}\n- row_count: {manifest['row_count']}\n- symbol_count: {manifest['symbol_count']}\n",
        encoding="utf-8",
    )
    return report


def main() -> int:
    manifest = build_atlas()
    write_readmes(manifest)
    smoke_json = ATLAS_ROOT / "research_packs" / "smoke" / "smoke_005930_000660_298040_267260_086520.json"
    smoke_md = smoke_json.with_suffix(".md")
    pack = build_pack(SMOKE_ITEMS, pack_id=smoke_json.stem)
    write_json(smoke_json, pack)
    write_pack_md(smoke_md, pack)
    write_json(ATLAS_ROOT / "samples" / "sample_research_pack.json", pack)
    write_pack_md(ATLAS_ROOT / "samples" / "sample_research_pack.md", pack)
    shutil.copyfile(ATLAS_ROOT / "ohlcv_min_by_symbol_year" / "005" / "005930" / "2024.csv", ATLAS_ROOT / "samples" / "sample_005930_2024.csv")
    write_diagnostics(manifest)
    report = size_report(manifest)
    full_in_main = report["atlas_total_size_mb"] <= 1536
    manifest["full_ohlcv_atlas_committed_to_main"] = full_in_main
    manifest["full_ohlcv_atlas_branch"] = "" if full_in_main else "price-atlas-data"
    manifest["data_branch_if_used"] = "" if full_in_main else "price-atlas-data"
    write_json(ATLAS_ROOT / "manifest.json", manifest)
    (DIAGNOSTICS_ROOT / "atlas_build_report.md").write_text(
        f"# Atlas Build Report\n\n- source_name: {SOURCE_NAME}\n- min_date: {manifest['min_date']}\n- max_date: {manifest['max_date']}\n- row_count: {manifest['row_count']}\n- source_raw_row_count: {manifest['source_raw_row_count']}\n- duplicate_code_date_rows_dropped: {manifest['duplicate_code_date_rows_dropped']}\n- invalid_required_ohlcv_rows_dropped: {manifest['invalid_required_ohlcv_rows_dropped']}\n- ohlc_consistency_repair_applied: {str(manifest['ohlc_consistency_repair_applied']).lower()}\n- ohlc_consistency_repaired_rows: {manifest['ohlc_consistency_repaired_rows']}\n- symbol_count: {manifest['symbol_count']}\n- active_like_symbol_count: {manifest['active_like_symbol_count']}\n- inactive_or_delisted_like_symbol_count: {manifest['inactive_or_delisted_like_symbol_count']}\n- atlas_total_size_mb: {report['atlas_total_size_mb']}\n- full_ohlcv_atlas_committed_to_main: {str(full_in_main).lower()}\n",
        encoding="utf-8",
    )
    print(json.dumps({**manifest, **report}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
