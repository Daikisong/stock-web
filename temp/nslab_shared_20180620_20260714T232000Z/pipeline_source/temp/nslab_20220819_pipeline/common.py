from __future__ import annotations

import csv
import hashlib
import json
import os
import re
import time
import urllib.error
import urllib.request
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Iterable, Sequence

TRADE_DATE = "2022-08-19"
PREVIOUS_TRADE_DATE = "2022-08-18"
NEXT_TRADE_DATE = "2022-08-22"
CUTOFF_AT = "2022-08-19T08:59:59+09:00"
WINDOW_START = "2022-08-18T15:30:00+09:00"
AVAILABLE_FROM = "2022-08-22T00:00:00+09:00"
INPUT_SHA256 = "ec55b86339923c35db8c7b31e01f1706213afa3ffdb535aac243f2fd56a454fb"
INPUT_BYTE_SIZE = 3039039
INPUT_ROW_COUNT = 1113
PROMPT_SHA256 = "b5ba21ce1f6e3a91dacf19e33e16d5db9dface141e90a67e78c8588ba1553029"
PROMPT_BYTE_SIZE = 430485
BLIND_SNAPSHOT_SHA256 = "adf92948b0062a49e7befedf2623686c99411c135a4999f6a388fdd80d22a77d"
BLIND_SNAPSHOT_ROWS = 2430
OUTCOME_SNAPSHOT_SHA256 = "68cd269f827703534198b1ad0dd1be5b7b28d3dd35ec7b4f49ba81df914cde32"
OUTCOME_SNAPSHOT_BYTES = 817772
OUTCOME_SNAPSHOT_ROWS = 2432
MODEL_NAME = "openai/gpt-4.1-mini"
KST = timezone(timedelta(hours=9))

ALLOWED_DISPOSITIONS = {
    "DIRECT_ISSUER_MATERIAL",
    "DIRECT_ISSUER_SECONDARY",
    "THEME_POLICY_INDUSTRY_EVENT",
    "MARKET_STATE_REGIME",
    "D1_CONTINUATION_SIGNAL",
    "DISCLOSURE_OR_MARKET_NOTICE",
    "BODY_TABLE_OR_LIST_AUDIT",
    "DUPLICATE",
    "LOW_SIGNAL_CONTEXT",
    "NON_MARKET_NEWS",
    "NON_KR_OR_NON_LISTED_CONTEXT",
    "TIME_UNVERIFIED_RETAINED",
    "PARSER_AMBIGUOUS_REVIEWED",
}
MATERIAL_DISPOSITIONS = {
    "DIRECT_ISSUER_MATERIAL",
    "DIRECT_ISSUER_SECONDARY",
    "THEME_POLICY_INDUSTRY_EVENT",
    "MARKET_STATE_REGIME",
    "D1_CONTINUATION_SIGNAL",
    "DISCLOSURE_OR_MARKET_NOTICE",
    "BODY_TABLE_OR_LIST_AUDIT",
    "PARSER_AMBIGUOUS_REVIEWED",
}
SCREENING_DECISIONS = {
    "INCLUDE",
    "WATCH_SECONDARY",
    "EXCLUDE",
    "AUDIT_ONLY",
    "REJECT_SEMANTIC_FALSE_POSITIVE",
}
FINAL_ALLOWED_QUOTE_ROLES = {
    "ISSUER_CONTRACT_ACTION",
    "ISSUER_ORDER_OR_SUPPLY_ACTION",
    "ISSUER_PROJECT_AWARDED_ACTION",
    "ISSUER_PRODUCT_RELEASE_ACTION",
    "ISSUER_SERVICE_RELEASE_ACTION",
    "ISSUER_COMMERCIALIZATION_ACTION",
    "ISSUER_REGULATORY_APPROVAL_ACTION",
    "ISSUER_CLINICAL_OR_PIPELINE_STAGE_ACTION",
    "ISSUER_GOVERNMENT_PROJECT_SELECTION_ACTION",
    "ISSUER_LICENSE_OR_TECH_TRANSFER_ACTION",
    "ISSUER_CAPITAL_POLICY_ACTION",
    "ISSUER_STRATEGIC_INVESTMENT_OR_CONTROL_ACTION",
    "ISSUER_ANALYST_NUMERIC_BRIDGE",
    "ISSUER_EXPLICIT_MARKET_STATE_NOTICE",
}
FINAL_FORBIDDEN_QUOTE_ROLES = {
    "COMMON_NOUN_ONLY",
    "POLICY_ACRONYM_ONLY",
    "PLACE_OR_NATURE_PHENOMENON_ONLY",
    "PRODUCT_ADJECTIVE_OR_BRAND_ONLY",
    "MANUFACTURER_ONLY",
    "ATTENDEE_LIST_ONLY",
    "INVESTOR_HOLDING_ONLY",
    "MARKET_FLOW_TABLE_MEMBER_ONLY",
    "THEME_LIST_MEMBER_ONLY",
    "IR_CALENDAR_ONLY",
    "PRESENTATION_OR_SEMINAR_ONLY",
    "CSR_OR_ROUTINE_ONLY",
    "TECHNICAL_SIGNAL_ONLY",
    "GENERAL_MARKET_COMMENTARY_ONLY",
    "THIRD_PARTY_RETAIL_DISCOUNT_ONLY",
    "INDEX_COMPONENT_ONLY",
    "AFFILIATE_OR_GROUP_MENTION_UNRESOLVED",
    "OTHER_COMPANY_ARTICLE",
    "PREFIX_OR_SUBSTRING_ONLY",
    "REPORT_OR_PRESENTATION_SPEAKER_ONLY",
    "BODY_TABLE_LIST_MEMBER",
    "FOREIGN_INVESTOR_OR_INSTITUTION_NET_BUY_TABLE_MEMBER",
    "NON_CANDIDATE_CONTEXT",
}
CATALYST_FACT_MATRIX = {
    "CONTRACT_ORDER": {"CONTRACT_SIGNED", "ORDER_RECEIVED", "SUPPLY_AGREEMENT", "PROJECT_AWARDED"},
    "PRODUCT_COMMERCIALIZATION": {"PRODUCT_LAUNCHED_BY_ISSUER", "PRODUCT_COMMERCIALIZATION_BY_ISSUER", "SERVICE_RELEASE_BY_ISSUER"},
    "BIO_STAGE_ADVANCE": {"REGULATORY_APPROVAL", "CLINICAL_STAGE_ADVANCE", "LICENSE_OR_TECH_TRANSFER_WITH_RIGHTS", "GOVERNMENT_PROJECT_SELECTED"},
    "CAPITAL_POLICY": {"DIVIDEND", "BUYBACK", "SHARE_CANCELLATION", "RIGHTS_ISSUE", "THIRD_PARTY_ALLOCATION", "MERGER_OR_SPINOFF", "STAKE_SALE_OR_CONTROL_CHANGE"},
    "STRATEGIC_INVESTMENT": {"THIRD_PARTY_ALLOCATION", "STAKE_SALE_OR_CONTROL_CHANGE", "MERGER_OR_SPINOFF"},
    "ANALYST_BRIDGE": {"ANALYST_NUMERIC_EARNINGS_BRIDGE"},
    "CONTINUATION_EXPLICIT": {"EXPLICIT_MARKET_STATE_NOTICE"},
}
CANONICAL_RECORD_TYPES = {
    "supervised_issuer_day_case",
    "supervised_direct_event_case",
    "supervised_theme_formation_case",
    "beneficiary_discovery_case",
    "theme_formation_case",
    "blind_leader_preference_pair",
    "candidate_generation_error_case",
    "candidate_ranking_error_case",
    "ranking_error_case",
    "row_disposition_error_case",
    "entity_resolution_error_case",
    "context_market_state_or_fact_case",
    "counterexample",
    "negative_control_case",
    "newsless_or_unexplained_case",
    "event_ticker_edge",
    "company_memory_delta",
    "memory_claim",
    "mechanism_memory",
    "research_question",
}

REQUIRED_BLOCKS = [
    "research_report.md", "blind_report.md", "postmortem_report.md", "phase_state.json",
    "access_log.jsonl", "acquisition_warnings.jsonl", "attempt_history.jsonl", "repair_log.jsonl",
    "source_ledger.jsonl", "row_disposition.jsonl", "material_review_queue.jsonl", "material_review.jsonl",
    "provisional_hypothesis.jsonl", "entity_resolution.jsonl", "entity_ledger_blind.jsonl",
    "fact_ledger_blind.jsonl", "inference_ledger_blind.jsonl", "candidate_screening.jsonl",
    "candidate_ranking_audit.jsonl", "candidate_semantic_witness.jsonl", "blind_prediction.json",
    "final_evidence_witness.jsonl", "final_semantic_audit.jsonl", "market_state_override_audit.jsonl",
    "body_table_candidate_generation_audit.jsonl", "ledger_population_audit.json", "blind_seal_receipt.json",
    "blind_packet_manifest.json", "outcome_ledger.jsonl", "outcome_leader_census.jsonl",
    "outcome_to_news_audit.jsonl", "postmortem_summary.json", "brain_delta.jsonl",
    "record_provenance_closure_audit.jsonl", "id_registry.jsonl", "canonical_graph.json",
    "research_episode.json", "validation_report.json", "phase_audit_report.json",
    "direct_ingest_contract.json", "bundle_manifest.json", "anti_reward_hack_audit.json",
    "semantic_regression_tests.jsonl", "semantic_regression_test_report.json",
]
JSON_BLOCKS = {name for name in REQUIRED_BLOCKS if name.endswith(".json")}
JSONL_BLOCKS = {name for name in REQUIRED_BLOCKS if name.endswith(".jsonl")}
TEXT_BLOCKS = {name for name in REQUIRED_BLOCKS if name.endswith(".md")}

_LAST_MODEL_CALL_AT = 0.0


def now_kst() -> str:
    return datetime.now(KST).isoformat(timespec="seconds")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_text(text: str) -> str:
    return sha256_bytes(text.encode("utf-8"))


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def json_payload(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)


def jsonl_payload(rows: Sequence[dict[str, Any]]) -> str:
    return "\n".join(canonical_json(row) for row in rows)


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json_payload(value) + "\n", encoding="utf-8")


def write_jsonl(path: Path, rows: Sequence[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = jsonl_payload(rows)
    path.write_text(payload + ("\n" if payload else ""), encoding="utf-8")


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def strip_code_fence(text: str) -> str:
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s*```$", "", text)
    return text.strip()


def parse_model_json(text: str) -> Any:
    cleaned = strip_code_fence(text)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        starts = [idx for idx in (cleaned.find("{"), cleaned.find("[")) if idx >= 0]
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


def model_json(
    token: str,
    *,
    system: str,
    user: str,
    label: str,
    log_path: Path,
    max_tokens: int = 12000,
    attempts: int = 8,
) -> Any:
    global _LAST_MODEL_CALL_AT
    endpoint = "https://models.github.ai/inference/chat/completions"
    error_messages: list[str] = []
    for attempt in range(1, attempts + 1):
        delay = max(0.0, 1.15 - (time.monotonic() - _LAST_MODEL_CALL_AT))
        if delay:
            time.sleep(delay)
        request_body = {
            "model": MODEL_NAME,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": 0,
            "max_tokens": max_tokens,
        }
        req = urllib.request.Request(
            endpoint,
            data=json.dumps(request_body, ensure_ascii=False).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
        started = time.monotonic()
        status = "error"
        response_model = None
        output_chars = 0
        try:
            with urllib.request.urlopen(req, timeout=300) as response:
                raw = response.read().decode("utf-8")
            _LAST_MODEL_CALL_AT = time.monotonic()
            data = json.loads(raw)
            response_model = data.get("model")
            content = str(data["choices"][0]["message"]["content"])
            output_chars = len(content)
            parsed = parse_model_json(content)
            status = "ok"
            return parsed
        except urllib.error.HTTPError as exc:
            try:
                detail = exc.read().decode("utf-8", errors="replace")[:1000]
            except Exception:
                detail = ""
            error_messages.append(f"HTTP {exc.code}: {detail}")
            retry_after = exc.headers.get("Retry-After") if exc.headers else None
            wait = float(retry_after) if retry_after and retry_after.isdigit() else min(90.0, 5.0 * (2 ** (attempt - 1)))
            time.sleep(wait)
        except Exception as exc:  # noqa: BLE001
            error_messages.append(f"{type(exc).__name__}: {exc}")
            time.sleep(min(45.0, 3.0 * attempt))
        finally:
            log_path.parent.mkdir(parents=True, exist_ok=True)
            with log_path.open("a", encoding="utf-8") as log:
                log.write(canonical_json({
                    "label": label,
                    "attempt": attempt,
                    "status": status,
                    "response_model": response_model,
                    "input_chars": len(system) + len(user),
                    "output_chars": output_chars,
                    "elapsed_seconds": round(time.monotonic() - started, 3),
                    "ts": now_kst(),
                }) + "\n")
    raise RuntimeError(f"model_json failed for {label}: {' | '.join(error_messages[-5:])}")


def enum_value(value: Any, allowed: set[str], default: str) -> str:
    candidate = str(value or "").strip().upper()
    return candidate if candidate in allowed else default


def string_or_none(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def bool_value(value: Any) -> bool:
    return value is True or str(value).strip().lower() == "true"


def float_or_none(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def int_or_none(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def normalize_name(value: str | None) -> str:
    if not value:
        return ""
    text = re.sub(r"\s+", "", value)
    text = re.sub(r"[㈜()（）·\-_/.,'\"‘’“”]", "", text)
    for suffix in ("주식회사", "유한회사", "홀딩스", "그룹"):
        if text.endswith(suffix) and len(text) > len(suffix) + 1:
            text = text[: -len(suffix)]
    return text.casefold()


def sentence_candidates(title: str, body: str) -> list[str]:
    candidates = [title.strip()]
    for segment in re.split(r"(?<=[.!?。！？])\s+|[\r\n]+", body):
        cleaned = segment.strip()
        if cleaned:
            candidates.append(cleaned)
    return candidates


def exact_quote_from_source(title: str, body: str, proposed: str | None) -> tuple[str, bool, str | None]:
    source = f"{title}\n{body}"
    if proposed and proposed in source:
        return proposed, True, None
    proposed_norm = re.sub(r"\s+", " ", proposed or "").strip()
    best = title.strip()
    best_score = -1.0
    for sentence in sentence_candidates(title, body):
        score = SequenceMatcher(None, proposed_norm, re.sub(r"\s+", " ", sentence)).ratio() if proposed_norm else 0.0
        if score > best_score:
            best = sentence
            best_score = score
    if not best:
        best = source[:240]
    if len(best) > 320:
        best = best[:320]
    return best, best in source, "MODEL_QUOTE_REPAIRED_TO_EXACT_SOURCE_SUBSTRING"


def row_batches(rows: Sequence[dict[str, Any]], *, max_items: int = 18, max_chars: int = 78000) -> list[list[dict[str, Any]]]:
    batches: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    chars = 0
    for row in rows:
        row_chars = len(str(row.get("title", ""))) + len(str(row.get("body", ""))) + 1200
        if current and (len(current) >= max_items or chars + row_chars > max_chars):
            batches.append(current)
            current = []
            chars = 0
        current.append(row)
        chars += row_chars
    if current:
        batches.append(current)
    return batches


def make_krx_options(text: str, snapshot_rows: Sequence[dict[str, str]], code_map: dict[str, dict[str, str]]) -> list[dict[str, str]]:
    options: dict[str, dict[str, str]] = {}
    for code in re.findall(r"(?<!\d)(\d{6})(?!\d)", text):
        if code in code_map:
            row = code_map[code]
            options[code] = {"ticker": code, "company": row.get("name", ""), "match_basis": "EXACT_SIX_DIGIT_CODE"}
    title = text.split("\n", 1)[0]
    for row in snapshot_rows:
        name = row.get("name", "").strip()
        code = row.get("code", "").zfill(6)
        if not name or not code or code in options:
            continue
        found = name in title or (len(normalize_name(name)) >= 3 and name in text)
        if found:
            options[code] = {"ticker": code, "company": name, "match_basis": "EXACT_NAME_OCCURRENCE_FOR_MODEL_ADJUDICATION"}
            if len(options) >= 16:
                break
    return list(options.values())


def final_semantic_eligible(review: dict[str, Any]) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    ticker = string_or_none(review.get("ticker"))
    company = string_or_none(review.get("candidate_company"))
    quote_role = str(review.get("quote_role") or "")
    fact_class = str(review.get("material_fact_class") or "")
    catalyst = str(review.get("catalyst_type") or "")
    subject = normalize_name(string_or_none(review.get("article_subject_company")))
    owner = normalize_name(string_or_none(review.get("local_predicate_owner")))
    target = normalize_name(company)
    if not ticker or not company:
        reasons.append("UNRESOLVED_TRADABLE_ISSUER")
    if quote_role not in FINAL_ALLOWED_QUOTE_ROLES:
        reasons.append("QUOTE_ROLE_NOT_FINAL_ALLOWED")
    allowed_facts = CATALYST_FACT_MATRIX.get(catalyst, set())
    if fact_class not in allowed_facts:
        reasons.append("CATALYST_FACT_MATRIX_MISMATCH")
    if not bool_value(review.get("quote_found_in_source_row")):
        reasons.append("QUOTE_NOT_FOUND")
    if not bool_value(review.get("mechanism_supported")):
        reasons.append("MECHANISM_UNSUPPORTED")
    relation_ok = bool(target and (target == subject or target == owner))
    if not relation_ok:
        reasons.append("TARGET_NOT_ARTICLE_SUBJECT_OR_LOCAL_PREDICATE_OWNER")
    return not reasons, reasons


def render_markdown(front: dict[str, Any], blocks: dict[str, str], order: Sequence[str] | None = None) -> str:
    lines = ["---"]
    for key, value in front.items():
        if isinstance(value, bool):
            rendered = "true" if value else "false"
        elif value is None:
            rendered = "null"
        elif isinstance(value, (int, float)):
            rendered = str(value)
        else:
            rendered = json.dumps(str(value), ensure_ascii=False)
        lines.append(f"{key}: {rendered}")
    lines.extend(["---", "", "# NSLAB Research Episode Bundle", ""])
    for name in (order or list(blocks)):
        payload = blocks.get(name, "")
        lines.append(f"<!-- NSLAB:BEGIN {name} -->")
        lines.append(payload.rstrip("\n"))
        lines.append(f"<!-- NSLAB:END {name} -->")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


BEGIN_RE = re.compile(r"<!-- NSLAB:BEGIN ([^>]+) -->")


def parse_markdown_blocks(text: str) -> tuple[dict[str, str], dict[str, int]]:
    blocks: dict[str, str] = {}
    counts: Counter[str] = Counter()
    position = 0
    while True:
        match = BEGIN_RE.search(text, position)
        if not match:
            break
        name = match.group(1).strip()
        end_marker = f"<!-- NSLAB:END {name} -->"
        end = text.find(end_marker, match.end())
        if end < 0:
            raise ValueError(f"missing end marker for {name}")
        counts[name] += 1
        if counts[name] == 1:
            blocks[name] = text[match.end():end].strip("\n")
        position = end + len(end_marker)
    return blocks, dict(counts)


def parse_block(name: str, payload: str) -> Any:
    if name in JSON_BLOCKS:
        return json.loads(payload or "{}")
    if name in JSONL_BLOCKS:
        return [json.loads(line) for line in payload.splitlines() if line.strip()]
    return payload


def check_record(check_id: str, actual: Any, expected: Any, *, severity: str = "critical", actual_source: str = "FINAL_MARKDOWN_REPARSE", expected_source: str = "MAIN_EXECUTION_PROMPT_CONTRACT") -> dict[str, Any]:
    passed = actual == expected if not isinstance(expected, dict) else actual == expected
    return {
        "check_id": check_id,
        "actual": actual,
        "expected": expected,
        "passed": passed,
        "severity": severity,
        "actual_source": actual_source,
        "expected_source": expected_source,
        "error_ids": [] if passed else [f"ERR-{check_id}"],
    }


def semantic_regression_rows() -> list[dict[str, Any]]:
    fixtures = [
        ("SEM-001", "오로라", "039830", "캐나다관광청 \"올겨울은 오로라 관측 최적기\"", "PLACE_OR_NATURE_PHENOMENON_ONLY", "CONTRACT_SIGNED", "CONTRACT_ORDER", "FAIL", "PLACE_OR_NATURE_PHENOMENON_ONLY"),
        ("SEM-002", "DSR", "155660", "2단계 스트레스 총부채원리금상환비율(DSR) 시행", "POLICY_ACRONYM_ONLY", "PRODUCT_LAUNCHED_BY_ISSUER", "PRODUCT_COMMERCIALIZATION", "FAIL", "POLICY_ACRONYM_ONLY"),
        ("SEM-003", "NEW", "160550", "ALL NEW 새우초밥을 할인 판매한다", "PRODUCT_ADJECTIVE_OR_BRAND_ONLY", "PRODUCT_LAUNCHED_BY_ISSUER", "PRODUCT_COMMERCIALIZATION", "FAIL", "PRODUCT_ADJECTIVE_OR_BRAND_ONLY"),
        ("SEM-004", "코스맥스", "192820", "제품의 제조사는 코스맥스이다", "MANUFACTURER_ONLY", "REGULATORY_APPROVAL", "BIO_STAGE_ADVANCE", "FAIL", "MANUFACTURER_ONLY"),
        ("SEM-005", "삼성바이오로직스", "207940", "출범식에는 삼성바이오로직스, 셀트리온, 롯데바이오로직스 등 바이오기업 관계자 20여 명이 참석했다", "ATTENDEE_LIST_ONLY", "CLINICAL_STAGE_ADVANCE", "BIO_STAGE_ADVANCE", "FAIL", "ATTENDEE_LIST_ONLY"),
        ("SEM-006", "알테오젠", "196170", "그는 알테오젠에 대규모 투자를 한 슈퍼 개미로 유명하다", "INVESTOR_HOLDING_ONLY", "CONTRACT_SIGNED", "CONTRACT_ORDER", "FAIL", "INVESTOR_HOLDING_ONLY"),
        ("SEM-007", "SK", "034730", "삼성 갔던 하이닉스 직원들 나 돌아갈래…만년 2등 꼬리표 뗀 SK하이닉스", "PREFIX_OR_SUBSTRING_ONLY", "PRODUCT_LAUNCHED_BY_ISSUER", "PRODUCT_COMMERCIALIZATION", "FAIL", "PREFIX_OR_SUBSTRING_ONLY"),
        ("SEM-008", "YG PLUS", "037270", "[주상전화] 그리드위즈 (453450)", "OTHER_COMPANY_ARTICLE", "PRODUCT_LAUNCHED_BY_ISSUER", "PRODUCT_COMMERCIALIZATION", "FAIL", "OTHER_COMPANY_ARTICLE"),
        ("SEM-009", "네이처셀", "007390", "25일, 코스닥 외국인 순매수상위에 제약 업종 8종목", "MARKET_FLOW_TABLE_MEMBER_ONLY", "PRODUCT_LAUNCHED_BY_ISSUER", "PRODUCT_COMMERCIALIZATION", "FAIL", "MARKET_FLOW_TABLE_MEMBER_ONLY"),
        ("SEM-010", "현대로템", "064350", "현대로템 어성필 체계공학실장은 한국의 육상 기동화력 개발 현황과 산학 협력 연구 및 전문 인력 양성 방안에 대해 발표했다", "PRESENTATION_OR_SEMINAR_ONLY", "CONTRACT_SIGNED", "CONTRACT_ORDER", "FAIL", "PRESENTATION_OR_SEMINAR_ONLY"),
        ("SEM-011", "셀루메드", "049180", "셀루메드, 혁신적 주사제형 피부이식재 셀루덤 젠 개발 완료", "ISSUER_PRODUCT_RELEASE_ACTION", "PRODUCT_LAUNCHED_BY_ISSUER", "PRODUCT_COMMERCIALIZATION", "PASS", ""),
        ("SEM-012", "퀀타매트릭스", "317690", "퀀타매트릭스, 최대주주 에즈라 제3자 배정 유상증자 참여", "ISSUER_STRATEGIC_INVESTMENT_OR_CONTROL_ACTION", "THIRD_PARTY_ALLOCATION", "STRATEGIC_INVESTMENT", "PASS", ""),
        ("SEM-013", "피노", "033790", "피노, 29.5억 규모 RF중계기 공급계약 체결", "ISSUER_ORDER_OR_SUPPLY_ACTION", "SUPPLY_AGREEMENT", "CONTRACT_ORDER", "PASS", ""),
    ]
    rows: list[dict[str, Any]] = []
    for fixture_id, company, ticker, quote, role, fact, catalyst, expected, reason in fixtures:
        actual = "PASS" if role in FINAL_ALLOWED_QUOTE_ROLES and fact in CATALYST_FACT_MATRIX.get(catalyst, set()) else "FAIL"
        actual_reason = "" if actual == "PASS" else role
        rows.append({
            "fixture_id": fixture_id,
            "candidate_company": company,
            "candidate_ticker": ticker,
            "quote": quote,
            "proposed_quote_role": role,
            "proposed_material_fact_class": fact,
            "proposed_catalyst_type": catalyst,
            "expected_verdict": expected,
            "expected_fail_reason": reason,
            "actual_verdict": actual,
            "actual_fail_reason": actual_reason,
            "passed": actual == expected and (expected == "PASS" or bool(actual_reason)),
        })
    return rows


def markdown_table(headers: Sequence[str], rows: Sequence[Sequence[Any]], limit: int | None = None) -> str:
    shown = list(rows[:limit] if limit is not None else rows)
    if not shown:
        return "(해당 모집단 없음; 대응 audit block 참조)"
    def esc(value: Any) -> str:
        return str(value if value is not None else "").replace("|", "\\|").replace("\n", " ")
    lines = ["| " + " | ".join(esc(h) for h in headers) + " |", "|" + "|".join("---" for _ in headers) + "|"]
    lines.extend("| " + " | ".join(esc(cell) for cell in row) + " |" for row in shown)
    return "\n".join(lines)


def register_ids(block_rows: dict[str, Sequence[dict[str, Any]]]) -> list[dict[str, Any]]:
    id_fields = {
        "source_id", "row_disposition_id", "material_review_queue_id", "material_review_id",
        "hypothesis_id", "entity_resolution_id", "entity_id", "fact_id", "inference_id",
        "observation_id", "screening_id", "candidate_id", "ranking_audit_id", "witness_id",
        "audit_id", "outcome_id", "outcome_leader_id", "record_id", "closure_audit_id",
        "pair_id", "case_id", "question_id",
    }
    registry: list[dict[str, Any]] = []
    seen: set[str] = set()
    for block, rows in block_rows.items():
        for row_index, row in enumerate(rows, start=1):
            for field in id_fields:
                value = row.get(field)
                if not isinstance(value, str) or not value or value in seen:
                    continue
                seen.add(value)
                registry.append({
                    "registry_id": f"IDREG-{len(registry)+1:06d}",
                    "object_id": value,
                    "id_field": field,
                    "artifact": block,
                    "row_index": row_index,
                })
    return registry


def outcome_strength(row: dict[str, Any] | None) -> str:
    if not row:
        return "NO_TRADABLE_OUTCOME"
    high = float_or_none(row.get("high_return_pct")) or 0.0
    close = float_or_none(row.get("close_return_pct")) or 0.0
    if bool_value(row.get("upper_limit_closed")):
        return "UPPER_LIMIT_CLOSED"
    if bool_value(row.get("upper_limit_touched")):
        return "UPPER_LIMIT_TOUCHED"
    if high >= 20:
        return "HIGH20"
    if high >= 15:
        return "HIGH15"
    if high >= 10:
        return "HIGH10"
    if close >= 5:
        return "POSITIVE5"
    if close <= -5:
        return "NEGATIVE5"
    return "MUTED"


def ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
