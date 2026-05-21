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
    ROW_STATUS_VALUES,
    SOURCE_NAME,
    SOURCE_REPO_URL,
    data_files,
    file_year,
    load_corporate_action_candidates,
    load_symbol_rows,
    read_marcap_file,
    utc_now,
    write_csv,
    write_json,
)
from scripts.build_research_pack import build_pack, write_pack_md

SOURCE_REPO_PATH = ROOT / ".cache" / "marcap"
ATLAS_ROOT = ROOT / "atlas"
DIAGNOSTICS_ROOT = ROOT / "diagnostics"
PUBLIC_ROOT = ROOT / "public"
SMOKE_ITEMS = [
    ("005930", "2024-01-02"),
    ("000660", "2024-01-02"),
    ("298040", "2024-01-02"),
    ("267260", "2024-01-02"),
    ("086520", "2024-01-02"),
]


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


def classify_frame(frame: pd.DataFrame) -> pd.Series:
    required = ["open", "high", "low", "close", "volume"]
    values = frame[required]
    status = pd.Series("invalid_ohlc_inconsistent", index=frame.index, dtype="object")
    missing = values.isna().any(axis=1)
    status.loc[missing] = "invalid_missing_ohlc"
    present = ~missing
    zero_volume = present & frame["volume"].eq(0)
    status.loc[zero_volume] = "non_tradable_zero_volume"
    positive_ohlc = (frame[["open", "high", "low", "close"]] > 0).all(axis=1)
    zero_ohlc = present & ~zero_volume & ~positive_ohlc
    status.loc[zero_ohlc] = "invalid_zero_ohlc"
    positive_volume = frame["volume"] > 0
    positive_all = present & positive_volume & positive_ohlc
    consistent = (
        frame["high"].ge(frame[["open", "close"]].max(axis=1))
        & frame["low"].le(frame[["open", "close"]].min(axis=1))
        & frame["high"].ge(frame["low"])
    )
    status.loc[positive_all & consistent] = "tradable_ohlcv"
    status.loc[positive_all & ~consistent] = "suspicious_ohlc_repaired_candidate"
    return status


def prepare_source_frame(frame: pd.DataFrame) -> pd.DataFrame:
    frame = frame.copy()
    for column in ["open", "high", "low", "close", "volume", "amount", "marcap", "stocks"]:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame["row_status"] = classify_frame(frame)
    return frame.sort_values(["code", "date"], kind="mergesort").reset_index(drop=True)


def tradable_dedup(frame: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    duplicate_rows = int(frame.duplicated(["code", "date"]).sum())
    sort_columns = [column for column in ["code", "date", "marcap", "amount", "volume", "name", "market"] if column in frame.columns]
    output = (
        frame.sort_values(sort_columns, kind="mergesort", na_position="first")
        .drop_duplicates(["code", "date"], keep="last")
        .sort_values(["code", "date"], kind="mergesort")
        .reset_index(drop=True)
    )
    return output, duplicate_rows


def write_ohlcv_frame(frame: pd.DataFrame, out_path: Path, include_status: bool) -> None:
    columns = ["date", "open", "high", "low", "close", "volume", "amount", "marcap", "stocks", "market"]
    rename = {
        "date": "d",
        "open": "o",
        "high": "h",
        "low": "l",
        "close": "c",
        "volume": "v",
        "amount": "a",
        "marcap": "mc",
        "stocks": "s",
        "market": "m",
    }
    if include_status:
        columns.append("row_status")
        rename["row_status"] = "rs"
    mini = frame[columns].rename(columns=rename)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    mini.to_csv(out_path, index=False, na_rep="")


def profile_seed(code: str) -> dict[str, Any]:
    return {
        "code": code,
        "current_or_latest_name": None,
        "markets": set(),
        "raw_first_date": None,
        "raw_last_date": None,
        "first_date": None,
        "last_date": None,
        "raw_trading_day_count": 0,
        "trading_day_count": 0,
        "raw_available_years": set(),
        "available_years": set(),
        "raw_year_files": [],
        "year_files": [],
        "compat_year_files": [],
        "latest_close": None,
        "latest_marcap": None,
        "latest_market": None,
        "raw_latest_close": None,
        "raw_latest_marcap": None,
        "raw_latest_market": None,
        "row_status_counts": {status: 0 for status in ROW_STATUS_VALUES},
    }


def update_raw_profile(profile: dict[str, Any], group: pd.DataFrame, year: int, path: Path) -> None:
    first_date = str(group["date"].min())
    last_date = str(group["date"].max())
    profile["raw_first_date"], profile["raw_last_date"] = update_minmax(profile["raw_first_date"], profile["raw_last_date"], first_date, last_date)
    profile["raw_trading_day_count"] += len(group)
    profile["raw_available_years"].add(year)
    profile["raw_year_files"].append(str(path.relative_to(ROOT)))
    for status, count in group["row_status"].value_counts().to_dict().items():
        profile["row_status_counts"][status] = profile["row_status_counts"].get(status, 0) + int(count)
    markets = [market for market in group["market"].dropna().unique().tolist() if market != ""]
    profile["markets"].update(markets)
    if last_date >= (profile.get("_raw_latest_seen_date") or ""):
        last_row = group.iloc[-1]
        profile["_raw_latest_seen_date"] = last_date
        profile["current_or_latest_name"] = last_row.get("name")
        profile["raw_latest_close"] = last_row.get("close")
        profile["raw_latest_marcap"] = last_row.get("marcap")
        profile["raw_latest_market"] = last_row.get("market")


def update_tradable_profile(profile: dict[str, Any], group: pd.DataFrame, year: int, tradable_path: Path, compat_path: Path) -> None:
    first_date = str(group["date"].min())
    last_date = str(group["date"].max())
    profile["first_date"], profile["last_date"] = update_minmax(profile["first_date"], profile["last_date"], first_date, last_date)
    profile["trading_day_count"] += len(group)
    profile["available_years"].add(year)
    profile["year_files"].append(str(tradable_path.relative_to(ROOT)))
    profile["compat_year_files"].append(str(compat_path.relative_to(ROOT)))
    markets = [market for market in group["market"].dropna().unique().tolist() if market != ""]
    profile["markets"].update(markets)
    if last_date >= (profile.get("_latest_seen_date") or ""):
        last_row = group.iloc[-1]
        profile["_latest_seen_date"] = last_date
        profile["latest_close"] = last_row.get("close")
        profile["latest_marcap"] = last_row.get("marcap")
        profile["latest_market"] = last_row.get("market")


def candidate_from_pair(previous: dict[str, Any], current: dict[str, Any]) -> dict[str, Any] | None:
    prev_close = float(previous["close"])
    close = float(current["close"])
    prev_stocks = float(previous["stocks"]) if previous.get("stocks") not in (None, 0) else None
    stocks = float(current["stocks"]) if current.get("stocks") not in (None, 0) else None
    price_ratio = close / prev_close if prev_close > 0 else None
    stocks_ratio = stocks / prev_stocks if prev_stocks and stocks else None
    reasons = []
    if stocks_ratio is not None and (stocks_ratio >= 1.2 or stocks_ratio <= 0.8):
        reasons.append("stocks_change_ge_20pct")
    if price_ratio is not None and price_ratio <= 0.6:
        reasons.append("price_ratio_le_0.6")
    if price_ratio is not None and price_ratio >= 1.8:
        reasons.append("price_ratio_ge_1.8")
    if stocks_ratio is not None and 4.5 <= stocks_ratio <= 5.5:
        reasons.append("split_5_to_1_like")
    if stocks_ratio is not None and 0.18 <= stocks_ratio <= 0.22:
        reasons.append("reverse_split_1_to_5_like")
    if price_ratio is not None and stocks_ratio is not None and abs(stocks_ratio - 1) >= 0.2 and abs((price_ratio * stocks_ratio) - 1) <= 0.25:
        reasons.append("price_inverse_to_stocks_ratio")
    if not reasons:
        return None
    return {
        "code": current["code"],
        "name": current.get("name"),
        "date": current["date"],
        "prev_trading_date": previous["date"],
        "prev_close": prev_close,
        "close": close,
        "prev_stocks": prev_stocks,
        "stocks": stocks,
        "price_ratio": price_ratio,
        "stocks_ratio": stocks_ratio,
        "market": current.get("market"),
        "reason": ";".join(reasons),
    }


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
    raw_yearly_counts: dict[int, int] = {}
    tradable_yearly_counts: dict[int, int] = {}
    global_min_date: str | None = None
    global_max_date: str | None = None
    raw_row_count = 0
    tradable_row_count = 0
    duplicate_tradable_rows_dropped = 0
    row_status_counts = {status: 0 for status in ROW_STATUS_VALUES}
    last_tradable_by_code: dict[str, dict[str, Any]] = {}
    corporate_candidates: list[dict[str, Any]] = []

    for path in files:
        year = file_year(path)
        raw_frame = prepare_source_frame(read_marcap_file(path))
        raw_count = len(raw_frame)
        raw_row_count += raw_count
        raw_yearly_counts[year] = raw_count
        source_files.append(str(path.relative_to(SOURCE_REPO_PATH)))
        year_min = str(raw_frame["date"].min())
        year_max = str(raw_frame["date"].max())
        global_min_date, global_max_date = update_minmax(global_min_date, global_max_date, year_min, year_max)
        for status, count in raw_frame["row_status"].value_counts().to_dict().items():
            row_status_counts[status] += int(count)

        tradable_frame, duplicate_rows = tradable_dedup(raw_frame[raw_frame["row_status"] == "tradable_ohlcv"].copy())
        duplicate_tradable_rows_dropped += duplicate_rows
        tradable_yearly_counts[year] = len(tradable_frame)
        tradable_row_count += len(tradable_frame)

        raw_market = raw_frame.groupby("market", dropna=False).agg(row_count=("code", "size"), symbol_count=("code", "nunique"), first_date=("date", "min"), last_date=("date", "max"))
        tradable_market = tradable_frame.groupby("market", dropna=False).agg(tradable_row_count=("code", "size"), tradable_symbol_count=("code", "nunique"))
        for market in sorted(set(raw_market.index.tolist()) | set(tradable_market.index.tolist()), key=lambda item: "" if pd.isna(item) else str(item)):
            raw_item = raw_market.loc[market] if market in raw_market.index else None
            tradable_item = tradable_market.loc[market] if market in tradable_market.index else None
            market_coverage.append(
                {
                    "year": year,
                    "market": "" if pd.isna(market) else market,
                    "row_count": int(tradable_item["tradable_row_count"]) if tradable_item is not None else 0,
                    "symbol_count": int(tradable_item["tradable_symbol_count"]) if tradable_item is not None else 0,
                    "raw_row_count": int(raw_item["row_count"]) if raw_item is not None else 0,
                    "raw_symbol_count": int(raw_item["symbol_count"]) if raw_item is not None else 0,
                    "first_date": raw_item["first_date"] if raw_item is not None else "",
                    "last_date": raw_item["last_date"] if raw_item is not None else "",
                }
            )

        for frame_group, groups in [(raw_frame.groupby(["code", "name"], dropna=False)["date"].agg(["min", "max"]).reset_index(), name_groups)]:
            for row in frame_group.to_dict("records"):
                key = (row["code"], "" if pd.isna(row["name"]) else row["name"])
                first, last = groups[key]
                groups[key] = [row["min"] if first is None or row["min"] < first else first, row["max"] if last is None or row["max"] > last else last]
        for row in raw_frame.groupby(["code", "market"], dropna=False)["date"].agg(["min", "max"]).reset_index().to_dict("records"):
            key = (row["code"], "" if pd.isna(row["market"]) else row["market"])
            first, last = market_groups[key]
            market_groups[key] = [row["min"] if first is None or row["min"] < first else first, row["max"] if last is None or row["max"] > last else last]

        for code, group in raw_frame.groupby("code", sort=True):
            prefix = code[:3]
            raw_out = ATLAS_ROOT / "ohlcv_raw_by_symbol_year" / prefix / code / f"{year}.csv"
            write_ohlcv_frame(group, raw_out, include_status=True)
            profile = profiles.setdefault(code, profile_seed(code))
            update_raw_profile(profile, group, year, raw_out)

        for code, group in tradable_frame.groupby("code", sort=True):
            prefix = code[:3]
            tradable_out = ATLAS_ROOT / "ohlcv_tradable_by_symbol_year" / prefix / code / f"{year}.csv"
            compat_out = ATLAS_ROOT / "ohlcv_min_by_symbol_year" / prefix / code / f"{year}.csv"
            write_ohlcv_frame(group, tradable_out, include_status=False)
            write_ohlcv_frame(group, compat_out, include_status=False)
            profile = profiles.setdefault(code, profile_seed(code))
            update_tradable_profile(profile, group, year, tradable_out, compat_out)
            for row in group.to_dict("records"):
                previous = last_tradable_by_code.get(code)
                if previous:
                    candidate = candidate_from_pair(previous, row)
                    if candidate:
                        corporate_candidates.append(candidate)
                last_tradable_by_code[code] = row

        print(
            f"loaded {path.name}: raw_rows={raw_count} tradable_rows={len(tradable_frame)} "
            f"zero_volume={int((raw_frame['row_status'] == 'non_tradable_zero_volume').sum())} "
            f"zero_ohlc={int((raw_frame['row_status'] == 'invalid_zero_ohlc').sum())} "
            f"suspicious={int((raw_frame['row_status'] == 'suspicious_ohlc_repaired_candidate').sum())} "
            f"duplicate_tradable_rows_dropped={duplicate_rows}"
        )

    assert global_min_date is not None and global_max_date is not None

    write_csv(
        ATLAS_ROOT / "corporate_actions" / "corporate_action_candidates.csv",
        corporate_candidates,
        ["code", "name", "date", "prev_trading_date", "prev_close", "close", "prev_stocks", "stocks", "price_ratio", "stocks_ratio", "market", "reason"],
    )
    candidates_by_code: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for candidate in corporate_candidates:
        candidates_by_code[candidate["code"]].append(candidate)

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

    for code in sorted(profiles):
        raw = profiles[code]
        prefix = code[:3]
        available_years = sorted(raw["available_years"])
        raw_available_years = sorted(raw["raw_available_years"])
        last_for_status = raw["raw_last_date"] or raw["last_date"] or ""
        status = infer_status(last_for_status, global_max_date)
        profile_path = f"atlas/symbol_profiles/{prefix}/{code}.json"
        candidate_dates = [item["date"] for item in candidates_by_code.get(code, [])]
        profile = {
            "code": code,
            "current_or_latest_name": raw["current_or_latest_name"],
            "name_history": name_history_by_code[code],
            "markets": sorted(raw["markets"]),
            "market_history": market_history_by_code[code],
            "first_date": raw["first_date"] or raw["raw_first_date"],
            "last_date": raw["last_date"] or raw["raw_last_date"],
            "raw_first_date": raw["raw_first_date"],
            "raw_last_date": raw["raw_last_date"],
            "trading_day_count": raw["trading_day_count"],
            "raw_trading_day_count": raw["raw_trading_day_count"],
            "available_years": available_years,
            "raw_available_years": raw_available_years,
            "year_files": sorted(raw["year_files"]),
            "raw_year_files": sorted(raw["raw_year_files"]),
            "compat_year_files": sorted(raw["compat_year_files"]),
            "latest_close": raw["latest_close"],
            "latest_marcap": raw["latest_marcap"],
            "latest_market": raw["latest_market"],
            "raw_latest_close": raw["raw_latest_close"],
            "raw_latest_marcap": raw["raw_latest_marcap"],
            "raw_latest_market": raw["raw_latest_market"],
            "row_status_counts": raw["row_status_counts"],
            "corporate_action_candidate_count": len(candidate_dates),
            "corporate_action_candidate_dates": candidate_dates[:50],
            "has_major_raw_discontinuity": bool(candidate_dates),
            "calibration_caveat": "Corporate-action candidate windows are blocked by default." if candidate_dates else "",
            "status_inferred": status,
            "status_inference_note": "Inferred only from FinanceData/marcap row presence, not an official listing/delisting status.",
            "price_data_source": SOURCE_NAME,
            "source_repo_url": SOURCE_REPO_URL,
            "price_adjustment_status": PRICE_ADJUSTMENT_STATUS,
            "caveat": CAVEAT,
        }
        write_json(ROOT / profile_path, profile)
        all_row = {
            "code": code,
            "current_or_latest_name": raw["current_or_latest_name"],
            "first_date": profile["first_date"],
            "last_date": profile["last_date"],
            "trading_day_count": raw["trading_day_count"],
            "raw_trading_day_count": raw["raw_trading_day_count"],
            "markets": "|".join(sorted(raw["markets"])),
            "available_year_count": len(available_years),
            "raw_available_year_count": len(raw_available_years),
            "latest_close": raw["latest_close"],
            "latest_marcap": raw["latest_marcap"],
            "status_inferred": status,
            "profile_path": profile_path,
        }
        all_symbol_rows.append(all_row)
        symbol_span_rows.append(
            {
                **{key: all_row[key] for key in ["code", "current_or_latest_name", "first_date", "last_date", "trading_day_count", "profile_path"]},
                "available_years": "|".join(map(str, available_years)),
                "raw_available_years": "|".join(map(str, raw_available_years)),
            }
        )
        by_prefix[prefix].append(
            {
                "code": code,
                "name": raw["current_or_latest_name"],
                "profile_path": profile_path,
                "available_years": available_years,
                "raw_available_years": raw_available_years,
                "first_date": profile["first_date"],
                "last_date": profile["last_date"],
                "status_inferred": status,
            }
        )
        for market in sorted(raw["markets"]):
            by_market_rows[market].append(
                {
                    "code": code,
                    "current_or_latest_name": raw["current_or_latest_name"],
                    "first_date": profile["first_date"],
                    "last_date": profile["last_date"],
                    "trading_day_count": raw["trading_day_count"],
                    "profile_path": profile_path,
                }
            )
        for item in name_history_by_code[code]:
            name_history_rows.append({"code": code, **item})

    current_rows = [row for row in all_symbol_rows if row["last_date"] == global_max_date or row["status_inferred"] == "active_like"]
    write_csv(
        ATLAS_ROOT / "universe" / "all_symbols.csv",
        all_symbol_rows,
        [
            "code",
            "current_or_latest_name",
            "first_date",
            "last_date",
            "trading_day_count",
            "raw_trading_day_count",
            "markets",
            "available_year_count",
            "raw_available_year_count",
            "latest_close",
            "latest_marcap",
            "status_inferred",
            "profile_path",
        ],
    )
    write_csv(
        ATLAS_ROOT / "universe" / "current_symbols.csv",
        current_rows,
        [
            "code",
            "current_or_latest_name",
            "first_date",
            "last_date",
            "trading_day_count",
            "raw_trading_day_count",
            "markets",
            "available_year_count",
            "raw_available_year_count",
            "latest_close",
            "latest_marcap",
            "status_inferred",
            "profile_path",
        ],
    )
    write_csv(ATLAS_ROOT / "universe" / "symbol_spans.csv", symbol_span_rows, ["code", "current_or_latest_name", "first_date", "last_date", "trading_day_count", "available_years", "raw_available_years", "profile_path"])
    write_csv(ATLAS_ROOT / "universe" / "name_history.csv", name_history_rows, ["code", "name", "first_date", "last_date"])
    write_csv(ATLAS_ROOT / "universe" / "market_coverage_by_year.csv", market_coverage, ["year", "market", "row_count", "symbol_count", "raw_row_count", "raw_symbol_count", "first_date", "last_date"])

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
        "row_count": tradable_row_count,
        "raw_row_count": raw_row_count,
        "tradable_row_count": tradable_row_count,
        "source_raw_row_count": raw_row_count,
        "duplicate_tradable_rows_dropped": duplicate_tradable_rows_dropped,
        "non_tradable_zero_volume_row_count": row_status_counts["non_tradable_zero_volume"],
        "invalid_zero_ohlc_row_count": row_status_counts["invalid_zero_ohlc"],
        "invalid_missing_ohlc_row_count": row_status_counts["invalid_missing_ohlc"],
        "invalid_ohlc_inconsistent_row_count": row_status_counts["invalid_ohlc_inconsistent"],
        "suspicious_ohlc_repaired_candidate_count": row_status_counts["suspicious_ohlc_repaired_candidate"],
        "corporate_action_candidate_count": len(corporate_candidates),
        "row_status_counts": row_status_counts,
        "symbol_count": len(profiles),
        "active_like_symbol_count": len(current_rows),
        "inactive_or_delisted_like_symbol_count": len(profiles) - len(current_rows),
        "markets": markets,
        "shard_type": "symbol_year_min_csv",
        "calibration_shard_root": "atlas/ohlcv_tradable_by_symbol_year",
        "raw_shard_root": "atlas/ohlcv_raw_by_symbol_year",
        "deprecated_or_compat_shard_root": "atlas/ohlcv_min_by_symbol_year",
        "ohlcv_shard_root": "atlas/ohlcv_tradable_by_symbol_year",
        "schema_path": "atlas/schema.json",
        "universe_path": "atlas/universe/all_symbols.csv",
        "research_pack_generator": "scripts/build_research_pack.py",
        "research_pack_default_price_basis": "tradable_raw",
        "ohlc_consistency_repair_applied": False,
        "ohlc_consistency_repair_applied_for_calibration": False,
        "full_ohlcv_atlas_committed_to_main": True,
        "full_ohlcv_atlas_branch": "",
        "data_branch_if_used": "",
        "notes": [
            "Raw/unadjusted OHLC. Corporate actions are not adjusted.",
            "Zero-volume and zero-OHLC rows are excluded from calibration shards.",
            "Corporate-action-contaminated windows are blocked from calibration by default.",
            "Raw reference rows remain visible under atlas/ohlcv_raw_by_symbol_year with row_status.",
            "atlas/ohlcv_min_by_symbol_year is a backward-compatible copy of calibration-safe tradable shards.",
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
            "loaded_years": sorted(raw_yearly_counts),
            "generated_at": utc_now(),
            "raw_row_count_by_year": raw_yearly_counts,
            "tradable_row_count_by_year": tradable_yearly_counts,
            "min_date": global_min_date,
            "max_date": global_max_date,
            "raw_row_count": raw_row_count,
            "tradable_row_count": tradable_row_count,
            "row_status_counts": row_status_counts,
            "corporate_action_candidate_count": len(corporate_candidates),
        },
    )
    write_json(
        ATLAS_ROOT / "schema.json",
        {
            "raw_shard_columns": {"d": "date", "o": "open", "h": "high", "l": "low", "c": "close", "v": "volume", "a": "amount", "mc": "marcap", "s": "stocks", "m": "market", "rs": "row_status"},
            "tradable_shard_columns": {"d": "date", "o": "open", "h": "high", "l": "low", "c": "close", "v": "volume", "a": "amount", "mc": "marcap", "s": "stocks", "m": "market"},
            "row_status_values": ROW_STATUS_VALUES,
            "data_quality_label_values": [
                "clean_tradable_path",
                "usable_with_caveat",
                "blocked_by_corporate_action",
                "blocked_by_insufficient_forward_window",
                "blocked_by_non_tradable_rows",
                "blocked_by_invalid_ohlc",
            ],
            "price_adjustment_status": PRICE_ADJUSTMENT_STATUS,
            "caveat": CAVEAT,
            "calibration_basis": "tradable_raw",
            "normalization": [
                "Raw rows are preserved with row_status under atlas/ohlcv_raw_by_symbol_year.",
                "Tradable shards include only rows with volume>0, positive OHLC, and OHLC consistency.",
                "No high/low repair is applied for calibration.",
                "Duplicate tradable code/date rows are deterministically reduced to one row by marcap/amount/volume sort order.",
            ],
            "corporate_action_candidate_logic": [
                "stocks changes by >=20% from previous tradable row",
                "close/prev_close <= 0.6 or >= 1.8",
                "stocks_ratio roughly 5:1 or 1:5",
                "price_ratio roughly inverse to stocks_ratio within tolerance",
            ],
            "MFE_N_pct": "(max high from entry_date through N tradable rows / entry_price - 1) * 100",
            "MAE_N_pct": "(min low from entry_date through N tradable rows / entry_price - 1) * 100",
            "calibration_usable_rules": [
                "price_basis is tradable_raw",
                "open/high/low/close/volume are positive and present",
                "entry row exists",
                "at least 180 forward tradable days are available",
                "MFE and MAE 30/90/180D are computed",
                "window_180D_corporate_action_contaminated is false when block_corporate_action_window=true",
            ],
            "window_level_contamination_flags": [
                "window_30D_corporate_action_contaminated",
                "window_90D_corporate_action_contaminated",
                "window_180D_corporate_action_contaminated",
                "window_1Y_corporate_action_contaminated",
                "window_2Y_corporate_action_contaminated",
            ],
            "status_inferred_note": "active/inactive-like status is inferred only from row presence and is not official listing status.",
        },
    )
    return manifest


def write_readmes(manifest: dict[str, Any]) -> None:
    (ROOT / "README.md").write_text(
        f"""# stock-web

Assistant-readable FinanceData/marcap OHLC atlas for E2R historical calibration.

This repo commits compact plain-text artifacts generated from FinanceData/marcap. Raw reference rows and calibration-safe tradable rows are separated.

## What To Read First

1. `atlas/manifest.json` for source, date range, row quality counts, and shard roots.
2. `diagnostics/chatgpt_bundle.txt` or `.json` for a compact ChatGPT verification bundle.
3. `atlas/research_packs/smoke/smoke_005930_000660_298040_267260_086520.json` for a ready E2R smoke pack.

## Price Shards

- Calibration-safe tradable rows: `atlas/ohlcv_tradable_by_symbol_year/{{prefix}}/{{code}}/{{year}}.csv`
- Raw reference rows with `row_status`: `atlas/ohlcv_raw_by_symbol_year/{{prefix}}/{{code}}/{{year}}.csv`
- Backward-compatible tradable copy: `atlas/ohlcv_min_by_symbol_year/{{prefix}}/{{code}}/{{year}}.csv`

## Example: Samsung Electronics

- Profile: `atlas/symbol_profiles/005/005930.json`
- 2024 tradable OHLC shard: `atlas/ohlcv_tradable_by_symbol_year/005/005930/2024.csv`
- 2024 raw OHLC shard: `atlas/ohlcv_raw_by_symbol_year/005/005930/2024.csv`
- Code prefix index: `atlas/index/by_code_prefix/005.json`

## Source Caveat

- Source: {SOURCE_NAME}
- Source repo: {SOURCE_REPO_URL}
- Price adjustment status: {PRICE_ADJUSTMENT_STATUS}
- Caveat: {CAVEAT}
- Zero-volume and zero-OHLC rows are excluded from calibration shards.
- Corporate-action-contaminated windows are blocked from calibration by default.

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
5. Use `atlas/ohlcv_tradable_by_symbol_year/{{prefix}}/{{code}}/{{year}}.csv` for calibration.
6. Use `atlas/ohlcv_raw_by_symbol_year/{{prefix}}/{{code}}/{{year}}.csv` only to inspect excluded raw rows and row_status.
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

- Source: {SOURCE_NAME}
- Source repo: {SOURCE_REPO_URL}
- Price adjustment status: {PRICE_ADJUSTMENT_STATUS}
- Raw/unadjusted OHLC. Corporate actions are not adjusted unless explicitly added later.
- No high/low repair is applied for calibration.
- Zero-volume and zero-OHLC rows are excluded from calibration shards.
- Corporate-action-contaminated windows are blocked from calibration by default.

Use only `calibration_usable=true` rows for E2R calibration. Reject cases without 180 forward tradable days. Do not use narrative-only rows for weight changes.
""",
        encoding="utf-8",
    )
    (ATLAS_ROOT / "research_packs" / "README.md").write_text("# Research Packs\n\nGenerated E2R-ready packs from calibration-safe tradable atlas shards.\n", encoding="utf-8")


def write_diagnostics(manifest: dict[str, Any], smoke_pack: dict[str, Any]) -> None:
    rows = load_symbol_rows(ATLAS_ROOT, "005930", "2024-01-01", "2024-12-31")
    sample_rows = rows[:5] + rows[-5:]
    trigger = smoke_pack["items"][0]
    path_points = trigger.get("path_summary", [])
    lines = [
        "CHATGPT_MARCAP_BUNDLE",
        f"generated_at={utc_now()}",
        f"source_name={SOURCE_NAME}",
        f"source_repo_url={SOURCE_REPO_URL}",
        f"price_adjustment_status={PRICE_ADJUSTMENT_STATUS}",
        f"min_date={manifest['min_date']}",
        f"max_date={manifest['max_date']}",
        f"raw_row_count={manifest['raw_row_count']}",
        f"tradable_row_count={manifest['tradable_row_count']}",
        f"non_tradable_zero_volume_row_count={manifest['non_tradable_zero_volume_row_count']}",
        f"invalid_zero_ohlc_row_count={manifest['invalid_zero_ohlc_row_count']}",
        f"corporate_action_candidate_count={manifest['corporate_action_candidate_count']}",
        f"symbol_count={manifest['symbol_count']}",
        f"active_like_symbol_count={manifest['active_like_symbol_count']}",
        f"inactive_or_delisted_like_symbol_count={manifest['inactive_or_delisted_like_symbol_count']}",
    ]
    selftests = []
    for item in smoke_pack["items"]:
        year_rows = load_symbol_rows(ATLAS_ROOT, item["code"], "2024-01-01", "2024-12-31")
        d180 = any(point.get("trading_day_offset") == 180 and point.get("available") for point in item.get("path_summary", []))
        line = "|".join(
            [
                "SELFTEST",
                item["code"],
                str(item["name"]),
                str(len(year_rows)),
                year_rows[0]["date"] if year_rows else "",
                year_rows[-1]["date"] if year_rows else "",
                "true" if year_rows else "false",
                "true" if item.get("calibration_usable") else "false",
                str(item.get("forward_window_trading_days")),
                str(item.get("MFE_30D_pct")),
                str(item.get("MFE_90D_pct")),
                str(item.get("MFE_180D_pct")),
                str(item.get("MAE_30D_pct")),
                str(item.get("MAE_90D_pct")),
                str(item.get("MAE_180D_pct")),
                "true" if d180 else "false",
                str(item.get("data_quality_label")),
                ";".join(item.get("calibration_block_reasons", [])),
            ]
        )
        lines.append(line)
        selftests.append(line)
    for row in sample_rows:
        lines.append("|".join(["OHLC_SAMPLE", "005930", str(row["date"]), str(row["open"]), str(row["high"]), str(row["low"]), str(row["close"]), str(row["volume"]), str(row["amount"]), str(row["marcap"]), str(row["market"])]))
    lines.append("|".join(["TRIGGER_SAMPLE", "005930", "2024-01-02", "next_trading_day_close", str(trigger.get("entry_date")), str(trigger.get("entry_price")), str(trigger.get("calibration_usable")).lower(), str(trigger.get("forward_window_trading_days")), str(trigger.get("MFE_30D_pct")), str(trigger.get("MFE_90D_pct")), str(trigger.get("MFE_180D_pct")), str(trigger.get("MAE_30D_pct")), str(trigger.get("MAE_90D_pct")), str(trigger.get("MAE_180D_pct")), str(trigger.get("peak_date")), str(trigger.get("peak_price")), str(trigger.get("drawdown_after_peak_pct") or "")]))
    for point in path_points:
        lines.append("|".join(["PATH_SAMPLE", "005930", str(trigger.get("entry_date")), str(point.get("label")), str(point.get("date")), str(point.get("close_return_pct")), str(point.get("high_to_date_return_pct")), str(point.get("low_to_date_return_pct")), str(point.get("available")).lower()]))

    raw_ecopro = load_symbol_rows(ATLAS_ROOT, "086520", "2024-04-09", "2024-04-09", price_basis="raw_all")
    tradable_ecopro = load_symbol_rows(ATLAS_ROOT, "086520", "2024-04-09", "2024-04-09")
    raw_has_zero = bool(raw_ecopro and any((row.get("open") in (0, 0.0) or row.get("low") in (0, 0.0) or row.get("volume") in (0, 0.0)) for row in raw_ecopro))
    candidates = load_corporate_action_candidates(ATLAS_ROOT, "086520")
    ecopro_item = next(item for item in smoke_pack["items"] if item["code"] == "086520")
    lines.append(f"ECOPRO_ZERO_ROW_CHECK|086520|2024-04-09|raw_has_zero_ohlc={str(raw_has_zero).lower()}|tradable_excluded={str(not tradable_ecopro).lower()}")
    lines.append(f"ECOPRO_CORP_ACTION_CHECK|086520|2024-04-25|detected={str(any(item.get('date') == '2024-04-25' for item in candidates)).lower()}|calibration_blocked={str(not ecopro_item.get('calibration_usable')).lower()}")
    text = "\n".join(lines) + "\n"
    (DIAGNOSTICS_ROOT / "chatgpt_bundle.txt").write_text(text, encoding="utf-8")
    (ATLAS_ROOT / "samples" / "chatgpt_bundle.txt").parent.mkdir(parents=True, exist_ok=True)
    (ATLAS_ROOT / "samples" / "chatgpt_bundle.txt").write_text(text, encoding="utf-8")
    bundle_json = {
        "manifest": manifest,
        "selftest_lines": selftests,
        "sample_005930_2024": sample_rows,
        "smoke_pack": smoke_pack,
        "ecopro_zero_row_check": {"code": "086520", "date": "2024-04-09", "raw_has_zero_ohlc": raw_has_zero, "tradable_excluded": not tradable_ecopro},
        "ecopro_corporate_action_check": {
            "code": "086520",
            "date": "2024-04-25",
            "detected": any(item.get("date") == "2024-04-25" for item in candidates),
            "calibration_blocked": not ecopro_item.get("calibration_usable"),
        },
    }
    write_json(DIAGNOSTICS_ROOT / "chatgpt_bundle.json", bundle_json)
    write_json(ATLAS_ROOT / "samples" / "chatgpt_bundle.json", bundle_json)
    PUBLIC_ROOT.mkdir(exist_ok=True)
    (PUBLIC_ROOT / "chatgpt_bundle.txt").write_text(text, encoding="utf-8")
    write_json(PUBLIC_ROOT / "chatgpt_bundle.json", bundle_json)


def size_report(manifest: dict[str, Any]) -> dict[str, Any]:
    files = [path for path in ATLAS_ROOT.rglob("*") if path.is_file()] + [path for path in DIAGNOSTICS_ROOT.rglob("*") if path.is_file()]
    total = sum(path.stat().st_size for path in files)
    largest = max((path.stat().st_size for path in files), default=0)
    report = {"atlas_total_size_mb": round(total / 1024 / 1024, 2), "largest_file_mb": round(largest / 1024 / 1024, 2), "file_count": len(files)}
    (DIAGNOSTICS_ROOT / "atlas_size_report.md").write_text(
        f"# Atlas Size Report\n\n- total_mb: {report['atlas_total_size_mb']}\n- largest_file_mb: {report['largest_file_mb']}\n- file_count: {report['file_count']}\n- raw_row_count: {manifest['raw_row_count']}\n- tradable_row_count: {manifest['tradable_row_count']}\n- symbol_count: {manifest['symbol_count']}\n",
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
    shutil.copyfile(ATLAS_ROOT / "ohlcv_tradable_by_symbol_year" / "005" / "005930" / "2024.csv", ATLAS_ROOT / "samples" / "sample_005930_2024.csv")
    write_diagnostics(manifest, pack)
    report = size_report(manifest)
    full_in_main = True
    manifest["full_ohlcv_atlas_committed_to_main"] = full_in_main
    manifest["full_ohlcv_atlas_branch"] = "" if full_in_main else "price-atlas-data"
    manifest["data_branch_if_used"] = "" if full_in_main else "price-atlas-data"
    write_json(ATLAS_ROOT / "manifest.json", manifest)
    (DIAGNOSTICS_ROOT / "atlas_build_report.md").write_text(
        f"# Atlas Build Report\n\n- source_name: {SOURCE_NAME}\n- min_date: {manifest['min_date']}\n- max_date: {manifest['max_date']}\n- raw_row_count: {manifest['raw_row_count']}\n- tradable_row_count: {manifest['tradable_row_count']}\n- non_tradable_zero_volume_row_count: {manifest['non_tradable_zero_volume_row_count']}\n- invalid_zero_ohlc_row_count: {manifest['invalid_zero_ohlc_row_count']}\n- invalid_ohlc_inconsistent_row_count: {manifest['invalid_ohlc_inconsistent_row_count']}\n- suspicious_ohlc_repaired_candidate_count: {manifest['suspicious_ohlc_repaired_candidate_count']}\n- corporate_action_candidate_count: {manifest['corporate_action_candidate_count']}\n- symbol_count: {manifest['symbol_count']}\n- active_like_symbol_count: {manifest['active_like_symbol_count']}\n- inactive_or_delisted_like_symbol_count: {manifest['inactive_or_delisted_like_symbol_count']}\n- atlas_total_size_mb: {report['atlas_total_size_mb']}\n- full_ohlcv_atlas_committed_to_main: {str(full_in_main).lower()}\n- ohlc_consistency_repair_applied_for_calibration: false\n",
        encoding="utf-8",
    )
    print(json.dumps({**manifest, **report}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
