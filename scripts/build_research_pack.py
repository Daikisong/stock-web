from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.atlas_utils import (
    CAVEAT,
    DEFAULT_POINTS,
    DEFAULT_WINDOWS,
    WINDOW_LABELS,
    corporate_action_window_flags,
    PRICE_ADJUSTMENT_STATUS,
    SOURCE_NAME,
    SOURCE_REPO_URL,
    compute_event_window,
    compute_path_summary,
    compute_trigger_backtest,
    load_corporate_action_candidates,
    load_profile,
    load_symbol_rows,
    normalize_code,
    parse_int_list,
    safe_json,
    utc_now,
    write_json,
)

ATLAS_ROOT = ROOT / "atlas"


def parse_items(raw: str) -> list[tuple[str, str]]:
    items: list[tuple[str, str]] = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        if ":" not in part:
            raise ValueError(f"invalid item {part}; expected code:trigger_date")
        code, trigger_date = part.split(":", 1)
        items.append((normalize_code(code), trigger_date.strip()))
    if not items:
        raise ValueError("--items is required")
    return items


def first_last_sample(rows: list[dict[str, Any]], n: int = 5) -> list[dict[str, Any]]:
    if len(rows) <= n * 2:
        selected = rows
    else:
        selected = rows[:n] + rows[-n:]
    return [
        {
            "date": row.get("date"),
            "open": row.get("open"),
            "high": row.get("high"),
            "low": row.get("low"),
            "close": row.get("close"),
            "volume": row.get("volume"),
        }
        for row in selected
    ]


def build_pack(
    items: list[tuple[str, str]],
    entry_mode: str = "next_trading_day_close",
    windows: list[int] | None = None,
    points: list[int] | None = None,
    event_window_pre: int = 10,
    event_window_post: int = 10,
    include_ohlcv_sample: bool = True,
    include_event_window: bool = True,
    price_basis: str = "tradable_raw",
    allow_raw_all: bool = False,
    block_corporate_action_window: bool = True,
    pack_id: str = "research_pack",
    atlas_root: Path = ATLAS_ROOT,
) -> dict[str, Any]:
    windows = windows or DEFAULT_WINDOWS
    points = points or DEFAULT_POINTS
    output_items = []
    for code, trigger_date in items:
        profile = load_profile(atlas_root, code)
        rows = load_symbol_rows(atlas_root, code, price_basis="tradable_raw")
        raw_rows = load_symbol_rows(atlas_root, code, price_basis="raw_all")
        trigger = compute_trigger_backtest(rows, trigger_date, entry_mode, windows, max(windows))
        entry_date = trigger.get("entry_date") or trigger_date
        path = compute_path_summary(rows, entry_date, points, "trigger_close")
        event_rows = compute_event_window(rows, trigger_date, event_window_pre, event_window_post) if include_event_window else []
        year_rows = load_symbol_rows(atlas_root, code, f"{trigger_date[:4]}-01-01", f"{trigger_date[:4]}-12-31")
        candidates = load_corporate_action_candidates(atlas_root, code)
        candidate_dates = [str(item.get("date")) for item in candidates if item.get("date")]
        flags_by_window = corporate_action_window_flags(rows, trigger.get("entry_date"), candidate_dates, windows)
        contamination_fields = {}
        for window in DEFAULT_WINDOWS:
            label = WINDOW_LABELS.get(window, f"{window}D")
            contaminated = bool(flags_by_window.get(window, False))
            contamination_fields[f"window_{label}_corporate_action_contaminated"] = contaminated
            if contaminated:
                trigger[f"MFE_{label}_pct"] = None
                trigger[f"MAE_{label}_pct"] = None
        corporate_action_within_180d = bool(flags_by_window.get(180, False))
        corporate_action_within_504d = bool(flags_by_window.get(504, False))
        raw_counts = profile.get("row_status_counts", {})
        block_reasons = []
        if price_basis == "raw_all" and not allow_raw_all:
            block_reasons.append("raw_all_price_basis_not_allowed_for_calibration")
        if trigger.get("forward_window_trading_days", 0) < 180:
            block_reasons.append("insufficient_forward_window")
        if block_corporate_action_window and corporate_action_within_180d:
            block_reasons.append("corporate_action_within_180D")
        required_metrics = ["MFE_30D_pct", "MFE_90D_pct", "MFE_180D_pct", "MAE_30D_pct", "MAE_90D_pct", "MAE_180D_pct"]
        if any(trigger.get(key) is None for key in required_metrics):
            block_reasons.append("required_mfe_mae_window_unavailable")
        if price_basis != "tradable_raw":
            block_reasons.append("non_default_price_basis")
        calibration_usable = price_basis == "tradable_raw" and not block_reasons
        if "corporate_action_within_180D" in block_reasons:
            data_quality_label = "blocked_by_corporate_action"
        elif "insufficient_forward_window" in block_reasons:
            data_quality_label = "blocked_by_insufficient_forward_window"
        elif raw_counts.get("invalid_zero_ohlc", 0) or raw_counts.get("invalid_missing_ohlc", 0) or raw_counts.get("invalid_ohlc_inconsistent", 0):
            data_quality_label = "blocked_by_invalid_ohlc" if not calibration_usable else "usable_with_caveat"
        elif raw_counts.get("non_tradable_zero_volume", 0):
            data_quality_label = "blocked_by_non_tradable_rows" if not calibration_usable else "usable_with_caveat"
        elif corporate_action_within_504d:
            data_quality_label = "usable_with_caveat"
        else:
            data_quality_label = "clean_tradable_path"
        trigger["calibration_usable"] = calibration_usable
        item = {
            "code": code,
            "name": profile.get("current_or_latest_name"),
            "trigger_date": trigger_date,
            "entry_mode": entry_mode,
            "price_basis": price_basis,
            "entry_date": trigger.get("entry_date"),
            "entry_price": trigger.get("entry_price"),
            "calibration_usable": calibration_usable,
            "forward_window_trading_days": trigger.get("forward_window_trading_days"),
            "tradable_row_count": len(rows),
            "raw_row_count": len(raw_rows),
            "excluded_non_tradable_rows": raw_counts.get("non_tradable_zero_volume", 0),
            "excluded_zero_ohlc_rows": raw_counts.get("invalid_zero_ohlc", 0),
            "excluded_invalid_ohlc_rows": raw_counts.get("invalid_missing_ohlc", 0)
            + raw_counts.get("invalid_ohlc_inconsistent", 0)
            + raw_counts.get("suspicious_ohlc_repaired_candidate", 0),
            "corporate_action_within_180D": corporate_action_within_180d,
            "corporate_action_within_504D": corporate_action_within_504d,
            "corporate_action_candidate_dates": candidate_dates,
            "calibration_block_reasons": sorted(set(block_reasons)),
            "data_quality_label": data_quality_label,
            **contamination_fields,
            "MFE_30D_pct": trigger.get("MFE_30D_pct"),
            "MFE_90D_pct": trigger.get("MFE_90D_pct"),
            "MFE_180D_pct": trigger.get("MFE_180D_pct"),
            "MFE_1Y_pct": trigger.get("MFE_1Y_pct"),
            "MFE_2Y_pct": trigger.get("MFE_2Y_pct"),
            "MAE_30D_pct": trigger.get("MAE_30D_pct"),
            "MAE_90D_pct": trigger.get("MAE_90D_pct"),
            "MAE_180D_pct": trigger.get("MAE_180D_pct"),
            "MAE_1Y_pct": trigger.get("MAE_1Y_pct"),
            "MAE_2Y_pct": trigger.get("MAE_2Y_pct"),
            "below_entry_price_flag_30D": trigger.get("below_entry_price_flag_30D"),
            "below_entry_price_flag_90D": trigger.get("below_entry_price_flag_90D"),
            "peak_date": trigger.get("peak_date"),
            "peak_price": trigger.get("peak_price"),
            "drawdown_after_peak_pct": trigger.get("drawdown_after_peak_pct"),
            "path_summary": [
                {
                    "label": point.get("label"),
                    "trading_day_offset": point.get("trading_day_offset"),
                    "date": point.get("date"),
                    "close_return_pct": point.get("close_return_pct"),
                    "high_to_date_return_pct": point.get("high_to_date_return_pct"),
                    "low_to_date_return_pct": point.get("low_to_date_return_pct"),
                    "available": point.get("available"),
                }
                for point in path.get("points", [])
            ],
            "event_window_pre10_post10": event_rows,
            "ohlcv_sample": first_last_sample(year_rows) if include_ohlcv_sample else [],
            "warnings": trigger.get("warnings", []) + path.get("warnings", []) + sorted(set(block_reasons)),
        }
        output_items.append(item)
    return safe_json(
        {
            "pack_id": pack_id,
            "generated_at": utc_now(),
            "source_name": SOURCE_NAME,
            "source_repo_url": SOURCE_REPO_URL,
            "price_adjustment_status": PRICE_ADJUSTMENT_STATUS,
            "research_pack_default_price_basis": "tradable_raw",
            "price_basis": price_basis,
            "allow_raw_all": allow_raw_all,
            "block_corporate_action_window": block_corporate_action_window,
            "caveat": CAVEAT,
            "items": output_items,
        }
    )


def write_pack_md(path: Path, pack: dict[str, Any]) -> None:
    lines = [
        f"# Research Pack: {pack['pack_id']}",
        "",
        f"- Source: {SOURCE_NAME}",
        f"- Source repo: {SOURCE_REPO_URL}",
        f"- Price adjustment status: {PRICE_ADJUSTMENT_STATUS}",
        f"- Caveat: {CAVEAT}",
        "",
        "This is a collector-generated OHLC artifact for historical calibration. It is not investment advice.",
        "",
        "## Item Summary",
        "",
        "| code | name | trigger_date | entry_date | calibration_usable | quality | forward_days | MFE_90D | MAE_90D | block_reasons |",
        "|---|---|---|---|---:|---|---:|---:|---:|---|",
    ]
    for item in pack["items"]:
        lines.append(
            "| {code} | {name} | {trigger_date} | {entry_date} | {calibration_usable} | {data_quality_label} | {forward_window_trading_days} | {MFE_90D_pct} | {MAE_90D_pct} | {calibration_block_reasons} |".format(
                **{**item, "calibration_block_reasons": "; ".join(item.get("calibration_block_reasons", []))}
            )
        )
    lines.extend(["", "## Path Summary"])
    for item in pack["items"]:
        lines.extend(
            [
                "",
                f"### {item['code']} {item['name']}",
                "",
                "| label | date | close_return_pct | high_to_date_return_pct | low_to_date_return_pct | available |",
                "|---|---|---:|---:|---:|---:|",
            ]
        )
        for point in item["path_summary"]:
            lines.append(
                f"| {point.get('label')} | {point.get('date')} | {point.get('close_return_pct')} | {point.get('high_to_date_return_pct')} | {point.get('low_to_date_return_pct')} | {point.get('available')} |"
            )
    lines.extend(["", "## Machine-Readable JSON", "", "```json", json.dumps(pack, ensure_ascii=False, indent=2), "```", ""])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--items", required=True)
    parser.add_argument("--entry-mode", default="next_trading_day_close")
    parser.add_argument("--windows", default="30,90,180,252,504")
    parser.add_argument("--points", default="1,2,3,5,10,20,30,60,90,180,252,504")
    parser.add_argument("--event-window-pre", type=int, default=10)
    parser.add_argument("--event-window-post", type=int, default=10)
    parser.add_argument("--include-ohlcv-sample", default="true")
    parser.add_argument("--include-event-window", default="true")
    parser.add_argument("--price-basis", default="tradable_raw", choices=["tradable_raw", "raw_all"])
    parser.add_argument("--allow-raw-all", default="false")
    parser.add_argument("--block-corporate-action-window", default="true")
    parser.add_argument("--out-json", required=True)
    parser.add_argument("--out-md", required=True)
    args = parser.parse_args()

    out_json = ROOT / args.out_json if not Path(args.out_json).is_absolute() else Path(args.out_json)
    out_md = ROOT / args.out_md if not Path(args.out_md).is_absolute() else Path(args.out_md)
    pack = build_pack(
        parse_items(args.items),
        entry_mode=args.entry_mode,
        windows=parse_int_list(args.windows, DEFAULT_WINDOWS),
        points=parse_int_list(args.points, DEFAULT_POINTS),
        event_window_pre=args.event_window_pre,
        event_window_post=args.event_window_post,
        include_ohlcv_sample=args.include_ohlcv_sample.lower() == "true",
        include_event_window=args.include_event_window.lower() == "true",
        price_basis=args.price_basis,
        allow_raw_all=args.allow_raw_all.lower() == "true",
        block_corporate_action_window=args.block_corporate_action_window.lower() == "true",
        pack_id=out_json.stem,
    )
    write_json(out_json, pack)
    write_pack_md(out_md, pack)
    print(f"wrote {out_json}")
    print(f"wrote {out_md}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
