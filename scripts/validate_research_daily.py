from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.atlas_utils import read_marcap_file
from scripts.research_daily_utils import (
    CALENDAR_COLUMNS,
    CALENDAR_PATH,
    RESEARCH_DAILY_ROOT,
    SNAPSHOT_COLUMNS,
    atomic_write_json,
    atomic_write_text,
    parse_markets,
    prepare_daily_source_frame,
    rel_path,
    sha256_file,
    source_files_for_range,
    utc_now,
)

SOURCE_REPO_PATH = ROOT / ".cache" / "marcap"
ATLAS_ROOT = ROOT / "atlas"
DIAGNOSTICS_ROOT = ROOT / "diagnostics"
RD_ROOT = ROOT / RESEARCH_DAILY_ROOT


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def read_csv_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def count_csv_rows(path: Path) -> tuple[int, Counter[str], dict[str, str] | None, list[str]]:
    row_count = 0
    codes: Counter[str] = Counter()
    first_row: dict[str, str] | None = None
    failures: list[str] = []
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames != SNAPSHOT_COLUMNS:
            failures.append(f"{path}: snapshot columns mismatch")
        for row in reader:
            row_count += 1
            if first_row is None:
                first_row = row
            code = row.get("code", "")
            codes[code] += 1
            if len(code) != 6 or not code.isdigit():
                failures.append(f"{path}: invalid code {code}")
            if row.get("snapshot_date") != first_row.get("snapshot_date"):
                failures.append(f"{path}: mixed snapshot_date")
            if row.get("max_source_date", "") > row.get("snapshot_date", ""):
                failures.append(f"{path}: max_source_date exceeds snapshot_date")
            if row.get("name_resolution_status") == "ambiguous_history_match" and row.get("name"):
                failures.append(f"{path}: ambiguous name was silently resolved for {code}")
    duplicated = [code for code, count in codes.items() if count > 1]
    if duplicated:
        failures.append(f"{path}: duplicate codes {duplicated[:5]}")
    return row_count, codes, first_row, failures


def source_expected_counts(start_date: str, end_date: str, markets: list[str]) -> dict[str, int]:
    source_start_year = int(start_date[:4]) - 1
    source_end_year = int(end_date[:4])
    counts: Counter[str] = Counter()
    for path in source_files_for_range(SOURCE_REPO_PATH, source_start_year, source_end_year):
        frame = prepare_daily_source_frame(read_marcap_file(path), markets)
        frame = frame[(frame["date"] >= start_date) & (frame["date"] <= end_date)]
        if frame.empty:
            continue
        counts.update(frame.groupby("date")["code"].count().astype(int).to_dict())
    return dict(counts)


def source_trade_dates(start_date: str, end_date: str, markets: list[str]) -> tuple[list[str], str | None]:
    source_start_year = int(start_date[:4]) - 1
    source_end_year = int(end_date[:4])
    dates: set[str] = set()
    for path in source_files_for_range(SOURCE_REPO_PATH, source_start_year, source_end_year):
        frame = prepare_daily_source_frame(read_marcap_file(path), markets)
        if frame.empty:
            continue
        dates.update(str(item) for item in frame.loc[frame["date"] <= end_date, "date"].dropna().unique().tolist())
    sorted_dates = sorted(dates)
    research_dates = [item for item in sorted_dates if start_date <= item <= end_date]
    seed_candidates = [item for item in sorted_dates if item < research_dates[0]]
    return research_dates, seed_candidates[-1] if seed_candidates else None


def load_tradable_shard_row(code: str, trade_date: str) -> dict[str, str] | None:
    path = ATLAS_ROOT / "ohlcv_tradable_by_symbol_year" / code[:3] / code / f"{trade_date[:4]}.csv"
    if not path.exists():
        return None
    with path.open("r", encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            if row.get("d") == trade_date:
                return row
    return None


def no_nan_in_json(value: Any) -> bool:
    if isinstance(value, dict):
        return all(no_nan_in_json(item) for item in value.values())
    if isinstance(value, list):
        return all(no_nan_in_json(item) for item in value)
    return not (isinstance(value, float) and math.isnan(value))


def numeric_equal(left: str, right: str) -> bool:
    try:
        return float(left) == float(right)
    except (TypeError, ValueError):
        return left == right


def validate_research_daily(full: bool = False) -> dict[str, Any]:
    failures: list[str] = []
    manifest_path = RD_ROOT / "manifest.json"
    schema_path = RD_ROOT / "schema.json"
    calendar_path = ROOT / CALENDAR_PATH
    if not manifest_path.exists():
        failures.append("missing atlas/research_daily/manifest.json")
        manifest: dict[str, Any] = {}
    else:
        manifest = read_json(manifest_path)
    if not schema_path.exists():
        failures.append("missing atlas/research_daily/schema.json")
        schema = {}
    else:
        schema = read_json(schema_path)
        if schema.get("columns") != SNAPSHOT_COLUMNS:
            failures.append("schema columns do not match required snapshot columns")
    if not calendar_path.exists():
        failures.append("missing atlas/research_daily/trading_calendar.csv")
        calendar_rows: list[dict[str, str]] = []
    else:
        with calendar_path.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            if reader.fieldnames != CALENDAR_COLUMNS:
                failures.append("trading_calendar columns mismatch")
            calendar_rows = list(reader)

    trade_dates = [row.get("trade_date", "") for row in calendar_rows]
    if len(trade_dates) != len(set(trade_dates)):
        failures.append("duplicate trade_date in trading_calendar")
    if trade_dates != sorted(trade_dates):
        failures.append("trading_calendar is not sorted by trade_date")

    start_date = manifest.get("research_start_date") or "2016-01-01"
    end_date = manifest.get("max_trade_date") or ""
    markets = parse_markets(",".join(manifest.get("markets", [])) if manifest.get("markets") else None)
    expected_dates: list[str] = []
    expected_seed = None
    expected_counts: dict[str, int] = {}
    if full and end_date:
        expected_dates, expected_seed = source_trade_dates(start_date, end_date, markets)
        if expected_dates != trade_dates:
            missing = sorted(set(expected_dates) - set(trade_dates))
            extra = sorted(set(trade_dates) - set(expected_dates))
            failures.append(f"trade date mismatch missing={missing[:5]} extra={extra[:5]}")
        if expected_seed != manifest.get("seed_snapshot_date"):
            failures.append(f"seed snapshot mismatch expected={expected_seed} actual={manifest.get('seed_snapshot_date')}")
        expected_counts = source_expected_counts(expected_seed or start_date, end_date, markets)

    access_paths = set()
    snapshot_paths = set()
    calendar_ok = 0
    for index, row in enumerate(calendar_rows):
        trade_date = row["trade_date"]
        previous_trade_date = row["previous_trade_date"]
        expected_previous = manifest.get("seed_snapshot_date") if index == 0 else calendar_rows[index - 1]["trade_date"]
        if previous_trade_date != expected_previous:
            failures.append(f"{trade_date}: previous_trade_date mismatch expected={expected_previous} actual={previous_trade_date}")
        if row["blind_snapshot_date"] != previous_trade_date:
            failures.append(f"{trade_date}: blind_snapshot_date does not equal previous_trade_date")
        if row["outcome_snapshot_date"] != trade_date:
            failures.append(f"{trade_date}: outcome_snapshot_date does not equal trade_date")
        access_path = ROOT / row["access_manifest_path"]
        if not access_path.exists():
            failures.append(f"{trade_date}: missing access manifest {row['access_manifest_path']}")
            continue
        access_paths.add(access_path)
        access = read_json(access_path)
        if access.get("blind_snapshot_path") != row["blind_snapshot_path"] or access.get("outcome_snapshot_path") != row["outcome_snapshot_path"]:
            failures.append(f"{trade_date}: access path payload mismatch")
        for prefix in ["blind", "outcome"]:
            snap_path = ROOT / row[f"{prefix}_snapshot_path"]
            snapshot_paths.add(snap_path)
            if not snap_path.exists():
                failures.append(f"{trade_date}: missing {prefix} snapshot {snap_path}")
                continue
            if sha256_file(snap_path) != row[f"{prefix}_snapshot_sha256"]:
                failures.append(f"{trade_date}: {prefix} snapshot sha256 mismatch")
            if snap_path.stat().st_size != int(row[f"{prefix}_snapshot_bytes"]):
                failures.append(f"{trade_date}: {prefix} snapshot byte count mismatch")
            if snap_path.stat().st_size > 50 * 1024 * 1024:
                failures.append(f"{snap_path}: file exceeds 50 MiB")
            if snap_path.stat().st_size > 100 * 1024 * 1024:
                failures.append(f"{snap_path}: file exceeds 100 MiB")
        calendar_ok += 1

    snapshot_validation_count = 0
    total_rows = 0
    data_quality_counter: Counter[str] = Counter()
    upper_status_counter: Counter[str] = Counter()
    snapshot_dates_from_files: set[str] = set()
    for snap_path in sorted(snapshot_paths):
        row_count, codes, first_row, row_failures = count_csv_rows(snap_path)
        failures.extend(row_failures[:10])
        snapshot_validation_count += 1
        total_rows += row_count
        if first_row:
            snapshot_date = first_row["snapshot_date"]
            snapshot_dates_from_files.add(snapshot_date)
            if full and expected_counts and row_count != expected_counts.get(snapshot_date, 0):
                failures.append(f"{snapshot_date}: row_count mismatch expected={expected_counts.get(snapshot_date, 0)} actual={row_count}")
        with snap_path.open("r", encoding="utf-8", newline="") as handle:
            for row in csv.DictReader(handle):
                data_quality_counter[row.get("data_quality_status", "")] += 1
                upper_status_counter[row.get("upper_limit_label_status", "")] += 1

    all_snapshot_files = {path for path in (RD_ROOT / "snapshots").rglob("*.csv")}
    expected_snapshot_files = snapshot_paths
    extra_snapshot_files = all_snapshot_files - expected_snapshot_files
    missing_snapshot_files = expected_snapshot_files - all_snapshot_files
    if extra_snapshot_files:
        failures.append(f"non-calendar snapshot files exist: {[rel_path(path, ROOT) for path in sorted(extra_snapshot_files)[:5]]}")
    if missing_snapshot_files:
        failures.append(f"calendar snapshot files missing: {[rel_path(path, ROOT) for path in sorted(missing_snapshot_files)[:5]]}")

    all_access_files = {path for path in (RD_ROOT / "access").rglob("*.json")}
    extra_access_files = all_access_files - access_paths
    if extra_access_files:
        failures.append(f"non-calendar access files exist: {[rel_path(path, ROOT) for path in sorted(extra_access_files)[:5]]}")

    smoke = validate_20260622_smoke()
    failures.extend(smoke["failures"])
    for path in [manifest_path, schema_path, DIAGNOSTICS_ROOT / "research_daily_build_report.json"]:
        if path.exists() and not no_nan_in_json(read_json(path)):
            failures.append(f"{path}: contains NaN")

    report = {
        "generated_at": utc_now(),
        "ok": not failures,
        "full": full,
        "trade_date_count": len(trade_dates),
        "snapshot_validation_count": snapshot_validation_count,
        "access_manifest_count": len(access_paths),
        "total_rows_checked": total_rows,
        "missing_dates": sorted(set(expected_dates) - set(trade_dates)) if expected_dates else [],
        "missing_files": [rel_path(path, ROOT) for path in sorted(missing_snapshot_files)] if "missing_snapshot_files" in locals() else [],
        "failure_count": len(failures),
        "failures": failures[:200],
        "data_quality_status_counts": dict(data_quality_counter),
        "upper_limit_label_status_counts": dict(upper_status_counter),
        "smoke_20260622": smoke,
    }
    DIAGNOSTICS_ROOT.mkdir(exist_ok=True)
    atomic_write_json(DIAGNOSTICS_ROOT / "research_daily_validation_report.json", report, overwrite=True)
    atomic_write_text(
        DIAGNOSTICS_ROOT / "research_daily_validation_report.md",
        "\n".join(
            [
                "# Research Daily Validation Report",
                "",
                f"- ok: {str(report['ok']).lower()}",
                f"- full: {str(full).lower()}",
                f"- trade_date_count: {report['trade_date_count']}",
                f"- snapshot_validation_count: {report['snapshot_validation_count']}",
                f"- access_manifest_count: {report['access_manifest_count']}",
                f"- total_rows_checked: {report['total_rows_checked']}",
                f"- failure_count: {report['failure_count']}",
                f"- data_quality_status_counts: {json.dumps(report['data_quality_status_counts'], ensure_ascii=False)}",
                f"- upper_limit_label_status_counts: {json.dumps(report['upper_limit_label_status_counts'], ensure_ascii=False)}",
                "",
                "## Failures",
                *(f"- {failure}" for failure in report["failures"]),
                "",
            ]
        ),
        overwrite=True,
    )

    if manifest_path.exists():
        manifest_payload = read_json(manifest_path)
        manifest_payload["validation_passed"] = report["ok"]
        atomic_write_json(manifest_path, manifest_payload, overwrite=True)
    atlas_manifest_path = ATLAS_ROOT / "manifest.json"
    if atlas_manifest_path.exists():
        atlas_manifest = read_json(atlas_manifest_path)
        if "research_daily" in atlas_manifest:
            atlas_manifest["research_daily"]["validation_passed"] = report["ok"]
            atomic_write_json(atlas_manifest_path, atlas_manifest, overwrite=True)
    return report


def validate_20260622_smoke() -> dict[str, Any]:
    failures: list[str] = []
    access_path = RD_ROOT / "access" / "2026" / "06" / "20260622.json"
    if not access_path.exists():
        return {"ok": False, "failures": ["missing 20260622 access manifest"]}
    access = read_json(access_path)
    if access.get("blind_snapshot_date") != "2026-06-19":
        failures.append(f"20260622 blind date mismatch: {access.get('blind_snapshot_date')}")
    if access.get("outcome_snapshot_date") != "2026-06-22":
        failures.append(f"20260622 outcome date mismatch: {access.get('outcome_snapshot_date')}")
    blind_path = ROOT / access["blind_snapshot_path"]
    outcome_path = ROOT / access["outcome_snapshot_path"]
    if blind_path.exists():
        with blind_path.open("r", encoding="utf-8", newline="") as handle:
            for index, row in enumerate(csv.DictReader(handle)):
                if row.get("max_source_date", "") > "2026-06-19":
                    failures.append("20260622 blind snapshot has future max_source_date")
                    break
                if index > 100:
                    break
    else:
        failures.append("20260622 blind snapshot missing")
    samsung_snapshot: dict[str, str] | None = None
    if outcome_path.exists():
        with outcome_path.open("r", encoding="utf-8", newline="") as handle:
            rows = list(csv.DictReader(handle))
        if not rows:
            failures.append("20260622 outcome snapshot is empty")
        samsung_snapshot = next((row for row in rows if row.get("code") == "005930"), None)
        if samsung_snapshot is None:
            failures.append("20260622 outcome snapshot missing 005930")
        if len(rows) != int(access["outcome_snapshot_row_count"]):
            failures.append("20260622 outcome row count differs from access manifest")
    else:
        failures.append("20260622 outcome snapshot missing")
    shard = load_tradable_shard_row("005930", "2026-06-22")
    if samsung_snapshot and shard:
        mapping = {
            "open": "o",
            "high": "h",
            "low": "l",
            "close": "c",
            "volume": "v",
            "amount": "a",
            "market_cap": "mc",
            "listed_shares": "s",
            "market": "m",
        }
        for snapshot_key, shard_key in mapping.items():
            if not numeric_equal(samsung_snapshot.get(snapshot_key, ""), shard.get(shard_key, "")):
                failures.append(f"005930 2026-06-22 mismatch {snapshot_key}: snapshot={samsung_snapshot.get(snapshot_key)} shard={shard.get(shard_key)}")
    elif not shard:
        failures.append("005930 2026 tradable shard row missing")
    return {"ok": not failures, "failures": failures}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate research_daily access manifests and snapshots.")
    parser.add_argument("--full", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    report = validate_research_daily(full=args.full)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
