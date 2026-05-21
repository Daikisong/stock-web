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

from scripts.atlas_utils import ROW_STATUS_VALUES, classify_row

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


def parse_price_row(row: dict[str, str]) -> dict[str, Any]:
    parsed = {"date": row.get("d")}
    for short, long in [("o", "open"), ("h", "high"), ("l", "low"), ("c", "close"), ("v", "volume"), ("a", "amount"), ("mc", "marcap"), ("s", "stocks")]:
        parsed[long] = float(row[short]) if row.get(short) not in ("", None) else None
    parsed["market"] = row.get("m") or None
    parsed["row_status"] = row.get("rs") or "tradable_ohlcv"
    return parsed


def validate_tradable_shard(path: Path, errors: list[str]) -> int:
    seen = set()
    prev = ""
    count = 0
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        for key in ["d", "o", "h", "l", "c", "v"]:
            if key not in (reader.fieldnames or []):
                fail(errors, f"{path}: missing {key}")
        for raw in reader:
            count += 1
            d = raw["d"]
            if d < prev:
                fail(errors, f"{path}: dates not sorted")
            prev = d
            if d in seen:
                fail(errors, f"{path}: duplicate date {d}")
            seen.add(d)
            row = parse_price_row(raw)
            status = classify_row(row)
            if status != "tradable_ohlcv":
                fail(errors, f"{path}: non-tradable row {d} classified as {status}")
    return count


def validate_raw_shard(path: Path, errors: list[str]) -> int:
    count = 0
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        if "rs" not in (reader.fieldnames or []):
            fail(errors, f"{path}: missing row_status rs")
        for raw in reader:
            count += 1
            if raw.get("rs") not in ROW_STATUS_VALUES:
                fail(errors, f"{path}: invalid row_status {raw.get('rs')}")
    return count


def read_csv_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def main() -> int:
    errors: list[str] = []
    manifest_path = ATLAS_ROOT / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {}
    if not manifest:
        fail(errors, "atlas/manifest.json missing")
    if manifest.get("source_name") != "FinanceData/marcap":
        fail(errors, "manifest source_name mismatch")
    if manifest.get("price_adjustment_status") != "raw_unadjusted_marcap":
        fail(errors, "manifest price_adjustment_status mismatch")
    if manifest.get("ohlc_consistency_repair_applied_for_calibration") is not False:
        fail(errors, "calibration high/low repair flag must be false")

    profile_paths = sorted((ATLAS_ROOT / "symbol_profiles").glob("*/*.json"))
    all_symbols_path = ATLAS_ROOT / "universe" / "all_symbols.csv"
    all_symbol_rows = read_csv_rows(all_symbols_path) if all_symbols_path.exists() else []
    if len(all_symbol_rows) != len(profile_paths):
        fail(errors, "all_symbols.csv row count does not equal profile count")

    for path in profile_paths:
        profile = json.loads(path.read_text(encoding="utf-8"))
        code = profile.get("code", "")
        if not (len(code) == 6 and code.isdigit()):
            fail(errors, f"{path}: code is not 6 digits")
        for year_file in profile.get("year_files", []):
            if "ohlcv_tradable_by_symbol_year" not in year_file or not (ROOT / year_file).exists():
                fail(errors, f"{path}: bad tradable year_file {year_file}")
        for year_file in profile.get("raw_year_files", []):
            if "ohlcv_raw_by_symbol_year" not in year_file or not (ROOT / year_file).exists():
                fail(errors, f"{path}: bad raw_year_file {year_file}")

    tradable_paths = sorted((ATLAS_ROOT / "ohlcv_tradable_by_symbol_year").glob("*/*/*.csv"))
    raw_paths = sorted((ATLAS_ROOT / "ohlcv_raw_by_symbol_year").glob("*/*/*.csv"))
    compat_paths = sorted((ATLAS_ROOT / "ohlcv_min_by_symbol_year").glob("*/*/*.csv"))
    tradable_rows = sum(validate_tradable_shard(path, errors) for path in tradable_paths)
    raw_rows = sum(validate_raw_shard(path, errors) for path in raw_paths)
    compat_rows = sum(validate_tradable_shard(path, errors) for path in compat_paths)
    if manifest.get("tradable_row_count") != tradable_rows:
        fail(errors, f"manifest tradable_row_count {manifest.get('tradable_row_count')} != {tradable_rows}")
    if manifest.get("raw_row_count") != raw_rows:
        fail(errors, f"manifest raw_row_count {manifest.get('raw_row_count')} != {raw_rows}")
    if compat_rows != tradable_rows:
        fail(errors, "backward-compatible ohlcv_min_by_symbol_year is not equal row-count to tradable shards")

    for code in SAMPLE_CODES:
        if not (ATLAS_ROOT / "symbol_profiles" / code[:3] / f"{code}.json").exists():
            fail(errors, f"sample profile missing: {code}")

    raw_ecopro = ATLAS_ROOT / "ohlcv_raw_by_symbol_year" / "086" / "086520" / "2024.csv"
    tradable_ecopro = ATLAS_ROOT / "ohlcv_tradable_by_symbol_year" / "086" / "086520" / "2024.csv"
    raw_0409 = [row for row in read_csv_rows(raw_ecopro) if row["d"] == "2024-04-09"] if raw_ecopro.exists() else []
    tradable_0409 = [row for row in read_csv_rows(tradable_ecopro) if row["d"] == "2024-04-09"] if tradable_ecopro.exists() else []
    if not raw_0409:
        fail(errors, "086520 2024-04-09 missing from raw shard")
    elif raw_0409[0].get("rs") not in {"non_tradable_zero_volume", "invalid_zero_ohlc"}:
        fail(errors, f"086520 2024-04-09 unexpected raw row_status {raw_0409[0].get('rs')}")
    if tradable_0409:
        fail(errors, "086520 2024-04-09 must not exist in tradable shard")

    corp_path = ATLAS_ROOT / "corporate_actions" / "corporate_action_candidates.csv"
    corp_rows = read_csv_rows(corp_path) if corp_path.exists() else []
    if not any(row.get("code") == "086520" and row.get("date") == "2024-04-25" for row in corp_rows):
        fail(errors, "086520 corporate action candidate 2024-04-25 not detected")

    smoke_path = ATLAS_ROOT / "research_packs" / "smoke" / "smoke_005930_000660_298040_267260_086520.json"
    smoke_payload = json.loads(smoke_path.read_text(encoding="utf-8")) if smoke_path.exists() else {}
    smoke_items = {item.get("code"): item for item in smoke_payload.get("items", [])}
    if len(smoke_items) != 5:
        fail(errors, "smoke research pack item count != 5")
    ecopro = smoke_items.get("086520", {})
    if ecopro.get("MAE_90D_pct") == -100.0:
        fail(errors, "086520 smoke MAE_90D_pct still has -100 artifact")
    if ecopro.get("calibration_usable") is not False or "corporate_action_within_180D" not in ecopro.get("calibration_block_reasons", []):
        fail(errors, "086520 smoke must be blocked by corporate_action_within_180D")
    for code, item in smoke_items.items():
        if item.get("calibration_usable"):
            if item.get("window_180D_corporate_action_contaminated"):
                fail(errors, f"{code} usable despite 180D corporate-action contamination")
            if item.get("forward_window_trading_days", 0) < 180:
                fail(errors, f"{code} usable with insufficient forward window")
            for key in ["MFE_30D_pct", "MFE_90D_pct", "MFE_180D_pct", "MAE_30D_pct", "MAE_90D_pct", "MAE_180D_pct"]:
                if item.get(key) is None:
                    fail(errors, f"{code} usable but missing {key}")

    bundle_text = (DIAGNOSTICS_ROOT / "chatgpt_bundle.txt").read_text(encoding="utf-8") if (DIAGNOSTICS_ROOT / "chatgpt_bundle.txt").exists() else ""
    if "ECOPRO_ZERO_ROW_CHECK" not in bundle_text or "ECOPRO_CORP_ACTION_CHECK" not in bundle_text:
        fail(errors, "diagnostics bundle missing Ecopro checks")

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
        f"- raw_shard_count: {len(raw_paths)}",
        f"- tradable_shard_count: {len(tradable_paths)}",
        f"- compat_shard_count: {len(compat_paths)}",
        f"- raw_rows: {raw_rows}",
        f"- tradable_rows: {tradable_rows}",
        f"- compat_rows: {compat_rows}",
        f"- corporate_action_candidates: {len(corp_rows)}",
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
