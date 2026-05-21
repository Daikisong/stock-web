from __future__ import annotations

import json
import math
from datetime import date, timedelta
from pathlib import Path

from scripts.atlas_utils import compute_path_summary, compute_trigger_backtest
from scripts.build_research_pack import build_pack

ROOT = Path(__file__).resolve().parents[1]
ATLAS = ROOT / "atlas"


def fixture_rows(count: int = 220):
    start = date(2024, 1, 2)
    return [
        {
            "date": (start + timedelta(days=i)).isoformat(),
            "open": 100 + i,
            "high": 105 + i,
            "low": 97 + i,
            "close": 101 + i,
            "volume": 1000 + i,
        }
        for i in range(count)
    ]


def test_research_pack_generator_computes_mfe_correctly_on_tiny_fixture():
    rows = fixture_rows()
    result = compute_trigger_backtest(rows, rows[0]["date"], "trigger_close", [30], 30)
    assert result["MFE_30D_pct"] == round((134 / 101 - 1) * 100, 2)


def test_research_pack_generator_computes_mae_correctly_on_tiny_fixture():
    rows = fixture_rows()
    result = compute_trigger_backtest(rows, rows[0]["date"], "trigger_close", [30], 30)
    assert result["MAE_30D_pct"] == round((97 / 101 - 1) * 100, 2)


def test_next_trading_day_close_entry_mode_works():
    rows = fixture_rows()
    result = compute_trigger_backtest(rows, rows[0]["date"], "next_trading_day_close", [30], 30)
    assert result["entry_date"] == rows[1]["date"]
    assert result["entry_price"] == rows[1]["close"]


def test_trigger_close_entry_mode_works():
    rows = fixture_rows()
    result = compute_trigger_backtest(rows, rows[0]["date"], "trigger_close", [30], 30)
    assert result["entry_date"] == rows[0]["date"]
    assert result["entry_price"] == rows[0]["close"]


def test_path_summary_includes_d180():
    rows = fixture_rows()
    result = compute_path_summary(rows, rows[0]["date"], [180])
    assert result["points"][0]["trading_day_offset"] == 180
    assert result["points"][0]["available"] is True


def test_drawdown_after_peak_only_uses_rows_after_peak():
    rows = fixture_rows(10)
    rows[3]["high"] = 1000
    rows[2]["low"] = 1
    rows[4].update({"open": 920, "high": 950, "low": 900, "close": 930})
    for row in rows[5:]:
        row.update({"open": 960, "high": 970, "low": 950, "close": 965})
    result = compute_trigger_backtest(rows, rows[0]["date"], "trigger_close", [5], 9)
    assert result["peak_date"] == rows[3]["date"]
    assert result["drawdown_after_peak_pct"] == round((900 / 1000 - 1) * 100, 2)


def test_smoke_research_pack_has_5_items():
    path = ATLAS / "research_packs" / "smoke" / "smoke_005930_000660_298040_267260_086520.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert len(payload["items"]) == 5


def test_no_generated_json_has_nan():
    def walk(value):
        if isinstance(value, dict):
            for item in value.values():
                yield from walk(item)
        elif isinstance(value, list):
            for item in value:
                yield from walk(item)
        else:
            yield value

    for path in list(ATLAS.rglob("*.json")) + list((ROOT / "diagnostics").rglob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        assert not any(isinstance(value, float) and math.isnan(value) for value in walk(payload)), path


def test_calibration_usable_requires_180_forward_trading_days():
    rows = fixture_rows(100)
    result = compute_trigger_backtest(rows, rows[0]["date"], "trigger_close", [30, 90, 180], 180)
    assert result["calibration_usable"] is False


def test_smoke_005930_still_calibration_usable():
    path = ATLAS / "research_packs" / "smoke" / "smoke_005930_000660_298040_267260_086520.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    item = next(item for item in payload["items"] if item["code"] == "005930")
    assert item["calibration_usable"] is True


def test_smoke_ecopro_blocks_corporate_action_and_no_minus_100_mae():
    path = ATLAS / "research_packs" / "smoke" / "smoke_005930_000660_298040_267260_086520.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    item = next(item for item in payload["items"] if item["code"] == "086520")
    assert item["MAE_90D_pct"] != -100.0
    assert item["calibration_usable"] is False
    assert "corporate_action_within_180D" in item["calibration_block_reasons"]


def test_raw_all_price_basis_cannot_be_calibration_usable():
    pack = build_pack([("005930", "2024-01-02")], price_basis="raw_all", pack_id="test_raw_all")
    assert pack["items"][0]["calibration_usable"] is False
    assert "raw_all_price_basis_not_allowed_for_calibration" in pack["items"][0]["calibration_block_reasons"]


def test_compute_trigger_filters_non_tradable_rows():
    rows = fixture_rows(220)
    rows.insert(10, {"date": "2024-01-12", "open": 0, "high": 0, "low": 0, "close": 100, "volume": 0})
    result = compute_trigger_backtest(rows, rows[0]["date"], "trigger_close", [30, 90, 180], 180)
    assert result["tradable_row_count"] == 220
    assert result["MAE_90D_pct"] != -100.0
