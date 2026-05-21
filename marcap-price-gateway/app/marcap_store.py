from __future__ import annotations

import math
import re
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import duckdb
import pandas as pd

from app.backtest import compute_event_window
from app.schemas import PRICE_ADJUSTMENT_STATUS, PRICE_DATA_SOURCE, SOURCE_REPO_URL

NORMALIZED_COLUMNS = [
    "date",
    "rank",
    "code",
    "name",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "amount",
    "changes",
    "change_code",
    "changes_ratio",
    "marcap",
    "stocks",
    "market_id",
    "market",
    "dept",
]

NUMERIC_COLUMNS = [
    "rank",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "amount",
    "changes",
    "change_code",
    "changes_ratio",
    "marcap",
    "stocks",
]

OUTPUT_COLUMNS = [
    "date",
    "code",
    "name",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "amount",
    "marcap",
    "stocks",
    "market",
]


def normalize_code(code: Any) -> str:
    text = str(code).strip()
    if not text:
        raise ValueError("code is required")
    if text.endswith(".0"):
        text = text[:-2]
    digits = re.sub(r"\D", "", text)
    if not digits:
        raise ValueError("code must contain digits")
    if len(digits) > 6:
        raise ValueError("code must be at most 6 digits")
    return digits.zfill(6)


def parse_date(value: str | date | datetime, name: str = "date") -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return datetime.strptime(str(value), "%Y-%m-%d").date()
    except ValueError as exc:
        raise ValueError(f"{name} must be YYYY-MM-DD") from exc


def _snake_case(name: str) -> str:
    text = re.sub(r"[^0-9A-Za-z]+", "_", str(name)).strip("_")
    text = re.sub(r"(.)([A-Z][a-z]+)", r"\1_\2", text)
    text = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", text)
    text = text.lower().strip("_")
    aliases = {
        "change_code": "change_code",
        "changes_code": "change_code",
        "changecode": "change_code",
        "changesratio": "changes_ratio",
        "chages_ratio": "changes_ratio",
        "chagesratio": "changes_ratio",
        "change_ratio": "changes_ratio",
        "marketid": "market_id",
        "market_id": "market_id",
    }
    return aliases.get(text, text)


def _data_dir(repo_path: Path) -> Path:
    return Path(repo_path) / "data"


def find_marcap_csv_files(repo_path: Path) -> list[Path]:
    data_dir = _data_dir(repo_path)
    if not data_dir.exists():
        return []
    return sorted(data_dir.glob("*.csv.gz"))


def find_marcap_parquet_files(repo_path: Path) -> list[Path]:
    data_dir = _data_dir(repo_path)
    if not data_dir.exists():
        return []
    return sorted(data_dir.glob("*.parquet"))


def find_marcap_data_files(repo_path: Path) -> list[Path]:
    csv_files = find_marcap_csv_files(repo_path)
    if csv_files:
        return csv_files
    return find_marcap_parquet_files(repo_path)


def latest_data_file(repo_path: Path) -> Path | None:
    files = find_marcap_data_files(repo_path)
    return files[-1] if files else None


def display_repo_url(repo_url: str) -> str:
    return repo_url[:-4] if repo_url.endswith(".git") else repo_url


def _file_year(path: Path) -> int | None:
    match = re.search(r"(19|20)\d{2}", path.name)
    return int(match.group(0)) if match else None


def _candidate_csv_files(repo_path: Path, start: date | None = None, end: date | None = None) -> list[Path]:
    files = find_marcap_data_files(repo_path)
    if not files:
        return []
    if start is None and end is None:
        return files
    start_year = start.year if start else min((_file_year(path) or 0) for path in files)
    end_year = end.year if end else max((_file_year(path) or 9999) for path in files)
    selected = []
    for path in files:
        year = _file_year(path)
        if year is None or start_year <= year <= end_year:
            selected.append(path)
    return selected or files


def _standardize_frame(frame: pd.DataFrame) -> pd.DataFrame:
    frame = frame.copy()
    frame.columns = [_snake_case(column) for column in frame.columns]
    if frame.columns.duplicated().any():
        merged = {}
        for column in dict.fromkeys(frame.columns):
            same_name = frame.loc[:, frame.columns == column]
            merged[column] = same_name.bfill(axis=1).iloc[:, 0] if same_name.shape[1] > 1 else same_name.iloc[:, 0]
        frame = pd.DataFrame(merged)
    if "date" not in frame.columns:
        raise ValueError("marcap CSV is missing Date/date column")
    if "code" not in frame.columns:
        raise ValueError("marcap CSV is missing Code/code column")

    frame["date"] = pd.to_datetime(frame["date"], errors="coerce").dt.date
    code_text = frame["code"].astype("string").str.strip().str.replace(r"\.0$", "", regex=True)
    frame["code"] = code_text.str.extract(r"(\d+)", expand=False).fillna("").str.zfill(6)
    for column in NUMERIC_COLUMNS:
        if column in frame.columns:
            frame[column] = pd.to_numeric(frame[column], errors="coerce")
    for column in NORMALIZED_COLUMNS:
        if column not in frame.columns:
            frame[column] = None
    frame = frame[NORMALIZED_COLUMNS]
    frame = frame[frame["date"].notna() & frame["code"].ne("")]
    return frame


def _clean_value(value: Any) -> Any:
    if value is None:
        return None
    if hasattr(value, "item"):
        value = value.item()
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            return None
        if value.is_integer():
            return int(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()[:10]
    if pd.isna(value):
        return None
    return value


def _records_from_frame(frame: pd.DataFrame) -> list[dict[str, Any]]:
    if frame.empty:
        return []
    records: list[dict[str, Any]] = []
    for row in frame.to_dict("records"):
        item = {key: _clean_value(value) for key, value in row.items()}
        if item.get("date") is not None:
            item["date"] = str(item["date"])[:10]
        if item.get("code") is not None:
            item["code"] = str(item["code"]).zfill(6)
        records.append(item)
    return records


def _read_csv_file(path: Path) -> pd.DataFrame:
    frame = pd.read_csv(path, compression="gzip", dtype={"Code": "string", "code": "string"})
    return _standardize_frame(frame)


def _read_data_file(path: Path) -> pd.DataFrame:
    if path.name.endswith(".csv.gz"):
        return _read_csv_file(path)
    if path.suffix == ".parquet":
        con = duckdb.connect()
        try:
            frame = con.execute("SELECT * FROM read_parquet(?)", [str(path)]).df()
        finally:
            con.close()
        return _standardize_frame(frame)
    raise ValueError(f"unsupported data file type: {path}")


def build_duckdb_cache(repo_path: Path, duckdb_path: Path, repo_url: str = SOURCE_REPO_URL) -> dict[str, Any]:
    files = find_marcap_data_files(repo_path)
    if not files:
        raise FileNotFoundError(f"No .csv.gz or .parquet files found under {_data_dir(repo_path)}")

    duckdb_path = Path(duckdb_path)
    duckdb_path.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(duckdb_path))
    try:
        con.execute("DROP TABLE IF EXISTS daily_prices")
        con.execute("DROP TABLE IF EXISTS metadata")
        con.execute(
            """
            CREATE TABLE daily_prices (
                date DATE,
                rank DOUBLE,
                code VARCHAR,
                name VARCHAR,
                open DOUBLE,
                high DOUBLE,
                low DOUBLE,
                close DOUBLE,
                volume DOUBLE,
                amount DOUBLE,
                changes DOUBLE,
                change_code DOUBLE,
                changes_ratio DOUBLE,
                marcap DOUBLE,
                stocks DOUBLE,
                market_id VARCHAR,
                market VARCHAR,
                dept VARCHAR
            )
            """
        )

        loaded: list[str] = []
        min_date: date | None = None
        max_date: date | None = None
        row_count = 0
        for path in files:
            frame = _read_data_file(path)
            if frame.empty:
                continue
            loaded.append(path.name)
            min_value = frame["date"].min()
            max_value = frame["date"].max()
            min_date = min_value if min_date is None else min(min_date, min_value)
            max_date = max_value if max_date is None else max(max_date, max_value)
            row_count += len(frame)
            con.register("loaded_frame", frame)
            con.execute("INSERT INTO daily_prices SELECT * FROM loaded_frame")
            con.unregister("loaded_frame")

        for sql in [
            "CREATE INDEX idx_daily_prices_code_date ON daily_prices(code, date)",
            "CREATE INDEX idx_daily_prices_date_market ON daily_prices(date, market)",
        ]:
            try:
                con.execute(sql)
            except Exception:
                pass

        con.execute(
            """
            CREATE TABLE metadata (
                source_name VARCHAR,
                source_repo_url VARCHAR,
                built_at VARCHAR,
                min_date DATE,
                max_date DATE,
                row_count BIGINT,
                year_files_loaded VARCHAR,
                price_adjustment_status VARCHAR
            )
            """
        )
        metadata = {
            "source_name": PRICE_DATA_SOURCE,
            "source_repo_url": display_repo_url(repo_url),
            "built_at": datetime.now(timezone.utc).isoformat(),
            "min_date": min_date,
            "max_date": max_date,
            "row_count": row_count,
            "year_files_loaded": ",".join(loaded),
            "price_adjustment_status": PRICE_ADJUSTMENT_STATUS,
        }
        con.execute(
            "INSERT INTO metadata VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [
                metadata["source_name"],
                metadata["source_repo_url"],
                metadata["built_at"],
                metadata["min_date"],
                metadata["max_date"],
                metadata["row_count"],
                metadata["year_files_loaded"],
                metadata["price_adjustment_status"],
            ],
        )
        return {key: _clean_value(value) for key, value in metadata.items()}
    finally:
        con.close()


class MarcapStore:
    def __init__(self, repo_path: Path, duckdb_path: Path, repo_url: str = SOURCE_REPO_URL):
        self.repo_path = Path(repo_path)
        self.duckdb_path = Path(duckdb_path)
        self.repo_url = repo_url

    def ensure_cache(self) -> bool:
        if self.duckdb_path.exists():
            return True
        if not find_marcap_data_files(self.repo_path):
            return False
        build_duckdb_cache(self.repo_path, self.duckdb_path, self.repo_url)
        return True

    @property
    def has_cache(self) -> bool:
        return self.duckdb_path.exists()

    def _connect(self) -> duckdb.DuckDBPyConnection:
        return duckdb.connect(str(self.duckdb_path), read_only=True)

    def _query(self, sql: str, params: list[Any] | None = None) -> list[dict[str, Any]]:
        con = self._connect()
        try:
            frame = con.execute(sql, params or []).df()
        finally:
            con.close()
        return _records_from_frame(frame)

    def _read_csv_range(
        self,
        start: date | None = None,
        end: date | None = None,
        code: str | None = None,
        market: str | None = None,
    ) -> pd.DataFrame:
        frames: list[pd.DataFrame] = []
        for path in _candidate_csv_files(self.repo_path, start, end):
            frame = _read_data_file(path)
            if start is not None:
                frame = frame[frame["date"] >= start]
            if end is not None:
                frame = frame[frame["date"] <= end]
            if code is not None:
                frame = frame[frame["code"] == code]
            if market:
                frame = frame[frame["market"] == market]
            if not frame.empty:
                frames.append(frame)
        if not frames:
            return pd.DataFrame(columns=NORMALIZED_COLUMNS)
        merged = pd.concat(frames, ignore_index=True)
        return merged.sort_values(["date", "code"]).reset_index(drop=True)

    def health(self) -> dict[str, Any]:
        base = {
            "source_name": PRICE_DATA_SOURCE,
            "source_repo_url": display_repo_url(self.repo_url),
            "cache_path": str(self.duckdb_path),
            "price_adjustment_status": PRICE_ADJUSTMENT_STATUS,
        }
        if self.has_cache:
            rows = self._query("SELECT * FROM metadata LIMIT 1")
            if rows:
                return {"status": "ok", **base, **rows[0]}
        files = find_marcap_data_files(self.repo_path)
        if not files:
            return {
                "status": "missing_data",
                **base,
                "min_date": None,
                "max_date": None,
                "row_count": 0,
                "year_files_loaded": "",
            }
        frame = self._read_csv_range()
        return {
            "status": "csv_fallback",
            **base,
            "min_date": _clean_value(frame["date"].min()) if not frame.empty else None,
            "max_date": _clean_value(frame["date"].max()) if not frame.empty else None,
            "row_count": int(len(frame)),
            "year_files_loaded": ",".join(path.name for path in files),
        }

    def search_symbols(self, q: str, limit: int = 20) -> list[dict[str, Any]]:
        q = str(q).strip()
        if not q:
            return []
        limit = max(1, min(int(limit), 100))
        code_q = re.sub(r"\D", "", q)
        if self.has_cache:
            where = "name LIKE ?"
            params: list[Any] = [f"%{q}%"]
            if code_q:
                where = f"({where} OR code LIKE ?)"
                params.append(f"{code_q.zfill(6)[:6]}%")
            params.append(limit)
            return self._query(
                f"""
                SELECT code, any_value(name) AS name, any_value(market) AS market,
                       min(date) AS first_date, max(date) AS last_date, count(*) AS row_count
                FROM daily_prices
                WHERE {where}
                GROUP BY code
                ORDER BY CASE WHEN code = ? THEN 0 ELSE 1 END, code
                LIMIT ?
                """,
                params[:-1] + [code_q.zfill(6) if code_q else "", params[-1]],
            )
        frame = self._read_csv_range()
        if code_q:
            mask = frame["name"].astype(str).str.contains(q, case=False, na=False) | frame["code"].str.startswith(
                code_q.zfill(6)[:6]
            )
        else:
            mask = frame["name"].astype(str).str.contains(q, case=False, na=False)
        grouped = (
            frame[mask]
            .groupby("code", as_index=False)
            .agg(name=("name", "first"), market=("market", "first"), first_date=("date", "min"), last_date=("date", "max"), row_count=("date", "count"))
            .sort_values("code")
            .head(limit)
        )
        return _records_from_frame(grouped)

    def get_ohlcv(
        self,
        code: Any,
        start: str | date,
        end: str | date,
        market: str | None = None,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        code_value = normalize_code(code)
        start_date = parse_date(start, "start")
        end_date = parse_date(end, "end")
        if start_date > end_date:
            raise ValueError("start must be on or before end")
        limit_clause = ""
        params: list[Any] = [code_value, start_date, end_date]
        market_clause = ""
        if market:
            market_clause = " AND market = ?"
            params.append(market)
        if limit is not None:
            limit_clause = " LIMIT ?"
            params.append(max(1, int(limit)))
        if self.has_cache:
            rows = self._query(
                f"""
                SELECT {', '.join(OUTPUT_COLUMNS)}
                FROM daily_prices
                WHERE code = ? AND date BETWEEN ? AND ?{market_clause}
                ORDER BY date
                {limit_clause}
                """,
                params,
            )
        else:
            frame = self._read_csv_range(start_date, end_date, code_value, market)
            if limit is not None:
                frame = frame.head(max(1, int(limit)))
            rows = _records_from_frame(frame[OUTPUT_COLUMNS])
        return rows

    def get_universe(self, date_value: str | date, market: str | None = None, limit: int | None = None) -> list[dict[str, Any]]:
        target = parse_date(date_value, "date")
        params: list[Any] = [target]
        market_clause = ""
        if market:
            market_clause = " AND market = ?"
            params.append(market)
        limit_clause = ""
        if limit is not None:
            limit_clause = " LIMIT ?"
            params.append(max(1, int(limit)))
        if self.has_cache:
            return self._query(
                f"""
                SELECT {', '.join(OUTPUT_COLUMNS)}
                FROM daily_prices
                WHERE date = ?{market_clause}
                ORDER BY marcap DESC NULLS LAST, code
                {limit_clause}
                """,
                params,
            )
        frame = self._read_csv_range(target, target, market=market)
        frame = frame.sort_values(["marcap", "code"], ascending=[False, True], na_position="last")
        if limit is not None:
            frame = frame.head(max(1, int(limit)))
        return _records_from_frame(frame[OUTPUT_COLUMNS])

    def get_latest_date(self) -> str | None:
        if self.has_cache:
            rows = self._query("SELECT max(date) AS latest_date FROM daily_prices")
            return rows[0].get("latest_date") if rows else None
        frame = self._read_csv_range()
        if frame.empty:
            return None
        return _clean_value(frame["date"].max())

    def get_first_available_on_or_after(self, code: Any, date_value: str | date) -> dict[str, Any] | None:
        code_value = normalize_code(code)
        target = parse_date(date_value)
        if self.has_cache:
            rows = self._query(
                f"""
                SELECT {', '.join(NORMALIZED_COLUMNS)}
                FROM daily_prices
                WHERE code = ? AND date >= ?
                ORDER BY date
                LIMIT 1
                """,
                [code_value, target],
            )
        else:
            frame = self._read_csv_range(target, None, code_value)
            rows = _records_from_frame(frame.head(1))
        return rows[0] if rows else None

    def get_forward_window(self, code: Any, entry_date: str | date, max_window: int = 504) -> list[dict[str, Any]]:
        code_value = normalize_code(code)
        target = parse_date(entry_date, "entry_date")
        limit = max(1, int(max_window) + 2)
        if self.has_cache:
            return self._query(
                f"""
                SELECT {', '.join(NORMALIZED_COLUMNS)}
                FROM daily_prices
                WHERE code = ? AND date >= ?
                ORDER BY date
                LIMIT ?
                """,
                [code_value, target, limit],
            )
        frame = self._read_csv_range(target, None, code_value)
        return _records_from_frame(frame.head(limit))

    def get_all_rows_for_code(self, code: Any) -> list[dict[str, Any]]:
        code_value = normalize_code(code)
        if self.has_cache:
            return self._query(
                f"""
                SELECT {', '.join(NORMALIZED_COLUMNS)}
                FROM daily_prices
                WHERE code = ?
                ORDER BY date
                """,
                [code_value],
            )
        frame = self._read_csv_range(code=code_value)
        return _records_from_frame(frame)

    def get_event_window(self, code: Any, anchor_date: str | date, pre: int = 10, post: int = 10) -> dict[str, Any]:
        rows = self.get_all_rows_for_code(code)
        return compute_event_window(rows, anchor_date, pre, post)
