from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    marcap_repo_url: str
    marcap_repo_path: Path
    marcap_duckdb_path: Path
    access_token: str
    default_start: str
    default_max_rows: int
    public_base_url: str

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            marcap_repo_url=os.getenv("MARCAP_REPO_URL", "https://github.com/FinanceData/marcap.git"),
            marcap_repo_path=Path(os.getenv("MARCAP_REPO_PATH", "./data/marcap")),
            marcap_duckdb_path=Path(os.getenv("MARCAP_DUCKDB_PATH", "./data/cache/marcap.duckdb")),
            access_token=os.getenv("ACCESS_TOKEN", "dev"),
            default_start=os.getenv("DEFAULT_START", "1995-05-02"),
            default_max_rows=int(os.getenv("DEFAULT_MAX_ROWS", "20000")),
            public_base_url=os.getenv("PUBLIC_BASE_URL", "").rstrip("/"),
        )

    @property
    def token_required(self) -> bool:
        return self.access_token not in ("", "dev")

    @property
    def access_mode(self) -> str:
        if self.token_required:
            return "token_path_required"
        return "dev_open_paths"
