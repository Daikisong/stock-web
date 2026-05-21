from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin

import httpx

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def route_templates() -> list[str]:
    return [
        "/",
        "/__ping",
        "/__health",
        "/__chatgpt_bundle.txt",
        "/chatgpt_bundle.txt",
        "/_chatgpt/bundle.txt",
        "/public/chatgpt_bundle.txt",
        "/static/chatgpt_bundle.txt",
        "/__chatgpt_bundle.json",
        "/_chatgpt/bundle.json",
        "/static/chatgpt_bundle.json",
        "/__selftest.txt",
        "/selftest.txt",
        "/_chatgpt/selftest.txt",
        "/static/selftest.txt",
        "/__sample-005930.txt",
        "/__sample-trigger-005930.txt",
        "/__sample-ohlcv.json?code=005930&start=2024-01-01&end=2024-12-31",
        "/__sample-trigger.json?code=005930&trigger_date=2024-01-02",
        "/__sample-path.json?code=005930&entry_date=2024-01-03",
        "/g/{token}/__health",
        "/g/{token}/api/ohlcv?code=005930&start=2024-01-01&end=2024-12-31",
        "/g/{token}/api/trigger-backtest?code=005930&trigger_date=2024-01-02",
        "/g/{token}/api/path-summary?code=005930&entry_date=2024-01-03",
    ]


def make_url(base_url: str, route: str, token: str) -> str:
    base = base_url.rstrip("/") + "/"
    return urljoin(base, route.lstrip("/").replace("{token}", token))


async def fetch_one(client: httpx.AsyncClient, url: str) -> dict:
    try:
        response = await client.get(url)
        text = response.text
        status_code = response.status_code
        content_type = response.headers.get("content-type", "")
        byte_count = len(response.content)
        ok = status_code == 200 and byte_count > 0
        return {
            "url": url,
            "status_code": status_code,
            "content_type": content_type,
            "byte_count": byte_count,
            "first_200_chars": text[:200].replace("\n", "\\n"),
            "ok": ok,
        }
    except Exception as exc:
        return {
            "url": url,
            "status_code": None,
            "content_type": "",
            "byte_count": 0,
            "first_200_chars": "",
            "ok": False,
            "error": str(exc),
        }


def build_text_report(base_url: str, generated_at: str, results: list[dict], bundle_preview: str) -> str:
    ok_routes = sum(1 for item in results if item["ok"])
    failed_routes = len(results) - ok_routes
    lines = [
        "CHATGPT_ROUTE_PROBE_REPORT",
        f"generated_at={generated_at}",
        f"base_url={base_url.rstrip('/')}",
        f"total_routes={len(results)}",
        f"ok_routes={ok_routes}",
        f"failed_routes={failed_routes}",
        "",
        "ROUTE|status|bytes|content_type|ok|url",
    ]
    for item in results:
        lines.append(
            "|".join(
                [
                    str(item.get("url", "")),
                    str(item.get("status_code", "")),
                    str(item.get("byte_count", 0)),
                    str(item.get("content_type", "")).replace("|", "/"),
                    "true" if item.get("ok") else "false",
                    str(item.get("url", "")),
                ]
            )
        )
    lines.extend(["", "BUNDLE_PREVIEW_BEGIN", bundle_preview[:1000], "BUNDLE_PREVIEW_END", ""])
    return "\n".join(lines)


async def run_probe(base_url: str, token: str) -> dict:
    generated_at = datetime.now(timezone.utc).isoformat()
    urls = [make_url(base_url, route, token) for route in route_templates()]
    async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
        results = await asyncio.gather(*(fetch_one(client, url) for url in urls))
        bundle_url = make_url(base_url, "/__chatgpt_bundle.txt", token)
        bundle_preview_result = await fetch_one(client, bundle_url)
    bundle_preview = bundle_preview_result.get("first_200_chars", "")
    report = {
        "generated_at": generated_at,
        "base_url": base_url.rstrip("/"),
        "total_routes": len(results),
        "ok_routes": sum(1 for item in results if item["ok"]),
        "failed_routes": sum(1 for item in results if not item["ok"]),
        "results": results,
        "bundle_preview": bundle_preview,
    }
    text_report = build_text_report(base_url, generated_at, results, bundle_preview)
    public_dir = ROOT / "public"
    public_dir.mkdir(parents=True, exist_ok=True)
    (public_dir / "probe_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (public_dir / "probe_report.txt").write_text(text_report, encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--token", required=True)
    args = parser.parse_args()
    report = asyncio.run(run_probe(args.base_url, args.token))
    print(json.dumps({key: report[key] for key in ["base_url", "total_routes", "ok_routes", "failed_routes"]}, indent=2))
    return 0 if report["failed_routes"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
