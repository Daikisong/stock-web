from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run() -> int:
    subprocess.run([sys.executable, "scripts/bootstrap_marcap.py"], check=True, cwd=ROOT)
    subprocess.run([sys.executable, "scripts/build_duckdb_cache.py"], check=True, cwd=ROOT)
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
