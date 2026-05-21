from __future__ import annotations

import gzip
import math

import pandas as pd

from app.backtest import compute_path_summary, compute_trigger_backtest
from tests.conftest import FIXTURE_PATH, trading_day


def _fixture_rows() -> list[dict]:
    with gzip.open(FIXTURE_PATH, "rt", encoding="utf-8") as handle:
        frame = pd.read_csv(handle, dtype={"Code": "string"})
    frame = frame[frame["Code"] == "005930"].rename(
        columns={
            "Date": "date",
            "Code": "code",
            "Name": "name",
            "Open": "open",
            "High": "high",
            "Low": "low",
            "Close": "close",
            "Volume": "volume",
            "Amount": "amount",
            "Marcap": "marcap",
            "Stocks": "stocks",
            "Market": "market",
        }
    )
    frame = frame[frame["date"] < "2023-01-01"]
    return frame.to_dict("records")


def test_trigger_backtest_computes_mfe_mae_correctly():
    rows = _fixture_rows()
    trigger_date = trading_day(10)
    result = compute_trigger_backtest(rows, trigger_date)

    entry_price = 112
    expected_mfe_30 = round((145 / entry_price - 1) * 100, 2)
    expected_mae_30 = round((108 / entry_price - 1) * 100, 2)

    assert result["entry_date"] == trading_day(11)
    assert result["entry_price"] == entry_price
    assert result["MFE_30D_pct"] == expected_mfe_30
    assert result["MAE_30D_pct"] == expected_mae_30
    assert result["below_entry_price_flag_30D"] is False
    assert result["calibration_usable"] is True


def test_path_summary_computes_d1_d30_d180_correctly():
    rows = _fixture_rows()
    result = compute_path_summary(rows, trading_day(0), points=[1, 30, 180])
    points = {point["trading_day_offset"]: point for point in result["points"]}

    assert result["entry_date"] == trading_day(0)
    assert result["entry_price"] == 101

    assert points[1]["date"] == trading_day(1)
    assert points[1]["close"] == 102
    assert points[1]["high_to_date"] == 106
    assert points[1]["low_to_date"] == 97
    assert points[1]["close_return_pct"] == round((102 / 101 - 1) * 100, 2)

    assert points[30]["date"] == trading_day(30)
    assert points[30]["close"] == 131
    assert points[30]["high_to_date"] == 135
    assert points[30]["low_to_date"] == 97

    assert points[180]["date"] == trading_day(180)
    assert points[180]["close"] == 281
    assert points[180]["high_to_date"] == 285
    assert points[180]["low_to_date"] == 97


def test_missing_windows_return_null_and_warnings():
    rows = _fixture_rows()
    result = compute_trigger_backtest(rows, trading_day(580), windows=[30, 90, 180])

    assert result["calibration_usable"] is False
    assert result["MFE_180D_pct"] is None
    assert result["MAE_180D_pct"] is None
    assert result["warnings"]
    assert not any(isinstance(value, float) and math.isnan(value) for value in result.values())
