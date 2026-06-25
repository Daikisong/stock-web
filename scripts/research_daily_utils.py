from __future__ import annotations

import csv
import hashlib
import json
import math
import os
import tempfile
from collections import deque
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import pandas as pd

from scripts.atlas_utils import (
    CAVEAT,
    PRICE_ADJUSTMENT_STATUS,
    SOURCE_NAME,
    SOURCE_REPO_URL,
    clean_scalar,
    data_files,
    file_year,
    read_marcap_file,
    safe_json,
)

RESEARCH_DAILY_VERSION = "1.0.0"
DEFAULT_RESEARCH_START_DATE = "2016-01-01"
DEFAULT_MARKETS = ["KOSPI", "KOSDAQ", "KOSDAQ GLOBAL"]
SUPPORTED_LIMIT_MARKETS = {"KOSPI", "KOSDAQ", "KOSDAQ GLOBAL"}
SNAPSHOT_ROOT = Path("atlas/research_daily/snapshots")
ACCESS_ROOT = Path("atlas/research_daily/access")
CALENDAR_PATH = Path("atlas/research_daily/trading_calendar.csv")
RESEARCH_DAILY_ROOT = Path("atlas/research_daily")

SNAPSHOT_COLUMNS = [
    "snapshot_date",
    "previous_market_trade_date",
    "code",
    "name",
    "name_resolution_status",
    "name_candidates",
    "market",
    "prev_symbol_trade_date",
    "days_since_prev_symbol_trade",
    "prev_close",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "amount",
    "market_cap",
    "listed_shares",
    "open_gap_pct",
    "high_return_pct",
    "low_return_pct",
    "close_return_pct",
    "turnover_pct",
    "return_3d_pct",
    "return_5d_pct",
    "return_10d_pct",
    "return_20d_pct",
    "amount_rank",
    "turnover_rank",
    "market_cap_rank",
    "high_return_rank",
    "close_return_rank",
    "limit_up_price",
    "upper_limit_touched",
    "upper_limit_closed",
    "upper_limit_released",
    "one_price_upper_limit",
    "upper_limit_label_status",
    "upper_limit_touch_count_5d",
    "upper_limit_close_count_5d",
    "high_return_ge_10_count_5d",
    "high_return_ge_20_count_5d",
    "corporate_action_warning",
    "new_listing_or_no_reference",
    "data_quality_status",
    "max_source_date",
]

CALENDAR_COLUMNS = [
    "trade_date",
    "previous_trade_date",
    "next_trade_date",
    "blind_snapshot_date",
    "blind_snapshot_path",
    "outcome_snapshot_date",
    "outcome_snapshot_path",
    "access_manifest_path",
    "blind_snapshot_sha256",
    "outcome_snapshot_sha256",
    "blind_snapshot_row_count",
    "outcome_snapshot_row_count",
    "blind_snapshot_bytes",
    "outcome_snapshot_bytes",
    "blind_max_source_date",
    "outcome_max_source_date",
    "source_manifest_sha256",
    "build_status",
]


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def parse_markets(raw: str | None, include_konex: bool = False) -> list[str]:
    if raw:
        markets = [item.strip() for item in raw.split(",") if item.strip()]
    else:
        markets = list(DEFAULT_MARKETS)
    if include_konex and "KONEX" not in markets:
        markets.append("KONEX")
    return markets


def research_snapshot_path(trade_date: str) -> Path:
    return SNAPSHOT_ROOT / trade_date[:4] / trade_date[5:7] / f"{trade_date.replace('-', '')}.csv"


def research_access_path(trade_date: str) -> Path:
    return ACCESS_ROOT / trade_date[:4] / trade_date[5:7] / f"{trade_date.replace('-', '')}.json"


def rel_path(path: Path, root: Path) -> str:
    if not path.is_absolute():
        return str(path).replace(os.sep, "/")
    return str(path.relative_to(root)).replace(os.sep, "/")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_write_text(path: Path, text: str, overwrite: bool = False) -> bool:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = text.encode("utf-8")
    if path.exists() and not overwrite and path.read_bytes() == encoded:
        return False
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(encoded)
        os.replace(tmp_name, path)
    finally:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)
    return True


def json_text(payload: Any) -> str:
    return json.dumps(safe_json(payload), ensure_ascii=False, indent=2, sort_keys=False) + "\n"


def atomic_write_json(path: Path, payload: Any, overwrite: bool = False) -> bool:
    return atomic_write_text(path, json_text(payload), overwrite=overwrite)


def csv_value(value: Any) -> str:
    value = clean_scalar(value)
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            return ""
        if value.is_integer():
            return str(int(value))
        return f"{value:.6f}".rstrip("0").rstrip(".")
    return str(value)


def rows_to_csv_text(rows: list[dict[str, Any]], columns: list[str]) -> str:
    from io import StringIO

    handle = StringIO()
    writer = csv.DictWriter(handle, fieldnames=columns, lineterminator="\n")
    writer.writeheader()
    for row in rows:
        writer.writerow({column: csv_value(row.get(column)) for column in columns})
    return handle.getvalue()


def pct(numerator: float | None, denominator: float | None) -> float | None:
    if numerator is None or denominator in (None, 0):
        return None
    value = (numerator / denominator - 1) * 100
    if math.isnan(value) or math.isinf(value):
        return None
    return round(value, 6)


def ratio_pct(numerator: float | None, denominator: float | None) -> float | None:
    if numerator is None or denominator in (None, 0):
        return None
    value = numerator / denominator * 100
    if math.isnan(value) or math.isinf(value):
        return None
    return round(value, 6)


def int_price(value: Any) -> int | None:
    if value in ("", None):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(number) or math.isinf(number):
        return None
    return int(round(number))


def classify_tradable_frame(frame: pd.DataFrame) -> pd.Series:
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
    positive_all = present & frame["volume"].gt(0) & positive_ohlc
    consistent = (
        frame["high"].ge(frame[["open", "close"]].max(axis=1))
        & frame["low"].le(frame[["open", "close"]].min(axis=1))
        & frame["high"].ge(frame["low"])
    )
    status.loc[positive_all & consistent] = "tradable_ohlcv"
    status.loc[positive_all & ~consistent] = "suspicious_ohlc_repaired_candidate"
    return status


def prepare_daily_source_frame(frame: pd.DataFrame, markets: list[str]) -> pd.DataFrame:
    frame = frame.copy()
    for column in ["open", "high", "low", "close", "volume", "amount", "marcap", "stocks"]:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame["row_status"] = classify_tradable_frame(frame)
    frame = frame[(frame["row_status"] == "tradable_ohlcv") & frame["market"].isin(markets)]
    sort_columns = [column for column in ["code", "date", "marcap", "amount", "volume", "name", "market"] if column in frame.columns]
    return (
        frame.sort_values(sort_columns, kind="mergesort", na_position="first")
        .drop_duplicates(["code", "date"], keep="last")
        .sort_values(["date", "code"], kind="mergesort")
        .reset_index(drop=True)
    )


def source_files_for_range(source_repo_path: Path, start_year: int, end_year: int) -> list[Path]:
    return [path for path in data_files(source_repo_path) if start_year <= file_year(path) <= end_year]


def load_name_history(atlas_root: Path) -> dict[str, list[dict[str, str]]]:
    path = atlas_root / "universe" / "name_history.csv"
    history: dict[str, list[dict[str, str]]] = {}
    if not path.exists():
        return history
    with path.open("r", encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            history.setdefault(row["code"], []).append(row)
    for rows in history.values():
        rows.sort(key=lambda item: (item["first_date"], item["last_date"], item["name"]))
    return history


def resolve_name(source_name: Any, code: str, snapshot_date: str, name_history: dict[str, list[dict[str, str]]]) -> tuple[str | None, str, str | None]:
    if source_name not in ("", None) and not pd.isna(source_name):
        return str(source_name), "exact_source_row", None
    candidates = [
        item["name"]
        for item in name_history.get(code, [])
        if item.get("first_date", "") <= snapshot_date <= item.get("last_date", "")
    ]
    unique = sorted(set(candidate for candidate in candidates if candidate))
    if len(unique) == 1:
        return unique[0], "unique_history_match", None
    if len(unique) > 1:
        return None, "ambiguous_history_match", "|".join(unique)
    return None, "unresolved", None


def load_corporate_action_dates(atlas_root: Path) -> dict[str, set[str]]:
    path = atlas_root / "corporate_actions" / "corporate_action_candidates.csv"
    dates: dict[str, set[str]] = {}
    if not path.exists():
        return dates
    with path.open("r", encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            code = row.get("code")
            candidate_date = row.get("date")
            if code and candidate_date:
                dates.setdefault(code, set()).add(candidate_date)
    return dates


def parse_iso_date(value: str) -> date:
    return date.fromisoformat(value)


def days_between(previous: str | None, current: str) -> int | None:
    if not previous:
        return None
    return (parse_iso_date(current) - parse_iso_date(previous)).days


def tick_size(trade_date: str, market: str, price: float) -> int | None:
    if market not in SUPPORTED_LIMIT_MARKETS or price <= 0:
        return None
    if trade_date >= "2023-01-25":
        bands = [
            (2_000, 1),
            (5_000, 5),
            (20_000, 10),
            (50_000, 50),
            (200_000, 100),
            (500_000, 500),
            (float("inf"), 1_000),
        ]
    elif market == "KOSPI":
        bands = [
            (1_000, 1),
            (5_000, 5),
            (10_000, 10),
            (50_000, 50),
            (100_000, 100),
            (500_000, 500),
            (float("inf"), 1_000),
        ]
    else:
        bands = [
            (1_000, 1),
            (5_000, 5),
            (10_000, 10),
            (50_000, 50),
            (float("inf"), 100),
        ]
    for upper, unit in bands:
        if price < upper:
            return unit
    return None


def floor_to_tick(value: float, trade_date: str, market: str) -> int | None:
    candidate = value
    last_tick = None
    for _ in range(5):
        unit = tick_size(trade_date, market, candidate)
        if unit is None:
            return None
        floored = math.floor(value / unit) * unit
        if unit == last_tick or floored == candidate:
            return int(floored)
        last_tick = unit
        candidate = floored
    return int(candidate)


def compute_limit_up_price(prev_close: int | None, trade_date: str, market: str) -> int | None:
    if prev_close is None or prev_close <= 0:
        return None
    return floor_to_tick(prev_close * 1.3, trade_date, market)


def deterministic_rank(rows: list[dict[str, Any]], value_key: str, rank_key: str) -> None:
    ranked = [
        row
        for row in rows
        if row.get(value_key) not in (None, "")
        and isinstance(row.get(value_key), (int, float))
        and not math.isnan(float(row[value_key]))
    ]
    ranked.sort(key=lambda item: (-float(item[value_key]), item["code"]))
    for index, row in enumerate(ranked, start=1):
        row[rank_key] = index


def finalize_snapshot_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    for key, rank_key in [
        ("amount", "amount_rank"),
        ("turnover_pct", "turnover_rank"),
        ("market_cap", "market_cap_rank"),
        ("high_return_pct", "high_return_rank"),
        ("close_return_pct", "close_return_rank"),
    ]:
        deterministic_rank(rows, key, rank_key)
    return sorted(rows, key=lambda item: item["code"])


def preserve_generated_at_if_stable(existing_path: Path, payload: dict[str, Any], stable_keys: Iterable[str]) -> dict[str, Any]:
    if not existing_path.exists():
        return payload
    try:
        existing = json.loads(existing_path.read_text(encoding="utf-8"))
    except Exception:
        return payload
    if all(existing.get(key) == payload.get(key) for key in stable_keys):
        payload["generated_at"] = existing.get("generated_at", payload.get("generated_at"))
    return payload
