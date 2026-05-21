from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.marcap_store import build_duckdb_cache
from app.settings import Settings


def run() -> int:
    settings = Settings.from_env()
    metadata = build_duckdb_cache(settings.marcap_repo_path, settings.marcap_duckdb_path, settings.marcap_repo_url)
    print(json.dumps(metadata, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
