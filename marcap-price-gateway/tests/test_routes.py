from __future__ import annotations

from tests.conftest import trading_day


def test_symbol_search_works(make_client):
    with make_client() as client:
        response = client.get("/g/dev/api/symbol-search?q=삼성")
        local_response = client.get("/api/symbol-search?q=삼성")
    assert response.status_code == 200
    payload = response.json()
    assert payload["count"] >= 1
    assert payload["rows"][0]["code"] == "005930"
    assert local_response.status_code == 200


def test_ohlcv_returns_sorted_rows(make_client):
    with make_client() as client:
        response = client.get(
            f"/g/dev/api/ohlcv?code=5930&start={trading_day(0)}&end={trading_day(5)}"
        )
    assert response.status_code == 200
    rows = response.json()["rows"]
    assert [row["date"] for row in rows] == sorted(row["date"] for row in rows)
    assert rows[0]["code"] == "005930"
    assert rows[0]["open"] == 100


def test_trigger_backtest_route_calibration_usable_true_and_false(make_client):
    with make_client() as client:
        enough = client.get(f"/g/dev/api/trigger-backtest?code=005930&trigger_date={trading_day(10)}")
        short = client.get("/g/dev/api/trigger-backtest?code=005930&trigger_date=2026-04-01")

    assert enough.status_code == 200
    assert enough.json()["calibration_usable"] is True

    assert short.status_code == 200
    short_payload = short.json()
    assert short_payload["calibration_usable"] is False
    assert short_payload["MFE_180D_pct"] is None
    assert short_payload["warnings"]


def test_path_summary_route_computes_required_points(make_client):
    with make_client() as client:
        response = client.get(
            f"/g/dev/api/path-summary?code=005930&entry_date={trading_day(0)}&points=1,30,180"
        )
    assert response.status_code == 200
    payload = response.json()
    points = {point["trading_day_offset"]: point for point in payload["points"]}
    assert points[1]["close"] == 102
    assert points[30]["close"] == 131
    assert points[180]["close"] == 281


def test_event_window_and_csv_routes(make_client):
    with make_client() as client:
        json_response = client.get(f"/g/dev/api/event-window?code=005930&anchor_date={trading_day(10)}&pre=2&post=2")
        csv_response = client.get(
            f"/g/dev/api/ohlcv?code=005930&start={trading_day(0)}&end={trading_day(1)}&format=csv"
        )
    assert json_response.status_code == 200
    assert [row["relative_day_index"] for row in json_response.json()["rows"]] == [-2, -1, 0, 1, 2]
    assert csv_response.status_code == 200
    assert csv_response.headers["content-type"].startswith("text/csv")
    assert "005930" in csv_response.text


def test_research_pack_route_returns_compact_pack(make_client):
    with make_client() as client:
        response = client.get(f"/g/dev/api/research-pack?items=005930:{trading_day(10)},000660:{trading_day(10)}")
    assert response.status_code == 200
    payload = response.json()
    assert payload["item_count"] == 2
    assert payload["results"][0]["trigger_backtest"]["calibration_usable"] is True
    assert "event_window_pre10_post10" in payload["results"][0]


def test_non_js_html_contains_visible_table_text(make_client):
    with make_client() as client:
        response = client.get(f"/g/dev/price-path/005930?start={trading_day(0)}&end={trading_day(3)}")
        trigger = client.get(f"/g/dev/trigger/005930?trigger_date={trading_day(10)}")

    assert response.status_code == 200
    assert "<table" in response.text
    assert "005930" in response.text
    assert "삼성전자" in response.text

    assert trigger.status_code == 200
    assert "MFE / MAE" in trigger.text
    assert "D+30" in trigger.text


def test_root_index_is_public_when_token_required(make_client):
    with make_client("secret-token") as client:
        response = client.get("/")
        token_index = client.get("/g/secret-token/")

    assert response.status_code == 200
    assert "/__ping" in response.text
    assert "/__health" in response.text
    assert "/g/secret-token/api/ohlcv" in response.text
    assert token_index.status_code == 200


def test_public_diagnostic_routes_work_without_token_when_token_required(make_client):
    with make_client("secret-token") as client:
        ohlcv = client.get(f"/__sample-ohlcv?code=005930&start={trading_day(0)}&end={trading_day(5)}")
        trigger = client.get(f"/__sample-trigger?code=005930&trigger_date={trading_day(10)}")
        path = client.get(f"/__sample-path?code=005930&entry_date={trading_day(0)}")
        selftest = client.get("/__selftest")

    assert ohlcv.status_code == 200
    assert ohlcv.json()["rows"][0]["code"] == "005930"

    assert trigger.status_code == 200
    assert trigger.json()["calibration_usable"] is True
    assert trigger.json()["forward_window_trading_days"] >= 180

    assert path.status_code == 200
    assert any(point["trading_day_offset"] == 180 and point["available"] for point in path.json()["points"])

    assert selftest.status_code == 200
    payload = selftest.json()
    assert payload["status"] == "ok"
    assert isinstance(payload["results"], list)
    assert payload["failed"]


def test_public_plain_text_selftest_route_is_tiny_and_unauthenticated(make_client):
    with make_client("secret-token") as client:
        response = client.get("/__selftest.txt")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    assert len(response.text.encode("utf-8")) < 8192
    assert "code|name|ohlcv_2024_count" in response.text
    assert "005930|" in response.text
