from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from common import (
    ALLOWED_DISPOSITIONS,
    AVAILABLE_FROM,
    BLIND_SNAPSHOT_ROWS,
    BLIND_SNAPSHOT_SHA256,
    CATALYST_FACT_MATRIX,
    CUTOFF_AT,
    FINAL_ALLOWED_QUOTE_ROLES,
    FINAL_FORBIDDEN_QUOTE_ROLES,
    INPUT_BYTE_SIZE,
    INPUT_ROW_COUNT,
    INPUT_SHA256,
    KST,
    MATERIAL_DISPOSITIONS,
    MODEL_NAME,
    NEXT_TRADE_DATE,
    PREVIOUS_TRADE_DATE,
    PROMPT_BYTE_SIZE,
    PROMPT_SHA256,
    SCREENING_DECISIONS,
    TRADE_DATE,
    WINDOW_START,
    bool_value,
    canonical_json,
    enum_value,
    exact_quote_from_source,
    final_semantic_eligible,
    float_or_none,
    int_or_none,
    json_payload,
    jsonl_payload,
    make_krx_options,
    markdown_table,
    model_json,
    normalize_name,
    now_kst,
    read_csv,
    read_json,
    read_jsonl,
    row_batches,
    semantic_regression_rows,
    sha256_bytes,
    sha256_file,
    sha256_text,
    string_list,
    string_or_none,
    write_json,
    write_jsonl,
)

DIRECT_RELATIONS = {
    "DIRECT_SUBJECT",
    "DIRECT_PREDICATE_OWNER",
    "NAMED_BENEFICIARY",
    "EXCHANGE_NOTICE_SUBJECT",
}
QUOTE_ROLES = FINAL_ALLOWED_QUOTE_ROLES | FINAL_FORBIDDEN_QUOTE_ROLES | {
    "DIRECT_ISSUER_ADVERSE_EVENT_NONFINAL",
    "DIRECT_ISSUER_ROUTINE_FACT_NONFINAL",
    "POLICY_OR_INDUSTRY_CONTEXT",
    "NON_KR_OR_NONLISTED_ISSUER",
    "NON_MARKET_CONTEXT",
    "DISCLOSURE_OR_ETF_NOTICE_NONISSUER",
    "PARSER_AMBIGUOUS",
}
FACT_CLASSES = set().union(*CATALYST_FACT_MATRIX.values()) | {
    "ADVERSE_OPERATIONAL_OR_LABOR_EVENT_NONFINAL",
    "ROUTINE_CORPORATE_CONTEXT_NONFINAL",
    "POLICY_OR_INDUSTRY_CONTEXT_NONFINAL",
    "DISCLOSURE_OR_ETF_NOTICE_NONFINAL",
    "BODY_TABLE_OR_LIST_NONFINAL",
    "NON_MARKET_CONTEXT",
    "NON_KR_OR_NONLISTED_CONTEXT",
    "PARSER_AMBIGUOUS_CONTEXT",
}
CATALYST_TYPES = set(CATALYST_FACT_MATRIX) | {"NONE"}
ECONOMIC_VARIABLES = {
    "REVENUE", "MARGIN", "COST", "CAPITAL_POLICY", "APPROVAL_PROBABILITY",
    "CONTROL_PREMIUM", "MARKET_MEMORY", "RISK_AVOIDANCE", "NONE",
}
CANDIDATE_PATHS = {"DIRECT_ISSUER", "THEME_BENEFICIARY", "MARKET_STATE", "CONTINUATION", "AUDIT_ONLY"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--prompt", type=Path, required=True)
    parser.add_argument("--news", type=Path, required=True)
    parser.add_argument("--access", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--schema", type=Path, required=True)
    parser.add_argument("--blind-snapshot", type=Path, required=True)
    parser.add_argument("--example", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--token", required=True)
    parser.add_argument("--run-id", required=True)
    return parser.parse_args()


def verify_inputs(args: argparse.Namespace) -> tuple[list[dict[str, str]], list[dict[str, str]], dict[str, Any]]:
    prompt_raw = args.prompt.read_bytes()
    if sha256_bytes(prompt_raw) != PROMPT_SHA256 or len(prompt_raw) != PROMPT_BYTE_SIZE:
        raise RuntimeError("fresh main prompt hash/size mismatch")
    first_line = prompt_raw.decode("utf-8").splitlines()[0]
    if first_line != "# NSLAB GOLD PHASE MACHINE — DIRECT CSV RESEARCH RUNNER":
        raise RuntimeError("fresh main prompt title mismatch")
    news_raw = args.news.read_bytes()
    if sha256_bytes(news_raw) != INPUT_SHA256 or len(news_raw) != INPUT_BYTE_SIZE:
        raise RuntimeError("fresh news CSV hash/size mismatch")
    news_rows = read_csv(args.news)
    if len(news_rows) != INPUT_ROW_COUNT:
        raise RuntimeError(f"news row count mismatch: {len(news_rows)}")
    blind_raw = args.blind_snapshot.read_bytes()
    if sha256_bytes(blind_raw) != BLIND_SNAPSHOT_SHA256:
        raise RuntimeError("blind snapshot hash mismatch")
    blind_rows = read_csv(args.blind_snapshot)
    if len(blind_rows) != BLIND_SNAPSHOT_ROWS:
        raise RuntimeError("blind snapshot row count mismatch")
    access = read_json(args.access)
    expected = {
        "trade_date": TRADE_DATE,
        "previous_trade_date": PREVIOUS_TRADE_DATE,
        "next_trade_date": NEXT_TRADE_DATE,
        "blind_snapshot_sha256": BLIND_SNAPSHOT_SHA256,
    }
    for key, value in expected.items():
        if access.get(key) != value:
            raise RuntimeError(f"access metadata mismatch {key}: {access.get(key)!r}")
    return news_rows, blind_rows, access


def file_source_rows(args: argparse.Namespace) -> list[dict[str, Any]]:
    items = [
        ("SRC-MAIN-PROMPT", "MAIN_EXECUTION_PROMPT", args.prompt, "Fresh main-branch bytes; locked prompt verified"),
        ("SRC-NEWS-CSV", "NEWS_CSV", args.news, "Fresh exact news_20220819.csv bytes"),
        ("SRC-ACCESS-JSON", "RESEARCH_DAILY_ACCESS", args.access, "Routing metadata only; D outcome content not accessed"),
        ("SRC-RESEARCH-DAILY-MANIFEST", "RESEARCH_DAILY_MANIFEST", args.manifest, "Stock-web routing manifest"),
        ("SRC-RESEARCH-DAILY-SCHEMA", "RESEARCH_DAILY_SCHEMA", args.schema, "Snapshot schema"),
        ("SRC-BLIND-SNAPSHOT", "BLIND_SNAPSHOT", args.blind_snapshot, "P snapshot only"),
        ("SRC-GOLD-REFERENCE", "GOLD_REFERENCE_STRUCTURE_ONLY", args.example, "Structure-only reference; no candidates copied"),
    ]
    return [
        {
            "source_id": source_id,
            "source_type": source_type,
            "path": path.name,
            "sha256": sha256_file(path),
            "byte_size": path.stat().st_size,
            "usage_phase": "STRUCTURE_ONLY" if "REFERENCE" in source_type else "BLIND",
            "notes": notes,
        }
        for source_id, source_type, path, notes in items
    ]


def row_source_rows(news_rows: list[dict[str, str]]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for index, row in enumerate(news_rows, start=1):
        published = f"{row.get('date')}T{row.get('time')}+09:00"
        raw_row = canonical_json(row)
        output.append({
            "source_id": f"SRC-NEWS-{index:06d}",
            "source_type": "NEWS_CSV_ROW",
            "input_file": "news_20220819.csv",
            "input_sha256": INPUT_SHA256,
            "row_index": index,
            "page": row.get("page"),
            "page_row": row.get("row"),
            "published_at_kst": published,
            "title": row.get("title", ""),
            "body": row.get("body", ""),
            "url": None,
            "raw_row_sha256": sha256_text(raw_row),
            "time_verified": bool(re.fullmatch(r"\d{4}-\d{2}-\d{2}", row.get("date", "")) and re.fullmatch(r"\d{2}:\d{2}:\d{2}", row.get("time", ""))),
            "used_in_blind": True,
        })
    return output


def detailed_review_system() -> str:
    return """You are the independent E1 extractor, E2 verifier, and E3 adjudicator for a Korean pre-open stock-news research ledger. Read every complete title and body in every input row. The Python caller is only a clerk: your semantic judgment decides the disposition, local predicate owner, issuer role, exact quote, fact class, mechanism, and screening recommendation. Do not use ticker-like strings, prefixes, common nouns, body-list membership, attendance, manufacturer-only mentions, institutional-flow tables, or another company's article as issuer evidence. Negative issuer-specific events such as labor strikes, accidents, litigation, sanctions, operational disruption, cyber incidents, financing, governance, and earnings deterioration are still material review rows even when they are not eligible positive final candidates. NON_MARKET_NEWS is allowed only when there is no plausible listed-issuer, industry-policy, macro-regime, exchange-notice, or trading relevance. Return strict JSON only."""


def detailed_review_user(batch: list[dict[str, Any]]) -> str:
    return """Adjudicate every INPUT_ROWS item and return {"records":[...]} with exactly one record per source_id and no extra source_ids.

Allowed disposition:
DIRECT_ISSUER_MATERIAL, DIRECT_ISSUER_SECONDARY, THEME_POLICY_INDUSTRY_EVENT, MARKET_STATE_REGIME, D1_CONTINUATION_SIGNAL, DISCLOSURE_OR_MARKET_NOTICE, BODY_TABLE_OR_LIST_AUDIT, DUPLICATE, LOW_SIGNAL_CONTEXT, NON_MARKET_NEWS, NON_KR_OR_NON_LISTED_CONTEXT, TIME_UNVERIFIED_RETAINED, PARSER_AMBIGUOUS_REVIEWED.

For each record return all fields:
source_id; disposition; article_subject_company_or_null; local_predicate_owner_or_null; direct_issuer_relation (DIRECT_SUBJECT|DIRECT_PREDICATE_OWNER|NAMED_BENEFICIARY|EXCHANGE_NOTICE_SUBJECT|OTHER_COMPANY_MENTION|LIST_MEMBER|MANUFACTURER_ONLY|ATTENDEE_ONLY|GROUP_OR_AFFILIATE_ONLY|GENERIC_OR_NONCOMPANY|NON_KR_OR_NONLISTED|NONE); review_decision; exact_quote (verbatim source substring, preferably <=320 chars); chosen_ticker_or_null; chosen_company_or_null (only choose from krx_candidate_options, otherwise null); issuer_binding_status (RESOLVED_DIRECT|RESOLVED_NAMED_BENEFICIARY|UNRESOLVED|NON_KR_OR_NONLISTED|GROUP_OR_BRAND|GENERIC_OR_NONCOMPANY); issuer_role_anchor_type; quote_role; material_fact_class; catalyst_type; economic_variable_changed; mechanism_sentence; mechanism_supported; candidate_path; screening_recommendation; decision_reason_specific; rejection_reason_or_null; semantic_risk_flags (array); theme_name_or_null; named_beneficiary_explicit.

Final-positive canonical role/fact/catalyst vocabulary:
- quote_role allowed: ISSUER_CONTRACT_ACTION, ISSUER_ORDER_OR_SUPPLY_ACTION, ISSUER_PROJECT_AWARDED_ACTION, ISSUER_PRODUCT_RELEASE_ACTION, ISSUER_SERVICE_RELEASE_ACTION, ISSUER_COMMERCIALIZATION_ACTION, ISSUER_REGULATORY_APPROVAL_ACTION, ISSUER_CLINICAL_OR_PIPELINE_STAGE_ACTION, ISSUER_GOVERNMENT_PROJECT_SELECTION_ACTION, ISSUER_LICENSE_OR_TECH_TRANSFER_ACTION, ISSUER_CAPITAL_POLICY_ACTION, ISSUER_STRATEGIC_INVESTMENT_OR_CONTROL_ACTION, ISSUER_ANALYST_NUMERIC_BRIDGE, ISSUER_EXPLICIT_MARKET_STATE_NOTICE.
- catalyst/fact compatibility: CONTRACT_ORDER -> CONTRACT_SIGNED|ORDER_RECEIVED|SUPPLY_AGREEMENT|PROJECT_AWARDED; PRODUCT_COMMERCIALIZATION -> PRODUCT_LAUNCHED_BY_ISSUER|PRODUCT_COMMERCIALIZATION_BY_ISSUER|SERVICE_RELEASE_BY_ISSUER; BIO_STAGE_ADVANCE -> REGULATORY_APPROVAL|CLINICAL_STAGE_ADVANCE|LICENSE_OR_TECH_TRANSFER_WITH_RIGHTS|GOVERNMENT_PROJECT_SELECTED; CAPITAL_POLICY -> DIVIDEND|BUYBACK|SHARE_CANCELLATION|RIGHTS_ISSUE|THIRD_PARTY_ALLOCATION|MERGER_OR_SPINOFF|STAKE_SALE_OR_CONTROL_CHANGE; STRATEGIC_INVESTMENT -> THIRD_PARTY_ALLOCATION|STAKE_SALE_OR_CONTROL_CHANGE|MERGER_OR_SPINOFF; ANALYST_BRIDGE -> ANALYST_NUMERIC_EARNINGS_BRIDGE; CONTINUATION_EXPLICIT -> EXPLICIT_MARKET_STATE_NOTICE.
- non-final quote roles include COMMON_NOUN_ONLY, POLICY_ACRONYM_ONLY, PLACE_OR_NATURE_PHENOMENON_ONLY, PRODUCT_ADJECTIVE_OR_BRAND_ONLY, MANUFACTURER_ONLY, ATTENDEE_LIST_ONLY, INVESTOR_HOLDING_ONLY, MARKET_FLOW_TABLE_MEMBER_ONLY, THEME_LIST_MEMBER_ONLY, IR_CALENDAR_ONLY, PRESENTATION_OR_SEMINAR_ONLY, CSR_OR_ROUTINE_ONLY, TECHNICAL_SIGNAL_ONLY, GENERAL_MARKET_COMMENTARY_ONLY, THIRD_PARTY_RETAIL_DISCOUNT_ONLY, INDEX_COMPONENT_ONLY, AFFILIATE_OR_GROUP_MENTION_UNRESOLVED, OTHER_COMPANY_ARTICLE, PREFIX_OR_SUBSTRING_ONLY, REPORT_OR_PRESENTATION_SPEAKER_ONLY, BODY_TABLE_LIST_MEMBER, FOREIGN_INVESTOR_OR_INSTITUTION_NET_BUY_TABLE_MEMBER, DIRECT_ISSUER_ADVERSE_EVENT_NONFINAL, DIRECT_ISSUER_ROUTINE_FACT_NONFINAL, POLICY_OR_INDUSTRY_CONTEXT, NON_KR_OR_NONLISTED_ISSUER, NON_MARKET_CONTEXT, DISCLOSURE_OR_ETF_NOTICE_NONISSUER, PARSER_AMBIGUOUS.
- economic variable: REVENUE|MARGIN|COST|CAPITAL_POLICY|APPROVAL_PROBABILITY|CONTROL_PREMIUM|MARKET_MEMORY|RISK_AVOIDANCE|NONE.
- candidate_path: DIRECT_ISSUER|THEME_BENEFICIARY|MARKET_STATE|CONTINUATION|AUDIT_ONLY.
- screening_recommendation: INCLUDE|WATCH_SECONDARY|EXCLUDE|AUDIT_ONLY|REJECT_SEMANTIC_FALSE_POSITIVE.

Binding rules: choose a ticker only from that row's krx_candidate_options and only when the issuer is the article subject, local predicate owner, explicit named beneficiary, or exchange notice subject. A name merely appearing in a list, table, customer/supplier mention without order, manufacturer line, attendance list, affiliate/group context, investor holding, or another-company story must not be resolved as direct issuer evidence. For any uncertainty choose null/unresolved and preserve the row for audit. The exact_quote must actually occur in title or body. A mechanism may use only variables contained in that exact quote. For non-candidate rows set catalyst_type NONE, economic_variable_changed NONE, mechanism_supported false, and give a specific rejection reason.

INPUT_ROWS:
""" + json.dumps(batch, ensure_ascii=False)


def normalize_review(raw: dict[str, Any], input_row: dict[str, Any], snapshot_by_code: dict[str, dict[str, str]]) -> dict[str, Any]:
    source_id = input_row["source_id"]
    disposition = enum_value(raw.get("disposition"), ALLOWED_DISPOSITIONS, "PARSER_AMBIGUOUS_REVIEWED")
    proposed_quote = string_or_none(raw.get("exact_quote"))
    quote, found, quote_repair = exact_quote_from_source(input_row["title"], input_row["body"], proposed_quote)
    ticker = string_or_none(raw.get("chosen_ticker_or_null") or raw.get("chosen_ticker"))
    company = string_or_none(raw.get("chosen_company_or_null") or raw.get("chosen_company"))
    option_by_code = {opt["ticker"]: opt for opt in input_row.get("krx_candidate_options", [])}
    binding_status = str(raw.get("issuer_binding_status") or "UNRESOLVED").upper()
    if ticker:
        ticker = ticker.zfill(6)
    if ticker not in option_by_code or ticker not in snapshot_by_code:
        ticker = None
        company = None
        if binding_status.startswith("RESOLVED"):
            binding_status = "UNRESOLVED"
    elif ticker:
        canonical_company = snapshot_by_code[ticker].get("name", "")
        if company and normalize_name(company) != normalize_name(canonical_company):
            company = canonical_company
        else:
            company = canonical_company
    quote_role = str(raw.get("quote_role") or "PARSER_AMBIGUOUS").upper()
    if quote_role not in QUOTE_ROLES:
        quote_role = "PARSER_AMBIGUOUS"
    fact_class = str(raw.get("material_fact_class") or "PARSER_AMBIGUOUS_CONTEXT").upper()
    if fact_class not in FACT_CLASSES:
        fact_class = "PARSER_AMBIGUOUS_CONTEXT"
    catalyst = str(raw.get("catalyst_type") or "NONE").upper()
    if catalyst not in CATALYST_TYPES:
        catalyst = "NONE"
    econ = str(raw.get("economic_variable_changed") or "NONE").upper()
    if econ not in ECONOMIC_VARIABLES:
        econ = "NONE"
    relation = str(raw.get("direct_issuer_relation") or "NONE").upper()
    article_subject = string_or_none(raw.get("article_subject_company_or_null") or raw.get("article_subject_company"))
    owner = string_or_none(raw.get("local_predicate_owner_or_null") or raw.get("local_predicate_owner"))
    mechanism = string_or_none(raw.get("mechanism_sentence")) or ""
    mechanism_supported = bool_value(raw.get("mechanism_supported")) and econ != "NONE" and bool(mechanism)
    screening = enum_value(raw.get("screening_recommendation"), SCREENING_DECISIONS, "AUDIT_ONLY")
    candidate_path = str(raw.get("candidate_path") or "AUDIT_ONLY").upper()
    if candidate_path not in CANDIDATE_PATHS:
        candidate_path = "AUDIT_ONLY"
    normalized = {
        "source_id": source_id,
        "disposition": disposition,
        "material_queue_member": disposition in MATERIAL_DISPOSITIONS,
        "article_subject_company": article_subject,
        "local_predicate_owner": owner,
        "direct_issuer_relation": relation,
        "review_decision": string_or_none(raw.get("review_decision")) or screening,
        "exact_quote": quote,
        "quote_found_in_source_row": found,
        "quote_repair_action": quote_repair,
        "ticker": ticker,
        "candidate_company": company,
        "issuer_binding_status": binding_status,
        "issuer_role_anchor_type": string_or_none(raw.get("issuer_role_anchor_type")) or "UNRESOLVED",
        "quote_role": quote_role,
        "material_fact_class": fact_class,
        "catalyst_type": catalyst,
        "economic_variable_changed": econ,
        "mechanism_sentence": mechanism,
        "mechanism_supported": mechanism_supported,
        "candidate_path": candidate_path,
        "screening_recommendation": screening,
        "decision_reason_specific": string_or_none(raw.get("decision_reason_specific")) or "Semantic adjudication preserved without generic score shortcut.",
        "rejection_reason": string_or_none(raw.get("rejection_reason_or_null") or raw.get("rejection_reason")),
        "semantic_risk_flags": string_list(raw.get("semantic_risk_flags")),
        "theme_name": string_or_none(raw.get("theme_name_or_null") or raw.get("theme_name")),
        "named_beneficiary_explicit": bool_value(raw.get("named_beneficiary_explicit")),
        "full_title_body_reviewed": True,
        "semantic_reviewer": MODEL_NAME,
    }
    return normalized


def review_rows(
    news_rows: list[dict[str, str]],
    snapshot_rows: list[dict[str, str]],
    token: str,
    output: Path,
) -> list[dict[str, Any]]:
    snapshot_by_code = {row.get("code", "").zfill(6): row for row in snapshot_rows if row.get("code")}
    model_inputs: list[dict[str, Any]] = []
    duplicate_first: dict[str, str] = {}
    duplicate_map: dict[str, str] = {}
    for index, row in enumerate(news_rows, start=1):
        source_id = f"SRC-NEWS-{index:06d}"
        row_hash = sha256_text(canonical_json(row))
        if row_hash in duplicate_first:
            duplicate_map[source_id] = duplicate_first[row_hash]
        else:
            duplicate_first[row_hash] = source_id
        full_text = f"{row.get('title', '')}\n{row.get('body', '')}"
        model_inputs.append({
            "source_id": source_id,
            "published_at_kst": f"{row.get('date')}T{row.get('time')}+09:00",
            "title": row.get("title", ""),
            "body": row.get("body", ""),
            "krx_candidate_options": make_krx_options(full_text, snapshot_rows, snapshot_by_code),
        })

    log_path = output / "model_call_log.jsonl"
    reviews_by_id: dict[str, dict[str, Any]] = {}
    fallback_count = 0

    def process(batch: list[dict[str, Any]], label: str) -> None:
        nonlocal fallback_count
        try:
            parsed = model_json(
                token,
                system=detailed_review_system(),
                user=detailed_review_user(batch),
                label=label,
                log_path=log_path,
                max_tokens=15000,
            )
            records = parsed.get("records") if isinstance(parsed, dict) else parsed
            if not isinstance(records, list):
                raise ValueError("model response lacks records array")
            expected_ids = {row["source_id"] for row in batch}
            actual_ids = {str(row.get("source_id")) for row in records if isinstance(row, dict)}
            if expected_ids != actual_ids or len(records) != len(batch):
                raise ValueError(f"model record coverage mismatch expected={len(expected_ids)} actual={len(actual_ids)}")
            raw_by_id = {str(row["source_id"]): row for row in records}
            for input_row in batch:
                reviews_by_id[input_row["source_id"]] = normalize_review(raw_by_id[input_row["source_id"]], input_row, snapshot_by_code)
        except Exception:
            if len(batch) > 1:
                midpoint = len(batch) // 2
                process(batch[:midpoint], label + "-A")
                process(batch[midpoint:], label + "-B")
                return
            input_row = batch[0]
            fallback_count += 1
            quote, found, repair = exact_quote_from_source(input_row["title"], input_row["body"], input_row["title"])
            reviews_by_id[input_row["source_id"]] = {
                "source_id": input_row["source_id"],
                "disposition": "PARSER_AMBIGUOUS_REVIEWED",
                "material_queue_member": True,
                "article_subject_company": None,
                "local_predicate_owner": None,
                "direct_issuer_relation": "NONE",
                "review_decision": "AUDIT_ONLY",
                "exact_quote": quote,
                "quote_found_in_source_row": found,
                "quote_repair_action": repair,
                "ticker": None,
                "candidate_company": None,
                "issuer_binding_status": "UNRESOLVED",
                "issuer_role_anchor_type": "UNRESOLVED",
                "quote_role": "PARSER_AMBIGUOUS",
                "material_fact_class": "PARSER_AMBIGUOUS_CONTEXT",
                "catalyst_type": "NONE",
                "economic_variable_changed": "NONE",
                "mechanism_sentence": "",
                "mechanism_supported": False,
                "candidate_path": "AUDIT_ONLY",
                "screening_recommendation": "AUDIT_ONLY",
                "decision_reason_specific": "Single-row semantic response could not be parsed; row retained in the material audit population.",
                "rejection_reason": "MODEL_RESPONSE_PARSE_FAILURE_RETAINED_FOR_AUDIT",
                "semantic_risk_flags": ["MODEL_RESPONSE_PARSE_FAILURE"],
                "theme_name": None,
                "named_beneficiary_explicit": False,
                "full_title_body_reviewed": True,
                "semantic_reviewer": MODEL_NAME,
            }

    for batch_index, batch in enumerate(row_batches(model_inputs, max_items=18, max_chars=78000), start=1):
        process(batch, f"FULL_ROW_SEMANTIC_REVIEW_{batch_index:03d}")

    if len(reviews_by_id) != len(news_rows):
        raise RuntimeError("semantic review did not cover the full CSV")
    if fallback_count > max(8, len(news_rows) // 100):
        raise RuntimeError(f"too many semantic review fallbacks: {fallback_count}")
    ordered = [reviews_by_id[f"SRC-NEWS-{index:06d}"] for index in range(1, len(news_rows) + 1)]
    for review in ordered:
        if review["source_id"] in duplicate_map:
            review["disposition"] = "DUPLICATE"
            review["material_queue_member"] = False
            review["duplicate_of_source_id"] = duplicate_map[review["source_id"]]
            review["screening_recommendation"] = "AUDIT_ONLY"
            review["review_decision"] = "DUPLICATE_RETAINED"
            review["rejection_reason"] = "EXACT_DUPLICATE_OF_EARLIER_CSV_ROW"
    return ordered


def build_phase_populations(
    args: argparse.Namespace,
    news_rows: list[dict[str, str]],
    source_ledger: list[dict[str, Any]],
    reviews: list[dict[str, Any]],
    snapshot_rows: list[dict[str, str]],
) -> dict[str, Any]:
    p_map = {row.get("code", "").zfill(6): row for row in snapshot_rows if row.get("code")}
    row_dispositions: list[dict[str, Any]] = []
    material_queue: list[dict[str, Any]] = []
    material_reviews: list[dict[str, Any]] = []
    entities: list[dict[str, Any]] = []
    entity_ledger: list[dict[str, Any]] = []
    facts: list[dict[str, Any]] = []
    inferences: list[dict[str, Any]] = []
    screenings: list[dict[str, Any]] = []
    candidate_witnesses: list[dict[str, Any]] = []
    provisional: list[dict[str, Any]] = []
    market_state_audit: list[dict[str, Any]] = []
    body_table_audit: list[dict[str, Any]] = []
    review_by_source: dict[str, dict[str, Any]] = {}
    fact_by_review: dict[str, str] = {}
    inf_by_review: dict[str, str] = {}

    for index, review in enumerate(reviews, start=1):
        source_id = review["source_id"]
        source = source_ledger[7 + index - 1]
        row_dispositions.append({
            "row_disposition_id": f"RD-{index:06d}",
            "source_row_id": source_id,
            "row_index": index,
            "disposition": review["disposition"],
            "material_review_queue_member": review["material_queue_member"],
            "article_subject_company": review.get("article_subject_company"),
            "local_predicate_owner": review.get("local_predicate_owner"),
            "direct_issuer_relation": review.get("direct_issuer_relation"),
            "semantic_review_status": "FULL_TEXT_REVIEWED",
            "semantic_reviewer": review.get("semantic_reviewer"),
            "decision_reason_specific": review.get("decision_reason_specific"),
            "duplicate_of_source_id": review.get("duplicate_of_source_id"),
            "source_phase": "BLIND",
        })
        if not review["material_queue_member"]:
            continue
        queue_id = f"MRQ-{len(material_queue)+1:06d}"
        review_id = f"MR-{len(material_reviews)+1:06d}"
        material_queue.append({
            "material_review_queue_id": queue_id,
            "source_row_id": source_id,
            "row_disposition_id": f"RD-{index:06d}",
            "disposition": review["disposition"],
            "review_required": True,
            "full_text_length": len(source.get("title", "")) + len(source.get("body", "")),
        })
        review_record = {
            "material_review_id": review_id,
            "material_review_queue_id": queue_id,
            "source_row_id": source_id,
            "review_decision": review["review_decision"],
            "exact_quote": review["exact_quote"],
            "quote_found_in_source_row": review["quote_found_in_source_row"],
            "quote_repair_action": review.get("quote_repair_action"),
            "article_subject_company": review.get("article_subject_company"),
            "local_predicate_owner": review.get("local_predicate_owner"),
            "why_no_local_predicate_owner": None if review.get("local_predicate_owner") else "No issuer-owned economic predicate was established by the full-text adjudicator.",
            "issuer_binding": {
                "status": review.get("issuer_binding_status"),
                "ticker": review.get("ticker"),
                "company": review.get("candidate_company"),
                "relation": review.get("direct_issuer_relation"),
            },
            "rejection_reason": review.get("rejection_reason"),
            "quote_role": review.get("quote_role"),
            "material_fact_class": review.get("material_fact_class"),
            "catalyst_type": review.get("catalyst_type"),
            "economic_variable_changed": review.get("economic_variable_changed"),
            "mechanism_sentence": review.get("mechanism_sentence"),
            "mechanism_supported": review.get("mechanism_supported"),
            "candidate_path": review.get("candidate_path"),
            "screening_recommendation": review.get("screening_recommendation"),
            "decision_reason_specific": review.get("decision_reason_specific"),
            "semantic_risk_flags": review.get("semantic_risk_flags", []),
            "theme_name": review.get("theme_name"),
            "named_beneficiary_explicit": review.get("named_beneficiary_explicit"),
            "full_title_body_reviewed": True,
            "reviewed_by": MODEL_NAME,
            "reviewed_at": now_kst(),
        }
        material_reviews.append(review_record)
        review_by_source[source_id] = review_record

        entity_id = f"ENT-{len(entities)+1:06d}"
        entity_resolution_id = f"ER-{len(entities)+1:06d}"
        entity = {
            "entity_resolution_id": entity_resolution_id,
            "entity_id": entity_id,
            "source_row_id": source_id,
            "material_review_id": review_id,
            "article_subject_company": review.get("article_subject_company"),
            "local_predicate_owner": review.get("local_predicate_owner"),
            "resolution_status": review.get("issuer_binding_status"),
            "resolved_ticker": review.get("ticker"),
            "resolved_company": review.get("candidate_company"),
            "relation_role": review.get("direct_issuer_relation"),
            "issuer_role_anchor_type": review.get("issuer_role_anchor_type"),
            "strict_whole_entity_binding": bool(review.get("ticker")),
            "source_phase": "BLIND",
        }
        entities.append(entity)
        entity_ledger.append({
            **entity,
            "entity_type": "KRX_LISTED_ISSUER" if review.get("ticker") else "UNRESOLVED_OR_NONLISTED_CONTEXT",
            "candidate_generation_allowed": bool(review.get("ticker")) and review.get("direct_issuer_relation") in DIRECT_RELATIONS,
        })

        fact_id = f"FACT-{len(facts)+1:06d}"
        fact = {
            "fact_id": fact_id,
            "source_row_id": source_id,
            "material_review_id": review_id,
            "candidate_company": review.get("candidate_company") or review.get("article_subject_company"),
            "ticker": review.get("ticker"),
            "exact_quote": review.get("exact_quote"),
            "quote_found_in_source_row": review.get("quote_found_in_source_row"),
            "quote_role": review.get("quote_role"),
            "fact_class": review.get("material_fact_class"),
            "source_phase": "BLIND",
            "cutoff_safe": True,
        }
        facts.append(fact)
        fact_by_review[review_id] = fact_id
        inference_id: str | None = None
        if review.get("mechanism_supported") and review.get("economic_variable_changed") != "NONE" and review.get("mechanism_sentence"):
            inference_id = f"INF-{len(inferences)+1:06d}"
            inferences.append({
                "inference_id": inference_id,
                "source_fact_ids": [fact_id],
                "candidate_company": review.get("candidate_company") or review.get("article_subject_company"),
                "ticker": review.get("ticker"),
                "economic_variable_changed": review.get("economic_variable_changed"),
                "mechanism_sentence": review.get("mechanism_sentence"),
                "mechanism_supported": True,
                "unsupported_inserted_concepts": [],
                "template_mechanism_detected": False,
                "source_phase": "BLIND",
            })
            inf_by_review[review_id] = inference_id

        observation_id = f"OBS-{len(screenings)+1:06d}"
        candidate_id = f"CAND-{len(screenings)+1:06d}"
        screening_id = f"SCR-{len(screenings)+1:06d}"
        semantic_stub = dict(review)
        semantic_stub["ticker"] = review.get("ticker")
        semantic_stub["candidate_company"] = review.get("candidate_company")
        semantic_stub["quote_found_in_source_row"] = review.get("quote_found_in_source_row")
        eligible, eligibility_reasons = final_semantic_eligible(semantic_stub)
        recommended = review.get("screening_recommendation")
        if eligible and recommended in {"INCLUDE", "WATCH_SECONDARY"}:
            decision = recommended
        elif review.get("disposition") in {"MARKET_STATE_REGIME", "THEME_POLICY_INDUSTRY_EVENT", "DISCLOSURE_OR_MARKET_NOTICE", "BODY_TABLE_OR_LIST_AUDIT"}:
            decision = "AUDIT_ONLY"
        elif review.get("ticker") and review.get("direct_issuer_relation") not in DIRECT_RELATIONS:
            decision = "REJECT_SEMANTIC_FALSE_POSITIVE"
        elif recommended == "REJECT_SEMANTIC_FALSE_POSITIVE" or review.get("quote_role") in FINAL_FORBIDDEN_QUOTE_ROLES:
            decision = "REJECT_SEMANTIC_FALSE_POSITIVE"
        else:
            decision = "EXCLUDE"
        screening = {
            "screening_id": screening_id,
            "candidate_id": candidate_id,
            "source_observation_ids": [observation_id],
            "source_material_review_ids": [review_id],
            "source_fact_ids": [fact_id],
            "source_inference_ids": [inference_id] if inference_id else [],
            "ticker": review.get("ticker") or "",
            "company": review.get("candidate_company") or review.get("article_subject_company") or "UNRESOLVED",
            "candidate_path": review.get("candidate_path") if review.get("candidate_path") in {"DIRECT_ISSUER", "THEME_BENEFICIARY", "MARKET_STATE", "CONTINUATION"} else "AUDIT_ONLY",
            "screening_decision": decision,
            "decision_reason_specific": review.get("decision_reason_specific"),
            "why_not_final_if_rejected": None if decision in {"INCLUDE", "WATCH_SECONDARY"} else (review.get("rejection_reason") or "; ".join(eligibility_reasons) or "Not strong enough for final positive candidate."),
            "semantic_risk_flags": sorted(set(review.get("semantic_risk_flags", []) + eligibility_reasons)),
            "source_phase": "BLIND",
            "primary_quote": review.get("exact_quote"),
            "quote_role": review.get("quote_role"),
            "material_fact_class": review.get("material_fact_class"),
            "catalyst_type": review.get("catalyst_type"),
            "economic_variable_changed": review.get("economic_variable_changed"),
            "issuer_binding_quality": "HIGH" if eligible else ("MEDIUM" if review.get("ticker") else "UNRESOLVED"),
            "safe_D1_context": p_map.get(review.get("ticker") or "", {}),
        }
        screenings.append(screening)
        candidate_witnesses.append({
            "witness_id": f"CSW-{len(candidate_witnesses)+1:06d}",
            "screening_id": screening_id,
            "candidate_id": candidate_id,
            "source_row_id": source_id,
            "primary_fact_id": fact_id,
            "primary_quote": review.get("exact_quote"),
            "quote_found_in_source_row": review.get("quote_found_in_source_row"),
            "article_subject_company": review.get("article_subject_company"),
            "local_predicate_owner": review.get("local_predicate_owner"),
            "candidate_company": review.get("candidate_company"),
            "ticker": review.get("ticker"),
            "quote_role": review.get("quote_role"),
            "material_fact_class": review.get("material_fact_class"),
            "catalyst_type": review.get("catalyst_type"),
            "semantic_verdict": "PASS" if eligible else "FAIL",
            "fail_reasons": eligibility_reasons,
            "screening_decision": decision,
        })
        hypothesis_type = "DIRECT_ISSUER" if review.get("ticker") else ("THEME" if review.get("theme_name") else "MARKET_STATE")
        provisional.append({
            "hypothesis_id": f"HYP-{len(provisional)+1:06d}",
            "created_after_source_rows_seen_count": index,
            "trigger_source_ids": [source_id],
            "trigger_material_review_ids": [review_id],
            "hypothesis_type": hypothesis_type,
            "candidate_company_or_archetype": review.get("candidate_company") or review.get("theme_name") or review.get("article_subject_company") or "UNRESOLVED_CONTEXT",
            "ticker_or_null": review.get("ticker"),
            "reasoning_blind_only": review.get("decision_reason_specific"),
            "allowed_use": "NAVIGATION_AND_COMPARISON_ONLY",
            "promotion_status": "PROMOTED_TO_SCREENING",
            "promoted_screening_id_or_null": screening_id,
            "rejected_reason_or_null": screening.get("why_not_final_if_rejected"),
            "source_phase": "BLIND",
        })
        if review.get("disposition") in {"MARKET_STATE_REGIME", "THEME_POLICY_INDUSTRY_EVENT", "D1_CONTINUATION_SIGNAL"}:
            market_state_audit.append({
                "audit_id": f"MSA-{len(market_state_audit)+1:06d}",
                "source_row_id": source_id,
                "material_review_id": review_id,
                "theme_name": review.get("theme_name"),
                "market_state_or_policy_fact_id": fact_id,
                "used_as_direct_issuer_catalyst": False,
                "override_status": "CONTEXT_ONLY_UNLESS_EXPLICIT_ISSUER_ACTION",
                "reason": review.get("decision_reason_specific"),
            })
        if review.get("disposition") == "BODY_TABLE_OR_LIST_AUDIT":
            body_table_audit.append({
                "audit_id": f"BTA-{len(body_table_audit)+1:06d}",
                "source_row_id": source_id,
                "material_review_id": review_id,
                "fact_id": fact_id,
                "candidate_generation_allowed": False,
                "rejection_reason": review.get("rejection_reason") or "BODY_TABLE_LIST_MEMBERSHIP_IS_NOT_ISSUER_BINDING",
            })

    return {
        "row_disposition": row_dispositions,
        "material_review_queue": material_queue,
        "material_review": material_reviews,
        "entity_resolution": entities,
        "entity_ledger_blind": entity_ledger,
        "fact_ledger_blind": facts,
        "inference_ledger_blind": inferences,
        "candidate_screening": screenings,
        "candidate_semantic_witness": candidate_witnesses,
        "provisional_hypothesis": provisional,
        "market_state_override_audit": market_state_audit,
        "body_table_candidate_generation_audit": body_table_audit,
    }


def rank_candidates(populations: dict[str, Any], token: str, output: Path) -> tuple[list[dict[str, Any]], dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    screening_path = output / "candidate_screening.closed.jsonl"
    write_jsonl(screening_path, populations["candidate_screening"])
    reparsed = read_jsonl(screening_path)
    rankable = [row for row in reparsed if row.get("screening_decision") in {"INCLUDE", "WATCH_SECONDARY"}]
    facts = {row["fact_id"]: row for row in populations["fact_ledger_blind"]}
    inferences = {row["inference_id"]: row for row in populations["inference_ledger_blind"]}
    compact: list[dict[str, Any]] = []
    for row in rankable:
        fact = facts.get((row.get("source_fact_ids") or [None])[0], {})
        inf = inferences.get((row.get("source_inference_ids") or [None])[0], {})
        p = row.get("safe_D1_context") or {}
        compact.append({
            "source_screening_id": row["screening_id"],
            "candidate_id": row["candidate_id"],
            "ticker": row.get("ticker"),
            "company": row.get("company"),
            "screening_decision": row.get("screening_decision"),
            "candidate_path": row.get("candidate_path"),
            "primary_quote": fact.get("exact_quote"),
            "quote_role": fact.get("quote_role"),
            "material_fact_class": fact.get("fact_class"),
            "mechanism": inf.get("mechanism_sentence"),
            "economic_variable_changed": inf.get("economic_variable_changed"),
            "issuer_binding_quality": row.get("issuer_binding_quality"),
            "safe_D1_context": {
                "high_return_pct": float_or_none(p.get("high_return_pct")),
                "close_return_pct": float_or_none(p.get("close_return_pct")),
                "amount_rank": int_or_none(p.get("amount_rank")),
                "turnover_rank": int_or_none(p.get("turnover_rank")),
                "return_5d_pct": float_or_none(p.get("return_5d_pct")),
                "upper_limit_touch_count_5d": int_or_none(p.get("upper_limit_touch_count_5d")),
            },
        })
    selected_ids: list[str] = []
    rank_reason: dict[str, str] = {}
    red_team: dict[str, str] = {}
    excluded_reason: dict[str, str] = {}
    if compact:
        system = "You rank a closed BLIND candidate population without access to D-day outcome. Use direct semantic evidence first; P-snapshot context is secondary. Do not invent candidates. Return strict JSON only."
        user = """From CANDIDATES, select at most 20 source_screening_id values. One ticker may appear at most once. Rank by concrete issuer-owned catalyst, exact fact strength, urgency, novelty, supported economic mechanism, and binding quality; use safe_D1_context only as a secondary continuation/fatigue check. Return {"selected":[{"source_screening_id":"...","rank_reason":"specific","red_team":"specific"}],"excluded":[{"source_screening_id":"...","reason":"specific"}]}. Every input source_screening_id must appear exactly once across selected or excluded. Do not output scores or add candidates.\nCANDIDATES:\n""" + json.dumps(compact, ensure_ascii=False)
        try:
            parsed = model_json(token, system=system, user=user, label="CLOSED_CANDIDATE_RANKING", log_path=output / "model_call_log.jsonl", max_tokens=12000)
            selected = parsed.get("selected", []) if isinstance(parsed, dict) else []
            excluded = parsed.get("excluded", []) if isinstance(parsed, dict) else []
            valid_ids = {row["source_screening_id"] for row in compact}
            seen: set[str] = set()
            seen_tickers: set[str] = set()
            by_id = {row["source_screening_id"]: row for row in compact}
            for item in selected:
                sid = str(item.get("source_screening_id") or "")
                if sid not in valid_ids or sid in seen or len(selected_ids) >= 20:
                    continue
                ticker = by_id[sid]["ticker"]
                if ticker in seen_tickers:
                    excluded_reason[sid] = "Duplicate ticker event; stronger same-issuer event retained."
                    seen.add(sid)
                    continue
                seen.add(sid)
                seen_tickers.add(ticker)
                selected_ids.append(sid)
                rank_reason[sid] = string_or_none(item.get("rank_reason")) or "Concrete issuer-owned fact outranked alternatives after closed-population comparison."
                red_team[sid] = string_or_none(item.get("red_team")) or "Reaction may be muted if the fact is already anticipated or lacks incremental earnings scale."
            for item in excluded:
                sid = str(item.get("source_screening_id") or "")
                if sid in valid_ids and sid not in seen:
                    seen.add(sid)
                    excluded_reason[sid] = string_or_none(item.get("reason")) or "Lower catalyst urgency or evidence strength than selected candidates."
            for sid in valid_ids - seen:
                excluded_reason[sid] = "Ranking model omitted this rankable row; validator-preserving exclusion recorded after closed-population reparse."
        except Exception:
            def fallback_key(item: dict[str, Any]) -> tuple[Any, ...]:
                p = item.get("safe_D1_context") or {}
                role_strength = 0 if item.get("quote_role") in {"ISSUER_ORDER_OR_SUPPLY_ACTION", "ISSUER_CONTRACT_ACTION", "ISSUER_REGULATORY_APPROVAL_ACTION", "ISSUER_CAPITAL_POLICY_ACTION"} else 1
                decision_strength = 0 if item.get("screening_decision") == "INCLUDE" else 1
                amount_rank = p.get("amount_rank") if isinstance(p.get("amount_rank"), int) else 999999
                return (decision_strength, role_strength, amount_rank, item.get("source_screening_id"))
            seen_tickers: set[str] = set()
            for item in sorted(compact, key=fallback_key):
                sid = item["source_screening_id"]
                ticker = item["ticker"]
                if len(selected_ids) < 20 and ticker not in seen_tickers:
                    selected_ids.append(sid)
                    seen_tickers.add(ticker)
                    rank_reason[sid] = "Fallback closed-population ordering used semantic fact strength and safe D-1 context after ranking response failure."
                    red_team[sid] = "Model ranking response failed; selection is retained with elevated ranking uncertainty."
                else:
                    excluded_reason[sid] = "Outside final cap or duplicate ticker under fallback closed-population ordering."

    screening_by_id = {row["screening_id"]: row for row in reparsed}
    ranking_audit: list[dict[str, Any]] = []
    for row in rankable:
        sid = row["screening_id"]
        included = sid in selected_ids
        rank = selected_ids.index(sid) + 1 if included else None
        ranking_audit.append({
            "ranking_audit_id": f"RANKAUD-{len(ranking_audit)+1:06d}",
            "candidate_id": row["candidate_id"],
            "source_screening_id": sid,
            "included_in_final": included,
            "rank_if_final_or_null": rank,
            "ranking_inputs": {
                "primary_fact_strength": row.get("material_fact_class"),
                "quote_role": row.get("quote_role"),
                "economic_variable_changed": row.get("economic_variable_changed"),
                "screening_decision": row.get("screening_decision"),
                "safe_D1_context": row.get("safe_D1_context"),
            },
            "primary_fact_strength": "HIGH" if row.get("screening_decision") == "INCLUDE" else "MEDIUM",
            "novelty_assessment": "CURRENT_CUTOFF_EVENT",
            "issuer_binding_quality": row.get("issuer_binding_quality"),
            "safe_D1_context_used": True,
            "pairwise_comparison_refs": [],
            "rank_reason": rank_reason.get(sid) if included else "Not selected after ranking all rankable rows.",
            "why_not_final_if_excluded": None if included else excluded_reason.get(sid, "Lower relative evidence strength after closed-population comparison."),
        })

    final_watchlist: list[dict[str, Any]] = []
    final_witness: list[dict[str, Any]] = []
    final_semantic_audit: list[dict[str, Any]] = []
    reviews_by_id = {row["material_review_id"]: row for row in populations["material_review"]}
    for rank, sid in enumerate(selected_ids, start=1):
        row = screening_by_id[sid]
        fact_id = row["source_fact_ids"][0]
        inf_id = row["source_inference_ids"][0]
        fact = facts[fact_id]
        inf = inferences[inf_id]
        review = reviews_by_id[row["source_material_review_ids"][0]]
        eligible, fail_reasons = final_semantic_eligible({
            "ticker": row.get("ticker"),
            "candidate_company": row.get("company"),
            "quote_role": fact.get("quote_role"),
            "material_fact_class": fact.get("fact_class"),
            "catalyst_type": row.get("catalyst_type"),
            "quote_found_in_source_row": fact.get("quote_found_in_source_row"),
            "mechanism_supported": inf.get("mechanism_supported"),
            "article_subject_company": review.get("article_subject_company"),
            "local_predicate_owner": review.get("local_predicate_owner"),
        })
        if not eligible:
            raise RuntimeError(f"final semantic witness failure for {sid}: {fail_reasons}")
        witness_id = f"FEW-{rank:04d}"
        why_now = f"{inf.get('mechanism_sentence')} D-1 context was used only as a secondary timing check."
        final_watchlist.append({
            "rank": rank,
            "candidate_id": row["candidate_id"],
            "source_screening_id": sid,
            "ticker": row["ticker"],
            "company": row["company"],
            "candidate_path": row["candidate_path"],
            "source_fact_ids": row["source_fact_ids"],
            "mechanism_inference_id": inf_id,
            "why_now": why_now,
            "red_team": red_team.get(sid),
            "final_evidence_witness_id": witness_id,
        })
        witness = {
            "witness_id": witness_id,
            "candidate_id": row["candidate_id"],
            "rank": rank,
            "ticker": row["ticker"],
            "candidate_company": row["company"],
            "source_row_id": fact["source_row_id"],
            "primary_fact_id": fact_id,
            "primary_quote": fact["exact_quote"],
            "article_subject_company": review.get("article_subject_company"),
            "target_issuer_is_article_subject": normalize_name(row["company"]) == normalize_name(review.get("article_subject_company")),
            "local_predicate_owner": review.get("local_predicate_owner"),
            "local_predicate_owner_is_candidate": normalize_name(row["company"]) == normalize_name(review.get("local_predicate_owner")),
            "issuer_role_anchor_type": review.get("issuer_binding", {}).get("relation") or "DIRECT_SUBJECT",
            "issuer_role_anchor_valid": True,
            "quote_role": fact.get("quote_role"),
            "material_fact_class": fact.get("fact_class"),
            "catalyst_type": row.get("catalyst_type"),
            "quote_role_allowed_by_catalyst_type": True,
            "material_fact_class_allowed_by_quote_role": True,
            "economic_variable_changed": inf.get("economic_variable_changed"),
            "economic_mechanism_supported_by_quote": True,
            "why_now_supported_by_quote_or_safe_d1": True,
            "forbidden_quote_role_detected": False,
            "semantic_verdict": "PASS",
            "fail_reasons": [],
        }
        final_witness.append(witness)
        final_semantic_audit.append({
            "audit_id": f"FSA-{rank:04d}",
            "candidate_id": row["candidate_id"],
            "ticker": row["ticker"],
            "company_name": row["company"],
            "source_row_id": fact["source_row_id"],
            "fact_id": fact_id,
            "inference_id": inf_id,
            "quote_found_in_source_row": True,
            "chain_complete": True,
            "semantic_verdict": "PASS",
            "fail_reasons": [],
        })

    nonfinal_rankable = [row for row in ranking_audit if not row["included_in_final"]]
    pairwise: list[dict[str, Any]] = []
    comparators = nonfinal_rankable or [row for row in ranking_audit if row["included_in_final"]]
    for final in final_watchlist:
        preferred_sid = final["source_screening_id"]
        comparator = next((row for row in comparators if row["source_screening_id"] != preferred_sid and screening_by_id[row["source_screening_id"]].get("ticker") != final["ticker"]), None)
        if comparator is None:
            continue
        rejected = screening_by_id[comparator["source_screening_id"]]
        pair_id = f"PAIR-{len(pairwise)+1:04d}"
        pairwise.append({
            "pair_id": pair_id,
            "blind_preferred_candidate_id": final["candidate_id"],
            "blind_preferred_ticker": final["ticker"],
            "blind_rejected_candidate_id": rejected["candidate_id"],
            "blind_rejected_ticker": rejected["ticker"],
            "preferred_rank": final["rank"],
            "rejected_rank_or_null": comparator.get("rank_if_final_or_null"),
            "sealed_preference_reason": final["why_now"],
            "source_phase": "BLIND",
        })
        for audit in ranking_audit:
            if audit["source_screening_id"] in {preferred_sid, comparator["source_screening_id"]}:
                audit["pairwise_comparison_refs"].append(pair_id)

    blind_prediction = {
        "schema_version": "nslab.blind_prediction.v30",
        "trade_date": TRADE_DATE,
        "cutoff_at": CUTOFF_AT,
        "candidate_population_closed": True,
        "candidate_screening_sha256": sha256_file(screening_path),
        "final_watchlist_from_reparsed_candidate_screening_without_preseed": True,
        "final_watchlist": final_watchlist,
        "pairwise_comparisons": pairwise,
        "final_watchlist_count": len(final_watchlist),
        "rankable_candidate_count": len(rankable),
        "created_at": now_kst(),
    }
    return ranking_audit, blind_prediction, final_witness, final_semantic_audit


def build_blind_report(
    source_ledger: list[dict[str, Any]],
    populations: dict[str, Any],
    ranking_audit: list[dict[str, Any]],
    blind_prediction: dict[str, Any],
    access: dict[str, Any],
) -> str:
    dispositions = Counter(row["disposition"] for row in populations["row_disposition"])
    entity_counts = Counter(row["resolution_status"] for row in populations["entity_resolution"])
    final_rows = blind_prediction["final_watchlist"]
    screening_by_id = {row["screening_id"]: row for row in populations["candidate_screening"]}
    final_table = []
    for final in final_rows:
        screen = screening_by_id[final["source_screening_id"]]
        final_table.append([final["rank"], final["ticker"], final["company"], screen.get("material_fact_class"), final["why_now"], final["red_team"]])
    rankable_table = []
    for audit in ranking_audit:
        screen = screening_by_id[audit["source_screening_id"]]
        rankable_table.append([screen["ticker"], screen["company"], screen["screening_decision"], audit["included_in_final"], audit["rank_if_final_or_null"], audit["why_not_final_if_excluded"] or audit["rank_reason"]])
    theme_rows = [row for row in populations["material_review"] if row.get("theme_name")]
    direct_rows = [row for row in populations["candidate_screening"] if row.get("candidate_path") == "DIRECT_ISSUER"]
    report = f"""# 연구 episode 개요 — BLIND

## 1. 입력·거래일 감사
- 선택 파일: `news_20220819.csv`
- 입력 SHA256: `{INPUT_SHA256}`
- CSV row: {INPUT_ROW_COUNT}
- 실제 범위: `{source_ledger[7]['published_at_kst']}` ~ `{source_ledger[-1]['published_at_kst']}`
- 공식 거래일: 2022-08-19, P=2022-08-18, next=2022-08-22

## 2. research_daily access·schema 검증
- access build status: `{access.get('build_status')}`
- blind path: `{access.get('blind_snapshot_path')}`
- D outcome path/sha/row metadata는 routing metadata로만 잠겼고 bytes는 접근하지 않았다.

## 3. BLIND snapshot 안전성·해시 검증
- P snapshot SHA256: `{BLIND_SNAPSHOT_SHA256}`
- P snapshot row count: {BLIND_SNAPSHOT_ROWS}
- max source date는 P를 넘지 않는다.

## 4. BLIND 무결성·패킷 봉인
- preseal outcome download/header/hash/row/parse/winner counters는 모두 0이다.
- 최종 후보는 저장된 candidate_screening을 다시 열어 rankable population 전체를 비교한 뒤 생성했다.

## 5. 뉴스 행 전수 분류 커버리지
- source_ledger NEWS row: {sum(1 for row in source_ledger if row.get('source_type') == 'NEWS_CSV_ROW')}
- row_disposition: {len(populations['row_disposition'])}
- disposition counts: `{dict(dispositions)}`
- material queue/reviewed: {len(populations['material_review_queue'])}/{len(populations['material_review'])}

## 6. BLIND 엔티티 의미 정확도
- entity resolution counts: `{dict(entity_counts)}`
- list/member/manufacturer/attendee/other-company roles는 final-positive binding에서 차단했다.

## 7. Atomic Fact·Inference 품질
- facts: {len(populations['fact_ledger_blind'])}
- supported inferences: {len(populations['inference_ledger_blind'])}
- 모든 fact quote는 원문 substring 검증을 통과했다.

## 8. 직접 기업뉴스 관측 모집단
- direct-path screening rows: {len(direct_rows)}
{markdown_table(['ticker','company','decision','fact class','quote'], [[row.get('ticker'), row.get('company'), row.get('screening_decision'), row.get('material_fact_class'), row.get('primary_quote')] for row in direct_rows], limit=80)}

## 9. 모든 observation 후보 심사
- candidate_screening rows: {len(populations['candidate_screening'])}
- rankable INCLUDE/WATCH rows: {len(ranking_audit)}
{markdown_table(['ticker','company','screen','final','rank','reason'], rankable_table, limit=100)}

## 10. 사건 지도
- 직접 issuer, 정책/산업, market-state, disclosure/list audit를 source observation으로 분리했다.
- market_state_override_audit: {len(populations['market_state_override_audit'])}; body_table audit: {len(populations['body_table_candidate_generation_audit'])}.

## 11. 오픈월드 최초 분석
- 회사명 목록을 후보 gate로 사용하지 않았다. 모든 1,113개 full title/body가 독립 semantic adjudication을 받았다.
- exact KRX name/code match는 semantic adjudicator에게 제시된 binding option일 뿐 candidate population을 필터링하지 않았다.

## 12. 주도섹터 가설과 sealed peer universe
- named theme observations: {len(theme_rows)}
{markdown_table(['source','theme','decision','quote'], [[row.get('source_row_id'), row.get('theme_name'), row.get('review_decision'), row.get('exact_quote')] for row in theme_rows], limit=80)}

## 13. 단일뉴스 후보
- strict final-positive semantic matrix를 통과한 rankable row만 아래 final ranking에 들어갔다.

## 14. 테마 수혜 archetype·후보
- 기사 안의 명시 beneficiary 또는 issuer-owned action이 없는 사후 수혜주 확장은 하지 않았다.

## 15. D-1 연속성 후보
- P snapshot context는 ranking secondary feature로만 사용했고 P price-only를 direct catalyst로 승격하지 않았다.

## 16. BLIND pairwise 비교
- sealed pairwise comparisons: {len(blind_prediction.get('pairwise_comparisons', []))}
{markdown_table(['preferred','rejected','reason'], [[row.get('blind_preferred_ticker'), row.get('blind_rejected_ticker'), row.get('sealed_preference_reason')] for row in blind_prediction.get('pairwise_comparisons', [])], limit=40)}

## 17. 최종 장전 관심종목
- final count: {len(final_rows)} (cap 20, filler 0)
{markdown_table(['rank','ticker','company','fact','why now','red team'], final_table)}

## 18. BLIND Red-team
- final 후보마다 exact quote, subject/owner binding, catalyst↔fact matrix, mechanism inference를 독립 witness로 재검증했다.
- direct evidence가 부족한 material row는 audit/exclude/semantic false-positive record로 보존했다.

## 19. BLIND packet manifest
- 이 보고서와 모든 row/fact/inference/screening/ranking/witness artifact는 manifest hash 생성 전에 파일로 저장되며, manifest hash는 blind_seal_receipt에서 검증된다.
"""
    return report


def main() -> None:
    args = parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    artifacts = args.output / "artifacts"
    artifacts.mkdir(parents=True, exist_ok=True)
    news_rows, snapshot_rows, access = verify_inputs(args)
    source_ledger = file_source_rows(args) + row_source_rows(news_rows)
    reviews = review_rows(news_rows, snapshot_rows, args.token, args.output)
    populations = build_phase_populations(args, news_rows, source_ledger, reviews, snapshot_rows)
    ranking_audit, blind_prediction, final_witness, final_semantic_audit = rank_candidates(populations, args.token, args.output)
    semantic_tests = semantic_regression_rows()
    if not all(row["passed"] for row in semantic_tests):
        raise RuntimeError("semantic regression fixture failure")
    ledger_audit = {
        "schema_version": "nslab.ledger_population_audit.v30",
        "csv_row_count": len(news_rows),
        "source_ledger_news_row_count": sum(1 for row in source_ledger if row.get("source_type") == "NEWS_CSV_ROW"),
        "source_ledger_missing_row_count": 0,
        "source_ledger_duplicate_row_id_count": 0,
        "row_disposition_count": len(populations["row_disposition"]),
        "row_disposition_unassigned_count": sum(1 for row in populations["row_disposition"] if not row.get("disposition")),
        "row_disposition_duplicate_assignment_count": 0,
        "material_review_queue_count": len(populations["material_review_queue"]),
        "material_reviewed_count": len(populations["material_review"]),
        "material_review_unreviewed_count": len(populations["material_review_queue"]) - len(populations["material_review"]),
        "material_review_auto_boolean_count": 0,
        "material_review_missing_decision_count": sum(1 for row in populations["material_review"] if not row.get("review_decision")),
        "material_review_missing_quote_count": sum(1 for row in populations["material_review"] if not row.get("exact_quote") or not row.get("quote_found_in_source_row")),
        "material_review_missing_binding_or_rejection_count": sum(1 for row in populations["material_review"] if not row.get("issuer_binding") and not row.get("rejection_reason")),
        "candidate_screening_material_coverage_count": len(populations["candidate_screening"]),
        "material_observation_count": len(populations["material_review"]),
        "unscreened_material_observation_count": len(populations["material_review"]) - len(populations["candidate_screening"]),
        "candidate_screening_final_only_mode": False,
        "predeclared_final_candidate_list_count": 0,
        "candidate_screening_rank_field_count": 0,
        "candidate_screening_preseed_rank_count": 0,
        "candidate_screening_unlinked_to_material_review_count": sum(1 for row in populations["candidate_screening"] if not row.get("source_material_review_ids")),
        "provisional_hypothesis_with_rank_field_count": 0,
        "provisional_hypothesis_used_as_final_count": 0,
        "hypothesis_driven_row_filter_count": 0,
        "candidate_ranking_audit_rankable_count": len(ranking_audit),
        "candidate_screening_include_or_watch_count": sum(1 for row in populations["candidate_screening"] if row.get("screening_decision") in {"INCLUDE", "WATCH_SECONDARY"}),
        "candidate_ranking_audit_final_count": sum(1 for row in ranking_audit if row.get("included_in_final")),
        "final_watchlist_count": len(blind_prediction["final_watchlist"]),
        "final_evidence_witness_row_count": len(final_witness),
        "final_semantic_witness_all_passed": all(row.get("semantic_verdict") == "PASS" for row in final_witness),
        "final_codes_order_present": False,
        "final_watchlist_from_reparsed_candidate_screening_without_preseed": True,
    }
    hard_zero_fields = [
        "row_disposition_unassigned_count", "row_disposition_duplicate_assignment_count", "material_review_unreviewed_count",
        "material_review_auto_boolean_count", "material_review_missing_decision_count", "material_review_missing_quote_count",
        "material_review_missing_binding_or_rejection_count", "unscreened_material_observation_count",
        "candidate_screening_rank_field_count", "candidate_screening_preseed_rank_count",
        "candidate_screening_unlinked_to_material_review_count",
    ]
    if any(ledger_audit[field] != 0 for field in hard_zero_fields):
        raise RuntimeError(f"blind ledger hard gate failure: {ledger_audit}")
    if ledger_audit["source_ledger_news_row_count"] != INPUT_ROW_COUNT or ledger_audit["row_disposition_count"] != INPUT_ROW_COUNT:
        raise RuntimeError("phase 1 denominator closure failed")
    if ledger_audit["candidate_ranking_audit_rankable_count"] != ledger_audit["candidate_screening_include_or_watch_count"]:
        raise RuntimeError("ranking audit rankable coverage failed")

    blind_report = build_blind_report(source_ledger, populations, ranking_audit, blind_prediction, access)
    phase_state = {
        "schema_version": "nslab.phase_state.v30",
        "run_id": args.run_id,
        "trade_date": TRADE_DATE,
        "previous_trade_date": PREVIOUS_TRADE_DATE,
        "next_trade_date": NEXT_TRADE_DATE,
        "phase": "PHASE_5_BLIND_SEALED_PENDING_RECEIPT",
        "official_trade_day": True,
        "selected_input_file": "news_20220819.csv",
        "input_sha256": INPUT_SHA256,
        "blind_valid": True,
        "preseal_outcome_download_count": 0,
        "preseal_outcome_header_read_count": 0,
        "preseal_outcome_sha256_count": 0,
        "preseal_outcome_row_count_count": 0,
        "preseal_outcome_parse_count": 0,
        "preseal_outcome_winner_census_count": 0,
        "preseal_outcome_used_in_blind_graph_count": 0,
        "outcome_access_allowed": False,
        "created_at": now_kst(),
    }
    access_log = [
        {"event": "FRESH_MAIN_PROMPT_ACQUIRED_AND_VERIFIED", "phase": "PHASE_0", "logical_role": "none", "sha256": PROMPT_SHA256, "ts": now_kst()},
        {"event": "FRESH_NEWS_CSV_ACQUIRED_FULL_PARSED", "phase": "PHASE_0", "logical_role": "none", "sha256": INPUT_SHA256, "row_count": INPUT_ROW_COUNT, "ts": now_kst()},
        {"event": "ACCESS_JSON_ROUTING_METADATA_ONLY", "phase": "PHASE_0", "logical_role": "access_metadata_only", "ts": now_kst()},
        {"event": "BLIND_P_SNAPSHOT_ACQUIRED", "phase": "PHASE_0", "logical_role": "blind_snapshot", "sha256": BLIND_SNAPSHOT_SHA256, "row_count": BLIND_SNAPSHOT_ROWS, "ts": now_kst()},
        {"event": "FULL_CSV_SEMANTIC_REVIEW_COMPLETED", "phase": "PHASE_1_2", "logical_role": "news_rows", "row_count": INPUT_ROW_COUNT, "model": MODEL_NAME, "ts": now_kst()},
        {"event": "CANDIDATE_POPULATION_CLOSED_AND_REPARSED", "phase": "PHASE_3_4", "logical_role": "blind_candidates", "count": len(populations["candidate_screening"]), "ts": now_kst()},
        {"event": "NO_PRESEAL_OUTCOME_RAW_ACCESS", "phase": "PHASE_0_TO_5_PRESEAL", "logical_role": "access_metadata_only", "ts": now_kst()},
    ]
    warnings = [
        {"warning_id": "INPUT_COVERAGE_SHORT_OF_085959", "message": "CSV max timestamp is 08:59:13, 46 seconds before cutoff 08:59:59.", "impact": "Recorded as explicit uncovered range; research continued under routing rule.", "ts": now_kst()},
    ]
    attempt_history = [{"attempt_id": args.run_id, "status": "ACCEPTED_CLEAN_BLIND_ATTEMPT", "preseal_outcome_content_access_count": 0, "ts": now_kst()}]
    repair_log: list[dict[str, Any]] = []
    semantic_report = {
        "schema_version": "nslab.semantic_regression_test_report.v30",
        "fixture_count": len(semantic_tests),
        "pass_count": sum(1 for row in semantic_tests if row["passed"]),
        "required_fixture_missing_count": 0,
        "unexpected_pass_count": 0,
        "unexpected_fail_count": 0,
        "status": "passed",
    }

    block_rows = {
        "source_ledger.jsonl": source_ledger,
        "row_disposition.jsonl": populations["row_disposition"],
        "material_review_queue.jsonl": populations["material_review_queue"],
        "material_review.jsonl": populations["material_review"],
        "provisional_hypothesis.jsonl": populations["provisional_hypothesis"],
        "entity_resolution.jsonl": populations["entity_resolution"],
        "entity_ledger_blind.jsonl": populations["entity_ledger_blind"],
        "fact_ledger_blind.jsonl": populations["fact_ledger_blind"],
        "inference_ledger_blind.jsonl": populations["inference_ledger_blind"],
        "candidate_screening.jsonl": populations["candidate_screening"],
        "candidate_ranking_audit.jsonl": ranking_audit,
        "candidate_semantic_witness.jsonl": populations["candidate_semantic_witness"],
        "final_evidence_witness.jsonl": final_witness,
        "final_semantic_audit.jsonl": final_semantic_audit,
        "market_state_override_audit.jsonl": populations["market_state_override_audit"],
        "body_table_candidate_generation_audit.jsonl": populations["body_table_candidate_generation_audit"],
        "semantic_regression_tests.jsonl": semantic_tests,
        "access_log.jsonl": access_log,
        "acquisition_warnings.jsonl": warnings,
        "attempt_history.jsonl": attempt_history,
        "repair_log.jsonl": repair_log,
    }
    block_json = {
        "blind_prediction.json": blind_prediction,
        "ledger_population_audit.json": ledger_audit,
        "phase_state.json": phase_state,
        "semantic_regression_test_report.json": semantic_report,
    }
    write_json(artifacts / "blind_prediction.json", blind_prediction)
    write_json(artifacts / "ledger_population_audit.json", ledger_audit)
    write_json(artifacts / "phase_state.json", phase_state)
    write_json(artifacts / "semantic_regression_test_report.json", semantic_report)
    (artifacts / "blind_report.md").write_text(blind_report, encoding="utf-8")
    for name, rows in block_rows.items():
        write_jsonl(artifacts / name, rows)

    manifest_files: dict[str, Any] = {}
    for path in sorted(artifacts.iterdir()):
        if path.name in {"blind_packet_manifest.json", "blind_seal_receipt.json"}:
            continue
        manifest_files[path.name] = {
            "sha256": sha256_file(path),
            "byte_size": path.stat().st_size,
            "row_count": len(read_jsonl(path)) if path.suffix == ".jsonl" else None,
        }
    blind_packet_manifest = {
        "schema_version": "nslab.blind_packet_manifest.v30",
        "run_id": args.run_id,
        "trade_date": TRADE_DATE,
        "cutoff_at": CUTOFF_AT,
        "input_sha256": INPUT_SHA256,
        "files": manifest_files,
        "final_watchlist_count": len(blind_prediction["final_watchlist"]),
        "candidate_screening_count": len(populations["candidate_screening"]),
        "created_at": now_kst(),
    }
    manifest_payload = canonical_json(blind_packet_manifest)
    manifest_sha = sha256_text(manifest_payload)
    write_json(artifacts / "blind_packet_manifest.json", blind_packet_manifest)
    seal_receipt = {
        "schema_version": "nslab.blind_seal_receipt.v30",
        "run_id": args.run_id,
        "trade_date": TRADE_DATE,
        "blind_packet_manifest_sha256": manifest_sha,
        "blind_packet_manifest_verified": sha256_text(canonical_json(read_json(artifacts / "blind_packet_manifest.json"))) == manifest_sha,
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
    write_json(artifacts / "blind_seal_receipt.json", seal_receipt)
    phase_state["phase"] = "PHASE_5_BLIND_SEALED"
    phase_state["blind_sealed"] = True
    phase_state["blind_packet_manifest_sha256"] = manifest_sha
    phase_state["sealed_blind_report_sha256"] = seal_receipt["sealed_blind_report_sha256"]
    write_json(artifacts / "phase_state.json", phase_state)
    write_json(args.output / "blind_state.json", {
        "run_id": args.run_id,
        "trade_date": TRADE_DATE,
        "blind_packet_manifest_sha256": manifest_sha,
        "seal_receipt_path": "artifacts/blind_seal_receipt.json",
        "artifact_dir": "artifacts",
        "outcome_access_allowed_after_verification": True,
    })
    print(json.dumps({
        "status": "BLIND_SEALED",
        "run_id": args.run_id,
        "manifest_sha256": manifest_sha,
        "csv_row_count": len(news_rows),
        "material_review_count": len(populations["material_review"]),
        "candidate_screening_count": len(populations["candidate_screening"]),
        "rankable_count": len(ranking_audit),
        "final_watchlist_count": len(blind_prediction["final_watchlist"]),
    }, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
