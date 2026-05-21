from __future__ import annotations


def test_ping_returns_ok(make_client):
    with make_client() as client:
        response = client.get("/__ping")
    assert response.status_code == 200
    assert response.text == "ok"


def test_health_returns_status(make_client):
    with make_client() as client:
        response = client.get("/__health")
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["source_name"] == "FinanceData/marcap"
    assert payload["source_repo_url"] == "https://github.com/FinanceData/marcap"
    assert payload["price_adjustment_status"] == "raw_unadjusted_marcap"
    assert payload["row_count"] >= 600


def test_token_route_works(make_client):
    with make_client("secret-token") as client:
        response = client.get("/g/secret-token/__health")
        missing = client.get("/api/symbol-search?q=삼성")
    assert response.status_code == 200
    assert response.json()["access_mode"] == "token_path_required"
    assert missing.status_code == 404
