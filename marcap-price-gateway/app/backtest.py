from __future__ import annotations

import math
from datetime import date, datetime
from typing import Any, Iterable

from app.schemas import PRICE_ADJUSTMENT_STATUS, PRICE_DATA_SOURCE, SOURCE_REPO_URL, CAVEAT, source_notes

ENTRY_MODES = {"trigger_close", "next_trading_day_close", "next_trading_day_open"}
DEFAULT_WINDOWS = [30, 90, 180, 252, 504]
DEFAULT_POINTS = [1, 2, 3, 5, 10, 20, 30, 60, 90, 180, 252, 504]
WINDOW_LABELS = {252: "1Y", 504: "2Y"}


def _to_date(value: Any) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return datetime.strptime(str(value)[:10], "%Y-%m-%d").date()


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    return _to_date(value).isoformat()


def _clean_number(value: Any) -> int | float | None:
    if value is None:
        return None
    try:
        if math.isnan(float(value)):
            return None
    except (TypeError, ValueError):
        pass
    if hasattr(value, "item"):
        value = value.item()
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return float(value)


def _pct(value: float | None) -> float | None:
    if value is None or math.isnan(value) or math.isinf(value):
        return None
    return round(value, 2)


def _as_float(row: dict[str, Any], key: str) -> float | None:
    value = row.get(key)
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(number) or math.isinf(number):
        return None
    return number


def _normalize_rows(rows: Any) -> list[dict[str, Any]]:
    if rows is None:
        return []
    if hasattr(rows, "to_dict"):
        records = rows.to_dict("records")
    else:
        records = list(rows)
    normalized: list[dict[str, Any]] = []
    for row in records:
        item = dict(row)
        if "date" in item and item["date"] is not None:
            item["date"] = _iso(item["date"])
        if "code" in item and item["code"] is not None:
            item["code"] = str(item["code"]).zfill(6)
        normalized.append(item)
    return sorted(normalized, key=lambda item: item.get("date") or "")


def _window_key(prefix: str, window: int) -> str:
    label = WINDOW_LABELS.get(window, f"{window}D")
    return f"{prefix}_{label}_pct"


def _below_key(window: int) -> str:
    label = WINDOW_LABELS.get(window, f"{window}D")
    return f"below_entry_price_flag_{label}"


def _empty_required_window_fields(result: dict[str, Any]) -> None:
    for window in DEFAULT_WINDOWS:
        result.setdefault(_window_key("MFE", window), None)
        result.setdefault(_window_key("MAE", window), None)
    result.setdefault("below_entry_price_flag_30D", None)
    result.setdefault("below_entry_price_flag_90D", None)


def choose_entry_row(rows: Any, trigger_date: str | date, entry_mode: str) -> dict[str, Any] | None:
    if entry_mode not in ENTRY_MODES:
        raise ValueError(f"unsupported entry_mode: {entry_mode}")
    sorted_rows = _normalize_rows(rows)
    target = _to_date(trigger_date)
    trigger_index = None
    for index, row in enumerate(sorted_rows):
        if _to_date(row["date"]) >= target:
            trigger_index = index
            break
    if trigger_index is None:
        return None
    entry_index = trigger_index if entry_mode == "trigger_close" else trigger_index + 1
    if entry_index >= len(sorted_rows):
        return None
    entry_row = dict(sorted_rows[entry_index])
    entry_row["_row_index"] = entry_index
    entry_row["_entry_mode"] = entry_mode
    if entry_mode == "next_trading_day_open":
        entry_row["_entry_price"] = _as_float(entry_row, "open")
    else:
        entry_row["_entry_price"] = _as_float(entry_row, "close")
    return entry_row


def compute_trigger_backtest(
    rows: Any,
    trigger_date: str | date,
    entry_mode: str = "next_trading_day_close",
    windows: Iterable[int] | None = None,
    max_window: int = 504,
) -> dict[str, Any]:
    windows = list(windows or DEFAULT_WINDOWS)
    sorted_rows = _normalize_rows(rows)
    warnings: list[str] = []
    result: dict[str, Any] = {
        "code": sorted_rows[0].get("code") if sorted_rows else None,
        "name": sorted_rows[0].get("name") if sorted_rows else None,
        "trigger_date": _iso(trigger_date),
        "entry_mode": entry_mode,
        "entry_date": None,
        "entry_price": None,
        "price_data_source": PRICE_DATA_SOURCE,
        "source_repo_url": SOURCE_REPO_URL,
        "price_adjustment_status": PRICE_ADJUSTMENT_STATUS,
        "caveat": CAVEAT,
        "calibration_usable": False,
        "forward_window_trading_days": 0,
        "peak_date": None,
        "peak_price": None,
        "drawdown_after_peak_pct": None,
        "warnings": warnings,
        "source_notes": source_notes(),
    }
    _empty_required_window_fields(result)

    if entry_mode not in ENTRY_MODES:
        raise ValueError(f"unsupported entry_mode: {entry_mode}")
    if not sorted_rows:
        warnings.append("No OHLC rows were provided.")
        return result

    required_columns = ["open", "high", "low", "close", "volume"]
    missing_columns = [key for key in required_columns if key not in sorted_rows[0]]
    if missing_columns:
        warnings.append(f"Missing required OHLCV columns: {', '.join(missing_columns)}.")

    entry_row = choose_entry_row(sorted_rows, trigger_date, entry_mode)
    if entry_row is None:
        warnings.append("Entry row is unavailable for the requested trigger date and entry mode.")
        return result

    entry_index = int(entry_row["_row_index"])
    entry_price = entry_row.get("_entry_price")
    if entry_price is None or entry_price <= 0:
        warnings.append("Entry price is unavailable or not positive.")
        return result

    forward_rows = sorted_rows[entry_index : entry_index + max_window + 1]
    result["entry_date"] = entry_row["date"]
    result["entry_price"] = _clean_number(entry_price)
    result["forward_window_trading_days"] = max(0, len(forward_rows) - 1)

    for window in windows:
        mfe_key = _window_key("MFE", int(window))
        mae_key = _window_key("MAE", int(window))
        below_key = _below_key(int(window))
        if len(forward_rows) < window:
            result[mfe_key] = None
            result[mae_key] = None
            if window in (30, 90):
                result[below_key] = None
            warnings.append(
                f"Window {window}D unavailable: need {window} trading rows from entry date inclusive, "
                f"found {len(forward_rows)}."
            )
            continue
        window_rows = forward_rows[:window]
        highs = [_as_float(row, "high") for row in window_rows]
        lows = [_as_float(row, "low") for row in window_rows]
        closes_after_entry = [_as_float(row, "close") for row in window_rows[1:]]
        if any(value is None for value in highs + lows):
            warnings.append(f"Window {window}D has missing high/low values.")
            result[mfe_key] = None
            result[mae_key] = None
        else:
            result[mfe_key] = _pct((max(highs) / entry_price - 1.0) * 100.0)
            result[mae_key] = _pct((min(lows) / entry_price - 1.0) * 100.0)
        if window in (30, 90):
            result[below_key] = any(close is not None and close < entry_price for close in closes_after_entry)

    observed_highs = [_as_float(row, "high") for row in forward_rows]
    if observed_highs and all(value is not None for value in observed_highs):
        peak_price = max(observed_highs)
        peak_offset = next(index for index, value in enumerate(observed_highs) if value == peak_price)
        peak_row = forward_rows[peak_offset]
        result["peak_date"] = peak_row["date"]
        result["peak_price"] = _clean_number(peak_price)
        lows_after_peak = [_as_float(row, "low") for row in forward_rows[peak_offset + 1 :]]
        lows_after_peak = [value for value in lows_after_peak if value is not None]
        if lows_after_peak:
            result["drawdown_after_peak_pct"] = _pct((min(lows_after_peak) / peak_price - 1.0) * 100.0)
        else:
            warnings.append("Drawdown after peak unavailable because no row exists after the peak date.")
    else:
        warnings.append("Peak calculation unavailable because high values are missing.")

    required_metrics = [
        "MFE_30D_pct",
        "MFE_90D_pct",
        "MFE_180D_pct",
        "MAE_30D_pct",
        "MAE_90D_pct",
        "MAE_180D_pct",
    ]
    result["calibration_usable"] = (
        not missing_columns
        and result["entry_date"] is not None
        and result["forward_window_trading_days"] >= 180
        and all(result.get(key) is not None for key in required_metrics)
    )
    return result


def compute_path_summary(
    rows: Any,
    entry_date: str | date,
    points: Iterable[int] | None = None,
    entry_mode: str = "trigger_close",
) -> dict[str, Any]:
    points = list(points or DEFAULT_POINTS)
    sorted_rows = _normalize_rows(rows)
    warnings: list[str] = []
    result: dict[str, Any] = {
        "code": sorted_rows[0].get("code") if sorted_rows else None,
        "name": sorted_rows[0].get("name") if sorted_rows else None,
        "entry_date": None,
        "entry_price": None,
        "entry_mode": entry_mode,
        "points": [],
        "warnings": warnings,
        "price_data_source": PRICE_DATA_SOURCE,
        "source_repo_url": SOURCE_REPO_URL,
        "price_adjustment_status": PRICE_ADJUSTMENT_STATUS,
        "caveat": CAVEAT,
    }
    if entry_mode not in ENTRY_MODES:
        raise ValueError(f"unsupported entry_mode: {entry_mode}")
    entry_row = choose_entry_row(sorted_rows, entry_date, entry_mode)
    if entry_row is None:
        warnings.append("Entry row is unavailable for the requested date and entry mode.")
        for point in points:
            result["points"].append({"label": f"D+{point}", "trading_day_offset": point, "available": False})
        return result

    entry_index = int(entry_row["_row_index"])
    entry_price = entry_row.get("_entry_price")
    result["entry_date"] = entry_row["date"]
    result["entry_price"] = _clean_number(entry_price)
    if entry_price is None or entry_price <= 0:
        warnings.append("Entry price is unavailable or not positive.")

    for point in points:
        target_index = entry_index + int(point)
        base = {"label": f"D+{point}", "trading_day_offset": int(point)}
        if entry_price is None or entry_price <= 0 or target_index >= len(sorted_rows):
            result["points"].append({**base, "available": False})
            warnings.append(f"Point D+{point} unavailable.")
            continue
        window_rows = sorted_rows[entry_index : target_index + 1]
        target_row = sorted_rows[target_index]
        close = _as_float(target_row, "close")
        highs = [_as_float(row, "high") for row in window_rows]
        lows = [_as_float(row, "low") for row in window_rows]
        high_to_date = max(value for value in highs if value is not None) if any(value is not None for value in highs) else None
        low_to_date = min(value for value in lows if value is not None) if any(value is not None for value in lows) else None
        result["points"].append(
            {
                **base,
                "date": target_row.get("date"),
                "close": _clean_number(close),
                "high_to_date": _clean_number(high_to_date),
                "low_to_date": _clean_number(low_to_date),
                "close_return_pct": _pct((close / entry_price - 1.0) * 100.0) if close is not None else None,
                "high_to_date_return_pct": _pct((high_to_date / entry_price - 1.0) * 100.0)
                if high_to_date is not None
                else None,
                "low_to_date_return_pct": _pct((low_to_date / entry_price - 1.0) * 100.0)
                if low_to_date is not None
                else None,
                "available": True,
            }
        )
    return result


def compute_event_window(rows: Any, anchor_date: str | date, pre: int = 10, post: int = 10) -> dict[str, Any]:
    sorted_rows = _normalize_rows(rows)
    warnings: list[str] = []
    result: dict[str, Any] = {
        "anchor_date": _iso(anchor_date),
        "resolved_anchor_date": None,
        "rows": [],
        "warnings": warnings,
        "price_data_source": PRICE_DATA_SOURCE,
        "source_repo_url": SOURCE_REPO_URL,
        "price_adjustment_status": PRICE_ADJUSTMENT_STATUS,
        "caveat": CAVEAT,
    }
    target = _to_date(anchor_date)
    anchor_index = None
    for index, row in enumerate(sorted_rows):
        if _to_date(row["date"]) >= target:
            anchor_index = index
            break
    if anchor_index is None:
        warnings.append("No trading row exists on or after the requested anchor date.")
        return result

    anchor_row = sorted_rows[anchor_index]
    anchor_close = _as_float(anchor_row, "close")
    result["resolved_anchor_date"] = anchor_row["date"]
    start = max(0, anchor_index - int(pre))
    end = min(len(sorted_rows), anchor_index + int(post) + 1)
    for index in range(start, end):
        row = sorted_rows[index]
        close = _as_float(row, "close")
        result["rows"].append(
            {
                "relative_day_index": index - anchor_index,
                "date": row.get("date"),
                "open": _clean_number(_as_float(row, "open")),
                "high": _clean_number(_as_float(row, "high")),
                "low": _clean_number(_as_float(row, "low")),
                "close": _clean_number(close),
                "volume": _clean_number(_as_float(row, "volume")),
                "amount": _clean_number(_as_float(row, "amount")),
                "marcap": _clean_number(_as_float(row, "marcap")),
                "close_return_from_anchor_pct": _pct((close / anchor_close - 1.0) * 100.0)
                if close is not None and anchor_close not in (None, 0)
                else None,
            }
        )
    return result
