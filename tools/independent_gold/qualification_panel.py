"""Build and score a deterministic *partial* official-dev diagnostic panel.

The panel is a fast annotator screening instrument, never a gold-generation
qualification.  Official labels are read only while selecting panel IDs and
while scoring completed group checkpoints.  The emitted execution batches
contain only organizer record IDs and target group names; no label or evidence
value is projected into an annotation request.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import pathlib
import sys
import unicodedata
from collections import Counter
from statistics import mean
from typing import Any, Iterable, Iterator, Mapping, Sequence

try:
    from tools.independent_gold.annotate_groups import (
        ABSENCE_ITEMS,
        GROUPS,
        GROUP_BY_NAME,
        GROUP_SCHEMA_VERSION,
        ITEMS,
        canonical_json,
        normalize_group_decision,
        parse_json_object,
        sha256_object,
        sha256_text,
    )
except ModuleNotFoundError:  # Direct ``python tools/.../qualification_panel.py``.
    from annotate_groups import (  # type: ignore[no-redef]
        ABSENCE_ITEMS,
        GROUPS,
        GROUP_BY_NAME,
        GROUP_SCHEMA_VERSION,
        ITEMS,
        canonical_json,
        normalize_group_decision,
        parse_json_object,
        sha256_object,
        sha256_text,
    )


MANIFEST_SCHEMA = "dacon.independent.qualification_panel.v1"
REPORT_SCHEMA = "dacon.independent.qualification_panel_score.v1"
DEFAULT_RECORDS = pathlib.Path("data_open/dev.jsonl.gz")
DEFAULT_LABELS = pathlib.Path("data_open/dev_labels.csv")
ITEM_TO_GROUP = {
    item: group_name for group_name, group_items, _ in GROUPS for item in group_items
}
GROUP_ORDER = {name: index for index, (name, _, _) in enumerate(GROUPS)}
HEX64 = frozenset("0123456789abcdef")

# Source-side cues are used only to rank diverse positives and lexical
# near-boundary negatives.  They are not rules and never create labels.
ITEM_CUE_FAMILIES: dict[str, tuple[tuple[str, tuple[str, ...]], ...]] = {
    "v1": (("institution", ("대학", "연구기관", "산학협력단")), ("facility", ("서비스센터", "자체시설", "사업장")), ("eligibility", ("입찰참가자격", "등록한 자", "업체"))),
    "v2": (("performance", ("수행실적", "납품실적", "용역실적")), ("threshold", ("이상", "최근 3년", "단일건")), ("price", ("추정가격", "기초금액", "예산"))),
    "v3": (("performance", ("실적", "납품실적", "수행실적")), ("amount", ("억원", "백만원", "천만원")), ("aggregation", ("단일건", "합산", "누계"))),
    "v4": (("issuer", ("공공기관", "국가기관", "대학병원")), ("specific_target", ("동일", "유사", "특정")), ("performance", ("실적", "수행"))),
    "v5": (("region", ("본점", "주된 영업소", "소재지")), ("price", ("추정가격", "고시금액", "기초금액")), ("eligibility", ("입찰참가", "제한경쟁", "지역제한"))),
    "v6": (("basic_region", ("시내", "군내", "구내", "시·군")), ("head_office", ("본점", "주된 영업소", "소재지")), ("small_quote", ("수의", "견적", "2인 이상"))),
    "v7": (("multi_region", ("인접", "관할", "지역 업체", "또는")), ("region", ("본점", "소재지", "지역제한")), ("exception", ("10인 미만", "납품", "정비시설"))),
    "v8": (("region", ("지역제한", "본점", "소재지")), ("performance", ("실적", "수행실적", "납품실적")), ("path_logic", ("모두", "동시에", "또는"))),
    "v9": (("model", ("모델명", "모델", "model")), ("maker", ("제조사", "브랜드", "maker")), ("equivalence", ("동등 이상", "동등이상", "호환"))),
    "v10": (("competitive", ("경쟁제품", "중소기업자간", "판로지원법")), ("direct", ("직접생산", "직생")), ("product_code", ("세부품명번호", "10자리"))),
    "v11": (("competitive", ("경쟁제품", "중소기업자간")), ("enterprise", ("중소기업", "소기업", "소상공인")), ("product_code", ("세부품명번호", "물품분류번호"))),
    "v12": (("direct", ("직접생산확인증명서", "직접생산")), ("general_product", ("일반제품", "비경쟁", "제외")), ("timing", ("마감일 전", "입찰 전", "계약 후"))),
    "v13": (("small_enterprise", ("소기업", "소상공인")), ("competitive", ("경쟁제품", "중소기업자간")), ("exception", ("공동사업", "제7조의2", "조합"))),
    "v14": (("enterprise", ("중소기업", "소상공인", "중기업")), ("price", ("고시금액", "추정가격", "억원")), ("general_product", ("일반제품", "비경쟁", "용역"))),
    "v15": (("small_enterprise", ("소기업", "소상공인")), ("price_band", ("1억원", "고시금액", "추정가격")), ("exception", ("제2조의3", "비영리", "예외"))),
    "v16": (("enterprise", ("중소기업", "중기업", "소상공인")), ("price_band", ("1억원", "고시금액", "추정가격")), ("eligibility", ("참가자격", "제한", "확인서"))),
    "v17": (("enterprise", ("중소기업", "중기업", "소기업")), ("low_price", ("1억원 미만", "추정가격", "기초금액")), ("exception", ("3인 이하", "유찰", "예외"))),
    "v18": (("small_enterprise", ("소기업", "소상공인")), ("low_price", ("1억원 미만", "추정가격", "기초금액")), ("eligibility", ("참가자격", "확인서", "제한"))),
    "v19": (("pledge", ("확약서", "공급확약", "기술지원확약")), ("issuer", ("제조사", "공급사", "기술지원사")), ("timing", ("입찰서 제출", "마감일", "계약 시"))),
    "v20": (("software_task", ("소프트웨어", "정보시스템", "유지관리")), ("participation", ("대기업 참여", "상호출자", "중견기업")), ("work", ("개발", "구축", "운영", "기술지원"))),
    "v21": (("joint", ("공동수급", "공동이행", "공동도급")), ("share", ("지분율", "출자비율", "최소")), ("percent", ("5%", "10%", "퍼센트"))),
    "v22": (("briefing", ("설명회", "현장설명", "사업설명")), ("mandatory", ("참석한 업체", "참석 필수", "참가자격")), ("proposal", ("협상", "제안서", "입찰"))),
    "v23": (("briefing", ("설명회", "현장설명", "사업설명")), ("dates", ("제출마감", "마감일", "개최일")), ("proposal", ("협상", "제안서", "긴급"))),
    "v24": (("contract", ("계약방법", "일반경쟁", "제한경쟁", "수의")), ("award", ("낙찰방법", "협상", "적격심사")), ("meta_fields", ("추정가격", "지역제한", "업종", "공동수급"))),
}

FORBIDDEN_PROVENANCE_MARKERS = (
    "gem" + "ma",
    "젬마",
    "sub" + "mission/",
    "sub" + "mission\\",
    "saved" + "_response",
    "production_prediction",
    "production-response",
    "운영_예측",
    "운영예측",
)


class PanelError(ValueError):
    """Raised for an invalid panel input or checkpoint."""


def file_sha256(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _is_sha256(value: Any) -> bool:
    return isinstance(value, str) and len(value) == 64 and set(value) <= HEX64


def read_records(path: pathlib.Path) -> dict[str, dict[str, Any]]:
    opener = gzip.open if path.suffix.lower() == ".gz" else pathlib.Path.open
    if path.suffix.lower() == ".gz":
        context = opener(path, "rt", encoding="utf-8")
    else:
        context = opener(path, "r", encoding="utf-8")
    records: dict[str, dict[str, Any]] = {}
    with context as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise PanelError(f"{path}:{line_number}: invalid JSON") from exc
            if not isinstance(row, dict) or not row.get("id"):
                raise PanelError(f"{path}:{line_number}: invalid organizer record")
            record_id = str(row["id"])
            if record_id in records:
                raise PanelError(f"{path}:{line_number}: duplicate record id {record_id}")
            records[record_id] = row
    if not records:
        raise PanelError(f"{path}: no organizer records")
    return records


def read_labels(path: pathlib.Path) -> dict[str, dict[str, int]]:
    labels: dict[str, dict[str, int]] = {}
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None or not {"id", *ITEMS} <= set(reader.fieldnames):
            raise PanelError(f"{path}: labels must contain id and v1..v24")
        for line_number, row in enumerate(reader, 2):
            record_id = str(row.get("id") or "")
            if not record_id or record_id in labels:
                raise PanelError(f"{path}:{line_number}: duplicate/empty label id")
            parsed: dict[str, int] = {}
            for item in ITEMS:
                raw = row.get(item)
                if raw not in ("0", "1"):
                    raise PanelError(f"{path}:{line_number}: {item} is not binary")
                parsed[item] = int(raw)
            labels[record_id] = parsed
    if not labels:
        raise PanelError(f"{path}: no label rows")
    return labels


def _normalized_text(value: Any) -> str:
    return unicodedata.normalize("NFKC", str(value)).casefold()


def _source_pattern(record: Mapping[str, Any], item: str) -> dict[str, Any]:
    docs = record.get("docs") or []
    text = "\n".join(str(doc.get("text") or "") for doc in docs if isinstance(doc, Mapping))
    meta = record.get("meta") or {}
    searchable = _normalized_text(text + "\n" + canonical_json(meta))
    matched: list[str] = []
    cue_hits = 0
    for family, terms in ITEM_CUE_FAMILIES[item]:
        family_hits = sum(searchable.count(_normalized_text(term)) for term in terms)
        if family_hits:
            matched.append(family)
            cue_hits += family_hits
    tags = [f"cue:{name}" for name in matched]
    if isinstance(meta, Mapping):
        for key, prefix in (
            ("적용계약법", "law"),
            ("업무구분", "work"),
            ("계약방법", "contract"),
            ("낙찰방법", "award"),
        ):
            value = meta.get(key)
            if value not in (None, "", "미입력"):
                tags.append(f"{prefix}:{str(value)[:60]}")
    doc_types = sorted(
        {str(doc.get("type") or "unknown") for doc in docs if isinstance(doc, Mapping)}
    )
    tags.append("docs:" + "+".join(doc_types))
    tags.append("multi_doc" if len(docs) > 1 else "single_doc")
    completeness = record.get("input_completeness") or {}
    dropped = record.get("dropped_doc_counts") or {}
    complete = bool(completeness) and all(value is True for value in completeness.values()) and not dropped
    tags.append("complete" if complete else "incomplete")
    tags = sorted(set(tags))
    return {
        "cue_families": matched,
        "cue_hits": cue_hits,
        "pattern_tags": tags,
        "pattern_signature": sha256_object(tags)[:16],
    }


def _stable_tie(item: str, role: str, record_id: str) -> str:
    return sha256_text(f"{MANIFEST_SCHEMA}\0{item}\0{role}\0{record_id}")


def _select_candidates(
    records: Mapping[str, Mapping[str, Any]],
    candidate_ids: Iterable[str],
    *,
    item: str,
    role: str,
    count: int,
) -> list[dict[str, Any]]:
    pool: list[dict[str, Any]] = []
    for record_id in sorted(set(candidate_ids)):
        pattern = _source_pattern(records[record_id], item)
        pool.append(
            {
                "id": record_id,
                "source_sha256": sha256_object(records[record_id]),
                **pattern,
            }
        )
    if len(pool) < count:
        raise PanelError(f"{item}: requires {count} {role} candidates, found {len(pool)}")

    selected: list[dict[str, Any]] = []
    used_signatures: set[str] = set()
    used_tags: set[str] = set()
    while len(selected) < count:
        def rank(candidate: Mapping[str, Any]) -> tuple[Any, ...]:
            signature_novel = int(candidate["pattern_signature"] not in used_signatures)
            tag_novelty = len(set(candidate["pattern_tags"]) - used_tags)
            if role == "hard_negative":
                return (
                    -int(candidate["cue_hits"]),
                    -signature_novel,
                    -tag_novelty,
                    _stable_tie(item, role, str(candidate["id"])),
                    str(candidate["id"]),
                )
            return (
                -signature_novel,
                -tag_novelty,
                -int(candidate["cue_hits"]),
                _stable_tie(item, role, str(candidate["id"])),
                str(candidate["id"]),
            )

        choice = min((candidate for candidate in pool if candidate not in selected), key=rank)
        selected.append(choice)
        used_signatures.add(str(choice["pattern_signature"]))
        used_tags.update(str(value) for value in choice["pattern_tags"])

    rendered: list[dict[str, Any]] = []
    for candidate in selected:
        families = ",".join(candidate["cue_families"]) or "none"
        if role == "hard_negative":
            rationale = (
                "official-negative selection only; source-side lexical near-boundary rank "
                f"cue_hits={candidate['cue_hits']}, cue_families={families}; no model output used"
            )
        else:
            rationale = (
                "official-positive selection only; deterministic source-pattern diversification "
                f"signature={candidate['pattern_signature']}, cue_families={families}; no model output used"
            )
        rendered.append(
            {
                "id": candidate["id"],
                "source_sha256": candidate["source_sha256"],
                "pattern_signature": candidate["pattern_signature"],
                "pattern_tags": candidate["pattern_tags"],
                "cue_families": candidate["cue_families"],
                "cue_hits": candidate["cue_hits"],
                "selection_rationale": rationale,
            }
        )
    return rendered


def _manifest_without_hash(manifest: Mapping[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in manifest.items() if key != "manifest_sha256"}


def build_manifest(
    records_path: pathlib.Path,
    labels_path: pathlib.Path,
    *,
    positives_per_item: int = 2,
    hard_negatives_per_item: int = 2,
) -> dict[str, Any]:
    if positives_per_item < 2:
        raise PanelError("positives_per_item must be at least 2")
    if hard_negatives_per_item < 1:
        raise PanelError("hard_negatives_per_item must be positive")
    records = read_records(records_path)
    labels = read_labels(labels_path)
    if set(records) != set(labels):
        missing_labels = sorted(set(records) - set(labels))
        missing_records = sorted(set(labels) - set(records))
        raise PanelError(
            f"records/labels id mismatch: missing_labels={missing_labels[:3]}, "
            f"missing_records={missing_records[:3]}"
        )

    items: dict[str, Any] = {}
    execution_ids: dict[str, set[str]] = {name: set() for name, _, _ in GROUPS}
    target_cells: set[tuple[str, str]] = set()
    for item in ITEMS:
        positives = [record_id for record_id, row in labels.items() if row[item] == 1]
        negatives = [record_id for record_id, row in labels.items() if row[item] == 0]
        selected_positive = _select_candidates(
            records,
            positives,
            item=item,
            role="positive",
            count=positives_per_item,
        )
        selected_negative = _select_candidates(
            records,
            negatives,
            item=item,
            role="hard_negative",
            count=hard_negatives_per_item,
        )
        group_name = ITEM_TO_GROUP[item]
        for candidate in (*selected_positive, *selected_negative):
            execution_ids[group_name].add(str(candidate["id"]))
            target_cells.add((str(candidate["id"]), item))
        items[item] = {
            "target_group": group_name,
            "positive_candidates": selected_positive,
            "hard_negative_candidates": selected_negative,
            "selection_contract": (
                "Official labels determine candidate class only. Source-derived pattern tags "
                "determine diversity/hardness and are not semantic predictions."
            ),
        }

    execution_batches = [
        {
            "target_group": group_name,
            "ids": sorted(execution_ids[group_name]),
            "target_items": list(group_items),
        }
        for group_name, group_items, _ in GROUPS
        if execution_ids[group_name]
    ]
    unique_ids = sorted({record_id for record_id, _ in target_cells})
    manifest: dict[str, Any] = {
        "schema_version": MANIFEST_SCHEMA,
        "purpose": "partial_official_dev_annotator_diagnostic_only",
        "qualified_for_gold_generation": False,
        "is_full_official_dev_evaluation": False,
        "prohibited_interpretation": (
            "This selected partial panel cannot satisfy or substitute for the full official-dev gate."
        ),
        "information_boundary": {
            "labels_used_only_for": ["panel_selection", "posthoc_scoring"],
            "labels_excluded_from": ["model_prompt", "request_artifact", "group_checkpoint"],
            "execution_projection": "execution_batches exposes only organizer IDs and target groups",
            "production_outputs_used": False,
        },
        "records_sha256": file_sha256(records_path),
        "labels_sha256": file_sha256(labels_path),
        "official_dev_record_count": len(records),
        "selection": {
            "positives_per_item": positives_per_item,
            "hard_negatives_per_item": hard_negatives_per_item,
            "algorithm": "source_pattern_diversity_and_lexical_near_boundary_v1",
        },
        "groups": {name: list(group_items) for name, group_items, _ in GROUPS},
        "items": items,
        "execution_batches": execution_batches,
        "counts": {
            "unique_ids": len(unique_ids),
            "target_cells": len(target_cells),
            "positive_target_cells": len(ITEMS) * positives_per_item,
            "hard_negative_target_cells": len(ITEMS) * hard_negatives_per_item,
        },
    }
    manifest["manifest_sha256"] = sha256_object(_manifest_without_hash(manifest))
    return manifest


def validate_manifest(
    manifest: Mapping[str, Any], records_path: pathlib.Path, labels_path: pathlib.Path
) -> None:
    if manifest.get("schema_version") != MANIFEST_SCHEMA:
        raise PanelError("wrong qualification-panel manifest schema")
    if manifest.get("qualified_for_gold_generation") is not False:
        raise PanelError("partial panel manifest must hard-code qualified_for_gold_generation=false")
    if manifest.get("is_full_official_dev_evaluation") is not False:
        raise PanelError("partial panel cannot claim full official-dev evaluation")
    if manifest.get("records_sha256") != file_sha256(records_path):
        raise PanelError("organizer records SHA256 differs from panel manifest")
    if manifest.get("labels_sha256") != file_sha256(labels_path):
        raise PanelError("official labels SHA256 differs from panel manifest")
    expected_hash = sha256_object(_manifest_without_hash(manifest))
    if manifest.get("manifest_sha256") != expected_hash:
        raise PanelError("panel manifest hash mismatch")
    items = manifest.get("items")
    if not isinstance(items, Mapping) or set(items) != set(ITEMS) or len(items) != len(ITEMS):
        raise PanelError("panel manifest items are not exactly v1..v24")
    for item in ITEMS:
        spec = items[item]
        if not isinstance(spec, Mapping) or spec.get("target_group") != ITEM_TO_GROUP[item]:
            raise PanelError(f"{item}: wrong target group")
        positives = spec.get("positive_candidates")
        negatives = spec.get("hard_negative_candidates")
        if not isinstance(positives, list) or len(positives) < 2:
            raise PanelError(f"{item}: fewer than two positive candidates")
        if not isinstance(negatives, list) or not negatives:
            raise PanelError(f"{item}: no hard-negative candidate")


def _target_cells(manifest: Mapping[str, Any]) -> dict[tuple[str, str], dict[str, str]]:
    result: dict[tuple[str, str], dict[str, str]] = {}
    for item in ITEMS:
        spec = manifest["items"][item]
        group_name = str(spec["target_group"])
        for list_name, role in (
            ("positive_candidates", "positive"),
            ("hard_negative_candidates", "hard_negative"),
        ):
            for candidate in spec[list_name]:
                record_id = str(candidate["id"])
                key = (record_id, item)
                if key in result:
                    raise PanelError(f"duplicate target cell: {record_id}:{item}")
                result[key] = {"selection_role": role, "target_group": group_name}
    return result


def _iter_jsonl(path: pathlib.Path) -> Iterator[tuple[int, dict[str, Any]]]:
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise PanelError(f"{path}:{line_number}: invalid JSON") from exc
            if not isinstance(row, dict):
                raise PanelError(f"{path}:{line_number}: row is not an object")
            yield line_number, row


def _checkpoint_index(
    path: pathlib.Path,
    wanted: set[tuple[str, str]],
    *,
    run_key: str | None,
) -> tuple[dict[tuple[str, str], tuple[int, dict[str, Any]]], dict[tuple[str, str], str]]:
    candidates: dict[tuple[str, str], list[tuple[int, dict[str, Any]]]] = {}
    for line_number, row in _iter_jsonl(path):
        key = (str(row.get("id") or ""), str(row.get("group") or ""))
        if key not in wanted:
            continue
        if run_key is not None and row.get("run_key") != run_key:
            continue
        candidates.setdefault(key, []).append((line_number, row))

    selected: dict[tuple[str, str], tuple[int, dict[str, Any]]] = {}
    errors: dict[tuple[str, str], str] = {}
    for key in wanted:
        rows = candidates.get(key, [])
        if not rows:
            errors[key] = "missing_checkpoint"
            continue
        run_keys = {str(row.get("run_key") or "") for _, row in rows}
        if run_key is None and len(run_keys) != 1:
            errors[key] = "ambiguous_run_keys"
            continue
        selected[key] = rows[-1]
    return selected, errors


def _provenance_is_independent(row: Mapping[str, Any]) -> bool:
    provenance = row.get("model_provenance")
    if not isinstance(provenance, Mapping):
        return False
    rendered = canonical_json(provenance).casefold()
    return not any(marker in rendered for marker in FORBIDDEN_PROVENANCE_MARKERS)


def _row_errors(
    row: Mapping[str, Any],
    *,
    record: Mapping[str, Any],
    record_id: str,
    group_name: str,
) -> list[str]:
    errors: list[str] = []
    if row.get("schema_version") != GROUP_SCHEMA_VERSION:
        errors.append("wrong_group_schema")
    if row.get("id") != record_id or row.get("group") != group_name:
        errors.append("checkpoint_identity_mismatch")
    if row.get("status") != "ok":
        errors.append("checkpoint_status_not_ok")
    expected_items = list(GROUP_BY_NAME[group_name][0])
    if row.get("target_items") != expected_items:
        errors.append("wrong_target_items")
    if row.get("source_sha256") != sha256_object(record):
        errors.append("source_sha256_mismatch")
    for field in (
        "source_sha256",
        "packet_sha256",
        "rubric_sha256",
        "system_prompt_sha256",
        "request_sha256",
        "raw_response_sha256",
        "content_sha256",
    ):
        if not _is_sha256(row.get(field)):
            errors.append(f"invalid_{field}")
    raw_content = row.get("raw_content")
    if not isinstance(raw_content, str) or sha256_text(raw_content) != row.get("content_sha256"):
        errors.append("content_hash_mismatch")
    elif isinstance(row.get("decisions"), Mapping):
        try:
            raw_decisions = normalize_group_decision(parse_json_object(raw_content), expected_items)
            for item in expected_items:
                stored = row["decisions"].get(item)
                if not isinstance(stored, Mapping) or any(
                    stored.get(key) != raw_decisions[item].get(key)
                    for key in ("label", "confidence", "evidence")
                ):
                    errors.append("raw_content_decision_mismatch")
                    break
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            errors.append("raw_content_decision_invalid")
    if not _provenance_is_independent(row):
        errors.append("missing_or_prohibited_model_provenance")
    if row.get("evidence_errors") not in (None, []):
        errors.append("checkpoint_evidence_errors")
    if not isinstance(row.get("decisions"), Mapping):
        errors.append("decisions_missing")
    return sorted(set(errors))


def _evidence_report(
    record: Mapping[str, Any], item: str, label: int | str, cell: Mapping[str, Any]
) -> dict[str, Any]:
    quote = cell.get("evidence")
    locations = cell.get("evidence_locations")
    errors: list[str] = []
    if not isinstance(quote, str):
        quote = ""
        errors.append("evidence_not_text")
    if not isinstance(locations, list):
        locations = []
        errors.append("evidence_locations_not_list")
    normalized_locations: list[dict[str, Any]] = []

    required = label == 1 and item not in ABSENCE_ITEMS
    if required:
        if not quote:
            errors.append("positive_without_evidence")
        if len(quote) > 500:
            errors.append("evidence_too_long")
        if not locations:
            errors.append("positive_without_coordinates")
        seen: set[tuple[int, int, int]] = set()
        docs = record.get("docs") or []
        for index, location in enumerate(locations):
            if not isinstance(location, Mapping):
                errors.append(f"location_{index}_not_object")
                continue
            try:
                doc_index = int(location["doc_index"])
                start = int(location["start"])
                end = int(location["end"])
                doc = docs[doc_index]
                text = str(doc.get("text") or "")
            except (KeyError, IndexError, TypeError, ValueError):
                errors.append(f"location_{index}_invalid_coordinate")
                continue
            key = (doc_index, start, end)
            if key in seen:
                errors.append(f"location_{index}_duplicate")
            seen.add(key)
            if start < 0 or end < start or end > len(text) or text[start:end] != quote:
                errors.append(f"location_{index}_quote_mismatch")
            if location.get("doc_id") != doc.get("doc_id"):
                errors.append(f"location_{index}_doc_id_mismatch")
            doc_hash = sha256_text(text)
            if location.get("source_doc_sha256") != doc_hash:
                errors.append(f"location_{index}_source_hash_mismatch")
            evidence_hash = sha256_text(quote)
            if location.get("evidence_sha256") != evidence_hash:
                errors.append(f"location_{index}_evidence_hash_mismatch")
            normalized_locations.append(
                {
                    "doc_index": doc_index,
                    "doc_id": doc.get("doc_id"),
                    "start": start,
                    "end": end,
                    "source_doc_sha256": doc_hash,
                    "evidence_sha256": evidence_hash,
                }
            )
    elif quote or locations:
        errors.append("non_witness_cell_carries_evidence")

    if errors:
        status = "invalid"
    elif required:
        status = "valid"
    elif label == 1 and item in ABSENCE_ITEMS:
        status = "not_applicable_absence_item"
    else:
        status = "not_required"
    return {
        "status": status,
        "quote": quote,
        "locations": normalized_locations,
        "errors": sorted(set(errors)),
    }


def _metric(counts: Mapping[str, int]) -> dict[str, Any]:
    tp, fp, fn = counts.get("tp", 0), counts.get("fp", 0), counts.get("fn", 0)
    denom = 2 * tp + fp + fn
    return {
        **{name: int(counts.get(name, 0)) for name in ("tp", "fp", "fn", "tn", "u", "invalid")},
        "diagnostic_positive_f1": 0.0 if denom == 0 else 2 * tp / denom,
    }


def score_panel(
    manifest: Mapping[str, Any],
    records_path: pathlib.Path,
    labels_path: pathlib.Path,
    checkpoint_path: pathlib.Path,
    *,
    run_key: str | None = None,
) -> dict[str, Any]:
    validate_manifest(manifest, records_path, labels_path)
    records = read_records(records_path)
    targets = _target_cells(manifest)
    required_group_rows = {(record_id, spec["target_group"]) for (record_id, _), spec in targets.items()}
    checkpoints, checkpoint_errors = _checkpoint_index(
        checkpoint_path, required_group_rows, run_key=run_key
    )

    # Official labels enter only after annotation artifacts have been selected
    # and integrity-checked for the requested record/group projection.
    labels = read_labels(labels_path)
    cells: list[dict[str, Any]] = []
    item_counts: dict[str, Counter[str]] = {item: Counter() for item in ITEMS}
    group_counts: dict[str, Counter[str]] = {name: Counter() for name, _, _ in GROUPS}
    totals: Counter[str] = Counter()

    for (record_id, item), target in sorted(
        targets.items(), key=lambda value: (int(value[0][1][1:]), value[0][0])
    ):
        if record_id not in records or record_id not in labels:
            raise PanelError(f"manifest target id is absent from official inputs: {record_id}")
        group_name = target["target_group"]
        group_key = (record_id, group_name)
        invalid_reasons: list[str] = []
        line_number: int | None = None
        row: Mapping[str, Any] | None = None
        if group_key in checkpoint_errors:
            invalid_reasons.append(checkpoint_errors[group_key])
        else:
            line_number, selected_row = checkpoints[group_key]
            row = selected_row
            invalid_reasons.extend(
                _row_errors(
                    row,
                    record=records[record_id],
                    record_id=record_id,
                    group_name=group_name,
                )
            )

        gold = labels[record_id][item]
        predicted: int | str | None = None
        evidence = {"status": "unavailable", "quote": "", "locations": [], "errors": []}
        if not invalid_reasons and row is not None:
            decision = row["decisions"].get(item)
            if not isinstance(decision, Mapping):
                invalid_reasons.append("target_decision_missing")
            else:
                predicted = decision.get("label")
                if isinstance(predicted, bool) or predicted not in (0, 1, "U"):
                    invalid_reasons.append("invalid_predicted_label")
                else:
                    evidence = _evidence_report(records[record_id], item, predicted, decision)
                    if evidence["status"] == "invalid":
                        invalid_reasons.extend(f"evidence:{value}" for value in evidence["errors"])

        if invalid_reasons:
            outcome = "invalid"
        elif predicted == "U":
            outcome = "u"
        elif gold == 1 and predicted == 1:
            outcome = "tp"
        elif gold == 0 and predicted == 1:
            outcome = "fp"
        elif gold == 1 and predicted == 0:
            outcome = "fn"
        else:
            outcome = "tn"
        totals[outcome] += 1
        item_counts[item][outcome] += 1
        group_counts[group_name][outcome] += 1
        cells.append(
            {
                "id": record_id,
                "item": item,
                "target_group": group_name,
                "selection_role": target["selection_role"],
                "gold_label": gold,
                "predicted_label": predicted,
                "outcome": outcome,
                "checkpoint_line": line_number,
                "run_key": row.get("run_key") if row is not None else None,
                "invalid_reasons": sorted(set(invalid_reasons)),
                "evidence_coordinate": evidence,
            }
        )

    by_item = {item: _metric(item_counts[item]) for item in ITEMS}
    by_group = {name: _metric(group_counts[name]) for name, _, _ in GROUPS}
    panel_f1 = mean(metric["diagnostic_positive_f1"] for metric in by_item.values())
    report = {
        "schema_version": REPORT_SCHEMA,
        "purpose": "partial_official_dev_annotator_diagnostic_only",
        "qualified_for_gold_generation": False,
        "is_full_official_dev_evaluation": False,
        "prohibited_interpretation": (
            "Scores cover selected target cells only and are not the official full-dev qualification."
        ),
        "manifest_sha256": manifest["manifest_sha256"],
        "records_sha256": manifest["records_sha256"],
        "labels_sha256": manifest["labels_sha256"],
        "checkpoint_sha256": file_sha256(checkpoint_path),
        "requested_run_key": run_key,
        "target_cells": len(targets),
        "checkpoint_group_rows_required": len(required_group_rows),
        "checkpoint_group_rows_selected": len(checkpoints),
        "panel_macro_positive_f1_diagnostic_only": panel_f1,
        "all_target_cells_binary_and_valid": totals["u"] == 0 and totals["invalid"] == 0,
        "counts": _metric(totals),
        "by_item": by_item,
        "by_group": by_group,
        "cells": cells,
    }
    return report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--build", action="store_true", help="build a deterministic panel manifest")
    mode.add_argument("--score", action="store_true", help="score group checkpoint JSONL on the panel")
    parser.add_argument("--records", type=pathlib.Path, default=DEFAULT_RECORDS)
    parser.add_argument("--labels", type=pathlib.Path, default=DEFAULT_LABELS)
    parser.add_argument("--manifest", type=pathlib.Path, required=True)
    parser.add_argument("--positive-count", type=int, default=2)
    parser.add_argument("--hard-negative-count", type=int, default=2)
    parser.add_argument("--group-results", type=pathlib.Path)
    parser.add_argument("--run-key")
    parser.add_argument("--report", type=pathlib.Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.build:
            manifest = build_manifest(
                args.records,
                args.labels,
                positives_per_item=args.positive_count,
                hard_negatives_per_item=args.hard_negative_count,
            )
            args.manifest.parent.mkdir(parents=True, exist_ok=True)
            args.manifest.write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            print(canonical_json({"manifest": str(args.manifest), **manifest["counts"], "qualified_for_gold_generation": False}))
            return 0

        if args.group_results is None or args.report is None:
            parser.error("--score requires --group-results and --report")
        manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
        if not isinstance(manifest, dict):
            raise PanelError("manifest must be a JSON object")
        report = score_panel(
            manifest,
            args.records,
            args.labels,
            args.group_results,
            run_key=args.run_key,
        )
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(
            json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        print(
            canonical_json(
                {
                    "report": str(args.report),
                    "target_cells": report["target_cells"],
                    "counts": report["counts"],
                    "qualified_for_gold_generation": False,
                }
            )
        )
        return 0
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        print(f"qualification panel failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
