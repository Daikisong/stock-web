from __future__ import annotations

import csv
import hashlib
import json
import sys
from datetime import datetime
from pathlib import Path

EXPECTED_PROMPT_SHA = "b5ba21ce1f6e3a91dacf19e33e16d5db9dface141e90a67e78c8588ba1553029"
EXPECTED_NEWS_SHA = "3b5712940be4426bc6e31434d85a364074f33a300bead25b7e264cd66e87807a"
EXPECTED_BLIND_SHA = "43cd5a9efcf00e8d972666c84c2d750081d20244ef329dd4a9daf1796abb5071"


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def main() -> None:
    acquired = Path(sys.argv[1]).resolve()
    paths = {
        "prompt": acquired / "new_bot/docs/research_prompt.md",
        "news": acquired / "new_bot/docs/csv/news_20180619.csv",
        "example": acquired / "new_bot/docs/example2.md",
        "access": acquired / "stock/atlas/research_daily/access/2018/06/20180619.json",
        "manifest": acquired / "stock/atlas/research_daily/manifest.json",
        "schema": acquired / "stock/atlas/research_daily/schema.json",
        "calendar": acquired / "stock/atlas/research_daily/trading_calendar.csv",
        "blind": acquired / "stock/atlas/research_daily/snapshots/2018/06/20180618.csv",
        "receipt": acquired / "raw_download_receipt.json",
    }
    for name, path in paths.items():
        if not path.exists():
            raise RuntimeError(f"missing current-run Raw input {name}: {path}")
    prompt_raw = paths["prompt"].read_bytes()
    news_raw = paths["news"].read_bytes()
    blind_raw = paths["blind"].read_bytes()
    prompt_sha = digest(prompt_raw); news_sha = digest(news_raw); blind_sha = digest(blind_raw)
    if prompt_sha != EXPECTED_PROMPT_SHA or len(prompt_raw) != 430485:
        raise RuntimeError("prompt Raw hash/size mismatch")
    prompt_text = prompt_raw.decode("utf-8")
    if prompt_text.splitlines()[0] != "# NSLAB GOLD PHASE MACHINE — DIRECT CSV RESEARCH RUNNER" or "nslab.gold_phase_machine.direct_csv_research.locked" not in prompt_text:
        raise RuntimeError("prompt Raw title/revision mismatch")
    if news_sha != EXPECTED_NEWS_SHA or len(news_raw) != 2379689:
        raise RuntimeError("news Raw hash/size mismatch")
    with paths["news"].open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    if len(rows) != 819 or not rows or list(rows[0]) != ["page", "row", "date", "time", "title", "body"]:
        raise RuntimeError("news full parse/schema mismatch")
    parsed = [datetime.fromisoformat(f"{row['date']}T{row['time']}+09:00") for row in rows]
    if min(parsed).isoformat() != "2018-06-18T15:30:00+09:00" or max(parsed).isoformat() != "2018-06-19T08:57:58+09:00":
        raise RuntimeError("news publication range mismatch")
    if sum(1 for char in news_raw.decode("utf-8-sig") if ord(char) < 32 and char not in "\n\r\t") != 0:
        raise RuntimeError("news control-character mismatch")
    access = json.loads(paths["access"].read_text(encoding="utf-8"))
    expected_access = {
        "trade_date": "2018-06-19",
        "previous_trade_date": "2018-06-18",
        "next_trade_date": "2018-06-20",
        "blind_snapshot_path": "atlas/research_daily/snapshots/2018/06/20180618.csv",
        "outcome_snapshot_path": "atlas/research_daily/snapshots/2018/06/20180619.csv",
        "build_status": "complete",
    }
    for key, expected in expected_access.items():
        if access.get(key) != expected:
            raise RuntimeError(f"access routing mismatch {key}: {access.get(key)!r}")
    with paths["blind"].open(encoding="utf-8-sig", newline="") as handle:
        blind_rows = list(csv.DictReader(handle))
    if blind_sha != EXPECTED_BLIND_SHA or len(blind_raw) != 718749 or len(blind_rows) != 2124:
        raise RuntimeError("blind Raw provenance mismatch")
    if blind_sha != access.get("blind_snapshot_sha256") or len(blind_raw) != access.get("blind_snapshot_bytes") or len(blind_rows) != access.get("blind_snapshot_row_count"):
        raise RuntimeError("blind/access metadata mismatch")
    if max(row.get("max_source_date", "") for row in blind_rows) > "2018-06-18":
        raise RuntimeError("blind snapshot contains post-P data")
    receipt = json.loads(paths["receipt"].read_text(encoding="utf-8"))
    for name, sha256, size in (("prompt", prompt_sha, len(prompt_raw)), ("news", news_sha, len(news_raw)), ("blind", blind_sha, len(blind_raw))):
        if receipt.get(name, {}).get("sha256") != sha256 or receipt.get(name, {}).get("byte_size") != size:
            raise RuntimeError(f"current-run Raw download receipt mismatch {name}")
    if (acquired / "stock/atlas/research_daily/snapshots/2018/06/20180619.csv").exists():
        raise RuntimeError("outcome snapshot exists before seal")
    report = {
        "schema_version": "nslab.current_run_acquisition_verification.v2",
        "status": "CURRENT_RUN_RAW_ACQUISITION_VERIFIED",
        "prompt_sha256": prompt_sha,
        "prompt_byte_size": len(prompt_raw),
        "news_sha256": news_sha,
        "news_byte_size": len(news_raw),
        "csv_row_count": len(rows),
        "min_published_at": min(parsed).isoformat(),
        "max_published_at": max(parsed).isoformat(),
        "blind_snapshot_sha256": blind_sha,
        "blind_snapshot_byte_size": len(blind_raw),
        "blind_snapshot_row_count": len(blind_rows),
        "preseal_outcome_content_access_count": 0,
    }
    (acquired / "acquisition_verification.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report, sort_keys=True))


if __name__ == "__main__":
    main()
