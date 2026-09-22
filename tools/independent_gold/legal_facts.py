"""Conservative, coordinate-preserving facts for legal gold adjudication.

This module does not decide any of the 24 competition labels.  It extracts the
observable premises needed to adjudicate v1-v9, v19 and v21-v24 while keeping
uncertainty explicit.  Document-derived facts always contain absolute character
coordinates into an organizer-provided document; metadata values retain their
original field name and value.

The implementation uses only Python's standard library and organizer-provided
records/reference files.  In particular, it is deliberately independent from
the competition runtime and from model-generated decisions.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import pathlib
import re
import sys
from collections import defaultdict
from typing import Any, Iterable, Iterator, Mapping, Sequence


ROOT = pathlib.Path(__file__).resolve().parents[2]
SCHEMA_VERSION = "dacon.independent.legal_facts.v3"
RELEVANT_ITEMS = (
    "v1",
    "v2",
    "v3",
    "v4",
    "v5",
    "v6",
    "v7",
    "v8",
    "v9",
    "v19",
    "v21",
    "v22",
    "v23",
    "v24",
)
AUDIT_ITEMS = tuple(item for item in RELEVANT_ITEMS if item != "v9")

META_FIELDS: dict[str, str] = {
    "contract_law": "적용계약법",
    "work_type": "업무구분",
    "contract_method": "계약방법",
    "award_method": "낙찰방법",
    "budget_won": "배정예산금액",
    "estimated_price_won": "입찰추정가격",
    "joint_method": "공동도급구성방식",
    "information_project": "정보화사업여부",
    "catalog_codes": "세부품명번호목록",
    "region_codes": "제한지역코드목록",
    "region_restricted": "지역제한여부",
    "industry_codes": "면허업종제한목록",
    "industry_restricted": "업종제한여부",
    "posted_date": "공고게시일자",
    "opening_date": "개찰예정일자",
    "urgent": "긴급공고여부",
}


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_object(value: Any) -> str:
    rendered = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return sha256_text(rendered)


def read_jsonl_gz(path: pathlib.Path) -> Iterator[dict[str, Any]]:
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            row = json.loads(line)
            if not isinstance(row, dict) or not row.get("id"):
                raise ValueError(f"{path}:{line_number}: invalid record")
            yield row


def _documents(record: Mapping[str, Any]) -> list[dict[str, Any]]:
    documents: list[dict[str, Any]] = []
    for index, raw in enumerate(record.get("docs") or []):
        text = str(raw.get("text") or "")
        documents.append(
            {
                "doc_index": index,
                "doc_id": str(raw.get("doc_id") or f"D{index}"),
                "doc_type": str(raw.get("type") or "unknown"),
                "text": text,
                "sha256": sha256_text(text),
            }
        )
    return documents


def _span(
    document: Mapping[str, Any], start: int, end: int, *, matched_by: Iterable[str] = ()
) -> dict[str, Any]:
    text = str(document["text"])
    start = max(0, min(int(start), len(text)))
    end = max(start, min(int(end), len(text)))
    excerpt = text[start:end]
    return {
        "doc_index": int(document["doc_index"]),
        "doc_id": str(document["doc_id"]),
        "doc_type": str(document["doc_type"]),
        "start": start,
        "end": end,
        "text": excerpt,
        "text_sha256": sha256_text(excerpt),
        "source_doc_sha256": str(document["sha256"]),
        "matched_by": sorted(set(str(value) for value in matched_by if value)),
    }


_BLANK_BREAK = re.compile(r"\n[ \t]*\n")


def _clause_bounds(text: str, start: int, end: int, *, max_chars: int = 1_400) -> tuple[int, int]:
    """Return the enclosing source paragraph, capped without losing the match."""

    left = 0
    for separator in _BLANK_BREAK.finditer(text, max(0, start - max_chars), start):
        left = separator.end()
    right_match = _BLANK_BREAK.search(text, end, min(len(text), end + max_chars))
    right = right_match.start() if right_match else min(len(text), end + max_chars // 2)
    if right - left <= max_chars:
        return left, right

    left = max(left, start - max_chars // 3)
    right = min(right, left + max_chars)
    if right < end:
        right = end
        left = max(0, right - max_chars)
    line_left = text.rfind("\n", max(0, left - 120), left)
    if line_left >= 0:
        left = line_left + 1
    line_right = text.find("\n", right, min(len(text), right + 160))
    if line_right >= 0 and line_right - left <= max_chars + 160:
        right = line_right
    return left, right


def _context_bounds(
    text: str,
    start: int,
    end: int,
    *,
    before: int = 520,
    after: int = 780,
    max_chars: int = 1_600,
) -> tuple[int, int]:
    """Return a bounded context window, aligned to nearby line boundaries."""

    left = max(0, start - before)
    right = min(len(text), end + after)
    line_left = text.rfind("\n", max(0, left - 180), left)
    if line_left >= 0:
        left = line_left + 1
    line_right = text.find("\n", right, min(len(text), right + 220))
    if line_right >= 0:
        right = line_right
    if right - left > max_chars:
        left = max(0, start - min(before, max_chars // 2))
        right = min(len(text), left + max_chars)
        if right < end:
            right = end
            left = max(0, right - max_chars)
    return left, right


def _fact(
    kind: str,
    support_items: Iterable[str],
    span: Mapping[str, Any],
    **values: Any,
) -> dict[str, Any]:
    result = {
        "fact_id": f"{kind}:{span['doc_index']}:{span['start']}:{span['end']}",
        "kind": kind,
        "support_items": sorted(set(support_items)),
        "span": dict(span),
    }
    result.update(values)
    return result


def _dedupe_facts(facts: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    chosen: dict[tuple[str, int, int, int], dict[str, Any]] = {}
    for fact in facts:
        span = fact["span"]
        key = (fact["kind"], span["doc_index"], span["start"], span["end"])
        if key not in chosen:
            chosen[key] = fact
            continue
        previous = chosen[key]
        previous["support_items"] = sorted(
            set(previous.get("support_items", ())) | set(fact.get("support_items", ()))
        )
        previous["span"]["matched_by"] = sorted(
            set(previous["span"].get("matched_by", ()))
            | set(fact["span"].get("matched_by", ()))
        )
    return sorted(
        chosen.values(),
        key=lambda row: (
            row["span"]["doc_index"],
            row["span"]["start"],
            row["span"]["end"],
            row["kind"],
        ),
    )


def _meta_facts(meta: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    facts: dict[str, dict[str, Any]] = {}
    for semantic_name, source_key in META_FIELDS.items():
        present = source_key in meta and meta.get(source_key) not in (None, "", "미입력")
        facts[semantic_name] = {
            "source": "meta",
            "source_key": source_key,
            "value": meta.get(source_key),
            "status": "present" if present else "missing_or_unentered",
        }
    return facts


MONEY_RE = re.compile(
    r"(?<![\d,])(?:금\s*)?(?:"
    r"(?:\d+(?:\.\d+)?\s*억\s*(?:\d+(?:\.\d+)?\s*천만\s*)?(?:원)?)"
    r"|(?:\d+(?:\.\d+)?\s*천만\s*원?)"
    r"|(?:\d+(?:\.\d+)?\s*백만\s*원?)"
    r"|(?:\d+(?:\.\d+)?\s*만\s*원)"
    r"|(?:\d{1,3}(?:,\d{3})+\s*원)"
    r"|(?:\d{5,}\s*원)"
    r")"
)
AMOUNT_LABEL_RE = re.compile(
    r"배정예산(?:금액)?|사업예산|소요예산|예산액|예산금액|기초금액|추정가격|"
    r"추정금액|용역금액|사업비|총사업비|계약금액|준공금액",
    re.IGNORECASE,
)


def parse_money_won(raw: str) -> int | None:
    compact = re.sub(r"[\s,]", "", raw)
    compact = compact.removeprefix("금")
    compact = compact.removesuffix("원")
    if re.fullmatch(r"\d+", compact):
        return int(compact)
    total = 0.0
    consumed = ""
    for unit, multiplier in (("억", 100_000_000), ("천만", 10_000_000), ("백만", 1_000_000), ("만", 10_000)):
        match = re.search(rf"(\d+(?:\.\d+)?){unit}", compact)
        if match:
            total += float(match.group(1)) * multiplier
            consumed += match.group(0)
    if total and consumed:
        return int(round(total))
    return None


def _money_mentions(text: str, absolute_start: int = 0) -> list[dict[str, Any]]:
    mentions: list[dict[str, Any]] = []
    for match in MONEY_RE.finditer(text):
        mentions.append(
            {
                "raw": match.group(0),
                "value_won": parse_money_won(match.group(0)),
                "start": absolute_start + match.start(),
                "end": absolute_start + match.end(),
            }
        )
    return mentions


RECENCY_RE = re.compile(r"최근\s*(\d{1,2})\s*년(?:간|\s*이내)?")
PERFORMANCE_TRIGGER_RE = re.compile(
    r"수행\s*실적|이행\s*실적|납품(?:한)?\s*실적|용역\s*실적|실적\s*제한|"
    r"실적증명|실적이\s*있는|실적을\s*보유|실적\s*보유|실적이\s*있어야|"
    r"사업을\s*수행\s*\(완료\)한|실적",
    re.IGNORECASE,
)
CLIENT_RE = re.compile(
    r"국가기관|정부투자기관|공공기관|지방자치단체|지자체|공기업|준정부기관|"
    r"대학병원|학교|민간단체|민간부분|발주기관"
)


def _classify_scope(
    document_text: str,
    start: int,
    end: int,
    *,
    focus_start: int | None = None,
    focus_end: int | None = None,
) -> tuple[str, str]:
    clause = document_text[start:end]
    scope_basis = "explicit_clause"
    if focus_start is not None and focus_end is not None:
        # Long normalized paragraphs can contain several numbered clauses.  A
        # fact near an eligibility sentence must not inherit an evaluation
        # heading hundreds of characters later merely because blank-line
        # recovery put both in the same extraction span.
        local_start = max(start, focus_start - 360)
        local_end = min(end, focus_end + 360)
        clause = document_text[local_start:local_end]
        scope_basis = "explicit_anchor_context"
    before = document_text[max(0, start - 2_600) : start]
    explicit_bid = re.search(
        r"참가\s*자격|참가자격|참여\s*자격|업체이어야|있는\s*자|보유한\s*자|"
        r"입찰\s*참여|참가\s*가능|제안서\s*제출\s*자격",
        clause,
    )
    explicit_evaluation = re.search(r"평가\s*(?:기준|항목|배점)|배점|점수|정량평가", clause)
    explicit_post = re.search(r"낙찰자|계약상대자|계약\s*(?:체결\s*)?시\s*제출", clause)
    explicit_submission = re.search(
        r"(?:제안서\s*)?제출\s*서류|구비\s*서류|제출서류\s*목록",
        clause,
    )
    if focus_start is not None and explicit_bid:
        return "bid_qualification", scope_basis
    if explicit_evaluation:
        return "evaluation", scope_basis
    if explicit_bid:
        return "bid_qualification", scope_basis
    if explicit_post:
        return "post_award_or_contract", scope_basis
    if explicit_submission:
        return "proof_document", "explicit_submission_heading"

    def nearest_heading(pattern: str) -> int:
        matches = list(re.finditer(pattern, before, re.MULTILINE))
        return matches[-1].start() if matches else -1

    roles = {
        "bid_qualification": nearest_heading(
            r"^[ \t]*(?:(?:제?\s*\d+|[가-힣])\s*[.)]\s*)?[^\n]{0,20}"
            r"(?:입찰\s*참가\s*자격|입찰\s*참여\s*자격|참여\s*자격)"
            r"(?!\s*등록)[^\n]{0,60}$"
        ),
        "evaluation": nearest_heading(
            r"^[ \t]*(?:(?:제?\s*\d+|[가-힣])\s*[.)]\s*)?[^\n]{0,24}"
            r"(?:평가\s*(?:기준|항목)|정량\s*평가|배점)"
            r"[^\n]{0,60}$"
        ),
        "proof_document": nearest_heading(
            r"^[ \t]*(?:(?:제?\s*\d+|[가-힣])\s*[.)]\s*)?[^\n]{0,30}"
            r"(?:(?:제출|구비)\s*서류|제안서[^\n]{0,45}제출)"
            r"[^\n]{0,60}$"
        ),
    }
    role, position = max(roles.items(), key=lambda value: value[1])
    if position >= 0:
        return role, "nearest_heading"
    return "unknown", "insufficient_context"


def _is_proof_document_only(
    document_text: str,
    start: int,
    clause: str,
    amounts: Sequence[Mapping[str, Any]],
) -> bool:
    """Recognize a proof-document listing without inventing a new threshold."""

    if "실적증명" not in clause or amounts:
        return False
    if re.search(
        r"(?:이상|이하|초과|미만)|실적이\s*있는\s*자|실적을\s*보유한\s*자|"
        r"실적이\s*있어야|업체이어야|참가\s*(?:자격|가능)",
        clause,
    ):
        return False
    before = document_text[max(0, start - 500) : start]
    in_submission_list = (
        re.search(r"제출\s*서류", before) is not None
        or re.search(r"제출\s*서류", clause) is not None
    )
    list_entry = re.match(r"\s*(?:[-·○◦※]|\(?\d+\)?[.)]?)\s*실적증명", clause) is not None
    return in_submission_list or list_entry


def _extract_performance(document: Mapping[str, Any]) -> list[dict[str, Any]]:
    text = str(document["text"])
    facts: list[dict[str, Any]] = []
    seen: set[tuple[int, int]] = set()
    for match in PERFORMANCE_TRIGGER_RE.finditer(text):
        start, end = _clause_bounds(text, match.start(), match.end())
        if (start, end) in seen:
            continue
        seen.add((start, end))
        clause = text[start:end]
        scope, scope_basis = _classify_scope(text, start, end)
        amounts = _money_mentions(clause, start)
        proof_document_only = _is_proof_document_only(text, start, clause, amounts)
        if proof_document_only:
            scope, scope_basis = "proof_document", "submission_list_without_threshold"
        recency = [int(value) for value in RECENCY_RE.findall(clause)]
        if re.search(r"단일\s*(?:건|계약|용역|행사)", clause):
            aggregation = "single"
        elif re.search(r"합산|누계|총\s*실적", clause):
            aggregation = "aggregate"
        else:
            aggregation = "unspecified"
        clients = sorted(set(CLIENT_RE.findall(clause)))
        if proof_document_only or scope == "proof_document":
            semantic_role = "proof_document"
            creates_bid_eligibility_requirement: bool | None = False
            creates_monetary_threshold = False
        elif scope == "evaluation":
            semantic_role = "evaluation_metric"
            creates_bid_eligibility_requirement = False
            creates_monetary_threshold = False
        elif scope == "post_award_or_contract":
            semantic_role = "post_award_reporting"
            creates_bid_eligibility_requirement = False
            creates_monetary_threshold = False
        elif scope == "bid_qualification":
            semantic_role = "eligibility_requirement"
            creates_bid_eligibility_requirement = True
            creates_monetary_threshold = bool(amounts)
        else:
            semantic_role = "performance_mention_unknown"
            creates_bid_eligibility_requirement = None
            creates_monetary_threshold = False
        ambiguity: list[str] = []
        if scope == "unknown":
            ambiguity.append("requirement_scope_unknown")
        if not amounts and semantic_role == "eligibility_requirement":
            ambiguity.append("no_explicit_performance_amount")
        span = _span(document, start, end, matched_by=(match.group(0),))
        facts.append(
            _fact(
                "performance_requirement",
                ("v2", "v3", "v4", "v8"),
                span,
                scope=scope,
                scope_basis=scope_basis,
                semantic_role=semantic_role,
                creates_bid_eligibility_requirement=creates_bid_eligibility_requirement,
                creates_monetary_threshold=creates_monetary_threshold,
                recency_years=sorted(set(recency)),
                aggregation=aggregation,
                amount_mentions=amounts,
                client_or_issuer_terms=clients,
                subject_text=clause,
                boundary_materiality={
                    "v3": (
                        "aggregation_does_not_change_amount_ratio"
                        if creates_monetary_threshold
                        else "not_a_monetary_eligibility_threshold"
                    )
                },
                ambiguity=ambiguity,
            )
        )
    return facts


INSTITUTION_TRIGGER_RE = re.compile(
    r"대학|대학교|산학협력단|연구기관|학회|의료기관|병원|공공기관|특정기관"
)
EXCLUSIVE_RE = re.compile(
    r"만\s*(?:참여|참가|입찰|제출)\s*(?:가능|할\s*수)|에\s*한하여|"
    r"(?:기관|단체|법인|업체)만|자격을\s*제한|참가\s*대상"
)


def _extract_institution_restrictions(document: Mapping[str, Any]) -> list[dict[str, Any]]:
    text = str(document["text"])
    facts: list[dict[str, Any]] = []
    seen: set[tuple[int, int]] = set()
    for match in INSTITUTION_TRIGGER_RE.finditer(text):
        start, end = _clause_bounds(text, match.start(), match.end())
        if (start, end) in seen:
            continue
        clause = text[start:end]
        if not EXCLUSIVE_RE.search(clause):
            continue
        seen.add((start, end))
        scope, scope_basis = _classify_scope(text, start, end)
        institutions = sorted(set(INSTITUTION_TRIGGER_RE.findall(clause)))
        ambiguity = [] if scope == "bid_qualification" else ["qualification_scope_not_explicit"]
        facts.append(
            _fact(
                "institution_qualification_restriction",
                ("v1",),
                _span(document, start, end, matched_by=(match.group(0),)),
                scope=scope,
                scope_basis=scope_basis,
                institution_terms=institutions,
                exclusive_language=EXCLUSIVE_RE.search(clause).group(0),
                ambiguity=ambiguity,
            )
        )
    return facts


METRO_REGIONS = (
    "서울특별시",
    "부산광역시",
    "대구광역시",
    "인천광역시",
    "광주광역시",
    "대전광역시",
    "울산광역시",
    "세종특별자치시",
    "경기도",
    "강원특별자치도",
    "강원도",
    "충청북도",
    "충청남도",
    "전북특별자치도",
    "전라북도",
    "전라남도",
    "경상북도",
    "경상남도",
    "제주특별자치도",
    "제주도",
)
REGION_TOKEN_RE = re.compile(r"\[(?:등록)?지역:[^\]]+\]")
REGION_TRIGGER_RE = re.compile(
    r"주된\s*영업소|본점\s*소재지|본점소재지|본사\s*\([^)]*영업소[^)]*\)|"
    r"본사\s*소재지|법인등기부상\s*본점|지역\s*제한"
)
REGION_NAME_RE = re.compile("|".join(re.escape(name) for name in sorted(METRO_REGIONS, key=len, reverse=True)))


def _region_mentions(clause: str, absolute_start: int) -> list[dict[str, Any]]:
    raw_matches: list[tuple[int, int, str, str, str | None]] = []
    for match in REGION_TOKEN_RE.finditer(clause):
        raw = match.group(0)
        unit_match = re.search(r"단위=([^|\]]+)", raw)
        metro_match = re.search(r"광역=([^|\]]+)", raw)
        raw_matches.append(
            (
                match.start(),
                match.end(),
                raw,
                unit_match.group(1) if unit_match else "unknown",
                metro_match.group(1) if metro_match else None,
            )
        )
    occupied = [(start, end) for start, end, *_ in raw_matches]
    for match in REGION_NAME_RE.finditer(clause):
        if any(start <= match.start() < end for start, end in occupied):
            continue
        raw_matches.append((match.start(), match.end(), match.group(0), "광역", match.group(0)))
    raw_matches.sort()
    # An anonymized basic-region token already carries its parent metro.  In
    # text such as ``서울특별시 [지역:r1|단위=기초|광역=서울특별시]`` the two
    # adjacent strings are one hierarchical place name, not two unconnected
    # regions whose Boolean relation is unknown.
    compacted: list[tuple[int, int, str, str, str | None]] = []
    for index, current in enumerate(raw_matches):
        if index + 1 < len(raw_matches):
            following = raw_matches[index + 1]
            gap = clause[current[1] : following[0]]
            same_hierarchy = (
                current[3] == "광역"
                and following[3] == "기초"
                and following[4] == current[4]
                and re.fullmatch(r"[\s·ㆍ,()\-/]*", gap) is not None
            )
            if same_hierarchy:
                continue
        compacted.append(current)
    raw_matches = compacted
    return [
        {
            "raw": raw,
            "unit": unit,
            "metro": metro,
            "start": absolute_start + start,
            "end": absolute_start + end,
        }
        for start, end, raw, unit, metro in raw_matches
    ]


def _region_connective(clause: str, regions: Sequence[Mapping[str, Any]], absolute_start: int) -> tuple[str, list[str]]:
    if len(regions) <= 1:
        return "single", []
    connectors: list[str] = []
    for left, right in zip(regions, regions[1:]):
        gap = clause[int(left["end"]) - absolute_start : int(right["start"]) - absolute_start]
        if "또는" in gap or "혹은" in gap:
            connectors.append("OR")
        elif re.search(r"(?:및|그리고|와|과)", gap):
            connectors.append("AND")
        elif "," in gap or "，" in gap:
            connectors.append("COMMA")
        else:
            connectors.append("UNMARKED")
    unique = set(connectors)
    if unique == {"OR"}:
        return "or", connectors
    if unique == {"AND"}:
        return "and", connectors
    if unique == {"COMMA"}:
        return "comma_list_ambiguous", connectors
    return "mixed_or_unknown", connectors


def _extract_regions(document: Mapping[str, Any]) -> list[dict[str, Any]]:
    text = str(document["text"])
    facts: list[dict[str, Any]] = []
    seen: set[tuple[int, int]] = set()
    for match in REGION_TRIGGER_RE.finditer(text):
        start, end = _clause_bounds(text, match.start(), match.end())
        if (start, end) in seen:
            continue
        seen.add((start, end))
        clause = text[start:end]
        regions = _region_mentions(clause, start)
        connective, connector_sequence = _region_connective(clause, regions, start)
        scope, scope_basis = _classify_scope(
            text,
            start,
            end,
            focus_start=match.start(),
            focus_end=match.end(),
        )
        ambiguity: list[str] = []
        if not regions:
            ambiguity.append("region_not_resolved_from_clause")
        if connective in {"comma_list_ambiguous", "mixed_or_unknown"}:
            ambiguity.append("region_boolean_relation_ambiguous")
        if "지점 및 대리점 포함" in clause or "지사" in clause:
            ambiguity.append("headquarters_basis_broadened_or_unclear")
        if scope == "unknown":
            ambiguity.append("requirement_scope_unknown")
        facts.append(
            _fact(
                "headquarters_region_requirement",
                ("v5", "v6", "v7", "v8", "v24"),
                _span(document, start, end, matched_by=(match.group(0),)),
                scope=scope,
                scope_basis=scope_basis,
                headquarters_language=match.group(0),
                regions=regions,
                connective=connective,
                connector_sequence=connector_sequence,
                ambiguity=ambiguity,
            )
        )
    return facts


MODEL_TRIGGER_RE = re.compile(
    r"제조사[·ㆍ/ ]*모델명|제조사|모델명|상표|브랜드|제품명|품번|"
    r"part\s*number|특정\s*모델|chipset|superchip|matrice",
    re.IGNORECASE,
)
EQUIVALENT_RE = re.compile(r"동등\s*(?:또는|이상)|동급\s*이상|or\s+equivalent", re.IGNORECASE)
MODEL_VALUE_RE = re.compile(
    r"(?:모델명|제품명|품번|part\s*number)\s*[:：]?\s*([^\n,;]{2,80})",
    re.IGNORECASE,
)


def _extract_model_candidates(document: Mapping[str, Any]) -> list[dict[str, Any]]:
    text = str(document["text"])
    facts: list[dict[str, Any]] = []
    seen: set[tuple[int, int]] = set()
    for match in MODEL_TRIGGER_RE.finditer(text):
        start, end = _clause_bounds(text, match.start(), match.end(), max_chars=1_000)
        if (start, end) in seen:
            continue
        clause = text[start:end]
        # A bare statutory use of "제조사" is too weak to be a model candidate.
        has_label_value = MODEL_VALUE_RE.search(clause) is not None
        has_modelish_token = re.search(r"\b(?=[A-Z0-9-]{4,}\b)(?=[A-Z0-9-]*[A-Z])(?=[A-Z0-9-]*\d)[A-Z0-9-]+\b", clause)
        strong_trigger = re.search(r"모델명|제품명|품번|part\s*number|superchip|matrice|chipset", clause, re.IGNORECASE)
        if not (has_label_value or has_modelish_token or strong_trigger):
            continue
        seen.add((start, end))
        values = [value.strip() for value in MODEL_VALUE_RE.findall(clause)]
        equivalent = bool(EQUIVALENT_RE.search(clause))
        ambiguity = [] if values else ["model_value_not_cleanly_delimited"]
        facts.append(
            _fact(
                "specific_model_candidate",
                ("v9",),
                _span(document, start, end, matched_by=(match.group(0),)),
                candidate_values=values,
                equivalent_wording_present=equivalent,
                ambiguity=ambiguity,
            )
        )
    return facts


PLEDGE_TRIGGER_RE = re.compile(
    r"물품공급[·ㆍ/ ]*기술지원\s*(?:확약서|협약서)|물품공급\s*확약서|"
    r"기술지원\s*확약서|공급확약서",
    re.IGNORECASE,
)


def _extract_pledges(document: Mapping[str, Any]) -> list[dict[str, Any]]:
    text = str(document["text"])
    facts: list[dict[str, Any]] = []
    seen: set[tuple[int, int]] = set()
    for match in PLEDGE_TRIGGER_RE.finditer(text):
        start, end = _clause_bounds(text, match.start(), match.end(), max_chars=1_300)
        if (start, end) in seen:
            continue
        seen.add((start, end))
        clause = text[start:end]
        issuers = sorted(
            set(
                re.findall(
                    r"물품제조사|제조사|공급사|기술지원사|수입사|총판|대리점|발주기관",
                    clause,
                )
            )
        )
        timings: list[str] = []
        if re.search(r"입찰서?\s*제출\s*마감일\s*전(?:까지|일)", clause):
            timings.append("before_bid_deadline")
        if re.search(r"입찰\s*(?:참가|시|서류).{0,50}제출", clause):
            timings.append("with_bid_or_participation")
        if re.search(r"계약\s*(?:체결\s*)?시.{0,40}제출|계약\s*시\s*제출", clause):
            timings.append("at_contract")
        if re.search(r"낙찰자.{0,60}제출", clause):
            timings.append("after_award")
        ambiguity: list[str] = []
        if not issuers:
            ambiguity.append("issuer_not_explicit")
        if not timings:
            ambiguity.append("required_time_not_explicit")
        if "before_bid_deadline" in timings and "at_contract" in timings:
            ambiguity.append("possession_and_submission_times_differ")
        facts.append(
            _fact(
                "third_party_supply_or_support_pledge",
                ("v19",),
                _span(document, start, end, matched_by=(match.group(0),)),
                issuer_terms=issuers,
                required_times=sorted(set(timings)),
                verbs=sorted(set(re.findall(r"보유|제출|발급|확인", clause))),
                ambiguity=ambiguity,
            )
        )
    return facts


JOINT_TRIGGER_RE = re.compile(r"공동수급|공동이행|분담이행|공동도급|공동협정서|공동수급협정서")
MIN_SHARE_RE = re.compile(
    r"(?:최소\s*)?(?:지분(?:참여)?\s*비율|지분율|출자비율|참여지분)\s*(?:은|이|:)?\s*"
    r"(\d+(?:\.\d+)?)\s*%\s*(?:이상|초과|이하|미만)?",
    re.IGNORECASE,
)


def _extract_joint_terms(document: Mapping[str, Any]) -> list[dict[str, Any]]:
    text = str(document["text"])
    facts: list[dict[str, Any]] = []
    seen: set[tuple[int, int]] = set()
    for match in JOINT_TRIGGER_RE.finditer(text):
        start, end = _clause_bounds(text, match.start(), match.end(), max_chars=1_300)
        if (start, end) in seen:
            continue
        seen.add((start, end))
        clause = text[start:end]
        if "공동이행" in clause:
            method = "joint_performance"
        elif "분담이행" in clause:
            method = "divided_performance"
        elif re.search(r"공동(?:수급|계약|도급).{0,30}(?:불허|불가|허용하지)", clause):
            method = "not_allowed"
        else:
            method = "unspecified"
        shares = [float(value) for value in MIN_SHARE_RE.findall(clause)]
        ambiguity: list[str] = []
        if method == "unspecified":
            ambiguity.append("joint_method_unspecified")
        if not shares:
            ambiguity.append("minimum_member_share_not_stated")
        facts.append(
            _fact(
                "joint_contract_term",
                ("v21",),
                _span(document, start, end, matched_by=(match.group(0),)),
                method=method,
                minimum_member_share_percent=shares,
                ambiguity=ambiguity,
            )
        )
    return facts


BRIEFING_TRIGGER_RE = re.compile(
    r"현장\s*설명회|사업\s*설명회|과업\s*설명회|제안요청(?:서)?\s*설명회|"
    r"제안요청서\s*설명|설명회\s*(?:참석|불참|미참석|개최)",
    re.IGNORECASE,
)
FULL_DATE_RE = re.compile(
    r"(?P<year>20\d{2})\s*[.년/-]\s*(?P<month>\d{1,2})\s*[.월/-]\s*"
    r"(?P<day>\d{1,2})\s*\.?\s*(?:일)?(?:\s*\([^)]*\))?"
    r"(?:\s*(?P<hour>\d{1,2})\s*:\s*(?P<minute>\d{2}))?"
)


def _date_mentions(
    text: str,
    absolute_start: int,
    *,
    document: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    mentions: list[dict[str, Any]] = []
    for match in FULL_DATE_RE.finditer(text):
        groups = match.groupdict()
        normalized = f"{int(groups['year']):04d}-{int(groups['month']):02d}-{int(groups['day']):02d}"
        if groups.get("hour") is not None:
            normalized += f"T{int(groups['hour']):02d}:{int(groups['minute']):02d}"
        local_before = text[max(0, match.start() - 100) : match.start()]
        local_after = text[match.end() : min(len(text), match.end() + 50)]
        local = local_before + local_after
        role_hints: list[str] = []
        briefing_cues = list(
            re.finditer(r"설명회|사업\s*설명|과업\s*설명|제안요청서\s*설명", local_before)
        )
        deadline_cues = list(
            re.finditer(r"마감|접수\s*(?:일시|기간)?|제출\s*(?:마감|기간)", local_before)
        )
        # A nearby proposal-deadline label supersedes an older briefing word in
        # the same 100-character window.  Otherwise one submission date can be
        # misreported as both the briefing and the deadline.
        briefing_position = briefing_cues[-1].start() if briefing_cues else -1
        deadline_position = deadline_cues[-1].start() if deadline_cues else -1
        if briefing_position >= deadline_position and briefing_position >= 0:
            role_hints.append("briefing")
        if deadline_position >= 0:
            role_hints.append("submission_deadline_or_period")
        if re.search(r"공고\s*(?:기간|게시|일자)", local_before):
            role_hints.append("notice_period_or_posting")
        if re.search(r"개찰", local):
            role_hints.append("opening")
        mention: dict[str, Any] = {
                "raw": match.group(0),
                "normalized": normalized,
                "role_hints": sorted(set(role_hints)) or ["unresolved"],
                "start": absolute_start + match.start(),
                "end": absolute_start + match.end(),
            }
        if document is not None:
            mention["source_span"] = _span(
                document,
                absolute_start + match.start(),
                absolute_start + match.end(),
                matched_by=("date_mention",),
            )
        mentions.append(mention)
    return mentions


def _extract_briefings(document: Mapping[str, Any]) -> list[dict[str, Any]]:
    text = str(document["text"])
    # Submission deadlines are frequently stated in an early schedule table while
    # the briefing occurrence appears much later in an RFP. Scan the whole
    # organizer-provided document once so v23 does not silently lose that premise
    # merely because the two dates fall outside the same local context window.
    document_dates = _date_mentions(text, 0, document=document)
    document_deadline_dates = [
        value
        for value in document_dates
        if "submission_deadline_or_period" in value["role_hints"]
    ]
    facts: list[dict[str, Any]] = []
    seen: set[tuple[int, int]] = set()
    for match in BRIEFING_TRIGGER_RE.finditer(text):
        start, end = _context_bounds(text, match.start(), match.end())
        if (start, end) in seen:
            continue
        context = text[start:end]
        # Index/table-of-contents mentions without an event, consequence or date
        # are not promoted to a factual briefing occurrence.
        has_event = re.search(r"개최|실시|일시|장소|참석|불참|미참석", context) or FULL_DATE_RE.search(
            context
        )
        if not has_event:
            continue
        seen.add((start, end))
        if re.search(
            r"참석.{0,50}(?:한하여|자격|가능)|불참.{0,50}(?:제외|불허|접수하지)|"
            r"미참석.{0,50}(?:제외|불허|접수하지)|참석하지\s*아니한.{0,60}허용되지",
            context,
            re.DOTALL,
        ):
            attendance = "mandatory_for_bid_or_proposal"
        elif re.search(r"참석여부와\s*상관없이|참석\s*여부와\s*관계없이", context):
            attendance = "explicitly_optional"
        elif re.search(
            r"(?:설명회\s*)?참석(?:은|이)?\s*(?:필수|의무)|"
            r"반드시\s*(?:설명회에\s*)?참석|"
            r"(?:설명회에\s*)?필히\s*참석|"
            r"(?:설명회에\s*)?참석하여야\s*한다",
            context,
            re.DOTALL,
        ):
            attendance = "not_resolved"
        else:
            attendance = "no_eligibility_link_observed"
        consequences = sorted(
            set(
                re.findall(
                    r"입찰\s*참가\s*대상에서\s*제외|입찰\s*참가는\s*허용되지|"
                    r"제안서는\s*접수하지\s*않음|제안서\s*제출\s*자격을\s*부여",
                    context,
                )
            )
        )
        dates = _date_mentions(context, start, document=document)
        briefing_dates = [value for value in dates if "briefing" in value["role_hints"]]
        if briefing_dates:
            briefing_date_source_status = "explicit_date_candidates_observed"
            interval_assessability = "measurable_from_supplied_source"
        elif re.search(
            r"(?:일정|일시|날짜)(?:\s*[·ㆍ,/]\s*|\s*(?:및|와|과)\s*)?"
            r"(?:장소)?[^.\n]{0,40}(?:개별\s*안내|추후\s*(?:안내|통보|공지))",
            context,
        ):
            briefing_date_source_status = "prospective_individual_notice_only"
            interval_assessability = "not_measurable_without_actual_briefing_date"
        else:
            briefing_date_source_status = "no_explicit_date_observed"
            interval_assessability = "not_measurable_without_actual_briefing_date"
        local_deadline_dates = [
            value for value in dates if "submission_deadline_or_period" in value["role_hints"]
        ]
        deadline_dates_by_coordinate = {
            (value["start"], value["end"]): value
            for value in (*local_deadline_dates, *document_deadline_dates)
        }
        deadline_dates = [
            deadline_dates_by_coordinate[key]
            for key in sorted(deadline_dates_by_coordinate)
        ]
        ambiguity: list[str] = []
        if attendance == "not_resolved":
            ambiguity.append("attendance_effect_not_resolved")
        if not briefing_dates:
            ambiguity.append("briefing_date_not_explicit")
        facts.append(
            _fact(
                "briefing_occurrence",
                ("v22", "v23"),
                _span(document, start, end, matched_by=(match.group(0),)),
                attendance_effect=attendance,
                consequence_terms=consequences,
                date_mentions=dates,
                briefing_date_candidates=briefing_dates,
                briefing_date_source_status=briefing_date_source_status,
                interval_assessability=interval_assessability,
                submission_deadline_candidates=deadline_dates,
                place_language_present=bool(re.search(r"장소\s*[:：]|회의실|센터|별관", context)),
                ambiguity=ambiguity,
            )
        )
    return facts


CONTRACT_METHOD_RE = re.compile(r"일반\s*경쟁|제한\s*경쟁|지명\s*경쟁|수의\s*계약")
AWARD_METHOD_RE = re.compile(
    r"협상에\s*의한\s*계약|적격\s*심사(?:제)?|규격\s*[·ㆍ/]?\s*가격\s*동시\s*입찰|"
    r"소액\s*수의\s*견적|최저가"
)
WORK_TYPE_RE = re.compile(
    r"일반\s*용역|기술\s*용역|학술\s*[.·ㆍ]?\s*연구\s*용역|"
    r"용\s*역\s*입\s*찰|물\s*품\s*입\s*찰|공\s*사\s*입\s*찰|"
    r"물품\s*(?:구매|제조|공급)"
)
INDUSTRY_RE = re.compile(r"[^\n]{0,100}(?:업종코드|업종\s*코드|면허|업종제한)[^\n]{0,160}")


def _extract_comparable_mentions(document: Mapping[str, Any]) -> dict[str, list[dict[str, Any]]]:
    text = str(document["text"])
    result: dict[str, list[dict[str, Any]]] = {
        "amount": [],
        "contract_method": [],
        "award_method": [],
        "work_type": [],
        "industry": [],
    }
    for label in AMOUNT_LABEL_RE.finditer(text):
        start, end = _clause_bounds(text, label.start(), label.end(), max_chars=900)
        clause = text[start:end]
        amounts = _money_mentions(clause, start)
        if not amounts:
            continue
        result["amount"].append(
            _fact(
                "document_amount_claim",
                ("v2", "v3", "v5", "v6", "v7", "v23", "v24"),
                _span(document, start, end, matched_by=(label.group(0),)),
                label=label.group(0),
                amount_mentions=amounts,
                ambiguity=[],
            )
        )
    for kind, pattern, semantic in (
        ("document_contract_method_claim", CONTRACT_METHOD_RE, "contract_method"),
        ("document_award_method_claim", AWARD_METHOD_RE, "award_method"),
    ):
        for match in pattern.finditer(text):
            start, end = _clause_bounds(text, match.start(), match.end(), max_chars=700)
            result[semantic].append(
                _fact(
                    kind,
                    ("v22", "v23", "v24"),
                    _span(document, start, end, matched_by=(match.group(0),)),
                    raw_value=match.group(0),
                    normalized_value=re.sub(r"\s+", "", match.group(0)),
                    ambiguity=[],
                )
            )
    # Work-type headings and explicit names are most reliable near the front of
    # each document.  Limiting the scan avoids treating every later use of
    # "물품" or "용역" as a declaration of the procurement category.
    front = text[:4_000]
    for match in WORK_TYPE_RE.finditer(front):
        start, end = _clause_bounds(text, match.start(), match.end(), max_chars=700)
        result["work_type"].append(
            _fact(
                "document_work_type_claim",
                ("v2", "v3", "v4", "v5", "v6", "v7", "v8", "v24"),
                _span(document, start, end, matched_by=(match.group(0),)),
                raw_value=match.group(0),
                normalized_value=re.sub(r"\s+", "", match.group(0)),
                ambiguity=["document_wording_requires_category_normalization"],
            )
        )
    for match in INDUSTRY_RE.finditer(text):
        start, end = _clause_bounds(text, match.start(), match.end(), max_chars=900)
        result["industry"].append(
            _fact(
                "document_industry_claim",
                ("v24",),
                _span(document, start, end, matched_by=(match.group(0)[:80],)),
                raw_value=match.group(0),
                ambiguity=["industry_equivalence_not_adjudicated"],
            )
        )
    for key in result:
        result[key] = _dedupe_facts(result[key])
    return result


def _source_law_mentions(document: Mapping[str, Any]) -> list[dict[str, Any]]:
    pattern = re.compile(
        r"국가를\s*당사자로\s*하는\s*계약에\s*관한\s*법률|국가계약법|"
        r"지방자치단체를\s*당사자로\s*하는\s*계약에\s*관한\s*법률|지방계약법"
    )
    text = str(document["text"])
    facts: list[dict[str, Any]] = []
    for match in pattern.finditer(text):
        start, end = _clause_bounds(text, match.start(), match.end(), max_chars=600)
        facts.append(
            _fact(
                "document_contract_law_mention",
                ("v1", "v2", "v3", "v4", "v5", "v6", "v7", "v8", "v21", "v23"),
                _span(document, start, end, matched_by=(match.group(0),)),
                raw_value=match.group(0),
                ambiguity=["citation_does_not_by_itself_prove_applicability"],
            )
        )
    return _dedupe_facts(facts)


def load_item_reference(path: pathlib.Path) -> dict[str, Any]:
    """Load only the relevant organizer item-to-law reference entries."""

    payload = json.loads(path.read_text(encoding="utf-8"))
    source_items = payload.get("항목") or {}
    missing = [item for item in RELEVANT_ITEMS if item not in source_items]
    if missing:
        raise ValueError(f"item reference missing: {missing}")
    selected = {item: source_items[item] for item in RELEVANT_ITEMS}
    return {
        "source": str(path),
        "source_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "basis_date": payload.get("기준일"),
        "items": selected,
    }


def extract_legal_facts(
    record: Mapping[str, Any], *, item_reference: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    """Extract observable facts without converting them into final labels."""

    documents = _documents(record)
    meta = record.get("meta") if isinstance(record.get("meta"), Mapping) else {}
    categories: dict[str, list[dict[str, Any]]] = {
        "institution_qualification_restrictions": [],
        "performance_requirements": [],
        "headquarters_region_requirements": [],
        "specific_model_candidates": [],
        "third_party_pledges": [],
        "joint_contract_terms": [],
        "briefings": [],
        "law_mentions": [],
        "document_amount_claims": [],
        "document_contract_method_claims": [],
        "document_award_method_claims": [],
        "document_work_type_claims": [],
        "document_industry_claims": [],
    }

    for document in documents:
        categories["institution_qualification_restrictions"].extend(
            _extract_institution_restrictions(document)
        )
        categories["performance_requirements"].extend(_extract_performance(document))
        categories["headquarters_region_requirements"].extend(_extract_regions(document))
        categories["specific_model_candidates"].extend(_extract_model_candidates(document))
        categories["third_party_pledges"].extend(_extract_pledges(document))
        categories["joint_contract_terms"].extend(_extract_joint_terms(document))
        categories["briefings"].extend(_extract_briefings(document))
        categories["law_mentions"].extend(_source_law_mentions(document))
        comparable = _extract_comparable_mentions(document)
        categories["document_amount_claims"].extend(comparable["amount"])
        categories["document_contract_method_claims"].extend(comparable["contract_method"])
        categories["document_award_method_claims"].extend(comparable["award_method"])
        categories["document_work_type_claims"].extend(comparable["work_type"])
        categories["document_industry_claims"].extend(comparable["industry"])

    for key in categories:
        categories[key] = _dedupe_facts(categories[key])

    ambiguities: list[dict[str, Any]] = []
    dropped = record.get("dropped_doc_counts") or {}
    if any(isinstance(value, (int, float)) and value > 0 for value in dropped.values()):
        ambiguities.append(
            {
                "code": "source_documents_dropped",
                "detail": dict(dropped),
                "effect": "absence and cross-document consistency cannot be presumed",
            }
        )
    completeness = record.get("input_completeness") or {}
    if isinstance(completeness, Mapping) and any(value is False for value in completeness.values()):
        ambiguities.append(
            {
                "code": "input_not_fully_observed",
                "detail": dict(completeness),
                "effect": "missing facts remain unknown rather than negative",
            }
        )

    meta_facts = _meta_facts(meta)
    comparisons = {
        "amounts": {
            "meta_budget": meta_facts["budget_won"],
            "meta_estimated_price": meta_facts["estimated_price_won"],
            "document_fact_ids": [row["fact_id"] for row in categories["document_amount_claims"]],
            "resolution": "not_adjudicated",
        },
        "contract_method": {
            "meta": meta_facts["contract_method"],
            "document_fact_ids": [
                row["fact_id"] for row in categories["document_contract_method_claims"]
            ],
            "resolution": "not_adjudicated",
        },
        "award_method": {
            "meta": meta_facts["award_method"],
            "document_fact_ids": [
                row["fact_id"] for row in categories["document_award_method_claims"]
            ],
            "resolution": "not_adjudicated",
        },
        "contract_law": {
            "meta": meta_facts["contract_law"],
            "document_fact_ids": [row["fact_id"] for row in categories["law_mentions"]],
            "resolution": "not_adjudicated",
        },
        "work_type": {
            "meta": meta_facts["work_type"],
            "document_fact_ids": [
                row["fact_id"] for row in categories["document_work_type_claims"]
            ],
            "resolution": "not_adjudicated",
        },
        "region": {
            "meta_restricted": meta_facts["region_restricted"],
            "meta_codes": meta_facts["region_codes"],
            "document_fact_ids": [
                row["fact_id"] for row in categories["headquarters_region_requirements"]
            ],
            "resolution": "not_adjudicated",
        },
        "industry": {
            "meta_restricted": meta_facts["industry_restricted"],
            "meta_codes": meta_facts["industry_codes"],
            "document_fact_ids": [row["fact_id"] for row in categories["document_industry_claims"]],
            "resolution": "not_adjudicated",
        },
    }

    result: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "record_id": str(record.get("id") or ""),
        "source_sha256": sha256_object(record),
        "label_decisions_present": False,
        "metadata_facts": meta_facts,
        "source_completeness": {
            "input_completeness": dict(completeness) if isinstance(completeness, Mapping) else completeness,
            "dropped_doc_counts": dict(dropped) if isinstance(dropped, Mapping) else dropped,
            "document_count": len(documents),
        },
        "facts": categories,
        "document_meta_comparisons": comparisons,
        "ambiguities": ambiguities,
    }
    if item_reference is not None:
        result["item_reference"] = {
            "basis_date": item_reference.get("basis_date"),
            "source_sha256": item_reference.get("source_sha256"),
            "items": list(item_reference.get("items") or ()),
        }
    return result


def _iter_nested_spans(value: Any) -> Iterator[dict[str, Any]]:
    if isinstance(value, Mapping):
        required = {
            "doc_index",
            "start",
            "end",
            "text",
            "text_sha256",
            "source_doc_sha256",
        }
        if required.issubset(value):
            yield dict(value)
        for nested in value.values():
            yield from _iter_nested_spans(nested)
    elif isinstance(value, (list, tuple)):
        for nested in value:
            yield from _iter_nested_spans(nested)


def iter_fact_spans(result: Mapping[str, Any], item: str | None = None) -> Iterator[dict[str, Any]]:
    for facts in (result.get("facts") or {}).values():
        for fact in facts:
            if item is not None and item not in fact.get("support_items", ()):
                continue
            yield from _iter_nested_spans(fact)


def validate_fact_coordinates(record: Mapping[str, Any], result: Mapping[str, Any]) -> list[str]:
    documents = _documents(record)
    errors: list[str] = []
    for span in iter_fact_spans(result):
        index = span.get("doc_index")
        if not isinstance(index, int) or not (0 <= index < len(documents)):
            errors.append(f"invalid_doc_index:{index}")
            continue
        document = documents[index]
        start, end = span.get("start"), span.get("end")
        if not isinstance(start, int) or not isinstance(end, int) or not (0 <= start <= end <= len(document["text"])):
            errors.append(f"invalid_range:{index}:{start}:{end}")
            continue
        exact = document["text"][start:end]
        if exact != span.get("text"):
            errors.append(f"text_mismatch:{index}:{start}:{end}")
        if sha256_text(exact) != span.get("text_sha256"):
            errors.append(f"text_hash_mismatch:{index}:{start}:{end}")
        if document["sha256"] != span.get("source_doc_sha256"):
            errors.append(f"document_hash_mismatch:{index}")
    return errors


def _locate_evidence(record: Mapping[str, Any], evidence: str) -> list[tuple[int, int, int]]:
    locations: list[tuple[int, int, int]] = []
    for index, raw_document in enumerate(record.get("docs") or []):
        text = str(raw_document.get("text") or "")
        offset = 0
        while evidence:
            found = text.find(evidence, offset)
            if found < 0:
                break
            locations.append((index, found, found + len(evidence)))
            offset = found + 1
    return locations


def audit_dev_evidence(dev_path: pathlib.Path, labels_path: pathlib.Path) -> dict[str, Any]:
    """Measure exact-coordinate coverage of organizer positive evidence.

    Official labels are read only for this offline audit.  They are never read
    by :func:`extract_legal_facts` and no label value is copied into its output.
    """

    records = {row["id"]: row for row in read_jsonl_gz(dev_path)}
    with labels_path.open("r", encoding="utf-8-sig", newline="") as handle:
        labels = list(csv.DictReader(handle))

    total_official_nonempty = 0
    relevant = 0
    located = 0
    covered = 0
    misses: list[dict[str, Any]] = []
    by_item: dict[str, dict[str, int]] = {
        item: {"nonempty_positive_evidence": 0, "located": 0, "covered": 0}
        for item in AUDIT_ITEMS
    }
    extracted_cache: dict[str, dict[str, Any]] = {}

    for row in labels:
        record_id = row["id"]
        record = records[record_id]
        for number in range(1, 25):
            if row.get(f"v{number}") == "1" and (row.get(f"e{number}") or "").strip():
                total_official_nonempty += 1
        for item in AUDIT_ITEMS:
            evidence = (row.get(f"e{item[1:]}") or "").strip()
            if row.get(item) != "1" or not evidence:
                continue
            relevant += 1
            by_item[item]["nonempty_positive_evidence"] += 1
            locations = _locate_evidence(record, evidence)
            if locations:
                located += 1
                by_item[item]["located"] += 1
            if record_id not in extracted_cache:
                extracted_cache[record_id] = extract_legal_facts(record)
            spans = list(iter_fact_spans(extracted_cache[record_id], item=item))
            is_covered = any(
                span["doc_index"] == doc_index
                and span["start"] <= start
                and span["end"] >= end
                for doc_index, start, end in locations
                for span in spans
            )
            if is_covered:
                covered += 1
                by_item[item]["covered"] += 1
            else:
                misses.append(
                    {
                        "id": record_id,
                        "item": item,
                        "evidence": evidence,
                        "locations": [list(value) for value in locations],
                        "candidate_spans": [
                            [span["doc_index"], span["start"], span["end"]] for span in spans
                        ],
                    }
                )

    return {
        "schema_version": SCHEMA_VERSION,
        "records": len(records),
        "official_nonempty_positive_evidence_all_items": total_official_nonempty,
        "relevant_nonempty_positive_evidence": relevant,
        "evidence_located_in_source": located,
        "evidence_covered": covered,
        "coverage": covered / relevant if relevant else None,
        "by_item": by_item,
        "misses": misses,
        "meaning": (
            "Coverage means one extracted source span for the same item fully contains the exact "
            "organizer evidence coordinates. It is a retrieval test, not label accuracy."
        ),
    }


def _extract_cli(args: argparse.Namespace) -> int:
    item_reference = load_item_reference(args.item_table)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with args.output.open("w", encoding="utf-8", newline="\n") as output:
        for record in read_jsonl_gz(args.input):
            result = extract_legal_facts(record, item_reference=item_reference)
            errors = validate_fact_coordinates(record, result)
            if errors:
                raise ValueError(f"{record['id']}: invalid fact coordinates: {errors[:5]}")
            output.write(json.dumps(result, ensure_ascii=False, separators=(",", ":")) + "\n")
            count += 1
            if args.limit is not None and count >= args.limit:
                break
    print(json.dumps({"records": count, "output": str(args.output)}, ensure_ascii=False))
    return 0


def _audit_cli(args: argparse.Namespace) -> int:
    report = audit_dev_evidence(args.dev, args.labels)
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0 if not report["misses"] else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    extract = subparsers.add_parser("extract", help="extract coordinate-preserving intermediate facts")
    extract.add_argument("--input", type=pathlib.Path, required=True)
    extract.add_argument("--output", type=pathlib.Path, required=True)
    extract.add_argument(
        "--item-table", type=pathlib.Path, default=ROOT / "data_open" / "data" / "항목표.json"
    )
    extract.add_argument("--limit", type=int)
    extract.set_defaults(func=_extract_cli)

    audit = subparsers.add_parser("audit-dev", help="audit exact official-dev evidence coverage")
    audit.add_argument("--dev", type=pathlib.Path, default=ROOT / "data_open" / "dev.jsonl.gz")
    audit.add_argument(
        "--labels", type=pathlib.Path, default=ROOT / "data_open" / "dev_labels.csv"
    )
    audit.add_argument("--output", type=pathlib.Path)
    audit.set_defaults(func=_audit_cli)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
