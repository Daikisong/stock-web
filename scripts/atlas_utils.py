from __future__ import annotations

import csv
import json
import math
import re
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import duckdb
import pandas as pd

SOURCE_NAME = "FinanceData/marcap"
SOURCE_REPO_URL = "https://github.com/FinanceData/marcap"
PRICE_ADJUSTMENT_STATUS = "raw_unadjusted_marcap"
CAVEAT = "Raw/unadjusted OHLC from FinanceData/marcap. Corporate actions are not adjusted unless explicitly added later."
ATLAS_VERSION = "1.0.0"
DEFAULT_WINDOWS = [30, 90, 180, 252, 504]
DEFAULT_POINTS = [1, 2, 3, 5, 10, 20, 30, 60, 90, 180, 252, 504]
WINDOW_LABELS = {252: "1Y", 504: "2Y"}
ROW_STATUS_VALUES = [
    "tradable_ohlcv",
    "non_tradable_zero_volume",
    "invalid_zero_ohlc",
    "invalid_missing_ohlc",
    "invalid_ohlc_inconsistent",
    "suspicious_ohlc_repaired_candidate",
]
DATA_QUALITY_LABELS = [
    "clean_tradable_path",
    "usable_with_caveat",
    "blocked_by_corporate_action",
    "blocked_by_insufficient_forward_window",
    "blocked_by_non_tradable_rows",
    "blocked_by_invalid_ohlc",
]

NORMALIZED_COLUMNS = [
    "date",
    "rank",
    "code",
    "name",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "amount",
    "changes",
    "change_code",
    "changes_ratio",
    "marcap",
    "stocks",
    "market_id",
    "market",
    "dept",
]
NUMERIC_COLUMNS = [
    "rank",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "amount",
    "changes",
    "change_code",
    "changes_ratio",
    "marcap",
    "stocks",
]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def snake_case(name: str) -> str:
    text = re.sub(r"[^0-9A-Za-z]+", "_", str(name)).strip("_")
    text = re.sub(r"(.)([A-Z][a-z]+)", r"\1_\2", text)
    text = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", text)
    text = text.lower().strip("_")
    aliases = {
        "changecode": "change_code",
        "changes_code": "change_code",
        "change_code": "change_code",
        "chages_ratio": "changes_ratio",
        "chagesratio": "changes_ratio",
        "changesratio": "changes_ratio",
        "change_ratio": "changes_ratio",
        "marketid": "market_id",
    }
    return aliases.get(text, text)


def data_files(repo_path: Path) -> list[Path]:
    data_dir = Path(repo_path) / "data"
    csv_files = sorted(data_dir.glob("marcap-*.csv.gz"))
    return csv_files or sorted(data_dir.glob("marcap-*.parquet"))


def file_year(path: Path) -> int:
    match = re.search(r"(19|20)\d{2}", path.name)
    if not match:
        raise ValueError(f"cannot infer year from {path}")
    return int(match.group(0))


def normalize_code(code: Any) -> str:
    digits = re.sub(r"\D", "", str(code).strip().replace(".0", ""))
    if not digits:
        raise ValueError("code must contain digits")
    return digits.zfill(6)


def standardize_frame(frame: pd.DataFrame) -> pd.DataFrame:
    frame = frame.copy()
    frame.columns = [snake_case(column) for column in frame.columns]
    if frame.columns.duplicated().any():
        merged = {}
        for column in dict.fromkeys(frame.columns):
            same_name = frame.loc[:, frame.columns == column]
            merged[column] = same_name.bfill(axis=1).iloc[:, 0] if same_name.shape[1] > 1 else same_name.iloc[:, 0]
        frame = pd.DataFrame(merged)
    if "date" not in frame.columns or "code" not in frame.columns:
        raise ValueError("source frame must contain date and code")
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce").dt.strftime("%Y-%m-%d")
    code_text = frame["code"].astype("string").str.strip().str.replace(r"\.0$", "", regex=True)
    frame["code"] = code_text.str.extract(r"(\d+)", expand=False).fillna("").str.zfill(6)
    for column in NUMERIC_COLUMNS:
        if column in frame.columns:
            frame[column] = pd.to_numeric(frame[column], errors="coerce")
    for column in NORMALIZED_COLUMNS:
        if column not in frame.columns:
            frame[column] = None
    frame = frame[NORMALIZED_COLUMNS]
    frame = frame[frame["date"].notna() & frame["code"].ne("")]
    return frame


def read_marcap_file(path: Path) -> pd.DataFrame:
    if path.name.endswith(".csv.gz"):
        frame = pd.read_csv(path, compression="gzip", dtype={"Code": "string", "code": "string"})
        return standardize_frame(frame)
    if path.suffix == ".parquet":
        con = duckdb.connect()
        try:
            frame = con.execute("SELECT * FROM read_parquet(?)", [str(path)]).df()
        finally:
            con.close()
        return standardize_frame(frame)
    raise ValueError(f"unsupported source file: {path}")


def clean_scalar(value: Any) -> Any:
    if value is None:
        return None
    if hasattr(value, "item"):
        value = value.item()
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            return None
        if value.is_integer():
            return int(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()[:10]
    try:
        if pd.isna(value):
            return None
    except TypeError:
        pass
    return value


def csv_scalar(value: Any) -> str:
    value = clean_scalar(value)
    return "" if value is None else str(value)


def safe_json(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): safe_json(item) for key, item in value.items()}
    if isinstance(value, list):
        return [safe_json(item) for item in value]
    return clean_scalar(value)


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(safe_json(payload), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: csv_scalar(row.get(key)) for key in fieldnames})


def pct(value: float | None) -> float | None:
    if value is None or math.isnan(value) or math.isinf(value):
        return None
    return round(value, 2)


def to_float(row: dict[str, Any], key: str) -> float | None:
    value = row.get(key)
    if value in ("", None):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(number) or math.isinf(number):
        return None
    return number


def classify_row(row: dict[str, Any]) -> str:
    values = {key: to_float(row, key) for key in ["open", "high", "low", "close", "volume"]}
    if any(values[key] is None for key in ["open", "high", "low", "close", "volume"]):
        return "invalid_missing_ohlc"
    open_price = values["open"]
    high = values["high"]
    low = values["low"]
    close = values["close"]
    volume = values["volume"]
    assert open_price is not None and high is not None and low is not None and close is not None and volume is not None
    if volume == 0:
        return "non_tradable_zero_volume"
    if any(value <= 0 for value in [open_price, high, low, close]):
        return "invalid_zero_ohlc"
    if high >= max(open_price, close) and low <= min(open_price, close) and high >= low:
        return "tradable_ohlcv"
    if high > 0 and low > 0 and volume > 0:
        return "suspicious_ohlc_repaired_candidate"
    return "invalid_ohlc_inconsistent"


def is_tradable_ohlcv_row(row: dict[str, Any]) -> bool:
    return classify_row(row) == "tradable_ohlcv"


def sort_rows(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted((dict(row) for row in rows), key=lambda item: item.get("date") or item.get("d") or "")


def choose_entry_row(rows: list[dict[str, Any]], trigger_date: str, entry_mode: str) -> tuple[int, dict[str, Any], float] | None:
    sorted_rows = sort_rows(rows)
    trigger_index = None
    for index, row in enumerate(sorted_rows):
        if str(row["date"]) >= trigger_date:
            trigger_index = index
            break
    if trigger_index is None:
        return None
    entry_index = trigger_index if entry_mode == "trigger_close" else trigger_index + 1
    if entry_index >= len(sorted_rows):
        return None
    entry_row = sorted_rows[entry_index]
    price_key = "open" if entry_mode == "next_trading_day_open" else "close"
    entry_price = to_float(entry_row, price_key)
    if entry_price is None or entry_price <= 0:
        return None
    return entry_index, entry_row, entry_price


def metric_key(prefix: str, window: int) -> str:
    return f"{prefix}_{WINDOW_LABELS.get(window, f'{window}D')}_pct"


def below_key(window: int) -> str:
    return f"below_entry_price_flag_{WINDOW_LABELS.get(window, f'{window}D')}"


def compute_trigger_backtest(
    rows: list[dict[str, Any]],
    trigger_date: str,
    entry_mode: str = "next_trading_day_close",
    windows: list[int] | None = None,
    max_window: int = 504,
) -> dict[str, Any]:
    windows = windows or DEFAULT_WINDOWS
    input_rows = sort_rows(rows)
    sorted_rows = [row for row in input_rows if is_tradable_ohlcv_row(row)]
    warnings: list[str] = []
    result: dict[str, Any] = {
        "trigger_date": trigger_date,
        "entry_mode": entry_mode,
        "entry_date": None,
        "entry_price": None,
        "calibration_usable": False,
        "forward_window_trading_days": 0,
        "peak_date": None,
        "peak_price": None,
        "drawdown_after_peak_pct": None,
        "input_row_count": len(input_rows),
        "tradable_row_count": len(sorted_rows),
        "warnings": warnings,
    }
    for window in DEFAULT_WINDOWS:
        result.setdefault(metric_key("MFE", window), None)
        result.setdefault(metric_key("MAE", window), None)
    result["below_entry_price_flag_30D"] = None
    result["below_entry_price_flag_90D"] = None
    if len(sorted_rows) != len(input_rows):
        warnings.append(f"Excluded {len(input_rows) - len(sorted_rows)} non-tradable rows before MFE/MAE computation.")
    if not sorted_rows:
        warnings.append("No tradable OHLC rows available.")
        return result
    required = ["open", "high", "low", "close", "volume"]
    missing_required = [key for key in required if key not in sorted_rows[0]]
    entry = choose_entry_row(sorted_rows, trigger_date, entry_mode)
    if entry is None:
        warnings.append("Entry row unavailable.")
        return result
    entry_index, entry_row, entry_price = entry
    forward_rows = sorted_rows[entry_index : entry_index + max_window + 1]
    result["entry_date"] = entry_row["date"]
    result["entry_price"] = clean_scalar(entry_price)
    result["forward_window_trading_days"] = max(0, len(forward_rows) - 1)
    for window in windows:
        mfe_key = metric_key("MFE", window)
        mae_key = metric_key("MAE", window)
        if len(forward_rows) < window:
            warnings.append(f"Window {window}D unavailable: found {len(forward_rows)} rows from entry inclusive.")
            result[mfe_key] = None
            result[mae_key] = None
            if window in (30, 90):
                result[below_key(window)] = None
            continue
        window_rows = forward_rows[:window]
        highs = [to_float(row, "high") for row in window_rows]
        lows = [to_float(row, "low") for row in window_rows]
        if any(value is None for value in highs + lows):
            warnings.append(f"Window {window}D has missing high/low.")
            continue
        result[mfe_key] = pct((max(highs) / entry_price - 1) * 100)
        result[mae_key] = pct((min(lows) / entry_price - 1) * 100)
        if window in (30, 90):
            closes_after = [to_float(row, "close") for row in window_rows[1:]]
            result[below_key(window)] = any(close is not None and close < entry_price for close in closes_after)
    highs = [to_float(row, "high") for row in forward_rows]
    if highs and all(value is not None for value in highs):
        peak_price = max(highs)
        peak_offset = next(index for index, value in enumerate(highs) if value == peak_price)
        result["peak_date"] = forward_rows[peak_offset]["date"]
        result["peak_price"] = clean_scalar(peak_price)
        lows_after_peak = [to_float(row, "low") for row in forward_rows[peak_offset + 1 :]]
        lows_after_peak = [value for value in lows_after_peak if value is not None]
        if lows_after_peak:
            result["drawdown_after_peak_pct"] = pct((min(lows_after_peak) / peak_price - 1) * 100)
        else:
            warnings.append("Drawdown after peak unavailable because no row exists after peak date.")
    required_metrics = ["MFE_30D_pct", "MFE_90D_pct", "MFE_180D_pct", "MAE_30D_pct", "MAE_90D_pct", "MAE_180D_pct"]
    result["calibration_usable"] = (
        not missing_required
        and result["entry_date"] is not None
        and result["forward_window_trading_days"] >= 180
        and all(result.get(key) is not None for key in required_metrics)
    )
    return result


def compute_path_summary(rows: list[dict[str, Any]], entry_date: str, points: list[int] | None = None, entry_mode: str = "trigger_close") -> dict[str, Any]:
    points = points or DEFAULT_POINTS
    sorted_rows = [row for row in sort_rows(rows) if is_tradable_ohlcv_row(row)]
    result = {"entry_date": None, "entry_price": None, "entry_mode": entry_mode, "points": [], "warnings": []}
    entry = choose_entry_row(sorted_rows, entry_date, entry_mode)
    if entry is None:
        result["warnings"].append("Entry row unavailable.")
        for point in points:
            result["points"].append({"label": f"D+{point}", "trading_day_offset": point, "available": False})
        return result
    entry_index, entry_row, entry_price = entry
    result["entry_date"] = entry_row["date"]
    result["entry_price"] = clean_scalar(entry_price)
    for point in points:
        target_index = entry_index + point
        base = {"label": f"D+{point}", "trading_day_offset": point}
        if target_index >= len(sorted_rows):
            result["points"].append({**base, "available": False})
            result["warnings"].append(f"Point D+{point} unavailable.")
            continue
        target_row = sorted_rows[target_index]
        window_rows = sorted_rows[entry_index : target_index + 1]
        close = to_float(target_row, "close")
        highs = [to_float(row, "high") for row in window_rows]
        lows = [to_float(row, "low") for row in window_rows]
        high_to_date = max(value for value in highs if value is not None) if any(value is not None for value in highs) else None
        low_to_date = min(value for value in lows if value is not None) if any(value is not None for value in lows) else None
        result["points"].append(
            {
                **base,
                "date": target_row.get("date"),
                "close": clean_scalar(close),
                "high_to_date": clean_scalar(high_to_date),
                "low_to_date": clean_scalar(low_to_date),
                "close_return_pct": pct((close / entry_price - 1) * 100) if close is not None else None,
                "high_to_date_return_pct": pct((high_to_date / entry_price - 1) * 100) if high_to_date is not None else None,
                "low_to_date_return_pct": pct((low_to_date / entry_price - 1) * 100) if low_to_date is not None else None,
                "available": True,
            }
        )
    return result


def compute_event_window(rows: list[dict[str, Any]], anchor_date: str, pre: int = 10, post: int = 10) -> list[dict[str, Any]]:
    sorted_rows = [row for row in sort_rows(rows) if is_tradable_ohlcv_row(row)]
    anchor_index = None
    for index, row in enumerate(sorted_rows):
        if str(row["date"]) >= anchor_date:
            anchor_index = index
            break
    if anchor_index is None:
        return []
    anchor_close = to_float(sorted_rows[anchor_index], "close")
    output = []
    for index in range(max(0, anchor_index - pre), min(len(sorted_rows), anchor_index + post + 1)):
        row = sorted_rows[index]
        close = to_float(row, "close")
        output.append(
            {
                "relative_day_index": index - anchor_index,
                "date": row.get("date"),
                "open": clean_scalar(to_float(row, "open")),
                "high": clean_scalar(to_float(row, "high")),
                "low": clean_scalar(to_float(row, "low")),
                "close": clean_scalar(close),
                "volume": clean_scalar(to_float(row, "volume")),
                "close_return_from_anchor_pct": pct((close / anchor_close - 1) * 100) if close is not None and anchor_close not in (None, 0) else None,
            }
        )
    return output


def load_profile(atlas_root: Path, code: str) -> dict[str, Any]:
    code = normalize_code(code)
    path = atlas_root / "symbol_profiles" / code[:3] / f"{code}.json"
    return json.loads(path.read_text(encoding="utf-8"))


def load_symbol_rows(atlas_root: Path, code: str, start: str | None = None, end: str | None = None, price_basis: str = "tradable_raw") -> list[dict[str, Any]]:
    code = normalize_code(code)
    profile = load_profile(atlas_root, code)
    if price_basis in {"tradable_raw", "tradable", "calibration"}:
        year_files = profile.get("year_files", [])
    elif price_basis in {"raw_all", "raw"}:
        year_files = profile.get("raw_year_files", [])
    elif price_basis in {"compat", "ohlcv_min"}:
        year_files = profile.get("compat_year_files", profile.get("year_files", []))
    else:
        raise ValueError(f"unsupported price_basis: {price_basis}")
    rows: list[dict[str, Any]] = []
    for year_file in year_files:
        path = atlas_root.parent / year_file
        if not path.exists():
            continue
        year = int(path.stem)
        if start and year < int(start[:4]):
            continue
        if end and year > int(end[:4]):
            continue
        with path.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            for row in reader:
                date_value = row["d"]
                if start and date_value < start:
                    continue
                if end and date_value > end:
                    continue
                rows.append(
                    {
                        "date": date_value,
                        "code": code,
                        "name": profile.get("current_or_latest_name"),
                        "open": clean_scalar(float(row["o"])) if row["o"] else None,
                        "high": clean_scalar(float(row["h"])) if row["h"] else None,
                        "low": clean_scalar(float(row["l"])) if row["l"] else None,
                        "close": clean_scalar(float(row["c"])) if row["c"] else None,
                        "volume": clean_scalar(float(row["v"])) if row["v"] else None,
                        "amount": clean_scalar(float(row["a"])) if row["a"] else None,
                        "marcap": clean_scalar(float(row["mc"])) if row["mc"] else None,
                        "stocks": clean_scalar(float(row["s"])) if row["s"] else None,
                        "market": row.get("m") or None,
                        "row_status": row.get("rs") or "tradable_ohlcv",
                    }
                )
    return sort_rows(rows)


def load_corporate_action_candidates(atlas_root: Path, code: str | None = None) -> list[dict[str, Any]]:
    path = atlas_root / "corporate_actions" / "corporate_action_candidates.csv"
    if not path.exists():
        return []
    output = []
    normalized = normalize_code(code) if code else None
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            if normalized and row.get("code") != normalized:
                continue
            item = dict(row)
            for key in ["prev_close", "close", "prev_stocks", "stocks", "price_ratio", "stocks_ratio"]:
                item[key] = to_float(item, key)
            output.append(item)
    return output


def corporate_action_window_flags(rows: list[dict[str, Any]], entry_date: str | None, candidate_dates: list[str], windows: list[int] | None = None) -> dict[int, bool]:
    windows = windows or DEFAULT_WINDOWS
    flags = {window: False for window in windows}
    if not entry_date or not candidate_dates:
        return flags
    sorted_rows = [row for row in sort_rows(rows) if is_tradable_ohlcv_row(row)]
    entry_index = None
    for index, row in enumerate(sorted_rows):
        if str(row.get("date")) == entry_date:
            entry_index = index
            break
    if entry_index is None:
        return flags
    candidate_set = set(candidate_dates)
    for window in windows:
        window_rows = sorted_rows[entry_index : entry_index + window]
        flags[window] = any(str(row.get("date")) in candidate_set for row in window_rows)
    return flags


def parse_int_list(raw: str, default: list[int]) -> list[int]:
    if not raw:
        return default
    return [int(item.strip()) for item in raw.split(",") if item.strip()]
