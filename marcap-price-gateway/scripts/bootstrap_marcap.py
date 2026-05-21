from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.marcap_store import find_marcap_data_files, latest_data_file
from app.settings import Settings


def run() -> int:
    settings = Settings.from_env()
    repo_path = Path(settings.marcap_repo_path)
    git = shutil.which("git")

    if repo_path.exists():
        if (repo_path / ".git").exists() and git:
            subprocess.run([git, "-C", str(repo_path), "pull", "--ff-only"], check=True)
        elif not git and find_marcap_data_files(repo_path):
            print("git is unavailable; existing marcap files found, continuing.")
        elif not (repo_path / ".git").exists():
            print(f"{repo_path} exists but is not a git repo; leaving existing files unchanged.")
        elif not git:
            print("git is unavailable and no existing data files were found.", file=sys.stderr)
            return 1
    else:
        if not git:
            print("git is required to clone FinanceData/marcap when data files are missing.", file=sys.stderr)
            return 1
        repo_path.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run([git, "clone", "--depth", "1", settings.marcap_repo_url, str(repo_path)], check=True)

    latest = latest_data_file(repo_path)
    if latest:
        print(f"latest_data_file={latest}")
    else:
        print(f"no .csv.gz files detected under {repo_path / 'data'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
