from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

from news_scalping_lab.research_import.versioned_bundle import inspect_versioned_bundle


def main() -> None:
    output = Path(sys.argv[1])
    path = output / "20180619_nslab_episode_bundle.md"
    result = inspect_versioned_bundle(path)
    (output / "repo_import_inspection.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )
    checks = {
        "adapter": result.get("adapter") == "v23-direct-ingest",
        "supported": result.get("supported") is True,
        "validation_passed": result.get("validation_passed") is True,
        "import_loss_audit_passed": result.get("import_loss_audit_passed") is True,
        "raw_normalized_record_count_matches": result.get("raw_normalized_record_count_matches") is True,
        "record_id_set_matches_raw": result.get("record_id_set_matches_raw") is True,
        "record_type_counts_match_raw": result.get("record_type_counts_match_raw") is True,
        "training_eligible_count_matches_raw": result.get("training_eligible_count_matches_raw") is True,
        "hash_mismatch_count": result.get("hash_mismatch_count") == 0,
        "hash_expectation_conflict_count": result.get("hash_expectation_conflict_count") == 0,
        "missing_source_reference_count": result.get("missing_source_reference_count") == 0,
        "missing_payload_reference_count": result.get("missing_payload_reference_count") == 0,
        "brain_delta_positive": result.get("parsed_brain_delta_jsonl_row_count", result.get("raw_record_count", 0)) > 0,
    }
    failed = [key for key, passed in checks.items() if not passed]
    if failed:
        raise RuntimeError(f"repo importer checks failed: {failed}")
    final_receipt = json.loads((output / "independent_final_validation_receipt.json").read_text(encoding="utf-8"))
    if final_receipt.get("status") != "ACCEPT_FULL_REOPEN_REPARSE_PASSED":
        raise RuntimeError("independent final reparse status mismatch")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if final_receipt.get("sha256") != digest or int(final_receipt.get("parsed_brain_delta_jsonl_row_count", 0)) <= 0:
        raise RuntimeError("independent final reparse hash/brain mismatch")
    print(json.dumps({"status": "REPO_IMPORT_AND_EXTERNAL_REPARSE_PASSED", "bundle_sha256": digest, "brain_delta_count": final_receipt["parsed_brain_delta_jsonl_row_count"]}, sort_keys=True))


if __name__ == "__main__":
    main()
