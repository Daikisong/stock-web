from __future__ import annotations

import shutil
from datetime import date, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.marcap_store import MarcapStore
from app.settings import Settings


FIXTURE_PATH = Path(__file__).parent / "fixtures" / "sample_marcap.csv.gz"


def trading_day(index: int) -> str:
    current = date(2020, 1, 2)
    seen = 0
    while seen < index:
        current += timedelta(days=1)
        if current.weekday() < 5:
            seen += 1
    return current.isoformat()


@pytest.fixture()
def sample_repo(tmp_path: Path) -> Path:
    repo_path = tmp_path / "marcap"
    data_dir = repo_path / "data"
    data_dir.mkdir(parents=True)
    shutil.copy(FIXTURE_PATH, data_dir / "marcap-2020.csv.gz")
    return repo_path


@pytest.fixture()
def make_client(sample_repo: Path, tmp_path: Path):
    def _make_client(access_token: str = "dev") -> TestClient:
        settings = Settings(
            marcap_repo_url="https://github.com/FinanceData/marcap.git",
            marcap_repo_path=sample_repo,
            marcap_duckdb_path=tmp_path / f"cache-{access_token}" / "marcap.duckdb",
            access_token=access_token,
            default_start="1995-05-02",
            default_max_rows=20000,
            public_base_url="",
        )
        app = create_app(settings=settings, store=MarcapStore(settings.marcap_repo_path, settings.marcap_duckdb_path))
        return TestClient(app)

    return _make_client
