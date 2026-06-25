from __future__ import annotations

import csv
import json
import subprocess
import sys
from collections import Counter
from pathlib import Path

from scripts.atlas_utils import read_marcap_file
from scripts.research_daily_utils import (
    SNAPSHOT_COLUMNS,
    compute_limit_up_price,
    prepare_daily_source_frame,
    resolve_name,
    sha256_file,
)
from scripts.build_research_daily import build_snapshot_row, state_seed

ROOT = Path(__file__).resolve().parents[1]
ATLAS = ROOT / "atlas"
RD = ATLAS / "research_daily"


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def numeric_equal(left: str, right: str) -> bool:
    try:
        return float(left) == float(right)
    except (TypeError, ValueError):
        return left == right


def test_research_daily_manifest_exists():
    assert (RD / "manifest.json").exists()


def test_schema_columns_are_exact():
    schema = json.loads((RD / "schema.json").read_text(encoding="utf-8"))
    assert schema["columns"] == SNAPSHOT_COLUMNS


def test_calendar_has_no_duplicate_dates():
    rows = read_csv(RD / "trading_calendar.csv")
    dates = [row["trade_date"] for row in rows]
    assert dates == sorted(dates)
    assert len(dates) == len(set(dates))


def test_calendar_paths_exist():
    for row in read_csv(RD / "trading_calendar.csv")[:20]:
        assert (ROOT / row["access_manifest_path"]).exists()
        assert (ROOT / row["blind_snapshot_path"]).exists()
        assert (ROOT / row["outcome_snapshot_path"]).exists()


def test_blind_path_points_to_previous_trade_date():
    row = next(item for item in read_csv(RD / "trading_calendar.csv") if item["trade_date"] == "2026-06-22")
    assert row["previous_trade_date"] == "2026-06-19"
    assert row["blind_snapshot_date"] == row["previous_trade_date"]
    assert row["blind_snapshot_path"].endswith("/20260619.csv")


def test_no_future_date_in_blind_snapshot():
    access = json.loads((RD / "access" / "2026" / "06" / "20260622.json").read_text(encoding="utf-8"))
    rows = read_csv(ROOT / access["blind_snapshot_path"])
    assert rows
    assert {row["snapshot_date"] for row in rows} == {"2026-06-19"}
    assert all(row["max_source_date"] <= "2026-06-19" for row in rows)


def test_snapshot_has_unique_zero_padded_codes():
    rows = read_csv(RD / "snapshots" / "2026" / "06" / "20260622.csv")
    codes = [row["code"] for row in rows]
    assert all(len(code) == 6 and code.isdigit() for code in codes)
    assert len(codes) == len(set(codes))


def test_snapshot_row_count_matches_source_day():
    source = read_marcap_file(ROOT / ".cache" / "marcap" / "data" / "marcap-2026.parquet")
    frame = prepare_daily_source_frame(source, ["KOSPI", "KOSDAQ", "KOSDAQ GLOBAL"])
    expected = int((frame["date"] == "2026-06-22").sum())
    rows = read_csv(RD / "snapshots" / "2026" / "06" / "20260622.csv")
    assert len(rows) == expected


def test_samsung_values_match_source_shard():
    snapshot = next(row for row in read_csv(RD / "snapshots" / "2026" / "06" / "20260622.csv") if row["code"] == "005930")
    shard = next(row for row in read_csv(ATLAS / "ohlcv_tradable_by_symbol_year" / "005" / "005930" / "2026.csv") if row["d"] == "2026-06-22")
    assert numeric_equal(snapshot["open"], shard["o"])
    assert numeric_equal(snapshot["high"], shard["h"])
    assert numeric_equal(snapshot["low"], shard["l"])
    assert numeric_equal(snapshot["close"], shard["c"])
    assert numeric_equal(snapshot["volume"], shard["v"])
    assert numeric_equal(snapshot["amount"], shard["a"])
    assert numeric_equal(snapshot["market_cap"], shard["mc"])
    assert numeric_equal(snapshot["listed_shares"], shard["s"])
    assert snapshot["market"] == shard["m"]


def test_historical_name_does_not_use_future_name():
    history = {"123456": [{"code": "123456", "name": "FutureName", "first_date": "2025-01-01", "last_date": "2025-12-31"}]}
    name, status, candidates = resolve_name(None, "123456", "2024-01-02", history)
    assert name is None
    assert status == "unresolved"
    assert candidates is None


def test_ambiguous_name_is_not_silently_resolved():
    history = {
        "123456": [
            {"code": "123456", "name": "A", "first_date": "2024-01-01", "last_date": "2024-12-31"},
            {"code": "123456", "name": "B", "first_date": "2024-01-01", "last_date": "2024-12-31"},
        ]
    }
    name, status, candidates = resolve_name(None, "123456", "2024-06-01", history)
    assert name is None
    assert status == "ambiguous_history_match"
    assert candidates == "A|B"


def synthetic_row(close: int = 13000, high: int = 13000, low: int = 12900, open_: int = 12900):
    return {
        "date": "2024-01-03",
        "code": "123456",
        "name": "테스트",
        "resolved_name": "테스트",
        "name_resolution_status": "exact_source_row",
        "name_candidates": None,
        "market": "KOSPI",
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "volume": 10,
        "amount": 130000,
        "marcap": 1300000,
        "stocks": 100,
    }


def seeded_state():
    state = state_seed()
    state["history"].append({"date": "2024-01-02", "close": 10000})
    return state


def test_upper_limit_touch():
    row = build_snapshot_row(synthetic_row(close=12900, high=13000), seeded_state(), "2024-01-02", set())
    assert row["limit_up_price"] == 13000
    assert row["upper_limit_touched"] is True


def test_upper_limit_close():
    row = build_snapshot_row(synthetic_row(close=13000, high=13000), seeded_state(), "2024-01-02", set())
    assert row["upper_limit_closed"] is True


def test_upper_limit_release():
    row = build_snapshot_row(synthetic_row(close=12900, high=13000), seeded_state(), "2024-01-02", set())
    assert row["upper_limit_released"] is True


def test_one_price_upper_limit():
    row = build_snapshot_row(synthetic_row(close=13000, high=13000, low=13000, open_=13000), seeded_state(), "2024-01-02", set())
    assert row["one_price_upper_limit"] is True


def test_new_listing_has_no_false_limit_label():
    row = build_snapshot_row(synthetic_row(), state_seed(), "2024-01-02", set())
    assert row["upper_limit_label_status"] == "blocked_new_listing"
    assert row["upper_limit_touched"] is None
    assert row["new_listing_or_no_reference"] is True


def test_corporate_action_blocks_limit_label():
    row = build_snapshot_row(synthetic_row(), seeded_state(), "2024-01-02", {"2024-01-03"})
    assert row["upper_limit_label_status"] == "blocked_corporate_action"
    assert row["corporate_action_warning"] is True
    assert row["upper_limit_touched"] is None


def test_incremental_build_is_idempotent():
    report_path = ROOT / "diagnostics" / "research_daily_build_report.json"
    if not report_path.exists():
        return
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report.get("snapshot_changed", 0) == 0
    assert report.get("access_changed", 0) == 0


def test_access_manifest_hashes_match_files():
    access = json.loads((RD / "access" / "2026" / "06" / "20260622.json").read_text(encoding="utf-8"))
    assert sha256_file(ROOT / access["blind_snapshot_path"]) == access["blind_snapshot_sha256"]
    assert sha256_file(ROOT / access["outcome_snapshot_path"]) == access["outcome_snapshot_sha256"]


def test_no_research_daily_file_over_50_mib():
    oversized = [path for path in RD.rglob("*") if path.is_file() and path.stat().st_size > 50 * 1024 * 1024]
    assert oversized == []


def test_20260622_access_bundle_is_complete():
    access = json.loads((RD / "access" / "2026" / "06" / "20260622.json").read_text(encoding="utf-8"))
    assert access["build_status"] == "complete"
    assert access["blind_snapshot_row_count"] > 0
    assert access["outcome_snapshot_row_count"] > 0
    assert access["blind_max_source_date"] == "2026-06-19"
    assert access["outcome_max_source_date"] == "2026-06-22"


def test_limit_up_price_uses_2023_tick_change():
    assert compute_limit_up_price(1600, "2024-01-02", "KOSPI") == 2080
    assert compute_limit_up_price(1600, "2022-01-03", "KOSDAQ") == 2080


def test_snapshot_ranks_are_unique_for_non_null_values():
    rows = read_csv(RD / "snapshots" / "2026" / "06" / "20260622.csv")
    ranks = [row["amount_rank"] for row in rows if row["amount_rank"]]
    assert len(ranks) == len(set(ranks))
    assert min(map(int, ranks)) == 1


def test_research_daily_manifest_linked_from_atlas_manifest():
    manifest = json.loads((ATLAS / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["research_daily"]["manifest_path"] == "atlas/research_daily/manifest.json"
    assert manifest["research_daily"]["validation_passed"] is True
