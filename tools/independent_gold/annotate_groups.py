"""Run source-packeted, independent annotation in small item groups.

This module is intentionally isolated from the competition submission.  It
only consumes organizer records, :mod:`packetize`, and the independent rubric.
Each record/group result is checkpointed before a complete 24-cell row is
emitted, so interrupted 20k runs can resume without repeating valid calls.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import gzip
import hashlib
import json
import os
import pathlib
import random
import re
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Any, Iterable, Iterator, Mapping, Sequence

try:
    from tools.independent_gold import catalog_facts
    from tools.independent_gold import fact_context as source_fact_context
    from tools.independent_gold import law_context as supplied_law_context
    from tools.independent_gold import qualification_context as source_qualification_context
    from tools.independent_gold.packetize import (
        ABSENCE_ITEMS,
        ITEMS,
        packetize_record,
        render_packet,
        sha256_object,
        sha256_text,
    )
except ModuleNotFoundError:  # Direct ``python tools/.../annotate_groups.py``.
    import catalog_facts  # type: ignore[no-redef]
    import fact_context as source_fact_context  # type: ignore[no-redef]
    import law_context as supplied_law_context  # type: ignore[no-redef]
    import qualification_context as source_qualification_context  # type: ignore[no-redef]
    from packetize import (  # type: ignore[no-redef]
        ABSENCE_ITEMS,
        ITEMS,
        packetize_record,
        render_packet,
        sha256_object,
        sha256_text,
    )


ROOT = pathlib.Path(__file__).resolve().parents[2]
RUBRIC_PATH = pathlib.Path(__file__).with_name("rubric_v1.md")
SCHEMA_VERSION = "dacon.independent.group_annotation.v1"
GROUP_SCHEMA_VERSION = "dacon.independent.group_result.v2"

# These are the smallest budgets on the packetizer's audited grid that retain
# all non-empty positive evidence in organizer dev for the respective group.
GROUPS: tuple[tuple[str, tuple[str, ...], int], ...] = (
    ("v1-4", ("v1", "v2", "v3", "v4"), 6_000),
    ("v5-8", ("v5", "v6", "v7", "v8"), 4_000),
    ("v9", ("v9",), 4_000),
    ("v10-13", ("v10", "v11", "v12", "v13"), 4_000),
    ("v14-19", ("v14", "v15", "v16", "v17", "v18", "v19"), 4_000),
    ("v20", ("v20",), 4_000),
    ("v21-23", ("v21", "v22", "v23"), 4_000),
    ("v24", ("v24",), 6_000),
)
GROUP_BY_NAME = {name: (items, budget) for name, items, budget in GROUPS}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _section(markdown: str, heading_pattern: str, *, level: int) -> str:
    """Return one Markdown section through the next same/higher heading."""

    match = re.search(heading_pattern, markdown, flags=re.MULTILINE)
    if match is None:
        raise ValueError(f"rubric section not found: {heading_pattern}")
    later_heading = re.search(rf"^#{{1,{level}}}\s+", markdown[match.end() :], flags=re.MULTILINE)
    end = match.end() + later_heading.start() if later_heading else len(markdown)
    return markdown[match.start() : end].strip()


def extract_group_rubric(rubric: str, target_items: Sequence[str]) -> str:
    """Create a small prompt from common rules and only the requested items."""

    requested = tuple(target_items)
    if not requested or len(set(requested)) != len(requested):
        raise ValueError("target_items must be non-empty and unique")
    unknown = set(requested) - set(ITEMS)
    if unknown:
        raise ValueError(f"unknown target_items: {sorted(unknown)}")
    common = _section(rubric, r"^##\s+공통 원칙\s*$", level=2)
    item_sections = [
        _section(rubric, rf"^###\s+{re.escape(item)}(?:\s+.*)?$", level=3)
        for item in requested
    ]
    order = ",".join(requested)
    count = len(requested)
    output_contract = (
        "## 이 요청의 출력 계약\n\n"
        f"대상 순서는 정확히 {order}이다. JSON 객체 하나만 출력한다. "
        f"labels는 이 순서의 정확히 {count}글자(0/1/U), confidence는 정확히 "
        f"{count}글자(H/M/L)여야 한다. evidence는 양성인 비부재탐지 항목만 "
        "키로 두고, 제공된 SOURCE 안에 글자 그대로 존재하는 500자 이하의 가장 "
        "짧고 충분한 인용을 값으로 둔다. 다른 항목을 판정하거나 설명문을 덧붙이지 않는다.\n\n"
        '{"labels":"...","confidence":"...","evidence":{}}'
    )
    return "\n\n".join((common, *item_sections, output_contract))


def build_group_messages(
    record: Mapping[str, Any],
    packet: Mapping[str, Any],
    system_prompt: str,
    group_name: str,
    fact_context_payload: Mapping[str, Any] | None = None,
    qualification_context_payload: Mapping[str, Any] | None = None,
    law_context_payload: Mapping[str, Any] | None = None,
) -> list[dict[str, str]]:
    meta = canonical_json(packet.get("meta") or {})
    completeness = canonical_json(
        {
            "input_completeness": packet.get("input_completeness") or {},
            "dropped_doc_counts": packet.get("dropped_doc_counts") or {},
            "full_source_visible": bool(packet.get("full_source_visible")),
            "absence_warning": packet.get("absence_warning"),
        }
    )
    fact_block = ""
    if fact_context_payload is not None:
        rendered_facts = source_fact_context.render_fact_context(fact_context_payload)
        fact_block = (
            "\n\nINDEPENDENT_FACT_CONTEXT (판정값이 아닌, 원문/제공 법령·카탈로그에서 "
            "좌표 검증된 사실 후보):\n"
            f"FACT_CONTEXT_SHA256: {fact_context_payload['context_sha256']}\n"
            f"{rendered_facts}\n"
            "이 컨텍스트는 라벨을 포함하지 않는다. source_segments의 인용과 SOURCE를 "
            "대조해 적용 범위·논리 연결을 직접 판정하라. extractor ambiguity나 omission은 "
            "그 불확실성이 대상 항목의 결론을 바꿀 수 있을 때만 U 사유다. "
            "context_char_budget_truncated는 조달금액이 잘렸다는 뜻이 아니라 이 검색 "
            "컨텍스트가 문자 한도로 일부 후보 사실을 생략했다는 뜻이다."
        )
    qualification_block = ""
    if qualification_context_payload is not None:
        rendered_qualification = source_qualification_context.render_qualification_context(
            qualification_context_payload
        )
        qualification_block = (
            "\n\nQUALIFICATION_FACT_CONTEXT (v10-v18/v20 판정값이 아닌, 공고 원문과 "
            "제공 카탈로그에서 좌표 검증된 자격·물품·소프트웨어 관계 후보):\n"
            f"QUALIFICATION_CONTEXT_SHA256: "
            f"{qualification_context_payload['context_sha256']}\n"
            f"{rendered_qualification}\n"
            "이 컨텍스트는 라벨을 포함하지 않는다. facts와 relation_summaries는 검색 "
            "보조물일 뿐이므로 source_segments의 원문, 적용 시점, 입찰 대상과의 결합을 "
            "직접 확인하라. omissions나 budget_truncated는 검색 컨텍스트에서 후보가 "
            "생략됐다는 뜻이며 원문 사실의 부재가 아니다. 생략 가능성이 대상 결론을 "
            "바꿀 수 있을 때만 U로 남겨라."
        )
    law_block = ""
    if law_context_payload is not None:
        rendered_law = supplied_law_context.render_law_context(law_context_payload)
        law_block = (
            "\n\nSUPPLIED_LAW_CONTEXT (대회가 제공한 법령 스냅샷의 원문 인용):\n"
            f"LAW_CONTEXT_SHA256: {law_context_payload['context_sha256']}\n"
            f"{rendered_law}\n"
            "각 인용은 source 경로·문자 좌표·SHA-256으로 검증되어 있다. 공고의 "
            "계약법, 업무종류, 금액, 절차와 맞는 인용만 적용하고 법 적용범위를 "
            "추측하지 말라."
        )
    user = (
        f"공고ID: {record['id']}\n"
        f"판정그룹: {group_name}\n"
        f"대상항목: {','.join(packet['target_items'])}\n"
        f"META: {meta}\n"
        f"COMPLETENESS: {completeness}\n"
        f"SOURCE_PACKET_SHA256: {sha256_object(packet)}\n"
        "아래 SOURCE, META, 그리고 제공 데이터에서 추출해 좌표 검증한 CONTEXT만 "
        "사용하라. 패킷과 사실 컨텍스트는 검색 보조물이며, 잘린 입력은 "
        "문구의 부재를 스스로 증명하지 않는다. 불충분하면 U로 남긴다. 운영 시스템의 "
        "예측이나 응답은 입력에 없으며 추측해서도 안 된다.\n\n"
        f"{render_packet(packet)}"
        f"{fact_block}"
        f"{qualification_block}"
        f"{law_block}"
    )
    return [{"role": "system", "content": system_prompt}, {"role": "user", "content": user}]


def group_request_payload(
    model: str,
    messages: Sequence[Mapping[str, str]],
    target_items: Sequence[str],
    *,
    seed: int = 17,
    max_tokens: int = 768,
) -> dict[str, Any]:
    count = len(target_items)
    if count < 1:
        raise ValueError("target_items must not be empty")
    item_names = list(target_items)
    schema = {
        "type": "object",
        "additionalProperties": False,
        "required": ["labels", "confidence", "evidence"],
        "properties": {
            "labels": {"type": "string", "pattern": rf"^[01U]{{{count}}}$"},
            "confidence": {"type": "string", "pattern": rf"^[HML]{{{count}}}$"},
            "evidence": {
                "type": "object",
                "propertyNames": {"enum": item_names},
                "additionalProperties": {"type": "string", "maxLength": 500},
            },
        },
    }
    return {
        "model": model,
        "messages": list(messages),
        "temperature": 0,
        "top_p": 1,
        "seed": seed,
        "max_tokens": max_tokens,
        "response_format": {
            "type": "json_schema",
            "json_schema": {"name": "compact_group_annotation", "strict": True, "schema": schema},
        },
    }


def post_json(
    endpoint: str,
    payload: Mapping[str, Any],
    timeout: float,
    api_key: str | None = None,
) -> dict[str, Any]:
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    request = urllib.request.Request(
        endpoint,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        value = json.loads(response.read().decode("utf-8"))
    if not isinstance(value, dict):
        raise ValueError("endpoint response must be a JSON object")
    return value


def parse_json_object(text: str) -> dict[str, Any]:
    value = text.strip()
    if value.startswith("```"):
        value = value.split("\n", 1)[-1]
        value = value.rsplit("```", 1)[0].strip()
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        start, end = value.find("{"), value.rfind("}")
        if start < 0 or end <= start:
            raise
        parsed = json.loads(value[start : end + 1])
    if not isinstance(parsed, dict):
        raise ValueError("model output must be a JSON object")
    return parsed


def response_content(response: Mapping[str, Any]) -> str:
    content = response["choices"][0]["message"]["content"]  # type: ignore[index]
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [part.get("text", "") for part in content if isinstance(part, dict)]
        return "".join(str(part) for part in parts)
    raise ValueError("response content is not text")


def normalize_group_decision(
    raw: Mapping[str, Any], target_items: Sequence[str]
) -> dict[str, dict[str, Any]]:
    items = tuple(target_items)
    unexpected_fields = set(raw) - {"labels", "confidence", "evidence"}
    missing_fields = {"labels", "confidence", "evidence"} - set(raw)
    if unexpected_fields or missing_fields:
        raise ValueError(
            "model output fields must be exactly labels/confidence/evidence; "
            f"missing={sorted(missing_fields)}, unexpected={sorted(unexpected_fields)}"
        )
    labels = raw.get("labels")
    confidence = raw.get("confidence")
    evidence = raw.get("evidence")
    if not isinstance(labels, str) or len(labels) != len(items) or any(ch not in "01U" for ch in labels):
        raise ValueError(f"labels must be exactly {len(items)} characters from 0/1/U")
    if not isinstance(confidence, str) or len(confidence) != len(items) or any(
        ch not in "HML" for ch in confidence
    ):
        raise ValueError(f"confidence must be exactly {len(items)} characters from H/M/L")
    if not isinstance(evidence, dict):
        raise ValueError("evidence must be an object")
    unexpected = set(evidence) - set(items)
    if unexpected:
        raise ValueError(f"evidence has out-of-group keys: {sorted(unexpected)}")
    confidence_names = {"H": "high", "M": "medium", "L": "low"}
    decisions: dict[str, dict[str, Any]] = {}
    for index, item in enumerate(items):
        label_char = labels[index]
        label: int | str = int(label_char) if label_char in "01" else "U"
        quote = evidence.get(item, "") or ""
        if not isinstance(quote, str):
            raise ValueError(f"evidence for {item} must be a string")
        quote = unicodedata.normalize("NFC", quote.strip())
        if len(quote) > 500:
            raise ValueError(f"evidence for {item} exceeds 500 characters")
        # Evidence is a positive witness only.  Silently erasing a model's
        # contradictory witness (for a 0/U or an absence item) would turn an
        # internally inconsistent response into an apparently valid result.
        if item in evidence and (label != 1 or item in ABSENCE_ITEMS):
            raise ValueError(
                f"evidence key for {item} is forbidden for label={label!r} "
                "or an absence-detection item"
            )
        if label == 1 and item not in ABSENCE_ITEMS and not quote:
            raise ValueError(f"positive {item} requires non-empty evidence")
        if label != 1 or item in ABSENCE_ITEMS:
            quote = ""
        decisions[item] = {
            "label": label,
            "confidence": confidence_names[confidence[index]],
            "evidence": quote,
            "evidence_locations": [],
        }
    return decisions


def validate_evidence(
    decisions: Mapping[str, dict[str, Any]],
    packet: Mapping[str, Any],
    fact_context_payload: Mapping[str, Any] | None = None,
    qualification_context_payload: Mapping[str, Any] | None = None,
) -> list[str]:
    """Attach exact organizer-source coordinates and return hard errors.

    Positive evidence must have been visible either in the source packet or in
    a hash-checked fact-context source segment.  This keeps the quotation gate
    strict while allowing the independent extractors to recover a decisive
    clause that the bounded packet ranker did not select.
    """

    errors: list[str] = []
    visible_segments: list[dict[str, Any]] = []
    for segment in packet.get("segments") or []:
        visible_segments.append(
            {
                "origin": "source_packet",
                "segment_id": segment["segment_id"],
                "doc_index": int(segment["doc_index"]),
                "doc_id": segment["doc_id"],
                "source_doc_sha256": segment["source_doc_sha256"],
                "start": int(segment["start"]),
                "text": str(segment.get("text") or ""),
            }
        )
    if fact_context_payload is not None:
        for segment in fact_context_payload.get("source_segments") or []:
            visible_segments.append(
                {
                    "origin": "fact_context",
                    "segment_id": segment["segment_id"],
                    "doc_index": int(segment["doc_index"]),
                    "doc_id": segment["doc_id"],
                    "source_doc_sha256": segment["source_doc_sha256"],
                    "start": int(segment["start"]),
                    "text": str(segment.get("quote") or ""),
                }
            )
    if qualification_context_payload is not None:
        for segment in qualification_context_payload.get("source_segments") or []:
            visible_segments.append(
                {
                    "origin": "qualification_context",
                    "segment_id": segment["segment_id"],
                    "doc_index": int(segment["doc_index"]),
                    "doc_id": segment["doc_id"],
                    "source_doc_sha256": segment["source_doc_sha256"],
                    "start": int(segment["start"]),
                    "text": str(segment.get("quote") or ""),
                }
            )
    for item, cell in decisions.items():
        quote = str(cell.get("evidence") or "")
        locations: list[dict[str, Any]] = []
        if quote:
            seen_locations: set[tuple[int, int, int]] = set()
            for segment in visible_segments:
                text = segment["text"]
                offset = text.find(quote)
                while offset >= 0:
                    source_start = int(segment["start"]) + offset
                    key = (int(segment["doc_index"]), source_start, source_start + len(quote))
                    if key not in seen_locations:
                        locations.append(
                            {
                                "origin": segment["origin"],
                                "segment_id": segment["segment_id"],
                                "doc_index": int(segment["doc_index"]),
                                "doc_id": segment["doc_id"],
                                "source_doc_sha256": segment["source_doc_sha256"],
                                "start": source_start,
                                "end": source_start + len(quote),
                                "segment_start": offset,
                                "segment_end": offset + len(quote),
                                "evidence_sha256": sha256_text(quote),
                            }
                        )
                        seen_locations.add(key)
                    offset = text.find(quote, offset + 1)
            if not locations:
                errors.append(f"{item}: evidence_not_verbatim_in_packet")
        if cell.get("label") == 1 and item not in ABSENCE_ITEMS and not quote:
            errors.append(f"{item}: positive_without_evidence")
        cell["evidence_locations"] = locations
    return errors


def endpoint_origin(endpoint: str) -> str:
    parsed = urllib.parse.urlsplit(endpoint)
    # Drop query/fragment and any userinfo; those can contain credentials.
    host = parsed.hostname or ""
    if parsed.port:
        host = f"{host}:{parsed.port}"
    return urllib.parse.urlunsplit((parsed.scheme, host, parsed.path, "", ""))


def make_run_key(
    *,
    rubric_sha256: str,
    endpoint: str,
    model: str,
    model_revision: str,
    seed: int,
    max_tokens: int,
    use_fact_context: bool = True,
    fact_context_max_chars: int = source_fact_context.DEFAULT_MAX_CHARS,
    use_qualification_context: bool = True,
    qualification_context_max_chars: int = source_qualification_context.DEFAULT_MAX_CHARS,
    qualification_catalog_sha256: str | None = None,
    use_law_context: bool = True,
    law_context_max_chars: int = supplied_law_context.DEFAULT_MAX_CHARS,
) -> str:
    if use_qualification_context and qualification_catalog_sha256 is None:
        qualification_catalog_sha256 = (
            source_qualification_context.qualification_facts.CatalogReference.load().sha256
        )
    return sha256_object(
        {
            "rubric_sha256": rubric_sha256,
            "endpoint_origin": endpoint_origin(endpoint),
            "model": model,
            "model_revision": model_revision,
            "seed": seed,
            "max_tokens": max_tokens,
            "fact_context": {
                "enabled": use_fact_context,
                "schema_version": source_fact_context.SCHEMA_VERSION,
                "max_chars": fact_context_max_chars,
            },
            "qualification_context": {
                "enabled": use_qualification_context,
                "schema_version": source_qualification_context.SCHEMA_VERSION,
                "extractor_schema_version": (
                    source_qualification_context.qualification_facts.SCHEMA_VERSION
                ),
                "max_chars": qualification_context_max_chars,
                "catalog_sha256": (
                    qualification_catalog_sha256 if use_qualification_context else None
                ),
            },
            "law_context": {
                "enabled": use_law_context,
                "schema_version": supplied_law_context.SCHEMA_VERSION,
                "max_chars": law_context_max_chars,
                "reference_manifest_sha256": (
                    supplied_law_context.reference_manifest_sha256()
                    if use_law_context
                    else None
                ),
            },
            "groups": GROUPS,
        }
    )


def _model_provenance(
    response: Mapping[str, Any],
    *,
    requested_model: str,
    model_revision: str,
    endpoint: str,
) -> dict[str, Any]:
    choice = (response.get("choices") or [{}])[0]
    return {
        "endpoint_kind": "openai_compatible_chat_completions",
        "endpoint_origin": endpoint_origin(endpoint),
        "requested_model": requested_model,
        "response_model": response.get("model"),
        "declared_model_revision": model_revision,
        "response_id": response.get("id"),
        "response_created": response.get("created"),
        "system_fingerprint": response.get("system_fingerprint"),
        "finish_reason": choice.get("finish_reason") if isinstance(choice, dict) else None,
    }


def annotate_group(
    record: Mapping[str, Any],
    *,
    group_name: str,
    rubric: str,
    endpoint: str,
    model: str,
    model_revision: str = "unspecified",
    timeout: float = 900,
    retries: int = 3,
    seed: int = 17,
    max_tokens: int = 768,
    api_key: str | None = None,
    run_key: str | None = None,
    prepared_facts: Mapping[str, Any] | None = None,
    catalog_index: catalog_facts.CatalogIndex | None = None,
    use_fact_context: bool = True,
    fact_context_max_chars: int = source_fact_context.DEFAULT_MAX_CHARS,
    prepared_qualification: Mapping[str, Any] | None = None,
    qualification_catalog: Any | None = None,
    use_qualification_context: bool = True,
    qualification_context_max_chars: int = source_qualification_context.DEFAULT_MAX_CHARS,
    use_law_context: bool = True,
    law_context_max_chars: int = supplied_law_context.DEFAULT_MAX_CHARS,
) -> dict[str, Any]:
    if group_name not in GROUP_BY_NAME:
        raise ValueError(f"unknown group: {group_name}")
    target_items, budget = GROUP_BY_NAME[group_name]
    packet = packetize_record(record, max_chars=budget, target_items=target_items)
    packet_hash = sha256_object(packet)
    prompt = extract_group_rubric(rubric, target_items)
    fact_payload: Mapping[str, Any] | None = None
    if use_fact_context:
        fact_payload = source_fact_context.build_fact_context(
            record,
            group_name,
            prepared=prepared_facts,
            catalog_index=catalog_index,
            max_chars=fact_context_max_chars,
        )
        fact_errors = source_fact_context.validate_fact_context(
            record, fact_payload, catalog_index=catalog_index
        )
        if fact_errors:
            raise ValueError(f"invalid fact context: {fact_errors[:3]}")
    qualification_payload: Mapping[str, Any] | None = None
    qualification_relevant = group_name in source_qualification_context.GROUP_FAMILIES
    if use_qualification_context and (
        qualification_relevant or run_key is None
    ) and qualification_catalog is None:
        qualification_catalog = (
            source_qualification_context.qualification_facts.CatalogReference.load()
        )
    qualification_catalog_hash = (
        qualification_catalog.sha256
        if use_qualification_context and qualification_catalog is not None
        else None
    )
    if use_qualification_context and qualification_relevant:
        if prepared_qualification is not None:
            prepared_catalog_hash = (
                ((prepared_qualification.get("reference_sources") or {}).get(
                    "competitive_product_catalog"
                ) or {}).get("sha256")
            )
            if prepared_catalog_hash != qualification_catalog_hash:
                raise ValueError(
                    "prepared qualification facts use a different catalog hash"
                )
        qualification_payload = source_qualification_context.build_qualification_context(
            record,
            group_name,
            prepared=prepared_qualification,
            catalog=qualification_catalog,
            max_chars=qualification_context_max_chars,
        )
        if qualification_payload is None:
            raise ValueError(f"missing qualification context for relevant group: {group_name}")
        qualification_errors = source_qualification_context.validate_qualification_context(
            record, qualification_payload
        )
        if qualification_errors:
            raise ValueError(
                f"invalid qualification context: {qualification_errors[:3]}"
            )
    law_payload: Mapping[str, Any] | None = None
    if use_law_context:
        law_payload = supplied_law_context.build_law_context(
            group_name, max_chars=law_context_max_chars
        )
        law_errors = supplied_law_context.validate_law_context(law_payload)
        if law_errors:
            raise ValueError(f"invalid supplied law context: {law_errors[:3]}")
    messages = build_group_messages(
        record,
        packet,
        prompt,
        group_name,
        fact_context_payload=fact_payload,
        qualification_context_payload=qualification_payload,
        law_context_payload=law_payload,
    )
    payload = group_request_payload(
        model, messages, target_items, seed=seed, max_tokens=max_tokens
    )
    request_hash = sha256_text(canonical_json(payload))
    rubric_hash = sha256_text(rubric)
    effective_run_key = run_key or make_run_key(
        rubric_sha256=rubric_hash,
        endpoint=endpoint,
        model=model,
        model_revision=model_revision,
        seed=seed,
        max_tokens=max_tokens,
        use_fact_context=use_fact_context,
        fact_context_max_chars=fact_context_max_chars,
        use_qualification_context=use_qualification_context,
        qualification_context_max_chars=qualification_context_max_chars,
        qualification_catalog_sha256=(
            qualification_catalog_hash
        ),
        use_law_context=use_law_context,
        law_context_max_chars=law_context_max_chars,
    )
    started = time.time()
    last_error = ""
    last_raw_hash: str | None = None
    last_content_hash: str | None = None
    last_content: str | None = None
    last_provenance: dict[str, Any] | None = None
    last_usage: Mapping[str, Any] = {}
    for attempt in range(1, retries + 1):
        try:
            response = post_json(endpoint, payload, timeout, api_key)
            raw_response = canonical_json(response)
            content = response_content(response)
            last_content = content
            last_raw_hash = sha256_text(raw_response)
            last_content_hash = sha256_text(content)
            last_provenance = _model_provenance(
                response,
                requested_model=model,
                model_revision=model_revision,
                endpoint=endpoint,
            )
            last_usage = response.get("usage") or {}
            parsed = parse_json_object(content)
            decisions = normalize_group_decision(parsed, target_items)
            evidence_errors = validate_evidence(
                decisions, packet, fact_payload, qualification_payload
            )
            if evidence_errors:
                raise ValueError("; ".join(evidence_errors))
            return {
                "schema_version": GROUP_SCHEMA_VERSION,
                "id": str(record["id"]),
                "group": group_name,
                "target_items": list(target_items),
                "status": "ok",
                "run_key": effective_run_key,
                "source_sha256": packet["source_sha256"],
                "packet_sha256": packet_hash,
                "fact_context_sha256": (
                    fact_payload.get("context_sha256") if fact_payload is not None else None
                ),
                "fact_context_schema_version": (
                    fact_payload.get("schema_version") if fact_payload is not None else None
                ),
                "fact_context_bounds": (
                    fact_payload.get("bounds") if fact_payload is not None else None
                ),
                "qualification_context_sha256": (
                    qualification_payload.get("context_sha256")
                    if qualification_payload is not None
                    else None
                ),
                "qualification_context_schema_version": (
                    qualification_payload.get("schema_version")
                    if qualification_payload is not None
                    else None
                ),
                "qualification_context_bounds": (
                    qualification_payload.get("bounds")
                    if qualification_payload is not None
                    else None
                ),
                "qualification_catalog_sha256": qualification_catalog_hash,
                "law_context_sha256": (
                    law_payload.get("context_sha256") if law_payload is not None else None
                ),
                "law_context_schema_version": (
                    law_payload.get("schema_version") if law_payload is not None else None
                ),
                "packet": {
                    "schema_version": packet["schema_version"],
                    "max_chars": packet["max_chars"],
                    "selected_chars": packet["selected_chars"],
                    "source_chars": packet["source_chars"],
                    "full_source_visible": packet["full_source_visible"],
                    "absence_warning": packet["absence_warning"],
                },
                "rubric_sha256": rubric_hash,
                "system_prompt_sha256": sha256_text(prompt),
                "request_sha256": request_hash,
                "raw_response_sha256": last_raw_hash,
                "content_sha256": last_content_hash,
                # The hash proves integrity; the content is retained so a
                # reviewer can inspect why a decision was made or why source
                # evidence validation accepted it.  A label ledger without
                # the actual model vote is not independently auditable.
                "raw_content": last_content,
                "model_provenance": last_provenance,
                "usage": last_usage,
                "attempt": attempt,
                "completed_utc": utc_now(),
                "elapsed_seconds": round(time.time() - started, 3),
                "decisions": decisions,
                "evidence_errors": [],
            }
        except (OSError, KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError) as exc:
            last_error = f"{type(exc).__name__}: {exc}"
            if attempt < retries:
                time.sleep(min(2 ** (attempt - 1) + random.random(), 15))
    return {
        "schema_version": GROUP_SCHEMA_VERSION,
        "id": str(record["id"]),
        "group": group_name,
        "target_items": list(target_items),
        "status": "error",
        "run_key": effective_run_key,
        "source_sha256": packet["source_sha256"],
        "packet_sha256": packet_hash,
        "fact_context_sha256": (
            fact_payload.get("context_sha256") if fact_payload is not None else None
        ),
        "fact_context_schema_version": (
            fact_payload.get("schema_version") if fact_payload is not None else None
        ),
        "qualification_context_sha256": (
            qualification_payload.get("context_sha256")
            if qualification_payload is not None
            else None
        ),
        "qualification_context_schema_version": (
            qualification_payload.get("schema_version")
            if qualification_payload is not None
            else None
        ),
        "qualification_context_bounds": (
            qualification_payload.get("bounds")
            if qualification_payload is not None
            else None
        ),
        "qualification_catalog_sha256": qualification_catalog_hash,
        "law_context_sha256": (
            law_payload.get("context_sha256") if law_payload is not None else None
        ),
        "law_context_schema_version": (
            law_payload.get("schema_version") if law_payload is not None else None
        ),
        "rubric_sha256": rubric_hash,
        "system_prompt_sha256": sha256_text(prompt),
        "request_sha256": request_hash,
        "raw_response_sha256": last_raw_hash,
        "content_sha256": last_content_hash,
        "raw_content": last_content,
        "model_provenance": last_provenance,
        "usage": last_usage,
        "attempt": retries,
        "completed_utc": utc_now(),
        "elapsed_seconds": round(time.time() - started, 3),
        "error": last_error,
    }


def merge_group_results(
    record: Mapping[str, Any],
    results: Mapping[str, Mapping[str, Any]],
    *,
    run_key: str,
) -> dict[str, Any]:
    missing = [name for name, _, _ in GROUPS if name not in results]
    if missing:
        raise ValueError(f"missing groups: {missing}")
    source_hash = sha256_object(record)
    decisions: dict[str, Any] = {}
    receipts: dict[str, Any] = {}
    provenance: list[dict[str, Any]] = []
    for group_name, target_items, _ in GROUPS:
        result = results[group_name]
        if result.get("status") != "ok":
            raise ValueError(f"group {group_name} is not ok")
        if result.get("run_key") != run_key:
            raise ValueError(f"group {group_name} has a different run key")
        if result.get("source_sha256") != source_hash:
            raise ValueError(f"group {group_name} has a different source hash")
        group_decisions = result.get("decisions") or {}
        if tuple(group_decisions) != target_items:
            raise ValueError(f"group {group_name} has wrong decision order or membership")
        for item in target_items:
            if item in decisions:
                raise ValueError(f"duplicate decision for {item}")
            cell = dict(group_decisions[item])
            cell["group"] = group_name
            decisions[item] = cell
        receipt = {
            key: result.get(key)
            for key in (
                "packet_sha256",
                "fact_context_sha256",
                "fact_context_schema_version",
                "fact_context_bounds",
                "qualification_context_sha256",
                "qualification_context_schema_version",
                "qualification_context_bounds",
                "qualification_catalog_sha256",
                "law_context_sha256",
                "law_context_schema_version",
                "rubric_sha256",
                "system_prompt_sha256",
                "request_sha256",
                "raw_response_sha256",
                "content_sha256",
                "attempt",
                "completed_utc",
                "elapsed_seconds",
                "usage",
            )
        }
        receipt["model_provenance"] = result.get("model_provenance")
        receipts[group_name] = receipt
        if isinstance(result.get("model_provenance"), dict):
            provenance.append(dict(result["model_provenance"]))
    if tuple(decisions) != ITEMS:
        raise ValueError("merged decisions are not exactly v1..v24")
    return {
        "schema_version": SCHEMA_VERSION,
        "id": str(record["id"]),
        "status": "ok",
        "run_key": run_key,
        "source_sha256": source_hash,
        "completed_utc": utc_now(),
        "model_provenance": provenance,
        "group_receipts": receipts,
        "decisions": decisions,
    }


def read_records(path: pathlib.Path) -> Iterator[dict[str, Any]]:
    if path.suffix.lower() == ".gz":
        handle_context = gzip.open(path, mode="rt", encoding="utf-8")
    else:
        handle_context = path.open(mode="rt", encoding="utf-8")
    with handle_context as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            row = json.loads(line)
            if not isinstance(row, dict) or not row.get("id"):
                raise ValueError(f"{path}:{line_number}: invalid record")
            yield row


def _read_jsonl(path: pathlib.Path) -> Iterator[dict[str, Any]]:
    if not path.exists():
        return
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict):
                yield row


def completed_annotation_sources(path: pathlib.Path, run_key: str) -> dict[str, str]:
    completed: dict[str, str] = {}
    for row in _read_jsonl(path):
        if (
            row.get("schema_version") == SCHEMA_VERSION
            and row.get("status") == "ok"
            and row.get("run_key") == run_key
            and row.get("id")
            and row.get("source_sha256")
        ):
            completed[str(row["id"])] = str(row["source_sha256"])
    return completed


def load_group_results(
    path: pathlib.Path,
    run_key: str,
    *,
    skip_ids: set[str] | None = None,
) -> dict[tuple[str, str], dict[str, Any]]:
    results: dict[tuple[str, str], dict[str, Any]] = {}
    excluded = skip_ids or set()
    for row in _read_jsonl(path):
        record_id = str(row.get("id") or "")
        group_name = str(row.get("group") or "")
        if record_id in excluded:
            continue
        if (
            row.get("schema_version") == GROUP_SCHEMA_VERSION
            and row.get("status") == "ok"
            and row.get("run_key") == run_key
            and group_name in GROUP_BY_NAME
        ):
            results[(record_id, group_name)] = row
    return results


def _batched(values: Iterable[dict[str, Any]], size: int) -> Iterator[list[dict[str, Any]]]:
    batch: list[dict[str, Any]] = []
    for value in values:
        batch.append(value)
        if len(batch) == size:
            yield batch
            batch = []
    if batch:
        yield batch


def run(args: argparse.Namespace) -> dict[str, int]:
    rubric = args.rubric.read_text(encoding="utf-8")
    rubric_hash = sha256_text(rubric)
    selected_group_names = tuple(args.groups or (name for name, _, _ in GROUPS))
    qualification_catalog = (
        source_qualification_context.qualification_facts.CatalogReference.load()
        if not args.no_qualification_context
        else None
    )
    run_key = make_run_key(
        rubric_sha256=rubric_hash,
        endpoint=args.endpoint,
        model=args.model,
        model_revision=args.model_revision,
        seed=args.seed,
        max_tokens=args.max_tokens,
        use_fact_context=not args.no_fact_context,
        fact_context_max_chars=args.fact_context_max_chars,
        use_qualification_context=not args.no_qualification_context,
        qualification_context_max_chars=args.qualification_context_max_chars,
        qualification_catalog_sha256=(
            qualification_catalog.sha256 if qualification_catalog is not None else None
        ),
        use_law_context=not args.no_law_context,
        law_context_max_chars=args.law_context_max_chars,
    )
    group_output = args.group_output or args.output.with_name(args.output.name + ".groups.jsonl")
    if group_output.resolve() == args.output.resolve():
        raise ValueError("output and group-output must be different files")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    group_output.parent.mkdir(parents=True, exist_ok=True)
    completed = completed_annotation_sources(args.output, run_key)
    available = load_group_results(group_output, run_key, skip_ids=set(completed))
    wanted = set(args.ids or [])
    seen: set[str] = set()
    selected = 0

    def selected_records() -> Iterator[dict[str, Any]]:
        nonlocal selected
        for record in read_records(args.input):
            record_id = str(record["id"])
            if record_id in seen:
                raise ValueError(f"duplicate input id: {record_id}")
            seen.add(record_id)
            if wanted and record_id not in wanted:
                continue
            if args.limit is not None and selected >= args.limit:
                break
            selected += 1
            if completed.get(record_id) == sha256_object(record):
                continue
            yield record

    stats = {"selected_records": 0, "skipped_records": 0, "group_calls": 0, "group_errors": 0, "merged_records": 0}
    api_key = os.environ.get(args.api_key_env) if args.api_key_env else None
    catalog_index = (
        catalog_facts.CatalogIndex.load() if not args.no_fact_context else None
    )
    with group_output.open("a", encoding="utf-8", newline="\n") as group_handle, args.output.open(
        "a", encoding="utf-8", newline="\n"
    ) as output_handle:
        for batch in _batched(selected_records(), args.batch_records):
            stats["selected_records"] += len(batch)
            prepared_by_id = (
                {
                    str(record["id"]): source_fact_context.prepare_fact_inputs(
                        record, catalog_index=catalog_index
                    )
                    for record in batch
                }
                if not args.no_fact_context
                else {}
            )
            prepared_qualification_by_id = (
                {
                    str(record["id"]): source_qualification_context.prepare_qualification_input(
                        record, catalog=qualification_catalog
                    )
                    for record in batch
                }
                if not args.no_qualification_context
                and any(
                    name in source_qualification_context.GROUP_FAMILIES
                    for name in selected_group_names
                )
                else {}
            )
            tasks: list[tuple[dict[str, Any], str]] = []
            for record in batch:
                record_id = str(record["id"])
                if completed.get(record_id) == sha256_object(record):
                    stats["skipped_records"] += 1
                    continue
                for group_name in selected_group_names:
                    prior = available.get((record_id, group_name))
                    if prior and prior.get("source_sha256") == sha256_object(record):
                        continue
                    tasks.append((record, group_name))

            def invoke(task: tuple[dict[str, Any], str]) -> dict[str, Any]:
                record, group_name = task
                return annotate_group(
                    record,
                    group_name=group_name,
                    rubric=rubric,
                    endpoint=args.endpoint,
                    model=args.model,
                    model_revision=args.model_revision,
                    timeout=args.timeout,
                    retries=args.retries,
                    seed=args.seed,
                    max_tokens=args.max_tokens,
                    api_key=api_key,
                    run_key=run_key,
                    prepared_facts=prepared_by_id.get(str(record["id"])),
                    catalog_index=catalog_index,
                    use_fact_context=not args.no_fact_context,
                    fact_context_max_chars=args.fact_context_max_chars,
                    prepared_qualification=prepared_qualification_by_id.get(
                        str(record["id"])
                    )
                    if group_name in source_qualification_context.GROUP_FAMILIES
                    else None,
                    qualification_catalog=qualification_catalog,
                    use_qualification_context=not args.no_qualification_context,
                    qualification_context_max_chars=args.qualification_context_max_chars,
                    use_law_context=not args.no_law_context,
                    law_context_max_chars=args.law_context_max_chars,
                )

            if tasks:
                if args.workers == 1:
                    iterator: Iterable[dict[str, Any]] = map(invoke, tasks)
                    executor = None
                else:
                    executor = concurrent.futures.ThreadPoolExecutor(max_workers=args.workers)
                    iterator = executor.map(invoke, tasks)
                try:
                    for result in iterator:
                        stats["group_calls"] += 1
                        group_handle.write(canonical_json(result) + "\n")
                        group_handle.flush()
                        if result["status"] == "ok":
                            available[(str(result["id"]), str(result["group"]))] = result
                        else:
                            stats["group_errors"] += 1
                        print(
                            f"[{stats['group_calls']}] {result['id']} {result['group']} {result['status']}",
                            flush=True,
                        )
                finally:
                    if executor is not None:
                        executor.shutdown(wait=True)

            for record in batch:
                record_id = str(record["id"])
                rows = {
                    name: available[(record_id, name)]
                    for name, _, _ in GROUPS
                    if (record_id, name) in available
                    and available[(record_id, name)].get("source_sha256") == sha256_object(record)
                }
                if len(rows) != len(GROUPS):
                    continue
                merged = merge_group_results(record, rows, run_key=run_key)
                output_handle.write(canonical_json(merged) + "\n")
                output_handle.flush()
                stats["merged_records"] += 1
                completed[record_id] = str(merged["source_sha256"])
                for name, _, _ in GROUPS:
                    available.pop((record_id, name), None)
    stats["skipped_records"] = max(0, selected - stats["selected_records"])
    return stats


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Independently annotate organizer records in resumable item groups."
    )
    parser.add_argument("--input", type=pathlib.Path, required=True)
    parser.add_argument("--output", type=pathlib.Path, required=True)
    parser.add_argument("--group-output", type=pathlib.Path)
    parser.add_argument("--rubric", type=pathlib.Path, default=RUBRIC_PATH)
    parser.add_argument("--endpoint", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--model-revision", default="unspecified")
    parser.add_argument("--api-key-env", default="ANNOTATION_API_KEY")
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--batch-records", type=int, default=32)
    parser.add_argument("--timeout", type=float, default=900)
    parser.add_argument("--retries", type=int, default=3)
    parser.add_argument("--seed", type=int, default=17)
    parser.add_argument("--max-tokens", type=int, default=768)
    parser.add_argument(
        "--fact-context-max-chars",
        type=int,
        default=source_fact_context.DEFAULT_MAX_CHARS,
    )
    parser.add_argument(
        "--no-fact-context",
        action="store_true",
        help="Disable independent coordinate-checked fact context (qualification ablation only).",
    )
    parser.add_argument(
        "--qualification-context-max-chars",
        type=int,
        default=source_qualification_context.DEFAULT_MAX_CHARS,
    )
    parser.add_argument(
        "--no-qualification-context",
        action="store_true",
        help=(
            "Disable coordinate-checked v10-v18/v20 qualification context "
            "(qualification ablation only)."
        ),
    )
    parser.add_argument(
        "--law-context-max-chars",
        type=int,
        default=supplied_law_context.DEFAULT_MAX_CHARS,
    )
    parser.add_argument(
        "--no-law-context",
        action="store_true",
        help="Disable supplied-law excerpts (qualification ablation only).",
    )
    parser.add_argument("--limit", type=int)
    parser.add_argument("--id", action="append", dest="ids")
    parser.add_argument(
        "--group",
        action="append",
        dest="groups",
        choices=tuple(GROUP_BY_NAME),
        help=(
            "Run only this item group (repeatable). Group checkpoints retain the same "
            "run key and can be reused by a later full run."
        ),
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.workers < 1 or args.batch_records < 1 or args.retries < 1:
        parser.error("workers, batch-records, and retries must be positive")
    if (
        args.max_tokens < 1
        or args.fact_context_max_chars < 1
        or args.qualification_context_max_chars < 1
        or args.law_context_max_chars < 1
        or (args.limit is not None and args.limit < 0)
    ):
        parser.error("max-tokens must be positive and limit must be non-negative")
    stats = run(args)
    print(json.dumps(stats, ensure_ascii=False, sort_keys=True))
    return 2 if stats["group_errors"] else 0


if __name__ == "__main__":
    sys.exit(main())
