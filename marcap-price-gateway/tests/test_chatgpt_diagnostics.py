from __future__ import annotations

import math

import pytest


BUNDLE_TEXT_ALIASES = [
    "/__chatgpt_bundle.txt",
    "/chatgpt_bundle.txt",
    "/_chatgpt/bundle.txt",
    "/public/chatgpt_bundle.txt",
    "/static/chatgpt_bundle.txt",
]

BUNDLE_JSON_ALIASES = [
    "/__chatgpt_bundle.json",
    "/_chatgpt/bundle.json",
    "/public/chatgpt_bundle.json",
    "/static/chatgpt_bundle.json",
]

SELFTEST_ALIASES = [
    "/__selftest.txt",
    "/selftest.txt",
    "/_chatgpt/selftest.txt",
    "/public/selftest.txt",
    "/static/selftest.txt",
]


@pytest.mark.parametrize("path", BUNDLE_TEXT_ALIASES)
def test_bundle_text_aliases_return_200(make_client, path):
    with make_client("secret-token") as client:
        response = client.get(path)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    assert response.headers["cache-control"].startswith("no-store")
    assert response.headers["x-chatgpt-diagnostic"] == "true"


@pytest.mark.parametrize("path", BUNDLE_JSON_ALIASES)
def test_bundle_json_aliases_return_200(make_client, path):
    with make_client("secret-token") as client:
        response = client.get(path)
    assert response.status_code == 200
    assert "json" in response.headers["content-type"]
    assert response.headers["cache-control"].startswith("no-store")


@pytest.mark.parametrize("path", SELFTEST_ALIASES)
def test_selftest_aliases_return_200(make_client, path):
    with make_client("secret-token") as client:
        response = client.get(path)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    assert "005930|" in response.text


def test_bundle_text_contains_required_symbols_and_sections(make_client):
    with make_client("secret-token") as client:
        response = client.get("/__chatgpt_bundle.txt")
    text = response.text
    for code in ["005930", "000660", "298040", "267260", "086520"]:
        assert code in text
    assert "OHLC_SAMPLE|" in text
    assert "TRIGGER_SAMPLE|" in text
    assert "PATH_SAMPLE|" in text
    assert len(text.encode("utf-8")) < 16_384


def test_bundle_json_has_usable_005930(make_client):
    with make_client("secret-token") as client:
        response = client.get("/__chatgpt_bundle.json")
    payload = response.json()
    samsung = next(item for item in payload["selftest"] if item["code"] == "005930")
    assert samsung["calibration_usable"] is True
    assert samsung["forward_window_trading_days"] >= 180
    assert samsung["MFE_30D_pct"] is not None
    assert samsung["MFE_90D_pct"] is not None
    assert samsung["MFE_180D_pct"] is not None
    assert samsung["MAE_30D_pct"] is not None
    assert samsung["MAE_90D_pct"] is not None
    assert samsung["MAE_180D_pct"] is not None
    assert len(response.content) < 65_536


def test_bundle_json_has_no_nan(make_client):
    def walk(value):
        if isinstance(value, dict):
            for item in value.values():
                yield from walk(item)
        elif isinstance(value, list):
            for item in value:
                yield from walk(item)
        else:
            yield value

    with make_client("secret-token") as client:
        payload = client.get("/__chatgpt_bundle.json").json()
    assert not any(isinstance(value, float) and math.isnan(value) for value in walk(payload))


@pytest.mark.parametrize(
    "path",
    [
        "/__sample-ohlcv.json?code=005930&start=2024-01-01&end=2024-12-31",
        "/__sample-trigger.json?code=005930&trigger_date=2024-01-02",
        "/__sample-path.json?code=005930&entry_date=2024-01-03",
    ],
)
def test_sample_json_routes_return_200_without_token(make_client, path):
    with make_client("secret-token") as client:
        response = client.get(path)
    assert response.status_code == 200
    assert response.headers["cache-control"].startswith("no-store")


@pytest.mark.parametrize(
    "path",
    [
        "/static/chatgpt_bundle.txt",
        "/static/chatgpt_bundle.json",
        "/static/selftest.txt",
        "/static/sample-005930.txt",
        "/static/sample-trigger-005930.txt",
    ],
)
def test_static_fallback_routes_exist(make_client, path):
    with make_client("secret-token") as client:
        response = client.get(path)
    assert response.status_code == 200
    assert response.headers["cache-control"].startswith("no-store")
