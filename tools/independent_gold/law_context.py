"""Bounded excerpts from the organizer-supplied law snapshot.

The notice fact extractors intentionally describe *what the notice says*.
They do not silently inject the legal threshold against which that statement
must be evaluated.  This module supplies that second half as exact excerpts
from ``data_open/data/법령패키지`` with file hashes and character coordinates.

It emits no competition label and never reads the submission runtime,
predictions, or saved model responses.  The curated anchors are deliberately
small: they cover only the legal propositions needed by the eight annotation
groups, and every rendered byte can be checked against the supplied files.
"""

from __future__ import annotations

import bisect
import copy
import hashlib
import json
import pathlib
from typing import Any, Mapping, Sequence


ROOT = pathlib.Path(__file__).resolve().parents[2]
LAW_ROOT = ROOT / "data_open" / "data" / "법령패키지" / "법령"
SCHEMA_VERSION = "dacon.independent.law_context.v4"
DEFAULT_MAX_CHARS = 16_000

GROUP_ITEMS: dict[str, tuple[str, ...]] = {
    "v1-4": ("v1", "v2", "v3", "v4"),
    "v5-8": ("v5", "v6", "v7", "v8"),
    "v9": ("v9",),
    "v10-13": ("v10", "v11", "v12", "v13"),
    "v14-19": ("v14", "v15", "v16", "v17", "v18", "v19"),
    "v20": ("v20",),
    "v21-23": ("v21", "v22", "v23"),
    "v24": ("v24",),
}

# ``before`` and ``after`` count complete source lines around the anchor line.
# A long legal paragraph is generally one physical line in the supplied text.
REFERENCE_SPECS: tuple[dict[str, Any], ...] = (
    {
        "reference_id": "national_notified_goods_services_threshold",
        "groups": ("v1-4", "v5-8", "v10-13", "v14-19"),
        "items": ("v2", "v5", "v10", "v11", "v13", "v14", "v15", "v16", "v17", "v18"),
        "path": "국가를 당사자로 하는 계약에 관한 법률 등의 재정경제부장관이 정하는 고시금액.txt",
        "anchor": "ㅇ 물품 및 용역: 2억 3천만 원",
        "before": 2,
        "after": 1,
        "priority": 0,
        "topic": "current supplied national notified threshold",
    },
    {
        "reference_id": "national_performance_restriction_limit",
        "groups": ("v1-4",),
        "items": ("v2", "v3", "v4"),
        "path": "(계약예규) 정부 입찰·계약 집행기준.txt",
        "anchor": "추정가격이 고시금액 미만인 제조 또는 용역계약의 경우에는 실적으로 경쟁참가자의 자격을 제한하여서는 아니된다",
        "before": 0,
        "after": 0,
        "priority": 0,
        "topic": "performance restriction threshold and maximum multiple",
    },
    {
        "reference_id": "local_performance_restriction_limit",
        "groups": ("v1-4",),
        "items": ("v2", "v3", "v4"),
        "path": "지방자치단체를 당사자로 하는 계약에 관한 법률 시행령.txt",
        "anchor": "해당 용역계약의 추정가격이 「국가를 당사자로 하는 계약에 관한 법률」 제4조제1항에 따라 재정경제부장관이 정하여 고시한 금액 이상이어야 한다",
        "before": 0,
        "after": 0,
        "priority": 0,
        "topic": "local same-kind service performance threshold",
    },
    {
        "reference_id": "local_performance_restriction_table",
        "groups": ("v1-4",),
        "items": ("v2",),
        "path": "지방자치단체 입찰 및 계약 집행기준.txt",
        "anchor": "기획재정부장관이 정하여 고시한 금액 이상 특수한 기술이 요구되는 용역",
        "before": 2,
        "after": 0,
        "priority": 0,
        "topic": "local identical-performance restriction applicability table",
    },
    {
        "reference_id": "national_performance_one_times_rule",
        "groups": ("v1-4",),
        "items": ("v3",),
        "path": "국가를 당사자로 하는 계약에 관한 법률 시행규칙.txt",
        "anchor": "해당 계약목적물의 추정가격(「건설산업기본법」등 다른 법령에서 시공능력 적용시 관급자재비를 포함하고 있는 경우에는 추정금액을 말한다. 이하 이 항에서 같다)의 1배 이내",
        "before": 1,
        "after": 1,
        "priority": 0,
        "topic": "performance amount comparison ceiling",
    },
    {
        "reference_id": "local_performance_one_times_rule",
        "groups": ("v1-4",),
        "items": ("v3",),
        "path": "지방자치단체를 당사자로 하는 계약에 관한 법률 시행규칙.txt",
        "anchor": "해당 계약목적물의 규모 또는 양의 1배의 범위에서 최소 실적기준을 정할 수 있다",
        "before": 0,
        "after": 1,
        "priority": 1,
        "topic": "local performance scale and amount ceilings",
    },
    {
        "reference_id": "national_headquarters_region_and_adjacent_exceptions",
        "groups": ("v5-8",),
        "items": ("v5", "v6", "v7", "v8"),
        "path": "국가를 당사자로 하는 계약에 관한 법률 시행규칙.txt",
        "anchor": "법인등기부상 본점소재지가 해당 공사 등의 현장ㆍ납품지 등이 소재하는 특별시ㆍ광역시ㆍ특별자치시ㆍ도 또는 특별자치도",
        "before": 0,
        "after": 4,
        "priority": 0,
        "topic": "headquarters region unit and adjacent-region exceptions",
    },
    {
        "reference_id": "national_small_quote_city_county_exception",
        "groups": ("v5-8",),
        "items": ("v6", "v7"),
        "path": "국가를 당사자로 하는 계약에 관한 법률 시행규칙.txt",
        "anchor": "해당계약의 이행에 필요한 자격을 갖춘 자가 5인 이상인 경우에는 그 시ㆍ군의 관할구역 안에 있는 자로 제한할 수 있다",
        "before": 0,
        "after": 0,
        "priority": 0,
        "topic": "small-quote city or county exception",
    },
    {
        "reference_id": "national_improper_duplicate_and_subregional_limits",
        "groups": ("v5-8",),
        "items": ("v6", "v8"),
        "path": "(계약예규) 정부 입찰·계약 집행기준.txt",
        "anchor": "시ㆍ군ㆍ자치구의 관할구역안에 있는 자로 제한하는 경우",
        "before": 8,
        "after": 2,
        "priority": 1,
        "topic": "improper duplicate and subregional restrictions",
    },
    {
        "reference_id": "local_headquarters_region_and_adjacent_exceptions",
        "groups": ("v5-8",),
        "items": ("v5", "v6", "v7", "v8"),
        "path": "지방자치단체를 당사자로 하는 계약에 관한 법률 시행규칙.txt",
        "anchor": "법인등기부상 본점이 해당 공사 등의 현장, 납품지 등이 있는 특별시ㆍ통합특별시ㆍ광역시ㆍ특별자치시ㆍ도ㆍ특별자치도",
        "before": 0,
        "after": 5,
        "priority": 1,
        "topic": "local headquarters region unit and exceptions",
    },
    {
        "reference_id": "local_region_restriction_price_limits",
        "groups": ("v5-8",),
        "items": ("v5", "v6", "v7", "v8"),
        "path": "지방자치단체를 당사자로 하는 계약에 관한 법률 시행규칙.txt",
        "anchor": "물품의 제조ㆍ구매ㆍ용역 등의 경우: 다음 각 목의 구분에 따른 금액",
        "before": 1,
        "after": 8,
        "priority": 0,
        "topic": "local goods and services regional-restriction price limits",
    },
    {
        "reference_id": "local_region_restriction_operating_table",
        "groups": ("v5-8",),
        "items": ("v5", "v6", "v7", "v8"),
        "path": "지방자치단체 입찰 및 계약 집행기준.txt",
        "anchor": "추정가격5.0억원미만세종시,시·군·구일반용역․물품",
        "before": 2,
        "after": 0,
        "priority": 0,
        "topic": "local regional-restriction operating threshold table",
    },
    {
        "reference_id": "local_small_quote_regional_scope",
        "groups": ("v5-8",),
        "items": ("v6", "v7", "v8"),
        "path": "지방자치단체 입찰 및 계약 집행기준.txt",
        "anchor": "계약담당자는 수의계약 안내공고 시 다음 각 호의 어느 하나에 해당하는 방법으로 견적서 제출 대상을 제한할 수 있다",
        "before": 8,
        "after": 10,
        "priority": 0,
        "topic": "local two-quote amount and permitted regional scope",
    },
    {
        "reference_id": "local_international_tender_sme_product_exclusion",
        "groups": ("v5-8",),
        "items": ("v5",),
        "path": "지방자치단체를 당사자로 하는 계약에 관한 법률.txt",
        "anchor": "「중소기업제품 구매촉진 및 판로지원에 관한 법률」에 따라 중소기업 제품을 제조ㆍ구매하는 경우",
        "before": 4,
        "after": 2,
        "priority": 0,
        "topic": "SME-product procurement excluded from Local Contracts Act Article 5(1) international-tender scope",
    },
    {
        "reference_id": "sme_product_definition_includes_services",
        "groups": ("v5-8",),
        "items": ("v5",),
        "path": "중소기업제품 구매촉진 및 판로지원에 관한 법률.txt",
        "anchor": '"중소기업제품"이란 중소기업자가 생산하는 물품, 제공하는 용역 및 수행하는 공사를 말한다',
        "before": 0,
        "after": 0,
        "priority": 0,
        "topic": "statutory SME-product definition expressly includes supplied services",
    },
    {
        "reference_id": "competitive_product_contract_method_for_local_scope",
        "groups": ("v5-8",),
        "items": ("v5",),
        "path": "중소기업제품 구매촉진 및 판로지원에 관한 법률.txt",
        "anchor": "공공기관의 장은 경쟁제품에 대하여는 대통령령으로 정하는 특별한 사유가 없으면 중소기업자만을 대상으로 하는 제한경쟁",
        "before": 1,
        "after": 1,
        "priority": 0,
        "topic": "competitive-product procurement is statutorily tied to SME-only competition under Article 7",
    },
    {
        "reference_id": "competitive_product_direct_production_for_local_scope",
        "groups": ("v5-8",),
        "items": ("v5",),
        "path": "중소기업제품 구매촉진 및 판로지원에 관한 법률.txt",
        "anchor": "제품조달계약을 체결하려면 그 중소기업자의 직접생산 여부를 확인하여야 한다",
        "before": 1,
        "after": 2,
        "priority": 0,
        "topic": "Article 9 ties direct-production confirmation to competitive-product procurement",
    },
    {
        "reference_id": "competitive_product_sme_contract_method",
        "groups": ("v10-13",),
        "items": ("v10", "v11", "v13"),
        "path": "중소기업제품 구매촉진 및 판로지원에 관한 법률.txt",
        "anchor": "공공기관의 장은 경쟁제품에 대하여는 대통령령으로 정하는 특별한 사유가 없으면 중소기업자만을 대상으로 하는 제한경쟁",
        "before": 5,
        "after": 10,
        "priority": 0,
        "topic": "competitive-product SME competition and small-enterprise special cases",
    },
    {
        "reference_id": "competitive_product_direct_production",
        "groups": ("v10-13",),
        "items": ("v10", "v12"),
        "path": "중소기업제품 구매촉진 및 판로지원에 관한 법률.txt",
        "anchor": "제품조달계약을 체결하려면 그 중소기업자의 직접생산 여부를 확인하여야 한다",
        "before": 1,
        "after": 5,
        "priority": 0,
        "topic": "direct-production confirmation duty and certificate",
    },
    {
        "reference_id": "public_body_general_product_notified_threshold_link",
        "groups": ("v14-19",),
        "items": ("v14", "v15", "v16"),
        "path": "중소기업제품 구매촉진 및 판로지원에 관한 법률.txt",
        "anchor": "공공기관의 장은 「국가를 당사자로 하는 계약에 관한 법률」 제4조제1항에 따라 재정경제부장관이 고시한 금액 미만의 물품 및 용역",
        "before": 1,
        "after": 1,
        "priority": 0,
        "topic": "public-body general-product band links to national notified threshold",
    },
    {
        "reference_id": "general_product_sme_price_bands",
        "groups": ("v10-13", "v14-19"),
        "items": ("v13", "v14", "v15", "v16", "v17", "v18"),
        "path": "국가를 당사자로 하는 계약에 관한 법률 시행령.txt",
        "anchor": "추정가격이 1억원 미만인 물품의 제조ㆍ구매 또는 용역의 경우에는",
        "before": 2,
        "after": 2,
        "priority": 0,
        "topic": "general-product enterprise-size price bands",
    },
    {
        "reference_id": "general_product_under_100m_rule_and_exceptions",
        "groups": ("v14-19",),
        "items": ("v17", "v18"),
        "path": "중소기업제품 구매촉진 및 판로지원에 관한 법률 시행령.txt",
        "anchor": "추정가격(「국가를 당사자로 하는 계약에 관한 법률 시행령」 제2조제1호",
        "before": 1,
        "after": 8,
        "priority": 0,
        "topic": "under-100-million small-enterprise rule and enumerated exceptions",
    },
    {
        "reference_id": "sw_large_enterprise_statutory_rule",
        "groups": ("v20",),
        "items": ("v20",),
        "path": "소프트웨어 진흥법.txt",
        "anchor": "국가기관등의 장은 소프트웨어사업 발주 시 과학기술정보통신부장관이 정하여 고시하는 사업금액 미만의 사업에 대해서는",
        "before": 1,
        "after": 6,
        "priority": 0,
        "topic": "large-enterprise participation restriction and statutory exceptions",
    },
    {
        "reference_id": "sw_amount_calculation",
        "groups": ("v20",),
        "items": ("v20",),
        "path": "중소 소프트웨어사업자의 사업 참여 지원에 관한 지침.txt",
        "anchor": "사업금액은「국가를 당사자로 하는 계약에 관한 법률 시행령」 제2조제1호에 따른 추정가격에 부가가치세를 포함한 금액으로 한다",
        "before": 5,
        "after": 3,
        "priority": 0,
        "topic": "software amount, separation, and annual-average calculation",
    },
    {
        "reference_id": "sw_participation_threshold_table",
        "groups": ("v20",),
        "items": ("v20",),
        "path": "중소 소프트웨어사업자의 사업 참여 지원에 관한 지침.txt",
        "anchor": "매출액 8천억원 이상인 대기업",
        "before": 6,
        "after": 10,
        "priority": 0,
        "topic": "20/40/80-billion software participation tiers",
    },
    {
        "reference_id": "national_joint_contract_minimum_share",
        "groups": ("v21-23",),
        "items": ("v21",),
        "path": "(계약예규) 공동계약운용요령.txt",
        "anchor": "구성원별 계약참여 최소지분율을 다음 각 호에 따라 처리한다",
        "before": 0,
        "after": 7,
        "priority": 0,
        "topic": "national joint-performance minimum share and adjustment",
    },
    {
        "reference_id": "local_joint_contract_minimum_share",
        "groups": ("v21-23",),
        "items": ("v21",),
        "path": "지방자치단체 입찰 및 계약 집행기준.txt",
        "anchor": "구성원별 계약참여 최소지분율은 5% 이상으로 해야 한다",
        "before": 1,
        "after": 2,
        "priority": 0,
        "topic": "local minimum share, adjustment, and nonapplication",
    },
    {
        "reference_id": "local_negotiated_notice_period",
        "groups": ("v21-23",),
        "items": ("v23",),
        "path": "지방자치단체를 당사자로 하는 계약에 관한 법률 시행령.txt",
        "anchor": "추정가격이 1억원 미만인 경우 10일",
        "before": 3,
        "after": 5,
        "priority": 0,
        "topic": "local negotiated-contract proposal periods",
    },
    {
        "reference_id": "local_proposal_explanation_dual_periods",
        "groups": ("v21-23",),
        "items": ("v23",),
        "path": "지방자치단체 입찰시 낙찰자 결정기준.txt",
        "anchor": "입찰공고는 제안요청서 설명일의 전일부터 기산하여 7일전에 공고해야 한다",
        "before": 2,
        "after": 3,
        "priority": 0,
        "topic": "seven-day notice-to-explanation and amount-tiered explanation-to-proposal periods",
    },
    {
        "reference_id": "national_negotiated_notice_period_contrast",
        "groups": ("v21-23",),
        "items": ("v23",),
        "path": "국가를 당사자로 하는 계약에 관한 법률 시행령.txt",
        "anchor": "제안서 제출마감일의 전날부터 기산하여 40일 전에 공고하여야 한다",
        "before": 0,
        "after": 4,
        "priority": 1,
        "topic": "national negotiated-contract period for law-scope contrast",
    },
)


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def sha256_object(value: Any) -> str:
    return sha256_text(canonical_json(value))


def _source_path(relative: str) -> pathlib.Path:
    path = (LAW_ROOT / relative).resolve()
    try:
        path.relative_to(LAW_ROOT.resolve())
    except ValueError as exc:
        raise ValueError(f"law path escapes supplied package: {relative}") from exc
    if not path.is_file():
        raise FileNotFoundError(path)
    return path


def _nth_find(text: str, needle: str, occurrence: int) -> int:
    start = 0
    for _ in range(occurrence + 1):
        found = text.find(needle, start)
        if found < 0:
            raise ValueError(f"law anchor not found: {needle}")
        start = found + len(needle)
    return found


def _excerpt(spec: Mapping[str, Any]) -> dict[str, Any]:
    path = _source_path(str(spec["path"]))
    text = path.read_text(encoding="utf-8")
    anchor = str(spec["anchor"])
    anchor_start = _nth_find(text, anchor, int(spec.get("occurrence", 0)))
    line_starts = [0]
    line_starts.extend(index + 1 for index, char in enumerate(text) if char == "\n")
    anchor_line = bisect.bisect_right(line_starts, anchor_start) - 1
    first_line = max(0, anchor_line - int(spec.get("before", 0)))
    last_line = min(
        len(line_starts) - 1, anchor_line + int(spec.get("after", 0))
    )
    start = line_starts[first_line]
    end = line_starts[last_line + 1] if last_line + 1 < len(line_starts) else len(text)
    quote = text[start:end]
    return {
        "reference_id": spec["reference_id"],
        "topic": spec["topic"],
        "support_items": list(spec["items"]),
        "priority": int(spec["priority"]),
        "source": {
            "relative_path": path.relative_to(ROOT).as_posix(),
            "source_sha256": sha256_text(text),
            "start": start,
            "end": end,
            "quote": quote,
            "quote_sha256": sha256_text(quote),
            "anchor": anchor,
            "anchor_start": anchor_start,
            "anchor_end": anchor_start + len(anchor),
        },
    }


def _hashable_context(context: Mapping[str, Any]) -> dict[str, Any]:
    payload = copy.deepcopy(dict(context))
    payload.pop("context_sha256", None)
    bounds = payload.get("bounds")
    if isinstance(bounds, dict):
        bounds.pop("rendered_chars", None)
    return payload


def _finalize(context: dict[str, Any]) -> dict[str, Any]:
    context["context_sha256"] = sha256_object(_hashable_context(context))
    context["bounds"]["rendered_chars"] = 0
    for _ in range(6):
        size = len(canonical_json(context))
        if context["bounds"]["rendered_chars"] == size:
            break
        context["bounds"]["rendered_chars"] = size
    return context


def build_law_context(group_name: str, *, max_chars: int = DEFAULT_MAX_CHARS) -> dict[str, Any]:
    """Build a deterministic group-specific context from supplied law only."""

    if group_name not in GROUP_ITEMS:
        raise ValueError(f"unknown group: {group_name}")
    if not isinstance(max_chars, int) or isinstance(max_chars, bool) or max_chars < 1:
        raise ValueError("max_chars must be a positive integer")
    rows = [
        _excerpt(spec)
        for spec in REFERENCE_SPECS
        if group_name in spec["groups"]
    ]
    rows.sort(key=lambda row: (row["priority"], row["reference_id"]))

    def assemble(selected: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
        return _finalize(
            {
                "schema_version": SCHEMA_VERSION,
                "semantic_role": "organizer_supplied_law_excerpts_without_decisions",
                "group": group_name,
                "target_items": list(GROUP_ITEMS[group_name]),
                "law_root": LAW_ROOT.relative_to(ROOT).as_posix(),
                "references": [copy.deepcopy(dict(row)) for row in selected],
                "omissions": {
                    "available": len(rows),
                    "included": len(selected),
                    "omitted": len(rows) - len(selected),
                    "budget_truncated": len(selected) < len(rows),
                },
                "bounds": {"max_chars": max_chars, "rendered_chars": 0},
            }
        )

    complete = assemble(rows)
    if len(canonical_json(complete)) <= max_chars:
        return complete
    selected: list[dict[str, Any]] = []
    for row in rows:
        trial = assemble([*selected, row])
        if len(canonical_json(trial)) <= max_chars:
            selected.append(row)
        elif row["priority"] == 0:
            raise ValueError(
                f"max_chars={max_chars} cannot retain mandatory law reference "
                f"{row['reference_id']}"
            )
    return assemble(selected)


def render_law_context(context: Mapping[str, Any]) -> str:
    rendered = canonical_json(context)
    max_chars = (context.get("bounds") or {}).get("max_chars")
    if not isinstance(max_chars, int) or len(rendered) > max_chars:
        raise ValueError("law context exceeds or lacks its declared max_chars")
    return rendered


def validate_law_context(context: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    if context.get("schema_version") != SCHEMA_VERSION:
        errors.append("schema_version_mismatch")
    group = context.get("group")
    if group not in GROUP_ITEMS:
        errors.append("unknown_group")
    elif context.get("target_items") != list(GROUP_ITEMS[str(group)]):
        errors.append("target_items_mismatch")
    def contains_labels_key(value: Any) -> bool:
        if isinstance(value, Mapping):
            return "labels" in value or any(contains_labels_key(child) for child in value.values())
        if isinstance(value, (list, tuple)):
            return any(contains_labels_key(child) for child in value)
        return False

    if contains_labels_key(context):
        errors.append("forbidden_labels_key")
    seen: set[str] = set()
    for index, row in enumerate(context.get("references") or []):
        prefix = f"reference:{index}"
        reference_id = str(row.get("reference_id") or "")
        if not reference_id or reference_id in seen:
            errors.append(f"{prefix}:duplicate_or_missing_id")
        seen.add(reference_id)
        source = row.get("source") or {}
        try:
            path = (ROOT / str(source.get("relative_path"))).resolve()
            path.relative_to(LAW_ROOT.resolve())
            text = path.read_text(encoding="utf-8")
        except (OSError, ValueError):
            errors.append(f"{prefix}:invalid_source_path")
            continue
        if sha256_text(text) != source.get("source_sha256"):
            errors.append(f"{prefix}:source_hash_mismatch")
        start, end = source.get("start"), source.get("end")
        if not isinstance(start, int) or not isinstance(end, int) or not (0 <= start <= end <= len(text)):
            errors.append(f"{prefix}:invalid_range")
            continue
        quote = text[start:end]
        if quote != source.get("quote"):
            errors.append(f"{prefix}:quote_mismatch")
        if sha256_text(quote) != source.get("quote_sha256"):
            errors.append(f"{prefix}:quote_hash_mismatch")
        anchor = str(source.get("anchor") or "")
        anchor_start, anchor_end = source.get("anchor_start"), source.get("anchor_end")
        if (
            not isinstance(anchor_start, int)
            or not isinstance(anchor_end, int)
            or text[anchor_start:anchor_end] != anchor
            or not (start <= anchor_start <= anchor_end <= end)
        ):
            errors.append(f"{prefix}:anchor_mismatch")
    expected_hash = sha256_object(_hashable_context(context))
    if context.get("context_sha256") != expected_hash:
        errors.append("context_hash_mismatch")
    rendered = canonical_json(context)
    bounds = context.get("bounds") or {}
    if bounds.get("rendered_chars") != len(rendered):
        errors.append("rendered_chars_mismatch")
    if not isinstance(bounds.get("max_chars"), int) or len(rendered) > bounds["max_chars"]:
        errors.append("max_chars_exceeded")
    return errors


def reference_manifest_sha256() -> str:
    """Commit to anchors plus all law files they rely on."""

    file_hashes = {}
    for relative in sorted({str(spec["path"]) for spec in REFERENCE_SPECS}):
        text = _source_path(relative).read_text(encoding="utf-8")
        file_hashes[relative] = sha256_text(text)
    return sha256_object(
        {"schema_version": SCHEMA_VERSION, "specs": REFERENCE_SPECS, "files": file_hashes}
    )


__all__ = [
    "DEFAULT_MAX_CHARS",
    "GROUP_ITEMS",
    "LAW_ROOT",
    "REFERENCE_SPECS",
    "SCHEMA_VERSION",
    "build_law_context",
    "reference_manifest_sha256",
    "render_law_context",
    "validate_law_context",
]
