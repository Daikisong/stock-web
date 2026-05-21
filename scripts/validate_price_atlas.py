from __future__ import annotations

import csv
import json
import math
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.atlas_utils import compute_trigger_backtest, load_symbol_rows

ATLAS_ROOT = ROOT / "atlas"
DIAGNOSTICS_ROOT = ROOT / "diagnostics"
SAMPLE_CODES = ["005930", "000660", "298040", "267260", "086520", "247540", "035420", "035720"]


def fail(errors: list[str], message: str) -> None:
    errors.append(message)


def walk_json(value: Any):
    if isinstance(value, dict):
        for item in value.values():
            yield from walk_json(item)
    elif isinstance(value, list):
        for item in value:
            yield from walk_json(item)
    else:
        yield value


def validate_shard(path: Path, errors: list[str]) -> tuple[int, str | None, str | None]:
    seen = set()
    prev = ""
    count = 0
    first = None
    last = None
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        required = ["d", "o", "h", "l", "c", "v"]
        for key in required:
            if key not in (reader.fieldnames or []):
                fail(errors, f"{path}: missing {key}")
        for row in reader:
            count += 1
            d = row["d"]
            if d < prev:
                fail(errors, f"{path}: dates not sorted")
            prev = d
            if d in seen:
                fail(errors, f"{path}: duplicate date {d}")
            seen.add(d)
            first = first or d
            last = d
            try:
                o = float(row["o"]) if row["o"] else None
                h = float(row["h"]) if row["h"] else None
                l = float(row["l"]) if row["l"] else None
                c = float(row["c"]) if row["c"] else None
            except ValueError:
                fail(errors, f"{path}: invalid numeric value")
                continue
            if c is None:
                fail(errors, f"{path}: close missing")
            if row.get("v", "") == "":
                fail(errors, f"{path}: volume missing")
            if h is not None and l is not None and h < l:
                fail(errors, f"{path}: high < low")
            if h is not None and o is not None and h < o:
                fail(errors, f"{path}: high < open")
            if h is not None and c is not None and h < c:
                fail(errors, f"{path}: high < close")
            if l is not None and o is not None and l > o:
                fail(errors, f"{path}: low > open")
            if l is not None and c is not None and l > c:
                fail(errors, f"{path}: low > close")
    return count, first, last


def main() -> int:
    errors: list[str] = []
    manifest_path = ATLAS_ROOT / "manifest.json"
    if not manifest_path.exists():
        fail(errors, "atlas/manifest.json missing")
        manifest = {}
    else:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("source_name") != "FinanceData/marcap":
        fail(errors, "manifest source_name mismatch")
    if manifest.get("price_adjustment_status") != "raw_unadjusted_marcap":
        fail(errors, "manifest price_adjustment_status mismatch")

    profile_paths = sorted((ATLAS_ROOT / "symbol_profiles").glob("*/*.json"))
    all_symbols_path = ATLAS_ROOT / "universe" / "all_symbols.csv"
    if all_symbols_path.exists():
        with all_symbols_path.open("r", encoding="utf-8", newline="") as handle:
            all_symbol_rows = list(csv.DictReader(handle))
        if len(all_symbol_rows) != len(profile_paths):
            fail(errors, "all_symbols.csv row count does not equal profile count")
    else:
        fail(errors, "all_symbols.csv missing")

    for path in profile_paths:
        profile = json.loads(path.read_text(encoding="utf-8"))
        code = profile.get("code", "")
        if not (len(code) == 6 and code.isdigit()):
            fail(errors, f"{path}: code is not 6 digits")
        if profile.get("status_inferred") not in {"active_like", "inactive_or_delisted_like"}:
            fail(errors, f"{path}: invalid status_inferred")
        for year_file in profile.get("year_files", []):
            if not (ROOT / year_file).exists():
                fail(errors, f"{path}: missing year_file {year_file}")

    shard_paths = sorted((ATLAS_ROOT / "ohlcv_min_by_symbol_year").glob("*/*/*.csv"))
    total_shard_rows = 0
    for path in shard_paths:
        count, _first, _last = validate_shard(path, errors)
        total_shard_rows += count
    if manifest.get("row_count") != total_shard_rows:
        fail(errors, f"manifest row_count {manifest.get('row_count')} does not equal total shard rows {total_shard_rows}")

    for code in SAMPLE_CODES:
        profile = ATLAS_ROOT / "symbol_profiles" / code[:3] / f"{code}.json"
        if not profile.exists():
            fail(errors, f"sample code profile missing: {code}")
        shard_2024 = ATLAS_ROOT / "ohlcv_min_by_symbol_year" / code[:3] / code / "2024.csv"
        if shard_2024.exists():
            count, _first, _last = validate_shard(shard_2024, errors)
            if count == 0:
                fail(errors, f"{code} 2024 shard empty")
        try:
            symbol_rows = load_symbol_rows(ATLAS_ROOT, code)
        except FileNotFoundError:
            symbol_rows = []
        if symbol_rows:
            forward = compute_trigger_backtest(symbol_rows, "2024-01-02", "next_trading_day_close", [30, 90, 180], 180)
            if forward.get("entry_date") and forward.get("forward_window_trading_days", 0) < 180:
                fail(errors, f"{code} has fewer than 180 forward trading days from 2024-01-03")

    smoke = ATLAS_ROOT / "research_packs" / "smoke" / "smoke_005930_000660_298040_267260_086520.json"
    if not smoke.exists():
        fail(errors, "smoke research pack missing")
    else:
        smoke_payload = json.loads(smoke.read_text(encoding="utf-8"))
        if len(smoke_payload.get("items", [])) != 5:
            fail(errors, "smoke research pack item count != 5")
        for item in smoke_payload.get("items", []):
            if item.get("calibration_usable"):
                for key in ["MFE_30D_pct", "MFE_90D_pct", "MFE_180D_pct", "MAE_30D_pct", "MAE_90D_pct", "MAE_180D_pct"]:
                    if item.get(key) is None:
                        fail(errors, f"smoke item {item.get('code')} missing {key}")
                if not any(point.get("trading_day_offset") == 180 and point.get("available") for point in item.get("path_summary", [])):
                    fail(errors, f"smoke item {item.get('code')} missing D+180")

    for path in list(ATLAS_ROOT.rglob("*.json")) + list(DIAGNOSTICS_ROOT.rglob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        if any(isinstance(value, float) and math.isnan(value) for value in walk_json(payload)):
            fail(errors, f"{path}: contains NaN")

    generated_files = [path for path in list(ATLAS_ROOT.rglob("*")) + list(DIAGNOSTICS_ROOT.rglob("*")) if path.is_file()]
    largest = max((path.stat().st_size for path in generated_files), default=0)
    over_50 = [str(path) for path in generated_files if path.stat().st_size > 50 * 1024 * 1024]
    over_100 = [str(path) for path in generated_files if path.stat().st_size > 100 * 1024 * 1024]
    if over_50:
        fail(errors, f"files over 50 MiB: {over_50[:5]}")
    if over_100:
        fail(errors, f"files over 100 MiB: {over_100[:5]}")

    report_lines = [
        "# Atlas Validation Report",
        "",
        f"- status: {'pass' if not errors else 'fail'}",
        f"- manifest_exists: {manifest_path.exists()}",
        f"- profile_count: {len(profile_paths)}",
        f"- shard_count: {len(shard_paths)}",
        f"- total_shard_rows: {total_shard_rows}",
        f"- manifest_row_count: {manifest.get('row_count')}",
        f"- largest_file_mb: {round(largest / 1024 / 1024, 2)}",
        f"- files_over_50_mib: {len(over_50)}",
        f"- files_over_100_mib: {len(over_100)}",
        f"- inactive_status_note: inactive_or_delisted_like is inferred only from row presence, not official status.",
        "",
        "## Errors",
        "",
    ]
    report_lines.extend([f"- {error}" for error in errors] or ["- none"])
    DIAGNOSTICS_ROOT.mkdir(parents=True, exist_ok=True)
    (DIAGNOSTICS_ROOT / "atlas_validation_report.md").write_text("\n".join(report_lines) + "\n", encoding="utf-8")
    print("\n".join(report_lines))
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
