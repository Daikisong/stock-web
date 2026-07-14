from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import random
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

POLLINATIONS_ENDPOINT = "https://text.pollinations.ai/openai"
GITHUB_ENDPOINT = "https://models.github.ai/inference/chat/completions"
GITHUB_MODELS = [
    "openai/gpt-4.1-mini",
    "openai/gpt-4o-mini",
    "mistral-ai/mistral-medium-2505",
    "cohere/cohere-command-a",
]


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def parse_json_payload(text: str) -> Any:
    cleaned = re.sub(r"^```(?:json)?\s*", "", text.strip(), flags=re.I)
    cleaned = re.sub(r"\s*```$", "", cleaned).strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        starts = [pos for pos in (cleaned.find("{"), cleaned.find("[")) if pos >= 0]
        if not starts:
            raise
        start = min(starts)
        for end in range(len(cleaned), start, -1):
            candidate = cleaned[start:end].strip()
            if not candidate or candidate[-1] not in "]}":
                continue
            try:
                return json.loads(candidate)
            except json.JSONDecodeError:
                continue
        raise


def http_json(url: str, body: dict[str, Any], headers: dict[str, str], timeout: int = 240) -> Any:
    request = urllib.request.Request(
        url,
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        raw = response.read().decode("utf-8", errors="replace")
    try:
        envelope = json.loads(raw)
    except json.JSONDecodeError:
        return parse_json_payload(raw)
    if isinstance(envelope, dict):
        content = envelope.get("choices", [{}])[0].get("message", {}).get("content")
        if isinstance(content, str):
            return parse_json_payload(content)
        if "records" in envelope:
            return envelope
    return envelope


def call_model(system: str, user: str, token: str, seed: int) -> tuple[Any, str]:
    errors: list[str] = []
    poll_body = {
        "model": "openai",
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": 0,
        "max_tokens": 12000,
        "reasoning_effort": "low",
        "stream": False,
        "seed": seed,
    }
    for attempt in range(1, 4):
        try:
            parsed = http_json(
                POLLINATIONS_ENDPOINT,
                poll_body,
                {"Content-Type": "application/json", "Accept": "application/json", "User-Agent": "NSLAB-Gold-20180619/1.0"},
            )
            return parsed, "pollinations/openai"
        except Exception as exc:  # noqa: BLE001
            errors.append(f"pollinations attempt {attempt}: {type(exc).__name__}: {exc}")
            time.sleep(min(12.0, 1.5 * attempt * attempt + random.random()))

    if not token:
        raise RuntimeError("all semantic endpoints failed and GITHUB_TOKEN is unavailable: " + " | ".join(errors[-4:]))

    for offset, model in enumerate(GITHUB_MODELS):
        body = {
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": 0,
            "max_tokens": 9000,
            "response_format": {"type": "json_object"},
            "seed": seed + offset,
        }
        for attempt in range(1, 3):
            try:
                parsed = http_json(
                    GITHUB_ENDPOINT,
                    body,
                    {
                        "Authorization": f"Bearer {token}",
                        "Content-Type": "application/json",
                        "Accept": "application/vnd.github+json",
                        "X-GitHub-Api-Version": "2026-03-10",
                        "User-Agent": "NSLAB-Gold-20180619/1.0",
                    },
                )
                return parsed, model
            except urllib.error.HTTPError as exc:
                detail = exc.read().decode("utf-8", errors="replace")[:600]
                errors.append(f"{model} HTTP {exc.code}: {detail}")
                retry_after = exc.headers.get("Retry-After") if exc.headers else None
                delay = float(retry_after) if retry_after and retry_after.replace(".", "", 1).isdigit() else min(20.0, 3.0 * attempt)
                time.sleep(delay)
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{model} attempt {attempt}: {type(exc).__name__}: {exc}")
                time.sleep(min(10.0, 2.0 * attempt))
    raise RuntimeError("semantic model calls exhausted: " + " | ".join(errors[-8:]))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pipeline", required=True)
    parser.add_argument("--news", required=True)
    parser.add_argument("--blind", required=True)
    parser.add_argument("--shard", required=True, type=int)
    parser.add_argument("--shard-count", required=True, type=int)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    if args.shard < 0 or args.shard >= args.shard_count:
        raise SystemExit("invalid shard")

    pipeline = Path(args.pipeline).resolve()
    sys.path.insert(0, str(pipeline))
    import blind  # type: ignore
    import common  # type: ignore

    news_path = Path(args.news)
    blind_path = Path(args.blind)
    news_rows = read_csv(news_path)
    snapshot_rows = read_csv(blind_path)
    snapshot_by_code = {str(row.get("code", "")).zfill(6): row for row in snapshot_rows if row.get("code")}

    start = len(news_rows) * args.shard // args.shard_count
    end = len(news_rows) * (args.shard + 1) // args.shard_count
    assigned: list[dict[str, Any]] = []
    for zero_index, row in enumerate(news_rows[start:end], start=start):
        index = zero_index + 1
        full_text = f"{row.get('title', '')}\n{row.get('body', '')}"
        assigned.append(
            {
                "source_id": f"SRC-NEWS-{index:06d}",
                "global_row_index": index,
                "published_at_kst": f"{row.get('date')}T{row.get('time')}+09:00",
                "title": row.get("title", ""),
                "body": row.get("body", ""),
                "krx_candidate_options": common.make_krx_options(full_text, snapshot_rows, snapshot_by_code),
            }
        )

    # Keep every complete title/body in the model input while constraining response size.
    batches = common.row_batches(assigned, max_items=4, max_chars=24000)
    token = os.environ.get("GITHUB_TOKEN", "") or os.environ.get("GITHUB_MODELS_TOKEN", "")
    output_rows: list[dict[str, Any]] = []
    call_log: list[dict[str, Any]] = []

    def process(batch: list[dict[str, Any]], label: str) -> list[dict[str, Any]]:
        started = time.monotonic()
        try:
            parsed, reviewer = call_model(
                blind.detailed_review_system(),
                blind.detailed_review_user(batch),
                token,
                20180619 + args.shard * 100 + len(output_rows),
            )
            records = parsed.get("records") if isinstance(parsed, dict) else parsed
            if not isinstance(records, list):
                raise ValueError("records array missing")
            expected = {row["source_id"] for row in batch}
            actual = {str(row.get("source_id")) for row in records if isinstance(row, dict)}
            if expected != actual or len(records) != len(batch):
                raise ValueError(f"coverage mismatch expected={sorted(expected)} actual={sorted(actual)}")
            raw_by_id = {str(row["source_id"]): row for row in records}
            normalized: list[dict[str, Any]] = []
            for input_row in batch:
                item = blind.normalize_review(raw_by_id[input_row["source_id"]], input_row, snapshot_by_code)
                item["semantic_reviewer"] = reviewer
                item["semantic_review_protocol"] = "FULL_TITLE_BODY_E1_E2_E3_ADJUDICATION"
                item["global_row_index"] = input_row["global_row_index"]
                normalized.append(item)
            call_log.append(
                {
                    "label": label,
                    "status": "ok",
                    "reviewer": reviewer,
                    "source_ids": sorted(expected),
                    "elapsed_seconds": round(time.monotonic() - started, 3),
                }
            )
            return normalized
        except Exception as exc:  # noqa: BLE001
            call_log.append(
                {
                    "label": label,
                    "status": "split_or_fail",
                    "error": f"{type(exc).__name__}: {exc}",
                    "source_ids": [row["source_id"] for row in batch],
                    "elapsed_seconds": round(time.monotonic() - started, 3),
                }
            )
            if len(batch) > 1:
                midpoint = len(batch) // 2
                return process(batch[:midpoint], label + "A") + process(batch[midpoint:], label + "B")
            raise

    for batch_index, batch in enumerate(batches, start=1):
        output_rows.extend(process(batch, f"S{args.shard:03d}-B{batch_index:03d}"))

    output_rows.sort(key=lambda row: int(row.get("global_row_index", 0)))
    expected_ids = [f"SRC-NEWS-{index:06d}" for index in range(start + 1, end + 1)]
    actual_ids = [str(row.get("source_id")) for row in output_rows]
    if actual_ids != expected_ids:
        raise RuntimeError(f"shard output order/coverage mismatch expected={expected_ids} actual={actual_ids}")
    if any(row.get("full_title_body_reviewed") is not True for row in output_rows):
        raise RuntimeError("full_title_body_reviewed flag missing")
    if any(not row.get("review_decision") for row in output_rows):
        raise RuntimeError("review_decision missing")
    if any(not row.get("exact_quote") for row in output_rows):
        raise RuntimeError("exact_quote missing")

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    payload = "\n".join(canonical_json(row) for row in output_rows)
    output.write_text(payload + ("\n" if payload else ""), encoding="utf-8")
    receipt = {
        "schema_version": "nslab.semantic_review_shard_receipt.v1",
        "shard": args.shard,
        "shard_count": args.shard_count,
        "source_start_index": start + 1 if end > start else None,
        "source_end_index": end if end > start else None,
        "row_count": len(output_rows),
        "source_ids_sha256": hashlib.sha256("\n".join(actual_ids).encode("utf-8")).hexdigest(),
        "full_title_body_reviewed_count": sum(1 for row in output_rows if row.get("full_title_body_reviewed") is True),
        "status": "SHARD_POPULATION_CLOSED",
        "calls": call_log,
    }
    output.with_suffix(".receipt.json").write_text(json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({k: receipt[k] for k in ("shard", "row_count", "status")}, sort_keys=True), flush=True)


if __name__ == "__main__":
    main()
