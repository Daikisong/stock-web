from __future__ import annotations

import glob
import hashlib
import json
import sys
from pathlib import Path


def main() -> None:
    root = Path(sys.argv[1])
    output = Path(sys.argv[2])
    rows: list[dict] = []
    receipts: list[dict] = []
    paths = sorted(glob.glob(str(root / "review_shard_*.jsonl")))
    for name in paths:
        path = Path(name)
        rows.extend(json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip())
        receipt_path = path.with_suffix(".receipt.json")
        if not receipt_path.exists():
            raise RuntimeError(f"missing receipt {receipt_path}")
        receipts.append(json.loads(receipt_path.read_text(encoding="utf-8")))
    if len(receipts) != 64 or sorted(item["shard"] for item in receipts) != list(range(64)):
        raise RuntimeError(f"semantic shard population mismatch {len(receipts)}")
    rows.sort(key=lambda row: int(row.get("global_row_index", 0)))
    expected = [f"SRC-NEWS-{index:06d}" for index in range(1, 820)]
    actual = [str(row.get("source_id")) for row in rows]
    if len(rows) != 819 or actual != expected or len(set(actual)) != 819:
        raise RuntimeError("semantic row population/order mismatch")
    if any(row.get("full_title_body_reviewed") is not True for row in rows):
        raise RuntimeError("full-title/body review flag missing")
    if any(not row.get("review_decision") or not row.get("exact_quote") for row in rows):
        raise RuntimeError("semantic decision/quote missing")
    output.mkdir(parents=True, exist_ok=True)
    payload = "\n".join(json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")) for row in rows) + "\n"
    (output / "reviews.jsonl").write_text(payload, encoding="utf-8")
    report = {
        "schema_version": "nslab.semantic_review_population_receipt.v3",
        "input_row_count": 819,
        "reviewed_row_count": len(rows),
        "shard_count": len(receipts),
        "source_id_order_sha256": hashlib.sha256("\n".join(actual).encode("utf-8")).hexdigest(),
        "full_title_body_reviewed_count": sum(row.get("full_title_body_reviewed") is True for row in rows),
        "status": "FULL_POPULATION_CLOSED",
    }
    (output / "semantic_review_population_receipt.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report, sort_keys=True))


if __name__ == "__main__":
    main()
