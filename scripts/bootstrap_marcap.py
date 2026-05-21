from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MARCAP_REPO_URL = "https://github.com/FinanceData/marcap.git"
MARCAP_REPO_PATH = ROOT / ".cache" / "marcap"


def data_files() -> list[Path]:
    data_dir = MARCAP_REPO_PATH / "data"
    return sorted(data_dir.glob("marcap-*.csv.gz")) or sorted(data_dir.glob("marcap-*.parquet"))


def main() -> int:
    git = shutil.which("git")
    if MARCAP_REPO_PATH.exists():
        if (MARCAP_REPO_PATH / ".git").exists() and git:
            subprocess.run([git, "-C", str(MARCAP_REPO_PATH), "pull", "--ff-only"], check=True)
        elif not git and data_files():
            print("git unavailable; using existing .cache/marcap data files")
        elif not (MARCAP_REPO_PATH / ".git").exists():
            print(f"{MARCAP_REPO_PATH} exists but is not a git repo; using existing files")
    else:
        if not git:
            print("git is required when .cache/marcap is missing", file=sys.stderr)
            return 1
        MARCAP_REPO_PATH.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run([git, "clone", "--depth", "1", MARCAP_REPO_URL, str(MARCAP_REPO_PATH)], check=True)

    files = data_files()
    if not files:
        print(f"no marcap data files found under {MARCAP_REPO_PATH / 'data'}", file=sys.stderr)
        return 2
    years = []
    for path in files:
        stem = path.name.replace(".csv.gz", "").replace(".parquet", "")
        try:
            years.append(int(stem.split("-")[-1]))
        except ValueError:
            pass
    print(f"data_format={files[0].suffix if files[0].suffix != '.gz' else '.csv.gz'}")
    print(f"detected_years={','.join(map(str, years))}")
    print(f"latest_file={files[-1]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
