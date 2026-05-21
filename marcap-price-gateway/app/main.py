from __future__ import annotations

import csv
import io
import json
import math
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.backtest import DEFAULT_POINTS, DEFAULT_WINDOWS, compute_path_summary, compute_trigger_backtest
from app.marcap_store import MarcapStore, normalize_code, parse_date
from app.schemas import CAVEAT, PRICE_ADJUSTMENT_STATUS, PRICE_DATA_SOURCE, SOURCE_REPO_URL, source_metadata
from app.settings import Settings

templates = Jinja2Templates(directory="app/templates")
APP_VERSION = "0.1.0"

SELFTEST_SYMBOLS = [
    ("005930", "Samsung Electronics"),
    ("000660", "SK hynix"),
    ("298040", "Hyosung Heavy Industries"),
    ("267260", "HD Hyundai Electric"),
    ("086520", "Ecopro"),
    ("247540", "Ecopro BM"),
    ("035420", "NAVER"),
    ("035720", "Kakao"),
    ("051910", "LG Chem"),
    ("373220", "LG Energy Solution"),
]

SELFTEST_TEXT_SYMBOLS = [
    ("005930", "Samsung Electronics"),
    ("000660", "SK hynix"),
    ("298040", "Hyosung Heavy Industries"),
    ("267260", "HD Hyundai Electric"),
    ("086520", "Ecopro"),
]

DIAGNOSTIC_EXACT_PATHS = {
    "/",
    "/chatgpt_bundle.txt",
    "/selftest.txt",
    "/probe_report.txt",
    "/probe_report.json",
}

DIAGNOSTIC_PREFIXES = (
    "/__",
    "/_chatgpt/",
    "/public/",
    "/static/",
)


def _csv_response(rows: list[dict[str, Any]], filename: str) -> Response:
    output = io.StringIO()
    fieldnames: list[str] = []
    for row in rows:
        for key in row.keys():
            if key not in fieldnames:
                fieldnames.append(key)
    if not fieldnames:
        fieldnames = ["message"]
    writer = csv.DictWriter(output, fieldnames=fieldnames, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow({key: "" if value is None else value for key, value in row.items()})
    return Response(
        output.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _parse_int_list(raw: str | None, default: list[int], name: str) -> list[int]:
    if not raw:
        return list(default)
    try:
        values = [int(item.strip()) for item in raw.split(",") if item.strip()]
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"{name} must be a comma-separated integer list") from exc
    if not values or any(value <= 0 for value in values):
        raise HTTPException(status_code=400, detail=f"{name} must contain positive integers")
    return values


def _add_source_to_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    enriched = []
    for row in rows:
        item = dict(row)
        item["source"] = PRICE_DATA_SOURCE
        item["price_data_source"] = PRICE_DATA_SOURCE
        item["source_repo_url"] = SOURCE_REPO_URL
        item["price_adjustment_status"] = PRICE_ADJUSTMENT_STATUS
        item["caveat"] = CAVEAT
        enriched.append(item)
    return enriched


def _json_payload(rows: list[dict[str, Any]], **extra: Any) -> dict[str, Any]:
    return {**source_metadata(), **extra, "count": len(rows), "rows": rows}


def _current_time() -> str:
    return datetime.now(timezone.utc).isoformat()


def _has_ohlcv(rows: list[dict[str, Any]]) -> bool:
    required = ["open", "high", "low", "close", "volume"]
    return bool(rows) and all(all(row.get(key) is not None for key in required) for row in rows)


def _compact_trigger_result(result: dict[str, Any]) -> dict[str, Any]:
    keys = [
        "code",
        "name",
        "trigger_date",
        "entry_mode",
        "entry_date",
        "entry_price",
        "calibration_usable",
        "forward_window_trading_days",
        "MFE_30D_pct",
        "MFE_90D_pct",
        "MFE_180D_pct",
        "MAE_30D_pct",
        "MAE_90D_pct",
        "MAE_180D_pct",
        "peak_date",
        "peak_price",
        "drawdown_after_peak_pct",
        "warnings",
    ]
    return {key: result.get(key) for key in keys}


def _compact_path_result(result: dict[str, Any]) -> dict[str, Any]:
    wanted = {1, 2, 3, 5, 10, 20, 30, 60, 90, 180, 252, 504}
    return {
        "code": result.get("code"),
        "name": result.get("name"),
        "entry_date": result.get("entry_date"),
        "entry_price": result.get("entry_price"),
        "entry_mode": result.get("entry_mode"),
        "points": [point for point in result.get("points", []) if point.get("trading_day_offset") in wanted],
        "warnings": result.get("warnings", []),
    }


def _bool_text(value: Any) -> str:
    return "true" if bool(value) else "false"


def _safe_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).replace("|", "/").replace("\n", " ").replace("\r", " ")


def _warning_text(warnings: list[Any]) -> str:
    return "; ".join(_safe_text(warning) for warning in warnings if warning)


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            return None
        return value
    return value


def _is_diagnostic_path(path: str) -> bool:
    return path in DIAGNOSTIC_EXACT_PATHS or any(path.startswith(prefix) for prefix in DIAGNOSTIC_PREFIXES)


def create_app(settings: Settings | None = None, store: MarcapStore | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    store = store or MarcapStore(settings.marcap_repo_path, settings.marcap_duckdb_path, settings.marcap_repo_url)
    public_dir = Path("public")
    public_dir.mkdir(parents=True, exist_ok=True)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.startup_warning = None
        try:
            store.ensure_cache()
        except Exception as exc:
            app.state.startup_warning = str(exc)
        try:
            write_public_diagnostic_files()
        except Exception as exc:
            app.state.startup_warning = f"{app.state.startup_warning or ''} {exc}".strip()
        yield

    app = FastAPI(
        title="marcap-price-gateway",
        description="Read-only FinanceData/marcap OHLC gateway for research calibration.",
        version="0.1.0",
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.store = store
    app.state.startup_warning = None
    app.mount("/static", StaticFiles(directory=str(public_dir)), name="static")

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=False,
        allow_methods=["GET", "OPTIONS"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def noindex_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Robots-Tag"] = "noindex, nofollow"
        if _is_diagnostic_path(request.url.path):
            response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
            response.headers["Pragma"] = "no-cache"
            response.headers["Expires"] = "0"
            response.headers["X-ChatGPT-Diagnostic"] = "true"
        return response

    def require_access(token: str | None = None) -> None:
        if token is None:
            if settings.token_required:
                raise HTTPException(status_code=404, detail="not found")
            return
        if settings.access_token in ("", "dev"):
            if settings.access_token == "" or token == settings.access_token:
                return
        if token != settings.access_token:
            raise HTTPException(status_code=404, detail="not found")

    def token_prefix(token: str | None = None) -> str:
        display_token = token or settings.access_token or "dev"
        return f"/g/{display_token}"

    def health_payload() -> dict[str, Any]:
        payload = store.health()
        payload["access_mode"] = settings.access_mode
        payload["current_time"] = _current_time()
        if app.state.startup_warning:
            payload["startup_warning"] = app.state.startup_warning
        return payload

    def symbol_diagnostic(code: str, english_name: str) -> dict[str, Any]:
        ohlcv_rows = store.get_ohlcv(code, "2024-01-01", "2024-12-31", limit=settings.default_max_rows)
        if not ohlcv_rows:
            raise ValueError("no OHLCV rows found for 2024")

        forward_rows = store.get_forward_window(code, "2024-01-02", 504)
        if not forward_rows:
            raise ValueError("no forward rows found for trigger backtest")
        trigger_result = compute_trigger_backtest(
            forward_rows,
            "2024-01-02",
            "next_trading_day_close",
            DEFAULT_WINDOWS,
            504,
        )
        trigger_result["code"] = code

        path_rows = store.get_forward_window(code, "2024-01-03", max(DEFAULT_POINTS))
        if not path_rows:
            raise ValueError("no forward rows found for path summary")
        path_result = compute_path_summary(path_rows, "2024-01-03", DEFAULT_POINTS, "trigger_close")
        path_d180_available = any(
            point.get("trading_day_offset") == 180 and point.get("available")
            for point in path_result.get("points", [])
        )

        return {
            "code": code,
            "requested_name": english_name,
            "name": ohlcv_rows[0].get("name"),
            "ohlcv_2024_count": len(ohlcv_rows),
            "first_date": ohlcv_rows[0].get("date"),
            "last_date": ohlcv_rows[-1].get("date"),
            "has_ohlcv": _has_ohlcv(ohlcv_rows),
            "calibration_usable": trigger_result.get("calibration_usable"),
            "forward_window_trading_days": trigger_result.get("forward_window_trading_days"),
            "MFE_30D_pct": trigger_result.get("MFE_30D_pct"),
            "MFE_90D_pct": trigger_result.get("MFE_90D_pct"),
            "MFE_180D_pct": trigger_result.get("MFE_180D_pct"),
            "MAE_30D_pct": trigger_result.get("MAE_30D_pct"),
            "MAE_90D_pct": trigger_result.get("MAE_90D_pct"),
            "MAE_180D_pct": trigger_result.get("MAE_180D_pct"),
            "path_D180_available": path_d180_available,
            "status": "ok",
            "warnings": trigger_result.get("warnings", []) + path_result.get("warnings", []),
        }

    def build_selftest_text() -> str:
        lines = [
            "code|name|ohlcv_2024_count|first_date|last_date|calibration_usable|"
            "forward_window_trading_days|MFE_90D_pct|MAE_90D_pct|path_D180_available|status|warnings"
        ]
        for code, english_name in SELFTEST_TEXT_SYMBOLS:
            try:
                result = symbol_diagnostic(code, english_name)
                lines.append(
                    "|".join(
                        [
                            code,
                            _safe_text(result.get("name") or english_name),
                            _safe_text(result.get("ohlcv_2024_count")),
                            _safe_text(result.get("first_date")),
                            _safe_text(result.get("last_date")),
                            _bool_text(result.get("calibration_usable")),
                            _safe_text(result.get("forward_window_trading_days")),
                            _safe_text(result.get("MFE_90D_pct")),
                            _safe_text(result.get("MAE_90D_pct")),
                            _bool_text(result.get("path_D180_available")),
                            "ok",
                            _warning_text(result.get("warnings", [])),
                        ]
                    )
                )
            except Exception as exc:
                lines.append(
                    "|".join([code, english_name, "0", "", "", "false", "0", "", "", "false", "error", _safe_text(exc)])
                )
        return "\n".join(lines) + "\n"

    def build_sample_005930_text() -> str:
        rows = store.get_ohlcv("005930", "2024-01-01", "2024-12-31", limit=settings.default_max_rows)
        if not rows:
            raise HTTPException(status_code=404, detail="no OHLCV rows found")
        selected_rows = rows[:5] + ([] if len(rows) <= 10 else [{"date": "..."}]) + rows[-5:]
        lines = [
            "code=005930",
            f"name={_safe_text(rows[0].get('name'))}",
            f"source={PRICE_DATA_SOURCE}",
            f"price_adjustment_status={PRICE_ADJUSTMENT_STATUS}",
            "date|open|high|low|close|volume",
        ]
        for row in selected_rows:
            if row.get("date") == "...":
                lines.append("...")
                continue
            lines.append(
                "|".join(
                    [
                        _safe_text(row.get("date")),
                        _safe_text(row.get("open")),
                        _safe_text(row.get("high")),
                        _safe_text(row.get("low")),
                        _safe_text(row.get("close")),
                        _safe_text(row.get("volume")),
                    ]
                )
            )
        return "\n".join(lines) + "\n"

    def build_sample_trigger_005930_text() -> str:
        rows = store.get_forward_window("005930", "2024-01-02", 504)
        if not rows:
            raise HTTPException(status_code=404, detail="no forward OHLCV rows found")
        result = compute_trigger_backtest(rows, "2024-01-02", "next_trading_day_close", DEFAULT_WINDOWS, 504)
        lines = [
            "code=005930",
            f"name={_safe_text(result.get('name'))}",
            "trigger_date=2024-01-02",
            f"entry_date={_safe_text(result.get('entry_date'))}",
            f"entry_price={_safe_text(result.get('entry_price'))}",
            f"calibration_usable={_bool_text(result.get('calibration_usable'))}",
            f"forward_window_trading_days={_safe_text(result.get('forward_window_trading_days'))}",
            f"MFE_30D_pct={_safe_text(result.get('MFE_30D_pct'))}",
            f"MFE_90D_pct={_safe_text(result.get('MFE_90D_pct'))}",
            f"MFE_180D_pct={_safe_text(result.get('MFE_180D_pct'))}",
            f"MAE_30D_pct={_safe_text(result.get('MAE_30D_pct'))}",
            f"MAE_90D_pct={_safe_text(result.get('MAE_90D_pct'))}",
            f"MAE_180D_pct={_safe_text(result.get('MAE_180D_pct'))}",
            f"warnings={_warning_text(result.get('warnings', []))}",
        ]
        return "\n".join(lines) + "\n"

    def build_bundle_json(public_base_url: str = "") -> dict[str, Any]:
        health_info = health_payload()
        selftest_results: list[dict[str, Any]] = []
        failed: list[dict[str, Any]] = []
        for code, english_name in SELFTEST_TEXT_SYMBOLS:
            try:
                selftest_results.append(symbol_diagnostic(code, english_name))
            except Exception as exc:
                failed.append({"code": code, "requested_name": english_name, "error": str(exc)})

        ohlcv_rows = store.get_ohlcv("005930", "2024-01-01", "2024-12-31", limit=settings.default_max_rows)
        sample_rows = ohlcv_rows[:5] + ohlcv_rows[-5:] if len(ohlcv_rows) > 10 else ohlcv_rows
        forward_rows = store.get_forward_window("005930", "2024-01-02", 504)
        trigger_result = compute_trigger_backtest(forward_rows, "2024-01-02", "next_trading_day_close", DEFAULT_WINDOWS, 504)
        path_rows = store.get_forward_window("005930", "2024-01-03", max(DEFAULT_POINTS))
        path_result = compute_path_summary(path_rows, "2024-01-03", DEFAULT_POINTS, "trigger_close")

        return _json_safe(
            {
                **source_metadata(),
                "generated_at": _current_time(),
                "public_base_url": public_base_url or settings.public_base_url,
                "app_version": APP_VERSION,
                "git_commit": os.getenv("GIT_COMMIT", ""),
                "source_name": PRICE_DATA_SOURCE,
                "source_repo_url": SOURCE_REPO_URL,
                "price_adjustment_status": PRICE_ADJUSTMENT_STATUS,
                "min_date": health_info.get("min_date"),
                "max_date": health_info.get("max_date"),
                "row_count": health_info.get("row_count"),
                "selftest": selftest_results,
                "failed": failed,
                "ohlc_sample": [
                    {
                        "code": row.get("code"),
                        "date": row.get("date"),
                        "open": row.get("open"),
                        "high": row.get("high"),
                        "low": row.get("low"),
                        "close": row.get("close"),
                        "volume": row.get("volume"),
                        "amount": row.get("amount"),
                        "marcap": row.get("marcap"),
                        "market": row.get("market"),
                    }
                    for row in sample_rows
                ],
                "trigger_sample": _compact_trigger_result({"code": "005930", **trigger_result}),
                "path_sample": _compact_path_result({"code": "005930", **path_result}),
            }
        )

    def build_bundle_text(public_base_url: str = "") -> str:
        bundle = build_bundle_json(public_base_url)
        lines = [
            "CHATGPT_MARCAP_BUNDLE",
            f"generated_at={_safe_text(bundle.get('generated_at'))}",
            f"public_base_url={_safe_text(bundle.get('public_base_url'))}",
            f"source_name={PRICE_DATA_SOURCE}",
            f"source_repo_url={SOURCE_REPO_URL}",
            f"price_adjustment_status={PRICE_ADJUSTMENT_STATUS}",
            f"min_date={_safe_text(bundle.get('min_date'))}",
            f"max_date={_safe_text(bundle.get('max_date'))}",
            f"row_count={_safe_text(bundle.get('row_count'))}",
            f"app_version={APP_VERSION}",
            f"git_commit={_safe_text(bundle.get('git_commit'))}",
        ]
        for result in bundle.get("selftest", []):
            lines.append(
                "|".join(
                    [
                        "SELFTEST",
                        _safe_text(result.get("code")),
                        _safe_text(result.get("name") or result.get("requested_name")),
                        _safe_text(result.get("ohlcv_2024_count")),
                        _safe_text(result.get("first_date")),
                        _safe_text(result.get("last_date")),
                        _bool_text(result.get("has_ohlcv")),
                        _bool_text(result.get("calibration_usable")),
                        _safe_text(result.get("forward_window_trading_days")),
                        _safe_text(result.get("MFE_30D_pct")),
                        _safe_text(result.get("MFE_90D_pct")),
                        _safe_text(result.get("MFE_180D_pct")),
                        _safe_text(result.get("MAE_30D_pct")),
                        _safe_text(result.get("MAE_90D_pct")),
                        _safe_text(result.get("MAE_180D_pct")),
                        _bool_text(result.get("path_D180_available")),
                        _safe_text(result.get("status")),
                        _warning_text(result.get("warnings", [])),
                    ]
                )
            )
        for row in bundle.get("ohlc_sample", []):
            lines.append(
                "|".join(
                    [
                        "OHLC_SAMPLE",
                        _safe_text(row.get("code")),
                        _safe_text(row.get("date")),
                        _safe_text(row.get("open")),
                        _safe_text(row.get("high")),
                        _safe_text(row.get("low")),
                        _safe_text(row.get("close")),
                        _safe_text(row.get("volume")),
                        _safe_text(row.get("amount")),
                        _safe_text(row.get("marcap")),
                        _safe_text(row.get("market")),
                    ]
                )
            )
        trigger = bundle.get("trigger_sample", {})
        lines.append(
            "|".join(
                [
                    "TRIGGER_SAMPLE",
                    "005930",
                    "2024-01-02",
                    _safe_text(trigger.get("entry_mode")),
                    _safe_text(trigger.get("entry_date")),
                    _safe_text(trigger.get("entry_price")),
                    _bool_text(trigger.get("calibration_usable")),
                    _safe_text(trigger.get("forward_window_trading_days")),
                    _safe_text(trigger.get("MFE_30D_pct")),
                    _safe_text(trigger.get("MFE_90D_pct")),
                    _safe_text(trigger.get("MFE_180D_pct")),
                    _safe_text(trigger.get("MAE_30D_pct")),
                    _safe_text(trigger.get("MAE_90D_pct")),
                    _safe_text(trigger.get("MAE_180D_pct")),
                    _safe_text(trigger.get("peak_date")),
                    _safe_text(trigger.get("peak_price")),
                    _safe_text(trigger.get("drawdown_after_peak_pct")),
                ]
            )
        )
        for point in bundle.get("path_sample", {}).get("points", []):
            lines.append(
                "|".join(
                    [
                        "PATH_SAMPLE",
                        "005930",
                        _safe_text(bundle.get("path_sample", {}).get("entry_date")),
                        _safe_text(point.get("label")),
                        _safe_text(point.get("date")),
                        _safe_text(point.get("close_return_pct")),
                        _safe_text(point.get("high_to_date_return_pct")),
                        _safe_text(point.get("low_to_date_return_pct")),
                        _bool_text(point.get("available")),
                    ]
                )
            )
        return "\n".join(lines) + "\n"

    def write_public_diagnostic_files(public_base_url: str = "") -> None:
        public_dir.mkdir(parents=True, exist_ok=True)
        (public_dir / "chatgpt_bundle.txt").write_text(build_bundle_text(public_base_url), encoding="utf-8")
        (public_dir / "chatgpt_bundle.json").write_text(
            json.dumps(build_bundle_json(public_base_url), ensure_ascii=False, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )
        (public_dir / "selftest.txt").write_text(build_selftest_text(), encoding="utf-8")
        (public_dir / "sample-005930.txt").write_text(build_sample_005930_text(), encoding="utf-8")
        (public_dir / "sample-trigger-005930.txt").write_text(build_sample_trigger_005930_text(), encoding="utf-8")
        if not (public_dir / "probe_report.txt").exists():
            (public_dir / "probe_report.txt").write_text("CHATGPT_ROUTE_PROBE_REPORT\ngenerated_at=\nstatus=not_generated\n", encoding="utf-8")
        if not (public_dir / "probe_report.json").exists():
            (public_dir / "probe_report.json").write_text('{"status":"not_generated"}\n', encoding="utf-8")

    @app.get("/__ping", response_class=PlainTextResponse)
    def ping() -> str:
        return "ok"

    @app.get("/g/{token}/__ping", response_class=PlainTextResponse)
    def ping_token(token: str) -> str:
        require_access(token)
        return "ok"

    @app.get("/__health")
    def health() -> dict[str, Any]:
        return health_payload()

    @app.get("/g/{token}/__health")
    def health_token(token: str) -> dict[str, Any]:
        require_access(token)
        return health_payload()

    @app.get("/__sample-ohlcv")
    @app.get("/__sample-ohlcv.json")
    def sample_ohlcv(
        code: str = Query(...),
        start: str = Query(...),
        end: str = Query(...),
        market: str | None = None,
        limit: int | None = None,
    ) -> dict[str, Any]:
        try:
            rows = store.get_ohlcv(code, start, end, market, limit or settings.default_max_rows)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if not rows:
            raise HTTPException(status_code=404, detail="no OHLCV rows found")
        return _json_payload(
            _add_source_to_rows(rows),
            code=normalize_code(code),
            start=start,
            end=end,
            market=market,
        )

    @app.get("/__sample-trigger")
    @app.get("/__sample-trigger.json")
    def sample_trigger(
        code: str = Query(...),
        trigger_date: str = Query(...),
        entry_mode: str = "next_trading_day_close",
        max_window: int = 504,
    ) -> dict[str, Any]:
        try:
            code_value = normalize_code(code)
            parse_date(trigger_date, "trigger_date")
            rows = store.get_forward_window(code_value, trigger_date, max_window)
            if not rows:
                raise HTTPException(status_code=404, detail="no forward OHLCV rows found")
            result = compute_trigger_backtest(rows, trigger_date, entry_mode, DEFAULT_WINDOWS, max_window)
            result["code"] = code_value
            return {**source_metadata(), **_compact_trigger_result(result)}
        except HTTPException:
            raise
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/__sample-path")
    @app.get("/__sample-path.json")
    def sample_path(
        code: str = Query(...),
        entry_date: str = Query(...),
        entry_mode: str = "trigger_close",
    ) -> dict[str, Any]:
        try:
            code_value = normalize_code(code)
            parse_date(entry_date, "entry_date")
            rows = store.get_forward_window(code_value, entry_date, max(DEFAULT_POINTS))
            if not rows:
                raise HTTPException(status_code=404, detail="no forward OHLCV rows found")
            result = compute_path_summary(rows, entry_date, DEFAULT_POINTS, entry_mode)
            result["code"] = code_value
            return {**source_metadata(), **_compact_path_result(result)}
        except HTTPException:
            raise
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/__selftest")
    def selftest() -> dict[str, Any]:
        results: list[dict[str, Any]] = []
        failed: list[dict[str, Any]] = []
        for code, english_name in SELFTEST_SYMBOLS:
            try:
                ohlcv_rows = store.get_ohlcv(code, "2024-01-01", "2024-12-31", limit=settings.default_max_rows)
                if not ohlcv_rows:
                    raise ValueError("no OHLCV rows found for 2024")

                forward_rows = store.get_forward_window(code, "2024-01-02", 504)
                if not forward_rows:
                    raise ValueError("no forward rows found for trigger backtest")
                trigger_result = compute_trigger_backtest(
                    forward_rows,
                    "2024-01-02",
                    "next_trading_day_close",
                    DEFAULT_WINDOWS,
                    504,
                )
                trigger_result["code"] = code

                path_rows = store.get_forward_window(code, "2024-01-03", max(DEFAULT_POINTS))
                if not path_rows:
                    raise ValueError("no forward rows found for path summary")
                path_result = compute_path_summary(path_rows, "2024-01-03", DEFAULT_POINTS, "trigger_close")

                result = {
                    "code": code,
                    "requested_name": english_name,
                    "name": ohlcv_rows[0].get("name"),
                    "ohlcv_row_count": len(ohlcv_rows),
                    "first_date": ohlcv_rows[0].get("date"),
                    "last_date": ohlcv_rows[-1].get("date"),
                    "has_open_high_low_close_volume": _has_ohlcv(ohlcv_rows),
                    "trigger_backtest_ok": trigger_result.get("entry_date") is not None
                    and trigger_result.get("MFE_180D_pct") is not None
                    and trigger_result.get("MAE_180D_pct") is not None,
                    "calibration_usable": trigger_result.get("calibration_usable"),
                    "forward_window_trading_days": trigger_result.get("forward_window_trading_days"),
                    "MFE_90D_pct": trigger_result.get("MFE_90D_pct"),
                    "MAE_90D_pct": trigger_result.get("MAE_90D_pct"),
                    "path_summary_ok": any(
                        point.get("trading_day_offset") == 180 and point.get("available")
                        for point in path_result.get("points", [])
                    ),
                    "warnings": trigger_result.get("warnings", []) + path_result.get("warnings", []),
                }
                results.append(result)
            except Exception as exc:
                failed.append({"code": code, "requested_name": english_name, "error": str(exc)})

        return {
            **source_metadata(),
            "status": "ok",
            "generated_at": _current_time(),
            "source": PRICE_DATA_SOURCE,
            "price_adjustment_status": PRICE_ADJUSTMENT_STATUS,
            "results": results,
            "failed": failed,
        }

    @app.get("/__selftest.txt", response_class=PlainTextResponse)
    @app.get("/selftest.txt", response_class=PlainTextResponse)
    @app.get("/_chatgpt/selftest.txt", response_class=PlainTextResponse)
    @app.get("/public/selftest.txt", response_class=PlainTextResponse)
    def selftest_text() -> str:
        return build_selftest_text()

    @app.get("/__sample-005930.txt", response_class=PlainTextResponse)
    def sample_005930_text() -> str:
        return build_sample_005930_text()

    @app.get("/__sample-trigger-005930.txt", response_class=PlainTextResponse)
    def sample_trigger_005930_text() -> str:
        return build_sample_trigger_005930_text()

    @app.get("/__chatgpt_bundle.txt", response_class=PlainTextResponse)
    @app.get("/chatgpt_bundle.txt", response_class=PlainTextResponse)
    @app.get("/_chatgpt/bundle.txt", response_class=PlainTextResponse)
    @app.get("/public/chatgpt_bundle.txt", response_class=PlainTextResponse)
    def chatgpt_bundle_text(request: Request) -> str:
        return build_bundle_text(settings.public_base_url or str(request.base_url).rstrip("/"))

    @app.get("/__chatgpt_bundle.json")
    @app.get("/_chatgpt/bundle.json")
    @app.get("/public/chatgpt_bundle.json")
    def chatgpt_bundle_json(request: Request) -> JSONResponse:
        return JSONResponse(build_bundle_json(settings.public_base_url or str(request.base_url).rstrip("/")))

    @app.get("/probe_report.txt", response_class=PlainTextResponse)
    def probe_report_text() -> str:
        path = public_dir / "probe_report.txt"
        if not path.exists():
            return "CHATGPT_ROUTE_PROBE_REPORT\ngenerated_at=\nstatus=not_generated\n"
        return path.read_text(encoding="utf-8")

    @app.get("/probe_report.json")
    def probe_report_json() -> JSONResponse:
        path = public_dir / "probe_report.json"
        if not path.exists():
            return JSONResponse({"status": "not_generated"})
        try:
            return JSONResponse(json.loads(path.read_text(encoding="utf-8")))
        except json.JSONDecodeError:
            return JSONResponse({"status": "invalid_probe_report"})

    @app.get("/", response_class=HTMLResponse)
    @app.get("/g/{token}/", response_class=HTMLResponse)
    def index(request: Request, token: str | None = None) -> HTMLResponse:
        if token is not None:
            require_access(token)
        health_info = health_payload()
        prefix = token_prefix(token)
        examples = {
            "ping": "/__ping",
            "health": "/__health",
            "bundle_text_primary": "/__chatgpt_bundle.txt",
            "bundle_text_short": "/chatgpt_bundle.txt",
            "bundle_text_chatgpt": "/_chatgpt/bundle.txt",
            "bundle_text_public": "/public/chatgpt_bundle.txt",
            "bundle_text_static": "/static/chatgpt_bundle.txt",
            "bundle_json_primary": "/__chatgpt_bundle.json",
            "bundle_json_chatgpt": "/_chatgpt/bundle.json",
            "bundle_json_public": "/public/chatgpt_bundle.json",
            "bundle_json_static": "/static/chatgpt_bundle.json",
            "selftest": "/__selftest",
            "sample_ohlcv": "/__sample-ohlcv?code=005930&start=2024-01-01&end=2024-12-31",
            "sample_trigger": "/__sample-trigger?code=005930&trigger_date=2024-01-02",
            "sample_path": "/__sample-path?code=005930&entry_date=2024-01-03",
            "sample_ohlcv_json": "/__sample-ohlcv.json?code=005930&start=2024-01-01&end=2024-12-31",
            "sample_trigger_json": "/__sample-trigger.json?code=005930&trigger_date=2024-01-02",
            "sample_path_json": "/__sample-path.json?code=005930&entry_date=2024-01-03",
            "selftest_text": "/__selftest.txt",
            "selftest_text_short": "/selftest.txt",
            "selftest_text_chatgpt": "/_chatgpt/selftest.txt",
            "selftest_text_static": "/static/selftest.txt",
            "sample_005930_text": "/__sample-005930.txt",
            "sample_trigger_005930_text": "/__sample-trigger-005930.txt",
            "probe_report_text": "/probe_report.txt",
            "probe_report_json": "/probe_report.json",
            "symbol_search": f"{prefix}/api/symbol-search?q=삼성",
            "ohlcv": f"{prefix}/api/ohlcv?code=005930&start=2024-01-01&end=2024-12-31",
            "trigger_backtest": f"{prefix}/api/trigger-backtest?code=005930&trigger_date=2024-01-02",
            "path_summary": f"{prefix}/api/path-summary?code=005930&entry_date=2024-01-03",
            "price_path": f"{prefix}/price-path/005930?start=2024-01-01&end=2024-12-31",
            "trigger_html": f"{prefix}/trigger/005930?trigger_date=2024-01-02",
        }
        return templates.TemplateResponse(
            request,
            "index.html",
            {
                "request": request,
                "health": health_info,
                "examples": examples,
                "metadata": source_metadata(),
            },
        )

    @app.get("/api/ohlcv")
    @app.get("/g/{token}/api/ohlcv")
    def api_ohlcv(
        token: str | None = None,
        code: str = Query(...),
        start: str = Query(...),
        end: str = Query(...),
        market: str | None = None,
        format: str = Query("json", pattern="^(json|csv)$"),
        limit: int | None = None,
    ) -> Any:
        require_access(token)
        try:
            rows = store.get_ohlcv(code, start, end, market, limit or settings.default_max_rows)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if not rows:
            raise HTTPException(status_code=404, detail="no OHLCV rows found")
        rows = _add_source_to_rows(rows)
        if format == "csv":
            return _csv_response(rows, f"ohlcv-{normalize_code(code)}-{start}-{end}.csv")
        return _json_payload(rows, code=normalize_code(code), start=start, end=end, market=market)

    @app.get("/api/trigger-backtest")
    @app.get("/g/{token}/api/trigger-backtest")
    def api_trigger_backtest(
        token: str | None = None,
        code: str = Query(...),
        trigger_date: str = Query(...),
        entry_mode: str = "next_trading_day_close",
        max_window: int = 504,
        windows: str | None = None,
    ) -> dict[str, Any]:
        require_access(token)
        try:
            code_value = normalize_code(code)
            parse_date(trigger_date, "trigger_date")
            window_values = _parse_int_list(windows, DEFAULT_WINDOWS, "windows")
            rows = store.get_forward_window(code_value, trigger_date, max_window)
            if not rows:
                raise HTTPException(status_code=404, detail="no forward OHLCV rows found")
            result = compute_trigger_backtest(rows, trigger_date, entry_mode, window_values, max_window)
            result["code"] = code_value
            result.update(source_metadata())
            return result
        except HTTPException:
            raise
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/path-summary")
    @app.get("/g/{token}/api/path-summary")
    def api_path_summary(
        token: str | None = None,
        code: str = Query(...),
        entry_date: str = Query(...),
        entry_mode: str = "trigger_close",
        points: str | None = None,
    ) -> dict[str, Any]:
        require_access(token)
        try:
            code_value = normalize_code(code)
            parse_date(entry_date, "entry_date")
            point_values = _parse_int_list(points, DEFAULT_POINTS, "points")
            rows = store.get_forward_window(code_value, entry_date, max(point_values))
            if not rows:
                raise HTTPException(status_code=404, detail="no forward OHLCV rows found")
            result = compute_path_summary(rows, entry_date, point_values, entry_mode)
            result["code"] = code_value
            result.update(source_metadata())
            return result
        except HTTPException:
            raise
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/event-window")
    @app.get("/g/{token}/api/event-window")
    def api_event_window(
        token: str | None = None,
        code: str = Query(...),
        anchor_date: str = Query(...),
        pre: int = 10,
        post: int = 10,
        format: str = Query("json", pattern="^(json|csv)$"),
    ) -> Any:
        require_access(token)
        try:
            code_value = normalize_code(code)
            parse_date(anchor_date, "anchor_date")
            result = store.get_event_window(code_value, anchor_date, pre, post)
            if not result["rows"]:
                raise HTTPException(status_code=404, detail="no event window rows found")
            rows = _add_source_to_rows(result["rows"])
            if format == "csv":
                return _csv_response(rows, f"event-window-{code_value}-{anchor_date}.csv")
            result["code"] = code_value
            result["rows"] = rows
            result.update(source_metadata())
            return result
        except HTTPException:
            raise
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/symbol-search")
    @app.get("/g/{token}/api/symbol-search")
    def api_symbol_search(token: str | None = None, q: str = Query(...), limit: int = 20) -> dict[str, Any]:
        require_access(token)
        rows = store.search_symbols(q, limit)
        rows = _add_source_to_rows(rows)
        return _json_payload(rows, query=q)

    @app.get("/api/universe")
    @app.get("/g/{token}/api/universe")
    def api_universe(
        token: str | None = None,
        date: str = Query(...),
        market: str | None = None,
        format: str = Query("json", pattern="^(json|csv)$"),
        limit: int | None = None,
    ) -> Any:
        require_access(token)
        try:
            parse_date(date, "date")
            rows = store.get_universe(date, market, limit)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if not rows:
            raise HTTPException(status_code=404, detail="no universe rows found")
        rows = _add_source_to_rows(rows)
        if format == "csv":
            return _csv_response(rows, f"universe-{date}.csv")
        return _json_payload(rows, date=date, market=market)

    @app.get("/api/research-pack")
    @app.get("/g/{token}/api/research-pack")
    def api_research_pack(
        token: str | None = None,
        items: str = Query(...),
        entry_mode: str = "next_trading_day_close",
    ) -> dict[str, Any]:
        require_access(token)
        parsed_items = [item.strip() for item in items.split(",") if item.strip()]
        if not parsed_items:
            raise HTTPException(status_code=400, detail="items is required")
        results = []
        for item in parsed_items:
            if ":" not in item:
                raise HTTPException(status_code=400, detail="items must use code:trigger_date format")
            code_raw, trigger_date = item.split(":", 1)
            try:
                code_value = normalize_code(code_raw)
                parse_date(trigger_date, "trigger_date")
                forward_rows = store.get_forward_window(code_value, trigger_date, 504)
                if not forward_rows:
                    raise ValueError("no forward OHLCV rows found")
                trigger_result = compute_trigger_backtest(forward_rows, trigger_date, entry_mode, DEFAULT_WINDOWS, 504)
                trigger_result["code"] = code_value
                entry_date = trigger_result.get("entry_date") or trigger_date
                path_rows = store.get_forward_window(code_value, entry_date, max(DEFAULT_POINTS))
                event_result = store.get_event_window(code_value, trigger_date, 10, 10)
                results.append(
                    {
                        "code": code_value,
                        "trigger_date": trigger_date,
                        "trigger_backtest": trigger_result,
                        "path_summary": compute_path_summary(path_rows, entry_date, DEFAULT_POINTS),
                        "event_window_pre10_post10": event_result,
                    }
                )
            except Exception as exc:
                results.append({"code": code_raw, "trigger_date": trigger_date, "error": str(exc)})
        return {
            **source_metadata(),
            "generated_at": _current_time(),
            "item_count": len(results),
            "results": results,
        }

    @app.get("/price-path/{code}", response_class=HTMLResponse)
    @app.get("/g/{token}/price-path/{code}", response_class=HTMLResponse)
    def html_price_path(
        request: Request,
        code: str,
        token: str | None = None,
        start: str | None = None,
        end: str | None = None,
        market: str | None = None,
        limit: int | None = None,
    ) -> HTMLResponse:
        require_access(token)
        start = start or settings.default_start
        end = end or store.get_latest_date() or datetime.now(timezone.utc).date().isoformat()
        try:
            rows = store.get_ohlcv(code, start, end, market, limit or settings.default_max_rows)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if not rows:
            raise HTTPException(status_code=404, detail="no OHLCV rows found")
        prefix = token_prefix(token)
        csv_link = f"{prefix}/api/ohlcv?code={normalize_code(code)}&start={start}&end={end}&format=csv"
        return templates.TemplateResponse(
            request,
            "price_path.html",
            {
                "request": request,
                "code": normalize_code(code),
                "start": start,
                "end": end,
                "rows": rows,
                "csv_link": csv_link,
                "metadata": source_metadata(),
            },
        )

    @app.get("/trigger/{code}", response_class=HTMLResponse)
    @app.get("/g/{token}/trigger/{code}", response_class=HTMLResponse)
    def html_trigger(
        request: Request,
        code: str,
        token: str | None = None,
        trigger_date: str = Query(...),
        entry_mode: str = "next_trading_day_close",
    ) -> HTMLResponse:
        require_access(token)
        try:
            code_value = normalize_code(code)
            rows = store.get_forward_window(code_value, trigger_date, 504)
            if not rows:
                raise HTTPException(status_code=404, detail="no forward OHLCV rows found")
            trigger_result = compute_trigger_backtest(rows, trigger_date, entry_mode, DEFAULT_WINDOWS, 504)
            trigger_result["code"] = code_value
            entry_date = trigger_result.get("entry_date") or trigger_date
            path_rows = store.get_forward_window(code_value, entry_date, max(DEFAULT_POINTS))
            path_summary = compute_path_summary(path_rows, entry_date, DEFAULT_POINTS)
            event_window = store.get_event_window(code_value, trigger_date, 10, 10)
            prefix = token_prefix(token)
            api_links = {
                "trigger_json": f"{prefix}/api/trigger-backtest?code={code_value}&trigger_date={trigger_date}&entry_mode={entry_mode}",
                "path_json": f"{prefix}/api/path-summary?code={code_value}&entry_date={entry_date}",
                "event_json": f"{prefix}/api/event-window?code={code_value}&anchor_date={trigger_date}",
            }
        except HTTPException:
            raise
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return templates.TemplateResponse(
            request,
            "trigger_backtest.html",
            {
                "request": request,
                "code": code_value,
                "trigger_date": trigger_date,
                "summary": trigger_result,
                "path_summary": path_summary,
                "event_window": event_window,
                "api_links": api_links,
                "metadata": source_metadata(),
            },
        )

    return app


app = create_app()
