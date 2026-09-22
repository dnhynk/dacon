"""Label-free template clustering for independent gold review routing.

The output of this module is *never* a decision artifact.  It groups organizer
source clauses so reviewers can inspect repeated structures together.  Exact
clusters mean equality of a conservative normalized template, not equality of
any competition label.  Near-duplicate sets are weaker candidate queues and
must never be used for automatic propagation.

Only organizer records, organizer metadata, and the independent retrieval /
fact modules are consumed.  Model responses, predictions, and development
labels are rejected as inputs.
"""

from __future__ import annotations

import argparse
import datetime as dt
import gzip
import hashlib
import json
import pathlib
import re
import unicodedata
from collections import Counter, defaultdict
from typing import Any, Iterable, Iterator, Mapping, Sequence

try:
    from tools.independent_gold import catalog_facts, legal_facts, packetize
except ModuleNotFoundError:  # Direct execution from this directory.
    import catalog_facts  # type: ignore[no-redef]
    import legal_facts  # type: ignore[no-redef]
    import packetize  # type: ignore[no-redef]


ROOT = pathlib.Path(__file__).resolve().parents[2]
SCHEMA_VERSION = "dacon.independent.template_clusters.v1"
PROFILE_SCHEMA_VERSION = "dacon.independent.template_profile.v1"
GROUPS: dict[str, tuple[str, ...]] = dict(packetize.ITEM_GROUPS)
GROUP_BY_ITEM = {
    item: group_name for group_name, items in GROUPS.items() for item in items
}
ABSENCE_GROUPS = frozenset(
    group_name
    for group_name, items in GROUPS.items()
    if set(items) & set(packetize.ABSENCE_ITEMS)
)

# These fields affect the legal boundary for the corresponding group.  Values
# are normalized below, but omitted/missing remains different from present.
GROUP_META_FIELDS: dict[str, tuple[str, ...]] = {
    "v1-4": (
        "contract_law",
        "work_type",
        "contract_method",
        "budget_won",
        "estimated_price_won",
        "industry_restricted",
    ),
    "v5-8": (
        "contract_law",
        "work_type",
        "contract_method",
        "estimated_price_won",
        "region_codes",
        "region_restricted",
    ),
    "v9": ("work_type", "catalog_codes"),
    "v10-13": (
        "contract_law",
        "work_type",
        "contract_method",
        "budget_won",
        "estimated_price_won",
        "catalog_codes",
    ),
    "v14-19": (
        "contract_law",
        "work_type",
        "contract_method",
        "budget_won",
        "estimated_price_won",
        "catalog_codes",
    ),
    "v20": (
        "contract_law",
        "work_type",
        "information_project",
        "budget_won",
        "estimated_price_won",
    ),
    "v21-23": (
        "contract_law",
        "work_type",
        "contract_method",
        "award_method",
        "joint_method",
        "budget_won",
        "estimated_price_won",
        "posted_date",
        "opening_date",
        "urgent",
    ),
    "v24": tuple(legal_facts.META_FIELDS),
}

DECISION_KEY_RE = re.compile(r"^[ve](?:[1-9]|1\d|2[0-4])$", re.IGNORECASE)
ORGANIZER_RECORD_KEYS = frozenset(
    {
        "id",
        "docs",
        "dropped_doc_counts",
        "input_completeness",
        "assembly_policy_version",
        "meta",
        "anon_applied",
    }
)
ORGANIZER_DOC_KEYS = frozenset({"doc_id", "type", "text", "n_chars", "src_ext"})
FORBIDDEN_SOURCE_KEY_MARKERS = (
    "label",
    "prediction",
    "response",
    "model_output",
    "production_output",
    "submission_output",
    "gemma_output",
    "candidate_decisions",
    "verification_decisions",
    "adjudication",
    "gold",
    "정답",
    "라벨",
    "예측",
    "모델_응답",
    "운영_출력",
)

FULL_DATE_RE = re.compile(
    r"(?P<year>20\d{2})\s*[.년/-]\s*(?P<month>\d{1,2})\s*[.월/-]\s*"
    r"(?P<day>\d{1,2})\s*\.?\s*(?:일)?"
    r"(?:\s*\([^)]{0,12}\))?(?:\s*(?P<hour>\d{1,2})\s*:\s*(?P<minute>\d{2}))?"
)
PARTIAL_DATE_RE = re.compile(
    r"(?<!\d)(?P<month>\d{1,2})\s*[./월]\s*(?P<day>\d{1,2})\s*\.?\s*(?:일)?(?!\d)"
)
PRODUCT_CODE_RE = re.compile(
    r"(?:(?:세부)?품명(?:번호|코드)|물품분류번호)\s*[:：]?\s*\[?\s*(\d{10})\s*\]?",
    re.IGNORECASE,
)
INDUSTRY_CODE_RE = re.compile(
    r"(?:업종|면허)\s*(?:코드|번호)\s*[:：]?\s*\[?\s*(\d{4})\s*\]?",
    re.IGNORECASE,
)
PERCENT_RE = re.compile(r"(?<![\d.])(\d+(?:\.\d+)?)\s*%")
GENERIC_NUMBER_RE = re.compile(
    r"(?<![A-Za-z0-9])\d+(?:[,.]\d+)*(?:\s*(?:년|개월|월|일|회|건|명|개|대|식|부|점|시간|km|m²|㎡))?",
    re.IGNORECASE,
)
ANON_TOKEN_RE = re.compile(r"\[([^\]\r\n]{1,220})\]")
ANON_KINDS = (
    "수요기관",
    "발주기관",
    "공고기관",
    "기관",
    "업체",
    "공고번호",
    "등록지역",
    "지역",
    "주소",
    "담당자",
    "전화",
    "이메일",
)

LOGIC_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("OR", re.compile(r"또는|혹은|중\s*하나|어느\s*하나|택\s*1")),
    ("AND", re.compile(r"및|그리고|동시에|모두|각각|전부")),
    ("NEG", re.compile(r"제외|아니한|아닌|불가|허용하지|없어야|없는")),
    ("EXCEPT", re.compile(r"다만|예외|제외한다|그러하지\s*아니")),
)
PHASE_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "bid_qualification",
        re.compile(
            r"입찰\s*참가\s*자격|입찰참가자격|참가\s*가능|참여\s*가능|"
            r"입찰서\s*제출\s*마감일\s*전|입찰\s*마감일\s*전"
        ),
    ),
    (
        "post_award_or_contract",
        re.compile(r"낙찰자|계약상대자|계약\s*(?:체결\s*)?(?:후|시)|착수\s*후"),
    ),
    (
        "evaluation",
        re.compile(r"평가\s*기준|배점|가점|정량\s*평가|제안서\s*평가|평가항목"),
    ),
    (
        "specification_or_task",
        re.compile(r"규격서|과업\s*(?:내용|범위|지시)|납품\s*(?:규격|조건)|요구\s*사항"),
    ),
)
ACTOR_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("bidder", re.compile(r"입찰자|입찰참가자|참가업체|참여업체|업체이어야|업체로서")),
    ("awardee", re.compile(r"낙찰자|계약상대자|수급인|계약업체")),
    ("manufacturer", re.compile(r"제조사|제조업체|생산자")),
    ("supplier", re.compile(r"공급사|총판|대리점|수입사|기술지원사")),
    ("personnel", re.compile(r"투입인력|참여인력|책임자|기술자|재직자")),
    ("institution", re.compile(r"대학|산학협력단|연구기관|공공기관|의료기관|병원")),
)
PRODUCT_NAME_RE = re.compile(
    r"(?:세부품명|물품명|품명|제품명|모델명)\s*[:：]?\s*([^\n,;()]{2,90})",
    re.IGNORECASE,
)

# Standard competition boundaries.  Arbitrary catalog conditions are added
# separately through catalog_facts, so this banding is never used as a final
# legal conclusion.
MONEY_THRESHOLDS = (100_000_000, 230_000_000, 2_000_000_000, 4_000_000_000, 8_000_000_000)
MAX_CLAUSE_CHARS = 1_800
DEFAULT_NEAR_THRESHOLD = 0.78


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def sha256_object(value: Any) -> str:
    return sha256_text(canonical_json(value))


def file_sha256(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_jsonl_gz(path: pathlib.Path) -> Iterator[dict[str, Any]]:
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            row = json.loads(line)
            if not isinstance(row, dict):
                raise ValueError(f"{path}:{line_number}: expected object")
            assert_admissible_record(row, location=f"{path}:{line_number}")
            yield row


def _normalized_key(value: Any) -> str:
    normalized = unicodedata.normalize("NFKC", str(value)).strip().casefold()
    return re.sub(r"[\s.\\/:-]+", "_", normalized)


def _assert_no_decision_fields(value: Any, *, location: str) -> None:
    """Reject nested label/model artifacts without scanning organizer prose."""

    if isinstance(value, Mapping):
        for raw_key, child in value.items():
            key = str(raw_key)
            normalized = _normalized_key(key)
            if DECISION_KEY_RE.fullmatch(normalized) or any(
                marker in normalized for marker in FORBIDDEN_SOURCE_KEY_MARKERS
            ):
                raise ValueError(
                    f"{location}.{key}: decision/model field is forbidden"
                )
            if isinstance(child, (Mapping, list)):
                _assert_no_decision_fields(child, location=f"{location}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            if isinstance(child, (Mapping, list)):
                _assert_no_decision_fields(child, location=f"{location}[{index}]")


def assert_admissible_record(record: Mapping[str, Any], *, location: str = "record") -> None:
    """Reject obvious decision/model artifacts before any clustering work."""

    record_id = record.get("id")
    if not isinstance(record_id, str) or not record_id.strip():
        raise ValueError(f"{location}: record has no id")
    extra = sorted(str(key) for key in set(record) - set(ORGANIZER_RECORD_KEYS))
    if extra:
        raise ValueError(f"{location}: non-organizer top-level fields are forbidden: {extra}")
    missing = sorted(
        key
        for key in ("id", "docs", "meta", "input_completeness", "dropped_doc_counts")
        if key not in record
    )
    if missing:
        raise ValueError(f"{location}: organizer source lacks fields: {missing}")
    _assert_no_decision_fields(record, location=location)

    docs = record.get("docs")
    if not isinstance(docs, list) or not docs:
        raise ValueError(f"{location}: docs must be a non-empty list")
    seen_doc_ids: set[str] = set()
    for index, raw in enumerate(docs):
        if not isinstance(raw, Mapping):
            raise ValueError(f"{location}: document {index} is not an object")
        extra_doc = sorted(str(key) for key in set(raw) - set(ORGANIZER_DOC_KEYS))
        if extra_doc:
            raise ValueError(
                f"{location}: document {index} has non-organizer fields: {extra_doc}"
            )
        missing_doc = sorted(key for key in ("doc_id", "type", "text") if key not in raw)
        if missing_doc:
            raise ValueError(
                f"{location}: document {index} lacks fields: {missing_doc}"
            )
        doc_id = raw["doc_id"]
        if not isinstance(doc_id, str) or not doc_id or doc_id in seen_doc_ids:
            raise ValueError(
                f"{location}: document {index} has duplicate/invalid doc_id {doc_id!r}"
            )
        seen_doc_ids.add(doc_id)
        if not isinstance(raw["type"], str) or not isinstance(raw["text"], str):
            raise ValueError(f"{location}:{doc_id}: document type/text must be strings")
        if "n_chars" in raw and raw["n_chars"] != len(raw["text"]):
            raise ValueError(f"{location}:{doc_id}: n_chars mismatch")

    meta = record.get("meta")
    completeness = record.get("input_completeness")
    dropped = record.get("dropped_doc_counts")
    if not isinstance(meta, Mapping):
        raise ValueError(f"{location}: meta must be an object")
    if not (
        isinstance(completeness, Mapping)
        and completeness
        and all(isinstance(key, str) and type(value) is bool for key, value in completeness.items())
    ):
        raise ValueError(f"{location}: input_completeness must be a non-empty boolean map")
    if not (
        isinstance(dropped, Mapping)
        and all(
            isinstance(key, str) and type(value) is int and value >= 1
            for key, value in dropped.items()
        )
    ):
        raise ValueError(
            f"{location}: dropped_doc_counts must contain positive integer counts"
        )


def _documents(record: Mapping[str, Any]) -> list[dict[str, Any]]:
    documents: list[dict[str, Any]] = []
    for index, raw in enumerate(record.get("docs") or []):
        if not isinstance(raw, Mapping):
            continue
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


def _paragraph_bounds(text: str, start: int, end: int) -> tuple[int, int, bool]:
    """Return a complete nearby paragraph when possible, else a marked window."""

    left = text.rfind("\n\n", max(0, start - MAX_CLAUSE_CHARS), start)
    left = 0 if left < 0 else left + 2
    right = text.find("\n\n", end, min(len(text), end + MAX_CLAUSE_CHARS))
    right = len(text) if right < 0 else right
    if right - left <= MAX_CLAUSE_CHARS:
        return left, right, False

    # Oversized tables and OCR paragraphs are bounded at line breaks.  The
    # clipping flag becomes a review risk and prevents an absence inference.
    left_line = text.rfind("\n", max(0, start - 700), start)
    left_line = max(0, left_line + 1)
    right_line = text.find("\n", end, min(len(text), end + 1_050))
    right_line = min(len(text), right_line if right_line >= 0 else end + 1_050)
    if right_line - left_line > MAX_CLAUSE_CHARS:
        center = (start + end) // 2
        left_line = max(0, center - MAX_CLAUSE_CHARS // 2)
        right_line = min(len(text), left_line + MAX_CLAUSE_CHARS)
    return left_line, right_line, True


def _money_band(value: int) -> str:
    boundaries = (0,) + MONEY_THRESHOLDS
    labels = (
        "lt_100m",
        "100m_to_lt_230m",
        "230m_to_lt_2b",
        "2b_to_lt_4b",
        "4b_to_lt_8b",
        "ge_8b",
    )
    for index, threshold in enumerate(MONEY_THRESHOLDS):
        if value < threshold:
            return labels[index]
        if value == threshold:
            return f"eq_{threshold}"
    return labels[-1]


def _percent_band(value: float) -> str:
    if value < 5:
        return "lt_5"
    if value == 5:
        return "eq_5"
    if value < 10:
        return "gt_5_lt_10"
    if value == 10:
        return "eq_10"
    return "gt_10"


def _relation(value: int, reference: Any) -> str | None:
    if isinstance(reference, bool) or not isinstance(reference, (int, float)):
        return None
    reference_int = int(reference)
    if value < reference_int:
        return "lt"
    if value > reference_int:
        return "gt"
    return "eq"


def _date_role(text: str, start: int, end: int) -> str:
    local = text[max(0, start - 90) : min(len(text), end + 45)]
    before = text[max(0, start - 90) : start]
    if re.search(r"설명회|현장설명|사업설명|과업설명", before):
        return "briefing"
    if re.search(r"제출\s*마감|접수\s*마감|제안서\s*제출|입찰서\s*제출", before):
        return "submission_deadline"
    if re.search(r"공고\s*(?:일|기간|게시)", before):
        return "posting"
    if re.search(r"개찰", local):
        return "opening"
    if re.search(r"계약\s*(?:기간|체결)", before):
        return "contract"
    return "unresolved"


def _normalize_anon_token(raw: str) -> tuple[str, dict[str, Any] | None]:
    compact = re.sub(r"\s+", "", raw)
    kind = next((value for value in ANON_KINDS if compact.startswith(value)), None)
    if kind is None:
        return f"[{raw}]", None
    unit_match = re.search(r"단위=([^|\]]+)", compact)
    subtype_match = re.match(r"[^(:]+\(([^)]+)\)", compact)
    signature = {
        "kind": kind,
        "unit": unit_match.group(1) if unit_match else None,
        "subtype": subtype_match.group(1) if subtype_match else None,
    }
    replacement = f"<ANON:{kind}"
    if signature["unit"]:
        replacement += f":unit={signature['unit']}"
    if signature["subtype"]:
        replacement += f":subtype={signature['subtype']}"
    replacement += ">"
    return replacement, signature


def _replace_with_callback(
    pattern: re.Pattern[str], text: str, callback: Any
) -> str:
    return pattern.sub(callback, text)


def normalize_clause(
    text: str,
    *,
    group_name: str,
    meta: Mapping[str, Any],
) -> dict[str, Any]:
    """Normalize surface variables while retaining decision-boundary shapes."""

    if group_name not in GROUPS:
        raise ValueError(f"unknown group: {group_name}")
    working = unicodedata.normalize("NFKC", text)
    anon_signatures: list[dict[str, Any]] = []
    product_codes: list[str] = []
    industry_codes: list[str] = []
    dates: list[dict[str, Any]] = []
    partial_dates: list[str] = []
    money: list[dict[str, Any]] = []
    percentages: list[dict[str, Any]] = []

    def anon_repl(match: re.Match[str]) -> str:
        replacement, signature = _normalize_anon_token(match.group(1))
        if signature is not None:
            anon_signatures.append(signature)
        return replacement

    working = _replace_with_callback(ANON_TOKEN_RE, working, anon_repl)

    def product_repl(match: re.Match[str]) -> str:
        product_codes.append(match.group(1))
        prefix = match.group(0)[: match.group(0).find(match.group(1))]
        return prefix + "<PRODUCT_CODE>"

    working = _replace_with_callback(PRODUCT_CODE_RE, working, product_repl)

    def industry_repl(match: re.Match[str]) -> str:
        industry_codes.append(match.group(1))
        prefix = match.group(0)[: match.group(0).find(match.group(1))]
        return prefix + "<INDUSTRY_CODE>"

    working = _replace_with_callback(INDUSTRY_CODE_RE, working, industry_repl)

    def full_date_repl(match: re.Match[str]) -> str:
        try:
            parsed = dt.datetime(
                int(match.group("year")),
                int(match.group("month")),
                int(match.group("day")),
                int(match.group("hour") or 0),
                int(match.group("minute") or 0),
            )
            iso = parsed.isoformat(timespec="minutes")
        except ValueError:
            iso = "invalid"
        dates.append(
            {
                "iso": iso,
                "role": _date_role(working, match.start(), match.end()),
                "has_time": match.group("hour") is not None,
            }
        )
        return "<DATE_TIME>" if match.group("hour") is not None else "<DATE>"

    working = _replace_with_callback(FULL_DATE_RE, working, full_date_repl)

    def partial_date_repl(match: re.Match[str]) -> str:
        value = f"{int(match.group('month')):02d}-{int(match.group('day')):02d}"
        partial_dates.append(value)
        # Exact partial dates are retained in the boundary signature because a
        # reliable interval cannot be computed without the year.
        return "<PARTIAL_DATE>"

    working = _replace_with_callback(PARTIAL_DATE_RE, working, partial_date_repl)

    def money_repl(match: re.Match[str]) -> str:
        raw = match.group(0)
        value = legal_facts.parse_money_won(raw)
        if value is None:
            return raw
        descriptor = {
            "band": _money_band(value),
            "to_budget": _relation(value, meta.get("배정예산금액")),
            "to_estimated": _relation(value, meta.get("입찰추정가격")),
        }
        money.append(descriptor)
        return f"<MONEY:{descriptor['band']}>"

    working = _replace_with_callback(legal_facts.MONEY_RE, working, money_repl)

    def percent_repl(match: re.Match[str]) -> str:
        value = float(match.group(1))
        band = _percent_band(value)
        percentages.append({"band": band})
        return f"<PERCENT:{band}>"

    working = _replace_with_callback(PERCENT_RE, working, percent_repl)

    def generic_number_repl(match: re.Match[str]) -> str:
        raw = match.group(0)
        unit_match = re.search(r"(?:년|개월|월|일|회|건|명|개|대|식|부|점|시간|km|m²|㎡)$", raw)
        unit = unit_match.group(0) if unit_match else "scalar"
        return f"<NUM:{unit}>"

    working = _replace_with_callback(GENERIC_NUMBER_RE, working, generic_number_repl)
    working = re.sub(r"\s+", " ", working).strip().casefold()

    parsed_dates = [
        dt.datetime.fromisoformat(row["iso"])
        for row in dates
        if row["iso"] != "invalid"
    ]
    date_gaps = [
        (right.date() - left.date()).days
        for left, right in zip(parsed_dates, parsed_dates[1:])
    ]
    money_relations: list[str] = []
    for left, right in zip(money, money[1:]):
        money_relations.append(f"{left['band']}->{right['band']}")

    signature = {
        "anon": sorted(anon_signatures, key=canonical_json),
        "product_codes": sorted(set(product_codes)),
        "industry_codes": sorted(set(industry_codes)),
        "dates": {
            "count": len(dates),
            "roles": [row["role"] for row in dates],
            "has_time": [row["has_time"] for row in dates],
            "gaps_days": date_gaps,
            "invalid": sum(row["iso"] == "invalid" for row in dates),
            "partial_exact": partial_dates,
        },
        "money": money,
        "money_pair_bands": money_relations,
        "percentages": percentages,
    }
    return {"normalized_text": working, "variable_signature": signature}


def _ordered_pattern_hits(
    text: str, patterns: Sequence[tuple[str, re.Pattern[str]]]
) -> list[str]:
    hits: list[tuple[int, str]] = []
    for name, pattern in patterns:
        hits.extend((match.start(), name) for match in pattern.finditer(text))
    return [name for _, name in sorted(hits)]


def _facet_signature(text: str) -> dict[str, Any]:
    phases = sorted(set(_ordered_pattern_hits(text, PHASE_PATTERNS)))
    actors = sorted(set(_ordered_pattern_hits(text, ACTOR_PATTERNS)))
    logic_sequence = _ordered_pattern_hits(text, LOGIC_PATTERNS)
    product_names = [
        re.sub(r"\s+", " ", value.strip()).casefold()
        for value in PRODUCT_NAME_RE.findall(text)
    ]
    return {
        "phases": phases or ["unknown"],
        "actors": actors or ["unknown"],
        "logic_sequence": logic_sequence or ["NONE"],
        "product_name_hashes": sorted({sha256_text(value) for value in product_names}),
    }


def _meta_status_value(meta: Mapping[str, Any], semantic_name: str) -> dict[str, Any]:
    source_key = legal_facts.META_FIELDS[semantic_name]
    value = meta.get(source_key)
    if value in (None, "", "미입력"):
        return {"status": "missing_or_unentered", "value": None}
    if semantic_name in {"budget_won", "estimated_price_won"}:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return {"status": "present_unparsed", "value": str(value)}
        return {"status": "present", "value": _money_band(int(value))}
    if semantic_name in {"posted_date", "opening_date"}:
        rendered = re.sub(r"\D", "", str(value))
        return {
            "status": "present",
            "value": "valid_yyyymmdd" if len(rendered) == 8 else f"shape_{len(rendered)}",
        }
    if semantic_name == "region_codes":
        rendered = str(value)
        units = sorted(
            set(re.findall(r"단위=([^|\]]+)", rendered))
            | ({"광역"} if re.search(r"특별시|광역시|특별자치시|도", rendered) else set())
        )
        count = max(1, len(re.findall(r"\[지역:|,|또는|\|", rendered)) + 1)
        return {"status": "present", "value": {"units": units, "count_shape": count}}
    if semantic_name == "catalog_codes":
        return {
            "status": "present",
            "value": sorted(set(re.findall(r"(?<!\d)\d{10}(?!\d)", str(value)))),
        }
    # v24 must compare exact organizer values.  Other groups retain categorical
    # values verbatim because they define law, method, role or Boolean scope.
    return {"status": "present", "value": value}


def _metadata_signature(
    record: Mapping[str, Any],
    group_name: str,
    *,
    catalog_result: Mapping[str, Any] | None,
) -> dict[str, Any]:
    meta = record.get("meta") if isinstance(record.get("meta"), Mapping) else {}
    result = {
        name: _meta_status_value(meta, name) for name in GROUP_META_FIELDS[group_name]
    }
    if group_name == "v24":
        # Same normalized template text cannot make two different organizer
        # metadata rows equivalent for a mismatch review.
        result["full_meta_sha256"] = sha256_object(meta)
    if group_name in {"v10-13", "v14-19"} and catalog_result is not None:
        result["catalog_scope"] = {
            "summary_decision": catalog_result["summary"]["decision"],
            "summary_reason_code": catalog_result["summary"]["reason_code"],
            "codes": [
                {
                    "code": row["code"],
                    "decision": row["decision"],
                    "reason_code": row["reason_code"],
                    "condition_kind": (
                        row["catalog_row"]["condition"]["kind"]
                        if row.get("catalog_row")
                        else None
                    ),
                }
                for row in catalog_result.get("codes") or []
            ],
        }
    return result


def _source_completeness_signature(record: Mapping[str, Any]) -> dict[str, Any]:
    completeness = record.get("input_completeness") or {}
    dropped = record.get("dropped_doc_counts") or {}
    return {
        "fully_observed": bool(completeness) and all(value is True for value in completeness.values()),
        "dropped_doc_types": sorted(
            str(key)
            for key, value in dropped.items()
            if isinstance(value, (int, float)) and value > 0
        )
        if isinstance(dropped, Mapping)
        else ["unparsed"],
    }


def extract_group_clauses(
    record: Mapping[str, Any],
    *,
    catalog_index: catalog_facts.CatalogIndex | None = None,
) -> dict[str, list[dict[str, Any]]]:
    """Scan organizer text once and produce coordinate-preserving group clauses."""

    assert_admissible_record(record)
    documents = _documents(record)
    meta = record.get("meta") if isinstance(record.get("meta"), Mapping) else {}
    candidates: dict[tuple[str, int, int, int], dict[str, Any]] = {}

    for document in documents:
        text = document["text"]
        for match in packetize.KEYWORD_PATTERN.finditer(text):
            specs = packetize.KEYWORD_INDEX.get(match.group(0).casefold(), ())
            groups = sorted({GROUP_BY_ITEM[item] for item, _ in specs})
            for group_name in groups:
                start, end, clipped = _paragraph_bounds(text, match.start(), match.end())
                key = (group_name, document["doc_index"], start, end)
                row = candidates.setdefault(
                    key,
                    {
                        "group": group_name,
                        "doc_index": document["doc_index"],
                        "doc_id": document["doc_id"],
                        "doc_type": document["doc_type"],
                        "source_doc_sha256": document["sha256"],
                        "start": start,
                        "end": end,
                        "text": text[start:end],
                        "clipped": clipped,
                        "anchors": set(),
                    },
                )
                row["anchors"].add(match.group(0).casefold())

    output: dict[str, list[dict[str, Any]]] = {group_name: [] for group_name in GROUPS}
    for row in candidates.values():
        group_name = row["group"]
        normalized = normalize_clause(row["text"], group_name=group_name, meta=meta)
        facets = _facet_signature(row["text"])
        fingerprint_payload = {
            "group": group_name,
            "doc_type": row["doc_type"],
            "normalized_text": normalized["normalized_text"],
            "variable_signature": normalized["variable_signature"],
            "facets": facets,
            "anchors": sorted(row["anchors"]),
            "clipped": row["clipped"],
        }
        output[group_name].append(
            {
                "clause_fingerprint": sha256_object(fingerprint_payload),
                "doc_index": row["doc_index"],
                "doc_id": row["doc_id"],
                "doc_type": row["doc_type"],
                "source_doc_sha256": row["source_doc_sha256"],
                "start": row["start"],
                "end": row["end"],
                "text_sha256": sha256_text(row["text"]),
                "normalized_text": normalized["normalized_text"],
                "variable_signature": normalized["variable_signature"],
                "facets": facets,
                "anchors": sorted(row["anchors"]),
                "clipped": row["clipped"],
            }
        )
    for group_name in output:
        output[group_name].sort(
            key=lambda row: (row["doc_index"], row["start"], row["end"], row["clause_fingerprint"])
        )
    return output


def _profile_features(clauses: Sequence[Mapping[str, Any]]) -> set[str]:
    features: set[str] = set()
    for clause in clauses:
        normalized = str(clause["normalized_text"])
        words = re.findall(r"[가-힣A-Za-z_<>:=.-]{2,}|\d+", normalized)
        features.update(f"w:{word}" for word in words)
        features.update(f"b:{left}|{right}" for left, right in zip(words, words[1:]))
        features.update(f"a:{value}" for value in clause.get("anchors") or [])
    return features


def _feature_sketch(features: Iterable[str], *, size: int = 48) -> list[str]:
    """Return a fixed-size bottom-hash sketch for bounded near comparison."""

    hashes = sorted({sha256_text(value)[:16] for value in features})
    return hashes[:size]


def simhash64(features: Iterable[str]) -> int:
    vector = [0] * 64
    unique = sorted(set(features))
    if not unique:
        return 0
    for feature in unique:
        digest = hashlib.sha256(feature.encode("utf-8")).digest()
        value = int.from_bytes(digest[:8], "big")
        for bit in range(64):
            vector[bit] += 1 if value & (1 << bit) else -1
    output = 0
    for bit, weight in enumerate(vector):
        if weight >= 0:
            output |= 1 << bit
    return output


def _near_boundary(
    group_name: str,
    metadata_signature: Mapping[str, Any],
    completeness: Mapping[str, Any],
    clauses: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    phases = sorted({phase for row in clauses for phase in row["facets"]["phases"]})
    actors = sorted({actor for row in clauses for actor in row["facets"]["actors"]})
    # Exact clusters intentionally use a clause *multiset*: reordering otherwise
    # identical source paragraphs must not change cluster identity.  Keep the
    # near-candidate boundary under the same invariant.  Per-clause logical
    # order is already committed by each clause fingerprint, while ordering
    # tokens across independent clauses is merely document layout.
    logic = sorted(token for row in clauses for token in row["facets"]["logic_sequence"])
    product_codes = sorted(
        {
            code
            for row in clauses
            for code in row["variable_signature"]["product_codes"]
        }
    )
    product_names = sorted(
        {
            value
            for row in clauses
            for value in row["facets"]["product_name_hashes"]
        }
    )
    return {
        "group": group_name,
        "metadata": metadata_signature,
        "completeness": completeness,
        "clause_count": len(clauses),
        "doc_types": sorted({str(row["doc_type"]) for row in clauses}),
        "phases": phases,
        "actors": actors,
        "logic_sequence": logic,
        "product_codes": product_codes,
        "product_name_hashes": product_names,
    }


def build_record_profiles(
    record: Mapping[str, Any],
    *,
    catalog_index: catalog_facts.CatalogIndex | None = None,
) -> dict[str, dict[str, Any]]:
    """Build all eight review-only group profiles for one organizer record."""

    assert_admissible_record(record)
    catalog_index = catalog_index or catalog_facts.CatalogIndex.load()
    clauses_by_group = extract_group_clauses(record, catalog_index=catalog_index)
    catalog_result = catalog_facts.resolve_record(record, catalog_index)
    documents = _documents(record)
    source_hash = packetize.sha256_object(record)
    completeness = _source_completeness_signature(record)
    profiles: dict[str, dict[str, Any]] = {}

    for group_name, clauses in clauses_by_group.items():
        metadata = _metadata_signature(
            record,
            group_name,
            catalog_result=catalog_result,
        )
        clause_multiset = sorted(Counter(row["clause_fingerprint"] for row in clauses).items())
        exact_payload: dict[str, Any] = {
            "schema_version": PROFILE_SCHEMA_VERSION,
            "group": group_name,
            "metadata": metadata,
            "source_completeness": completeness,
            "clause_multiset": clause_multiset,
        }
        if not clauses:
            # Empty retrieval is not evidence of an empty semantic template.
            # Force it to remain record-specific and route it to high review.
            exact_payload["empty_source_sha256"] = source_hash

        boundary = _near_boundary(group_name, metadata, completeness, clauses)
        features = _profile_features(clauses)
        feature_sketch = _feature_sketch(features)
        similarity_hash = simhash64(features)
        risks: list[str] = []
        if not clauses:
            risks.append("no_relevant_clause_retrieved")
        if any(bool(row["clipped"]) for row in clauses):
            risks.append("one_or_more_clauses_clipped")
        if not completeness["fully_observed"]:
            risks.append("source_not_fully_observed")
        if group_name in ABSENCE_GROUPS and not completeness["fully_observed"]:
            risks.append("absence_group_with_incomplete_source")
        if any(len(set(row["facets"]["phases"])) > 1 for row in clauses):
            risks.append("mixed_phase_clause")
        if any("EXCEPT" in row["facets"]["logic_sequence"] for row in clauses):
            risks.append("explicit_exception_language")
        risk_tier = (
            "critical"
            if "absence_group_with_incomplete_source" in risks or "no_relevant_clause_retrieved" in risks
            else "high"
            if risks
            else "standard"
        )
        profiles[group_name] = {
            "schema_version": PROFILE_SCHEMA_VERSION,
            "record_id": str(record["id"]),
            "group": group_name,
            "target_items": list(GROUPS[group_name]),
            "source_sha256": source_hash,
            "documents": [
                {
                    "doc_index": row["doc_index"],
                    "doc_id": row["doc_id"],
                    "doc_type": row["doc_type"],
                    "chars": len(row["text"]),
                    "sha256": row["sha256"],
                }
                for row in documents
            ],
            "source_completeness": completeness,
            "metadata_signature": metadata,
            "exact_fingerprint": sha256_object(exact_payload),
            "near_boundary_key": sha256_object(boundary),
            "near_simhash64": f"{similarity_hash:016x}",
            "near_band_keys": [
                sha256_text(
                    f"{sha256_object(boundary)}:{band}:{(similarity_hash >> (band * 16)) & 0xFFFF:04x}"
                )
                for band in range(4)
            ]
            if clauses
            else [],
            "near_eligible": bool(clauses),
            "feature_count": len(features),
            "feature_sketch": feature_sketch,
            "clause_multiset": clause_multiset,
            "clauses": clauses,
            "risk_flags": sorted(set(risks)),
            "routing": {
                "risk_tier": risk_tier,
                "review_required": True,
                "allowed_action": "batch_for_independent_review",
                "automatic_decision_propagation_allowed": False,
                "semantic_equivalence_claimed": False,
            },
        }
    return profiles


def validate_profile(record: Mapping[str, Any], profile: Mapping[str, Any]) -> list[str]:
    """Validate every member coordinate and source hash against organizer text."""

    errors: list[str] = []
    documents = _documents(record)
    group_name = str(profile.get("group") or "")
    if group_name not in GROUPS:
        errors.append(f"invalid_group:{group_name}")
        return errors
    if str(record.get("id")) != profile.get("record_id"):
        errors.append("record_id_mismatch")
    if packetize.sha256_object(record) != profile.get("source_sha256"):
        errors.append("record_source_hash_mismatch")
    expected_documents = [
        {
            "doc_index": row["doc_index"],
            "doc_id": row["doc_id"],
            "doc_type": row["doc_type"],
            "chars": len(row["text"]),
            "sha256": row["sha256"],
        }
        for row in documents
    ]
    if profile.get("documents") != expected_documents:
        errors.append("document_manifest_mismatch")
    completeness = _source_completeness_signature(record)
    if profile.get("source_completeness") != completeness:
        errors.append("source_completeness_mismatch")

    meta = record.get("meta") if isinstance(record.get("meta"), Mapping) else {}
    clauses = profile.get("clauses") or []
    if not isinstance(clauses, list):
        errors.append("clauses_not_list")
        return errors
    seen_coordinates: set[tuple[int, int, int]] = set()
    for clause in clauses:
        if not isinstance(clause, Mapping):
            errors.append("clause_not_object")
            continue
        index = clause.get("doc_index")
        if not isinstance(index, int) or not (0 <= index < len(documents)):
            errors.append(f"invalid_doc_index:{index}")
            continue
        doc = documents[index]
        start, end = clause.get("start"), clause.get("end")
        if not isinstance(start, int) or not isinstance(end, int) or not (0 <= start <= end <= len(doc["text"])):
            errors.append(f"invalid_range:{index}:{start}:{end}")
            continue
        exact = doc["text"][start:end]
        coordinate = (index, start, end)
        if coordinate in seen_coordinates:
            errors.append(f"duplicate_coordinate:{index}:{start}:{end}")
        seen_coordinates.add(coordinate)
        if sha256_text(exact) != clause.get("text_sha256"):
            errors.append(f"text_hash_mismatch:{index}:{start}:{end}")
        if doc["sha256"] != clause.get("source_doc_sha256"):
            errors.append(f"document_hash_mismatch:{index}")
        if doc["doc_id"] != clause.get("doc_id"):
            errors.append(f"document_id_mismatch:{index}")
        if doc["doc_type"] != clause.get("doc_type"):
            errors.append(f"document_type_mismatch:{index}")

        normalized = normalize_clause(exact, group_name=group_name, meta=meta)
        facets = _facet_signature(exact)
        if clause.get("normalized_text") != normalized["normalized_text"]:
            errors.append(f"normalized_text_mismatch:{index}:{start}:{end}")
        if clause.get("variable_signature") != normalized["variable_signature"]:
            errors.append(f"variable_signature_mismatch:{index}:{start}:{end}")
        if clause.get("facets") != facets:
            errors.append(f"facet_signature_mismatch:{index}:{start}:{end}")
        anchors = clause.get("anchors")
        if not isinstance(anchors, list) or anchors != sorted(set(anchors)):
            errors.append(f"invalid_anchors:{index}:{start}:{end}")
            anchors = []
        for anchor in anchors:
            specs = packetize.KEYWORD_INDEX.get(str(anchor).casefold(), ())
            if (
                str(anchor).casefold() not in exact.casefold()
                or group_name not in {GROUP_BY_ITEM[item] for item, _ in specs}
            ):
                errors.append(f"invalid_anchor:{index}:{start}:{end}:{anchor}")
        fingerprint_payload = {
            "group": group_name,
            "doc_type": doc["doc_type"],
            "normalized_text": normalized["normalized_text"],
            "variable_signature": normalized["variable_signature"],
            "facets": facets,
            "anchors": anchors,
            "clipped": bool(clause.get("clipped")),
        }
        if sha256_object(fingerprint_payload) != clause.get("clause_fingerprint"):
            errors.append(f"clause_fingerprint_mismatch:{index}:{start}:{end}")

    expected_clause_order = sorted(
        clauses,
        key=lambda row: (
            row.get("doc_index", -1),
            row.get("start", -1),
            row.get("end", -1),
            str(row.get("clause_fingerprint") or ""),
        ),
    )
    if clauses != expected_clause_order:
        errors.append("clause_order_mismatch")
    clause_multiset = sorted(
        Counter(str(row.get("clause_fingerprint")) for row in clauses).items()
    )
    if profile.get("clause_multiset") != clause_multiset:
        errors.append("clause_multiset_mismatch")
    exact_payload: dict[str, Any] = {
        "schema_version": PROFILE_SCHEMA_VERSION,
        "group": group_name,
        "metadata": profile.get("metadata_signature"),
        "source_completeness": completeness,
        "clause_multiset": clause_multiset,
    }
    if not clauses:
        exact_payload["empty_source_sha256"] = packetize.sha256_object(record)
    if profile.get("exact_fingerprint") != sha256_object(exact_payload):
        errors.append("exact_fingerprint_mismatch")

    boundary = _near_boundary(
        group_name,
        profile.get("metadata_signature") or {},
        completeness,
        clauses,
    )
    boundary_hash = sha256_object(boundary)
    if profile.get("near_boundary_key") != boundary_hash:
        errors.append("near_boundary_mismatch")
    features = _profile_features(clauses)
    similarity_hash = simhash64(features)
    if profile.get("feature_count") != len(features):
        errors.append("feature_count_mismatch")
    if profile.get("feature_sketch") != _feature_sketch(features):
        errors.append("feature_sketch_mismatch")
    if profile.get("near_simhash64") != f"{similarity_hash:016x}":
        errors.append("near_simhash_mismatch")
    expected_band_keys = (
        [
            sha256_text(
                f"{boundary_hash}:{band}:{(similarity_hash >> (band * 16)) & 0xFFFF:04x}"
            )
            for band in range(4)
        ]
        if clauses
        else []
    )
    if profile.get("near_band_keys") != expected_band_keys:
        errors.append("near_band_keys_mismatch")
    if profile.get("near_eligible") is not bool(clauses):
        errors.append("near_eligible_mismatch")
    return errors


def _jaccard(left: set[str], right: set[str]) -> float:
    union = left | right
    return len(left & right) / len(union) if union else 1.0


def _hamming64(left: int, right: int) -> int:
    return (left ^ right).bit_count()


class _UnionFind:
    def __init__(self, values: Iterable[str]) -> None:
        self.parent = {value: value for value in values}

    def find(self, value: str) -> str:
        parent = self.parent[value]
        if parent != value:
            self.parent[value] = self.find(parent)
        return self.parent[value]

    def union(self, left: str, right: str) -> None:
        left_root, right_root = self.find(left), self.find(right)
        if left_root != right_root:
            self.parent[max(left_root, right_root)] = min(left_root, right_root)


def _member_from_profile(profile: Mapping[str, Any]) -> dict[str, Any]:
    coordinates = [
        {
            "clause_fingerprint": row["clause_fingerprint"],
            "doc_index": row["doc_index"],
            "doc_id": row["doc_id"],
            "doc_type": row["doc_type"],
            "source_doc_sha256": row["source_doc_sha256"],
            "start": row["start"],
            "end": row["end"],
            "text_sha256": row["text_sha256"],
            "clipped": row["clipped"],
        }
        for row in profile["clauses"]
    ]
    representative = next(
        (
            coordinate
            for coordinate, clause in zip(coordinates, profile["clauses"])
            if not clause["clipped"]
        ),
        coordinates[0] if coordinates else None,
    )
    return {
        "record_id": profile["record_id"],
        "source_sha256": profile["source_sha256"],
        "document_count": len(profile["documents"]),
        "documents_sha256": sha256_object(profile["documents"]),
        "clause_count": len(coordinates),
        "coordinates": coordinates,
        "coordinates_sha256": sha256_object(coordinates),
        "representative_coordinate": representative,
        "risk_flags": profile["risk_flags"],
        "risk_tier": profile["routing"]["risk_tier"],
    }


def _cluster_group_profiles(
    group_name: str,
    by_exact: Mapping[str, Mapping[str, Any]],
    *,
    near_threshold: float,
    top_n: int,
    risk_counts: Mapping[str, int],
) -> dict[str, Any]:
    exact_rows: list[dict[str, Any]] = []
    representative_by_id: dict[str, Mapping[str, Any]] = {}
    features_by_id: dict[str, set[str]] = {}
    simhash_by_id: dict[str, int] = {}
    bands: dict[str, list[str]] = defaultdict(list)
    fingerprint_by_cluster_id: dict[str, str] = {}

    for fingerprint in sorted(by_exact):
        aggregate = by_exact[fingerprint]
        members = sorted(aggregate["members"], key=lambda row: str(row["record_id"]))
        cluster_id = f"X-{group_name}-{fingerprint[:16]}"
        previous_fingerprint = fingerprint_by_cluster_id.setdefault(cluster_id, fingerprint)
        if previous_fingerprint != fingerprint:
            raise ValueError(
                f"truncated cluster id collision for {group_name}:{cluster_id}"
            )
        representative = aggregate["representative"]
        representative_by_id[cluster_id] = representative
        features_by_id[cluster_id] = set(representative["feature_sketch"])
        simhash_by_id[cluster_id] = int(str(representative["near_simhash64"]), 16)
        if representative["near_eligible"]:
            for band_key in representative["near_band_keys"]:
                bands[str(band_key)].append(cluster_id)
        normalized_samples = []
        for row in representative["clauses"][:3]:
            normalized = str(row["normalized_text"])
            normalized_samples.append(
                {
                    "clause_fingerprint": row["clause_fingerprint"],
                    "doc_type": row["doc_type"],
                    "normalized_text_sample": normalized[:500],
                    "normalized_text_chars": len(normalized),
                    "variable_signature": row["variable_signature"],
                    "facets": row["facets"],
                    "anchors": row["anchors"],
                    "clipped": row["clipped"],
                }
            )
        exact_rows.append(
            {
                "cluster_id": cluster_id,
                "cluster_kind": "exact_normalized_template",
                "safe_for": "review_batching_only",
                "automatic_decision_propagation_allowed": False,
                "semantic_equivalence_claimed": False,
                "fingerprint": fingerprint,
                "member_count": len(members),
                "representative": {
                    "record_id": representative["record_id"],
                    "source_sha256": representative["source_sha256"],
                    "metadata_signature": representative["metadata_signature"],
                    "clause_count": len(representative["clauses"]),
                    "clause_multiset_sha256": sha256_object(representative["clause_multiset"]),
                    "normalized_clause_samples": normalized_samples,
                    "omitted_normalized_clause_samples": max(
                        0, len(representative["clauses"]) - len(normalized_samples)
                    ),
                },
                "members": members,
            }
        )

    # Candidate generation operates on exact-cluster representatives.  A pair
    # must share the strict semantic boundary and one 16-bit SimHash band, then
    # pass both Jaccard and Hamming checks.  The result remains review-only.
    uf = _UnionFind(representative_by_id)
    edge_scores: dict[tuple[str, str], float] = {}
    seen_pairs: set[tuple[str, str]] = set()
    oversized_band_buckets = 0
    for cluster_ids in bands.values():
        unique = sorted(set(cluster_ids))
        if len(unique) > 400:
            # Avoid quadratic explosions in generic boilerplate.  Such buckets
            # are not declared near duplicates; exact clusters remain usable.
            oversized_band_buckets += 1
            continue
        for left_index, left in enumerate(unique):
            for right in unique[left_index + 1 :]:
                pair = (left, right)
                if pair in seen_pairs:
                    continue
                seen_pairs.add(pair)
                left_profile = representative_by_id[left]
                right_profile = representative_by_id[right]
                if left_profile["near_boundary_key"] != right_profile["near_boundary_key"]:
                    continue
                hamming = _hamming64(simhash_by_id[left], simhash_by_id[right])
                if hamming > 16:
                    continue
                score = _jaccard(features_by_id[left], features_by_id[right])
                if score < near_threshold:
                    continue
                uf.union(left, right)
                edge_scores[pair] = score

    components: dict[str, list[str]] = defaultdict(list)
    for cluster_id in representative_by_id:
        components[uf.find(cluster_id)].append(cluster_id)
    near_sets: list[dict[str, Any]] = []
    for component in sorted(
        (sorted(values) for values in components.values() if len(values) > 1),
        key=lambda values: (-len(values), values),
    ):
        component_edges = [
            score
            for (left, right), score in edge_scores.items()
            if left in component and right in component
        ]
        member_count = sum(
            next(row["member_count"] for row in exact_rows if row["cluster_id"] == cluster_id)
            for cluster_id in component
        )
        set_hash = sha256_object(component)
        near_sets.append(
            {
                "candidate_set_id": f"N-{group_name}-{set_hash[:16]}",
                "cluster_kind": "near_duplicate_review_candidates",
                "safe_for": "review_routing_only",
                "automatic_decision_propagation_allowed": False,
                "semantic_equivalence_claimed": False,
                "exact_cluster_ids": component,
                "exact_cluster_count": len(component),
                "member_count": member_count,
                "edge_count": len(component_edges),
                "minimum_edge_jaccard": min(component_edges) if component_edges else None,
            }
        )

    exact_rows.sort(key=lambda row: (-row["member_count"], row["cluster_id"]))
    sizes = [int(row["member_count"]) for row in exact_rows]
    repeated = [row for row in exact_rows if row["member_count"] > 1]
    near_member_ids: set[str] = set()
    exact_by_id = {row["cluster_id"]: row for row in exact_rows}
    for candidate_set in near_sets:
        for cluster_id in candidate_set["exact_cluster_ids"]:
            near_member_ids.update(
                str(row["record_id"]) for row in exact_by_id[cluster_id]["members"]
            )
    profile_count = sum(len(row["members"]) for row in by_exact.values())
    return {
        "group": group_name,
        "target_items": list(GROUPS[group_name]),
        "statistics": {
            "records": profile_count,
            "unique_exact_clusters": len(exact_rows),
            "repeated_exact_clusters": len(repeated),
            "singleton_exact_clusters": sum(size == 1 for size in sizes),
            "members_in_repeated_exact_clusters": sum(
                row["member_count"] for row in repeated
            ),
            "largest_exact_cluster": max(sizes, default=0),
            "near_duplicate_candidate_sets": len(near_sets),
            "records_in_near_candidate_sets": len(near_member_ids),
            "oversized_lsh_buckets_skipped": oversized_band_buckets,
            "risk_tiers": dict(sorted(risk_counts.items())),
            "top_exact_cluster_sizes": [
                {
                    "cluster_id": row["cluster_id"],
                    "member_count": row["member_count"],
                }
                for row in exact_rows[:top_n]
            ],
        },
        "exact_clusters": exact_rows,
        "near_duplicate_candidate_sets": near_sets,
    }


def build_census(
    records: Iterable[Mapping[str, Any]],
    *,
    input_path: pathlib.Path | None = None,
    limit: int | None = None,
    near_threshold: float = DEFAULT_NEAR_THRESHOLD,
    top_n: int = 20,
) -> dict[str, Any]:
    """Build a full, provenance-preserving review-routing census."""

    if not (0.0 < near_threshold <= 1.0):
        raise ValueError("near_threshold must be in (0, 1]")
    if limit is not None and limit <= 0:
        raise ValueError("limit must be positive")
    catalog_index = catalog_facts.CatalogIndex.load()
    by_group: dict[str, dict[str, dict[str, Any]]] = {name: {} for name in GROUPS}
    risk_counts_by_group: dict[str, Counter[str]] = {
        name: Counter() for name in GROUPS
    }
    records_seen = 0
    seen_record_ids: set[str] = set()
    coordinate_errors: list[dict[str, Any]] = []
    for record in records:
        if limit is not None and records_seen >= limit:
            break
        assert_admissible_record(record)
        record_id = str(record["id"])
        if record_id in seen_record_ids:
            raise ValueError(f"duplicate record id: {record_id}")
        seen_record_ids.add(record_id)
        profiles = build_record_profiles(record, catalog_index=catalog_index)
        for group_name, profile in profiles.items():
            errors = validate_profile(record, profile)
            if errors:
                coordinate_errors.append(
                    {"record_id": record["id"], "group": group_name, "errors": errors}
                )
            fingerprint = str(profile["exact_fingerprint"])
            aggregate = by_group[group_name].get(fingerprint)
            member = _member_from_profile(profile)
            if aggregate is None:
                by_group[group_name][fingerprint] = {
                    "representative": profile,
                    "members": [member],
                }
            else:
                representative = aggregate["representative"]
                collision_fields = [
                    field
                    for field in (
                        "near_boundary_key",
                        "near_simhash64",
                        "clause_multiset",
                    )
                    if representative[field] != profile[field]
                ]
                if collision_fields:
                    raise ValueError(
                        "exact fingerprint collision for "
                        f"{group_name}:{fingerprint}; "
                        f"representative={representative['record_id']}; "
                        f"incoming={profile['record_id']}; "
                        f"fields={','.join(collision_fields)}"
                    )
                aggregate["members"].append(member)
                # Representative choice must not depend on organizer input
                # order.  The normalized cluster is the same, while the
                # smallest stable source identity provides deterministic
                # coordinates and provenance.
                old_key = (
                    str(representative["record_id"]),
                    str(representative["source_sha256"]),
                )
                new_key = (str(profile["record_id"]), str(profile["source_sha256"]))
                if new_key < old_key:
                    aggregate["representative"] = profile
            risk_counts_by_group[group_name][profile["routing"]["risk_tier"]] += 1
        records_seen += 1
    if coordinate_errors:
        raise ValueError(f"coordinate validation failed: {coordinate_errors[:5]}")

    groups = {
        group_name: _cluster_group_profiles(
            group_name,
            clusters,
            near_threshold=near_threshold,
            top_n=top_n,
            risk_counts=risk_counts_by_group[group_name],
        )
        for group_name, clusters in by_group.items()
    }
    total_profiles = records_seen * len(GROUPS)
    repeated_members = sum(
        group["statistics"]["members_in_repeated_exact_clusters"]
        for group in groups.values()
    )
    payload: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "generated_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "semantic_role": "independent_review_batching_and_risk_routing_only",
        "review_only_contract": {
            "automatic_decision_propagation_allowed": False,
            "semantic_equivalence_claimed_for_exact_clusters": False,
            "semantic_equivalence_claimed_for_near_candidates": False,
            "human_or_independent_adjudicator_must_read_each_member": True,
            "labels_predictions_or_model_responses_consumed": False,
        },
        "input": {
            "path": str(input_path) if input_path else None,
            "sha256": file_sha256(input_path) if input_path else None,
            "records": records_seen,
            "limit": limit,
            "catalog": {
                "path": (
                    catalog_index.path.resolve().relative_to(ROOT.resolve()).as_posix()
                    if catalog_index.path.resolve().is_relative_to(ROOT.resolve())
                    else str(catalog_index.path.resolve())
                ),
                "sha256": catalog_index.sha256,
                "rows": catalog_index.total_rows,
            },
        },
        "normalization_contract": {
            "normalized_variables": [
                "full dates with relative gap signature",
                "money with legal band and meta relation signature",
                "percentages with 5/10 percent boundary signature",
                "generic counts with unit shape",
                "approved anonymized tokens with kind/unit/subtype",
            ],
            "preserved_boundaries": [
                "AND/OR/NEG/EXCEPT sequence",
                "bid qualification / evaluation / post-award phase",
                "bidder / awardee / manufacturer / supplier / personnel actor",
                "exact ten-digit product code",
                "product-name hash",
                "exact four-digit industry code",
                "law/method/work-type metadata",
                "catalog condition resolution for v10-v18",
                "source completeness and dropped-document type",
            ],
        },
        "statistics": {
            "records": records_seen,
            "group_profiles": total_profiles,
            "unique_exact_clusters": sum(
                group["statistics"]["unique_exact_clusters"] for group in groups.values()
            ),
            "repeated_exact_clusters": sum(
                group["statistics"]["repeated_exact_clusters"] for group in groups.values()
            ),
            "singleton_exact_clusters": sum(
                group["statistics"]["singleton_exact_clusters"] for group in groups.values()
            ),
            "members_in_repeated_exact_clusters": repeated_members,
            "profiles_not_in_repeated_exact_clusters": total_profiles - repeated_members,
        },
        "groups": groups,
    }
    payload["content_sha256"] = sha256_object(
        {key: value for key, value in payload.items() if key not in {"generated_at_utc", "content_sha256"}}
    )
    return payload


def write_census(census: Mapping[str, Any], output: pathlib.Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(f".{output.name}.tmp")
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as handle:
            # json.dump streams the large census instead of materializing a
            # second multi-gigabyte string in memory.  Replace only after a
            # complete write so interrupted runs never look final.
            json.dump(census, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
        temporary.replace(output)
    except BaseException:
        if temporary.exists():
            temporary.unlink()
        raise


def render_summary_markdown(census: Mapping[str, Any]) -> str:
    lines = [
        "# 독립 템플릿 클러스터 census",
        "",
        "> 이 결과는 검수 묶음/위험 라우팅 전용이다. exact 또는 near cluster의 라벨 자동 전파는 금지된다.",
        "",
        f"- 공고: {census['statistics']['records']:,}건",
        f"- 그룹 프로필: {census['statistics']['group_profiles']:,}개",
        f"- exact cluster: {census['statistics']['unique_exact_clusters']:,}개",
        f"- 반복 exact cluster: {census['statistics']['repeated_exact_clusters']:,}개",
        f"- singleton exact cluster: {census['statistics']['singleton_exact_clusters']:,}개",
        f"- 반복 exact cluster 소속 프로필: {census['statistics']['members_in_repeated_exact_clusters']:,}개",
        "",
        "| 그룹 | 프로필 | 고유 exact | 반복 exact | singleton | 반복 소속 | 최대 크기 | near 후보셋 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for group_name, group in census["groups"].items():
        stats = group["statistics"]
        lines.append(
            f"| {group_name} | {stats['records']:,} | {stats['unique_exact_clusters']:,} | "
            f"{stats['repeated_exact_clusters']:,} | {stats['singleton_exact_clusters']:,} | "
            f"{stats['members_in_repeated_exact_clusters']:,} | {stats['largest_exact_cluster']:,} | "
            f"{stats['near_duplicate_candidate_sets']:,} |"
        )
    lines.extend(
        [
            "",
            "## 강제 안전 경계",
            "",
            "- 모든 cluster는 `automatic_decision_propagation_allowed=false`다.",
            "- exact는 보수적 정규화 템플릿이 같다는 뜻일 뿐 법적 결론이 같다는 뜻이 아니다.",
            "- near는 동일한 의미 경계와 SimHash 후보를 다시 Jaccard로 거른 검수 후보일 뿐이다.",
            "- 문서 탈락, 빈 retrieval, clause clipping, 예외/혼합시점 문구는 risk flag로 보존한다.",
            "- 모든 member에 record 원문 hash, 문서 hash, clause 좌표와 clause hash가 남는다.",
            "",
            f"Content SHA-256: `{census['content_sha256']}`",
            "",
        ]
    )
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build label-free exact/near template clusters for independent review routing."
    )
    parser.add_argument(
        "--input",
        type=pathlib.Path,
        default=ROOT / "data_open" / "train_unlabeled.jsonl.gz",
    )
    parser.add_argument("--output", type=pathlib.Path, required=True)
    parser.add_argument("--summary-md", type=pathlib.Path)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--near-threshold", type=float, default=DEFAULT_NEAR_THRESHOLD)
    parser.add_argument("--top-n", type=int, default=20)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    census = build_census(
        read_jsonl_gz(args.input),
        input_path=args.input,
        limit=args.limit,
        near_threshold=args.near_threshold,
        top_n=args.top_n,
    )
    write_census(census, args.output)
    if args.summary_md:
        args.summary_md.parent.mkdir(parents=True, exist_ok=True)
        args.summary_md.write_text(render_summary_markdown(census), encoding="utf-8")
    print(
        json.dumps(
            {
                "records": census["statistics"]["records"],
                "group_profiles": census["statistics"]["group_profiles"],
                "unique_exact_clusters": census["statistics"]["unique_exact_clusters"],
                "repeated_exact_clusters": census["statistics"]["repeated_exact_clusters"],
                "output": str(args.output),
                "content_sha256": census["content_sha256"],
                "automatic_decision_propagation_allowed": False,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "GROUPS",
    "PROFILE_SCHEMA_VERSION",
    "SCHEMA_VERSION",
    "assert_admissible_record",
    "build_census",
    "build_record_profiles",
    "extract_group_clauses",
    "normalize_clause",
    "render_summary_markdown",
    "simhash64",
    "validate_profile",
    "write_census",
]
