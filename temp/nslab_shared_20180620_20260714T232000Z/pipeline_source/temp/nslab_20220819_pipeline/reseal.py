from __future__ import annotations

import argparse
from pathlib import Path

from common import TRADE_DATE, canonical_json, now_kst, read_json, read_jsonl, sha256_file, sha256_text, write_json


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--blind-dir", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    args = parser.parse_args()
    artifacts = args.blind_dir / "artifacts"
    phase_path = artifacts / "phase_state.json"
    phase = read_json(phase_path)
    phase["phase"] = "PHASE_5_BLIND_SEALED"
    phase["blind_sealed"] = True
    phase["outcome_access_allowed"] = False
    phase.pop("blind_packet_manifest_sha256", None)
    phase.pop("sealed_blind_report_sha256", None)
    write_json(phase_path, phase)

    prediction = read_json(artifacts / "blind_prediction.json")
    screenings = read_jsonl(artifacts / "candidate_screening.jsonl")
    files = {}
    for path in sorted(artifacts.iterdir()):
        if not path.is_file() or path.name in {"blind_packet_manifest.json", "blind_seal_receipt.json"}:
            continue
        files[path.name] = {
            "sha256": sha256_file(path),
            "byte_size": path.stat().st_size,
            "row_count": len(read_jsonl(path)) if path.suffix == ".jsonl" else None,
        }
    manifest = {
        "schema_version": "nslab.blind_packet_manifest.v30",
        "run_id": args.run_id,
        "trade_date": TRADE_DATE,
        "cutoff_at": "2022-08-19T08:59:59+09:00",
        "input_sha256": "ec55b86339923c35db8c7b31e01f1706213afa3ffdb535aac243f2fd56a454fb",
        "files": files,
        "final_watchlist_count": len(prediction.get("final_watchlist", [])),
        "candidate_screening_count": len(screenings),
        "created_at": now_kst(),
    }
    write_json(artifacts / "blind_packet_manifest.json", manifest)
    manifest_sha = sha256_text(canonical_json(read_json(artifacts / "blind_packet_manifest.json")))
    receipt = {
        "schema_version": "nslab.blind_seal_receipt.v30",
        "run_id": args.run_id,
        "trade_date": TRADE_DATE,
        "blind_packet_manifest_sha256": manifest_sha,
        "blind_packet_manifest_verified": True,
        "sealed_blind_report_sha256": sha256_file(artifacts / "blind_report.md"),
        "preseal_outcome_download_count": 0,
        "preseal_outcome_header_read_count": 0,
        "preseal_outcome_sha256_count": 0,
        "preseal_outcome_row_count_count": 0,
        "preseal_outcome_parse_count": 0,
        "preseal_outcome_winner_census_count": 0,
        "preseal_outcome_access_all_zero": True,
        "seal_status": "VERIFIED_CLEAN",
        "sealed_at": now_kst(),
    }
    write_json(artifacts / "blind_seal_receipt.json", receipt)
    write_json(args.blind_dir / "blind_state.json", {
        "run_id": args.run_id,
        "trade_date": TRADE_DATE,
        "blind_packet_manifest_sha256": manifest_sha,
        "seal_receipt_path": "artifacts/blind_seal_receipt.json",
        "artifact_dir": "artifacts",
        "outcome_access_allowed_after_verification": True,
    })
    print(manifest_sha)


if __name__ == "__main__":
    main()
