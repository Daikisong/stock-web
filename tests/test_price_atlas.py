from __future__ import annotations

import csv
import json
from pathlib import Path

from scripts.atlas_utils import classify_row, compute_trigger_backtest, load_profile, load_symbol_rows

ROOT = Path(__file__).resolve().parents[1]
ATLAS = ROOT / "atlas"


def test_manifest_exists():
    assert (ATLAS / "manifest.json").exists()


def test_005930_profile_exists():
    profile = ATLAS / "symbol_profiles" / "005" / "005930.json"
    assert profile.exists()
    assert json.loads(profile.read_text(encoding="utf-8"))["code"] == "005930"


def test_005930_2024_ohlc_shard_exists_and_has_columns():
    path = ATLAS / "ohlcv_min_by_symbol_year" / "005" / "005930" / "2024.csv"
    assert path.exists()
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        assert {"d", "o", "h", "l", "c", "v"}.issubset(reader.fieldnames or [])
        first = next(reader)
    assert first["d"].startswith("2024-")


def test_code_stays_zero_padded():
    assert load_profile(ATLAS, "5930")["code"] == "005930"


def test_sample_ohlc_validation_catches_high_less_than_low():
    bad = [{"date": "2024-01-02", "open": 10, "high": 9, "low": 11, "close": 10, "volume": 1}]
    result = compute_trigger_backtest(bad, "2024-01-02", "trigger_close", [30], 30)
    assert result["calibration_usable"] is False


def test_no_file_over_50_mib_in_committed_atlas():
    oversized = [path for path in ATLAS.rglob("*") if path.is_file() and path.stat().st_size > 50 * 1024 * 1024]
    assert oversized == []


def test_no_file_over_100_mib():
    oversized = [path for path in ATLAS.rglob("*") if path.is_file() and path.stat().st_size > 100 * 1024 * 1024]
    assert oversized == []


def test_profile_year_files_all_exist():
    profile = json.loads((ATLAS / "symbol_profiles" / "005" / "005930.json").read_text(encoding="utf-8"))
    assert profile["year_files"]
    assert all((ROOT / path).exists() for path in profile["year_files"])


def test_all_symbols_count_equals_profile_count():
    with (ATLAS / "universe" / "all_symbols.csv").open("r", encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    profile_count = len(list((ATLAS / "symbol_profiles").glob("*/*.json")))
    assert len(rows) == profile_count


def test_inactive_or_delisted_like_is_inferred_only():
    profile = json.loads((ATLAS / "symbol_profiles" / "005" / "005930.json").read_text(encoding="utf-8"))
    assert "status_inference_note" in profile


def test_chatgpt_bundle_contains_required_sections():
    text = (ROOT / "diagnostics" / "chatgpt_bundle.txt").read_text(encoding="utf-8")
    assert "SELFTEST|005930|" in text
    assert "OHLC_SAMPLE|005930|" in text
    assert "TRIGGER_SAMPLE|005930|" in text
    assert "PATH_SAMPLE|005930|" in text


def test_manifest_records_atlas_normalization():
    manifest = json.loads((ATLAS / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["ohlc_consistency_repair_applied_for_calibration"] is False
    assert manifest["calibration_shard_root"] == "atlas/ohlcv_tradable_by_symbol_year"
    assert manifest["raw_shard_root"] == "atlas/ohlcv_raw_by_symbol_year"


def test_classify_row_tradable():
    row = {"open": 100, "high": 110, "low": 95, "close": 105, "volume": 10}
    assert classify_row(row) == "tradable_ohlcv"


def test_classify_row_zero_volume():
    row = {"open": 100, "high": 110, "low": 95, "close": 105, "volume": 0}
    assert classify_row(row) == "non_tradable_zero_volume"


def test_classify_row_zero_ohlc():
    row = {"open": 0, "high": 110, "low": 95, "close": 105, "volume": 10}
    assert classify_row(row) == "invalid_zero_ohlc"


def test_classify_row_suspicious_repair_candidate():
    row = {"open": 100, "high": 99, "low": 95, "close": 105, "volume": 10}
    assert classify_row(row) == "suspicious_ohlc_repaired_candidate"


def test_ecopro_zero_volume_raw_row_excluded_from_tradable():
    raw_rows = load_symbol_rows(ATLAS, "086520", "2024-04-09", "2024-04-09", price_basis="raw_all")
    tradable_rows = load_symbol_rows(ATLAS, "086520", "2024-04-09", "2024-04-09")
    assert raw_rows
    assert raw_rows[0]["row_status"] in {"non_tradable_zero_volume", "invalid_zero_ohlc"}
    assert tradable_rows == []


def test_ecopro_corporate_action_candidate_detected():
    rows = []
    with (ATLAS / "corporate_actions" / "corporate_action_candidates.csv").open("r", encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert any(row["code"] == "086520" and row["date"] == "2024-04-25" for row in rows)
