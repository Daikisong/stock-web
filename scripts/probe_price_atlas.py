from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIAGNOSTICS_ROOT = ROOT / "diagnostics"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def main() -> int:
    paths = [
        "atlas/manifest.json",
        "atlas/source_manifest.json",
        "atlas/schema.json",
        "atlas/universe/all_symbols.csv",
        "atlas/index/by_code_prefix/005.json",
        "atlas/symbol_profiles/005/005930.json",
        "atlas/ohlcv_min_by_symbol_year/005/005930/2024.csv",
        "atlas/research_packs/smoke/smoke_005930_000660_298040_267260_086520.json",
        "atlas/research_packs/smoke/smoke_005930_000660_298040_267260_086520.md",
        "diagnostics/chatgpt_bundle.txt",
        "diagnostics/chatgpt_bundle.json",
        "diagnostics/atlas_build_report.md",
        "diagnostics/atlas_validation_report.md",
        "diagnostics/atlas_size_report.md",
    ]
    results = []
    for item in paths:
        path = ROOT / item
        results.append({"path": item, "exists": path.exists(), "bytes": path.stat().st_size if path.exists() else 0})
    ok = all(item["exists"] for item in results)
    bundle_path = ROOT / "diagnostics" / "chatgpt_bundle.txt"
    bundle_preview = bundle_path.read_text(encoding="utf-8")[:1000] if bundle_path.exists() else ""
    payload = {
        "generated_at": utc_now(),
        "status": "pass" if ok else "fail",
        "total_paths": len(results),
        "ok_paths": sum(1 for item in results if item["exists"]),
        "failed_paths": [item["path"] for item in results if not item["exists"]],
        "results": results,
        "bundle_preview": bundle_preview,
    }
    lines = [
        "PRICE_ATLAS_PROBE_REPORT",
        f"generated_at={payload['generated_at']}",
        f"status={payload['status']}",
        f"total_paths={payload['total_paths']}",
        f"ok_paths={payload['ok_paths']}",
        f"failed_paths={len(payload['failed_paths'])}",
        "",
        "PATH|exists|bytes",
    ]
    lines.extend(f"{item['path']}|{str(item['exists']).lower()}|{item['bytes']}" for item in results)
    lines.extend(["", "BUNDLE_PREVIEW_BEGIN", bundle_preview, "BUNDLE_PREVIEW_END", ""])
    DIAGNOSTICS_ROOT.mkdir(parents=True, exist_ok=True)
    (DIAGNOSTICS_ROOT / "probe_report.txt").write_text("\n".join(lines), encoding="utf-8")
    (DIAGNOSTICS_ROOT / "probe_report.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
