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
    PRICE_ADJUSTMENT_STATUS,
    SOURCE_NAME,
    SOURCE_REPO_URL,
    compute_event_window,
    compute_path_summary,
    compute_trigger_backtest,
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
    pack_id: str = "research_pack",
    atlas_root: Path = ATLAS_ROOT,
) -> dict[str, Any]:
    windows = windows or DEFAULT_WINDOWS
    points = points or DEFAULT_POINTS
    output_items = []
    for code, trigger_date in items:
        profile = load_profile(atlas_root, code)
        rows = load_symbol_rows(atlas_root, code)
        trigger = compute_trigger_backtest(rows, trigger_date, entry_mode, windows, max(windows))
        entry_date = trigger.get("entry_date") or trigger_date
        path = compute_path_summary(rows, entry_date, points, "trigger_close")
        event_rows = compute_event_window(rows, trigger_date, event_window_pre, event_window_post) if include_event_window else []
        year_rows = load_symbol_rows(atlas_root, code, f"{trigger_date[:4]}-01-01", f"{trigger_date[:4]}-12-31")
        item = {
            "code": code,
            "name": profile.get("current_or_latest_name"),
            "trigger_date": trigger_date,
            "entry_mode": entry_mode,
            "entry_date": trigger.get("entry_date"),
            "entry_price": trigger.get("entry_price"),
            "calibration_usable": trigger.get("calibration_usable"),
            "forward_window_trading_days": trigger.get("forward_window_trading_days"),
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
            "warnings": trigger.get("warnings", []) + path.get("warnings", []),
        }
        output_items.append(item)
    return safe_json(
        {
            "pack_id": pack_id,
            "generated_at": utc_now(),
            "source_name": SOURCE_NAME,
            "source_repo_url": SOURCE_REPO_URL,
            "price_adjustment_status": PRICE_ADJUSTMENT_STATUS,
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
        "| code | name | trigger_date | entry_date | calibration_usable | forward_days | MFE_90D | MAE_90D | warnings |",
        "|---|---|---|---|---:|---:|---:|---:|---|",
    ]
    for item in pack["items"]:
        lines.append(
            "| {code} | {name} | {trigger_date} | {entry_date} | {calibration_usable} | {forward_window_trading_days} | {MFE_90D_pct} | {MAE_90D_pct} | {warnings} |".format(
                **{**item, "warnings": "; ".join(item.get("warnings", []))}
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
        pack_id=out_json.stem,
    )
    write_json(out_json, pack)
    write_pack_md(out_md, pack)
    print(f"wrote {out_json}")
    print(f"wrote {out_md}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
