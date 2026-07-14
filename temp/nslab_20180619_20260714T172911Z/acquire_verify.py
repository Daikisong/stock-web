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


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def main() -> None:
    acquired = Path(sys.argv[1])
    checkout_new_bot = Path(sys.argv[2])
    checkout_stock = Path(sys.argv[3])
    prompt = acquired / "new_bot/docs/research_prompt.md"
    news = acquired / "new_bot/docs/csv/news_20180619.csv"
    blind = acquired / "stock/atlas/research_daily/snapshots/2018/06/20180618.csv"
    access_path = acquired / "stock/atlas/research_daily/access/2018/06/20180619.json"
    raw_receipt_path = acquired / "raw_download_receipt.json"
    required = [prompt, news, blind, access_path, raw_receipt_path, acquired / "new_bot/docs/example2.md"]
    for path in required:
        if not path.exists():
            raise RuntimeError(f"missing current-run input {path}")
    if prompt.read_bytes() != (checkout_new_bot / "docs/research_prompt.md").read_bytes():
        raise RuntimeError("Raw prompt/current main checkout byte mismatch")
    if news.read_bytes() != (checkout_new_bot / "docs/csv/news_20180619.csv").read_bytes():
        raise RuntimeError("Raw CSV/current main checkout byte mismatch")
    if blind.read_bytes() != (checkout_stock / "atlas/research_daily/snapshots/2018/06/20180618.csv").read_bytes():
        raise RuntimeError("Raw P snapshot/current main checkout byte mismatch")
    p_raw = prompt.read_bytes(); n_raw = news.read_bytes(); b_raw = blind.read_bytes()
    p_sha = sha(p_raw); n_sha = sha(n_raw); b_sha = sha(b_raw)
    if p_sha != EXPECTED_PROMPT_SHA or len(p_raw) != 430485:
        raise RuntimeError("prompt hash/size mismatch")
    p_text = p_raw.decode("utf-8")
    if p_text.splitlines()[0] != "# NSLAB GOLD PHASE MACHINE — DIRECT CSV RESEARCH RUNNER" or "nslab.gold_phase_machine.direct_csv_research.locked" not in p_text:
        raise RuntimeError("prompt title/revision mismatch")
    if n_sha != EXPECTED_NEWS_SHA or len(n_raw) != 2379689:
        raise RuntimeError("news hash/size mismatch")
    with news.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    if len(rows) != 819 or not rows or list(rows[0]) != ["page", "row", "date", "time", "title", "body"]:
        raise RuntimeError("news full parse/schema mismatch")
    parsed = [datetime.fromisoformat(f"{row['date']}T{row['time']}+09:00") for row in rows]
    if min(parsed).isoformat() != "2018-06-18T15:30:00+09:00" or max(parsed).isoformat() != "2018-06-19T08:57:58+09:00":
        raise RuntimeError("news publication range mismatch")
    control = sum(1 for char in n_raw.decode("utf-8-sig") if ord(char) < 32 and char not in "\n\r\t")
    if control:
        raise RuntimeError("unexpected control characters")
    access = json.loads(access_path.read_text(encoding="utf-8"))
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
            raise RuntimeError(f"access mismatch {key}")
    with blind.open(encoding="utf-8-sig", newline="") as handle:
        blind_rows = list(csv.DictReader(handle))
    if b_sha != EXPECTED_BLIND_SHA or len(b_raw) != 718749 or len(blind_rows) != 2124:
        raise RuntimeError("blind snapshot provenance mismatch")
    if b_sha != access["blind_snapshot_sha256"] or len(b_raw) != access["blind_snapshot_bytes"] or len(blind_rows) != access["blind_snapshot_row_count"]:
        raise RuntimeError("blind/access parity mismatch")
    if max(row.get("max_source_date", "") for row in blind_rows) > "2018-06-18":
        raise RuntimeError("blind snapshot contains post-P data")
    raw_receipt = json.loads(raw_receipt_path.read_text(encoding="utf-8"))
    for name, digest, size in (("prompt", p_sha, len(p_raw)), ("news", n_sha, len(n_raw)), ("blind", b_sha, len(b_raw))):
        if raw_receipt[name]["sha256"] != digest or raw_receipt[name]["byte_size"] != size:
            raise RuntimeError(f"Raw receipt mismatch {name}")
    if (acquired / "stock/atlas/research_daily/snapshots/2018/06/20180619.csv").exists():
        raise RuntimeError("outcome snapshot accessed before seal")
    summary = {
        "status": "CURRENT_RUN_ACQUISITION_VERIFIED",
        "prompt_sha256": p_sha,
        "prompt_byte_size": len(p_raw),
        "news_sha256": n_sha,
        "news_byte_size": len(n_raw),
        "csv_row_count": len(rows),
        "min_published_at": min(parsed).isoformat(),
        "max_published_at": max(parsed).isoformat(),
        "blind_snapshot_sha256": b_sha,
        "blind_snapshot_byte_size": len(b_raw),
        "blind_snapshot_row_count": len(blind_rows),
        "preseal_outcome_content_access_count": 0,
    }
    (acquired / "acquisition_verification.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(summary, sort_keys=True))


if __name__ == "__main__":
    main()
