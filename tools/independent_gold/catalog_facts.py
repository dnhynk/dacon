"""Independent, source-grounded facts for competitive-product codes.

The resolver in this file is deliberately narrow.  It reads only an organizer
record and the supplied Ministry of SMEs and Startups catalog.  It does not
infer a competition label and it does not depend on any submission runtime.

There are two important conservative boundaries:

* A ten-digit number is retained as a source token, but it is promoted to a
  product-code mention only when it is an exact catalog code or its local
  source context explicitly identifies a product/classification code.  This
  prevents ten-digit prices from silently becoming products.
* A registered catalog row is not automatically applicable when its special
  note needs a price, material, purpose, capacity, legal-scope, or contract
  fact.  Exact, simple price predicates can be evaluated from organizer meta;
  every other unresolved condition remains ``unknown``.

The resulting objects are JSON serializable and retain exact document/meta and
catalog coordinates for later adjudication.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import pathlib
import re
from collections import Counter, defaultdict
from typing import Any, Iterable, Iterator, Mapping, Sequence


ROOT = pathlib.Path(__file__).resolve().parents[2]
DEFAULT_CATALOG_PATH = (
    ROOT
    / "data_open"
    / "data"
    / "법령패키지"
    / "중기부고시"
    / "중기부고시_경쟁제품_세부품명.csv"
)
SCHEMA_VERSION = "dacon.independent.catalog_facts.v2"
CATALOG_COLUMNS = (
    "대분류번호",
    "대분류",
    "제품명번호",
    "제품명",
    "세부품명번호",
    "세부품명",
    "특이사항",
    "공사용자재직접구매",
)

STRICT_TEN_DIGIT_RE = re.compile(r"(?<!\d)\d{10}(?!\d)")
CODE_FIELD_RE = re.compile(
    r"(?:세부\s*품명(?:\s*번호)?|정부\s*물품\s*분류\s*번호|"
    r"물품\s*분류\s*번호|품명\s*번호|G2B\s*(?:물품)?\s*분류\s*번호|"
    r"Commodity\s+Classification\s+Code)",
    re.IGNORECASE,
)
META_CODE_FIELD_RE = re.compile(
    r"(?:세부\s*품명|물품\s*분류|품명\s*번호|분류\s*번호)", re.IGNORECASE
)
PRICE_RE = re.compile(
    r"(?P<number>\d[\d,.]*)\s*(?P<unit>억원|천만원|백만원|만원|원)"
    r"[\s)）]*(?:의\s*)?(?P<comparator>미만|이하|이상|초과)"
)
PURE_PRICE_RE = re.compile(
    r"^(?:추정\s*가격|공공\s*입찰\s*금액|입찰\s*금액|금액|총액)?\s*"
    r"\d[\d,.]*\s*(?:억원|천만원|백만원|만원|원)[\s)）]*(?:의\s*)?"
    r"(?:미만|이하|이상|초과)\s*(?:에\s*)?한함[.!。]?$"
)

MATERIAL_RE = re.compile(
    r"(?:소재|재질|플라스틱|스테인리스|스테인레스|고무|금속|목재|철제|"
    r"폴리에틸렌|라텍스|석탄계|석유화학계|알루미늄|합성수지)",
    re.IGNORECASE,
)
PURPOSE_RE = re.compile(
    r"(?:농업용|공업용|산업용|통신용|송변전용|홍보용|인쇄기용|국가유산수리용|"
    r"군사용|해상용|수상용|가정용|상업용|국방규격|경찰규격|설치|노선사업|"
    r"공공분양주택|도심공공주택|신혼희망타운|자회사와\s*수의계약|"
    r"용도|사용하는|분야|전시회|산책로)",
    re.IGNORECASE,
)
TECHNICAL_RE = re.compile(
    r"(?:용량|처리능력|인양능력|발전용량|자체중량|총톤수|속도|픽셀|"
    r"\d\s*(?:kW|㎏|kg|ton|톤|mm|m|L|리터)\b|FDM\s*방식)",
    re.IGNORECASE,
)
LEGAL_SCOPE_RE = re.compile(
    r"(?:법률|시행령|고시|법\s*제\d+조|소프트웨어\s*진흥법|경비업법)",
    re.IGNORECASE,
)
CONTRACT_SCOPE_RE = re.compile(
    r"(?:다수공급자계약|총액\s*계약|공공입찰|수의계약|예측량|점유율|예외)",
    re.IGNORECASE,
)
COMMISSIONING_INSTITUTION_IDENTITY_RE = re.compile(
    r"(?:\[수요기관[^\]\n]*\]|수요\s*기관|발주\s*기관|발주처)"
    r"[^.\n]{0,45}(?:표준화?\s*템플릿|기관\s*명|명칭|로고|C\s*I|B\s*I|캐릭터)|"
    r"(?:표준화?\s*템플릿|기관\s*명|명칭|로고|C\s*I|B\s*I|캐릭터)"
    r"[^.\n]{0,45}(?:\[수요기관[^\]\n]*\]|수요\s*기관|발주\s*기관|발주처)",
    re.IGNORECASE,
)

_UNIT_MULTIPLIER = {
    "원": 1,
    "만원": 10_000,
    "백만원": 1_000_000,
    "천만원": 10_000_000,
    "억원": 100_000_000,
}
_COMPARATOR_SYMBOL = {"미만": "lt", "이하": "le", "이상": "ge", "초과": "gt"}


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_object(value: Any) -> str:
    rendered = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return _sha256_bytes(rendered.encode("utf-8"))


def _relative_source_path(path: pathlib.Path) -> str:
    try:
        return path.resolve().relative_to(ROOT.resolve()).as_posix()
    except ValueError:
        return str(path.resolve())


class CatalogIndex:
    """Validated lookup over the supplied competitive-product CSV."""

    def __init__(
        self,
        path: pathlib.Path,
        rows: Sequence[dict[str, Any]],
        non_code_rows: Sequence[dict[str, Any]],
        sha256: str,
    ):
        self.path = path
        self.rows = tuple(rows)
        self.non_code_rows = tuple(non_code_rows)
        self.total_rows = len(self.rows) + len(self.non_code_rows)
        self.sha256 = sha256
        self.by_code = {str(row["세부품명번호"]): row for row in rows}

    @classmethod
    def load(cls, path: pathlib.Path | str = DEFAULT_CATALOG_PATH) -> "CatalogIndex":
        source = pathlib.Path(path)
        raw = source.read_bytes()
        with source.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            missing = set(CATALOG_COLUMNS) - set(reader.fieldnames or ())
            if missing:
                raise ValueError(f"catalog missing columns: {sorted(missing)}")
            rows: list[dict[str, Any]] = []
            non_code_rows: list[dict[str, Any]] = []
            seen: set[str] = set()
            for row_number, raw_row in enumerate(reader, 2):
                row = {column: str(raw_row.get(column) or "").strip() for column in CATALOG_COLUMNS}
                code = row["세부품명번호"]
                coordinate = {
                    "source_kind": "catalog_csv",
                    "path": _relative_source_path(source),
                    "row_number": row_number,
                    "code_column": "세부품명번호",
                    "note_column": "특이사항",
                }
                # The supplied file contains one intentional umbrella row for
                # 국방규격 without a ten-digit code.  Preserve it for audits,
                # but never make it addressable as an exact code.
                if not code:
                    row["catalog_coordinate"] = coordinate
                    row["condition"] = parse_special_note(
                        row["특이사항"], coordinate=coordinate
                    )
                    non_code_rows.append(row)
                    continue
                if not re.fullmatch(r"\d{10}", code):
                    raise ValueError(f"catalog row {row_number}: invalid 10-digit code {code!r}")
                if code in seen:
                    raise ValueError(f"catalog row {row_number}: duplicate code {code}")
                seen.add(code)
                row["catalog_coordinate"] = coordinate
                row["condition"] = parse_special_note(
                    row["특이사항"], coordinate=row["catalog_coordinate"]
                )
                rows.append(row)
        return cls(source, rows, non_code_rows, _sha256_bytes(raw))

    def get(self, code: str) -> dict[str, Any] | None:
        return self.by_code.get(code)

    def __contains__(self, code: object) -> bool:
        return code in self.by_code

    def __len__(self) -> int:
        return len(self.rows)


def _split_clauses(note: str) -> list[tuple[str, int, int]]:
    if not note:
        return []
    markers = list(re.finditer(r"(?<!\S)\d+\.\s*", note))
    if not markers:
        start = len(note) - len(note.lstrip())
        end = len(note.rstrip())
        return [(note[start:end], start, end)] if start < end else []

    clauses: list[tuple[str, int, int]] = []
    if note[: markers[0].start()].strip():
        start = len(note[: markers[0].start()]) - len(note[: markers[0].start()].lstrip())
        end = len(note[: markers[0].start()].rstrip())
        clauses.append((note[start:end], start, end))
    for index, marker in enumerate(markers):
        raw_start = marker.end()
        raw_end = markers[index + 1].start() if index + 1 < len(markers) else len(note)
        raw = note[raw_start:raw_end]
        leading = len(raw) - len(raw.lstrip())
        trailing_end = len(raw.rstrip())
        start = raw_start + leading
        end = raw_start + trailing_end
        if start < end:
            clauses.append((note[start:end], start, end))
    return clauses


def _won_value(number: str, unit: str) -> int:
    numeric = float(number.replace(",", ""))
    return int(round(numeric * _UNIT_MULTIPLIER[unit]))


def _metric_for_price(clause: str, match_start: int) -> str:
    prefix = clause[max(0, match_start - 24) : match_start]
    if re.search(r"추정\s*가격", prefix):
        return "estimated_price"
    if re.search(r"공공\s*입찰\s*금액|입찰\s*금액", prefix):
        return "public_bid_amount"
    if re.search(r"총액", prefix):
        return "total_amount"
    return "amount"


def _condition_categories(text: str, has_price: bool) -> list[str]:
    categories: set[str] = set()
    if has_price:
        categories.add("price")
    if MATERIAL_RE.search(text):
        categories.add("material")
    if PURPOSE_RE.search(text):
        categories.add("purpose")
    if TECHNICAL_RE.search(text):
        categories.add("technical_spec")
    if LEGAL_SCOPE_RE.search(text):
        categories.add("legal_scope")
    if CONTRACT_SCOPE_RE.search(text):
        categories.add("contract_scope")
    if not categories and re.search(r"(?:한함|한정|제외|예외|적용)", text):
        categories.add("other_scope")
    return sorted(categories)


def parse_special_note(
    note: str, *, coordinate: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    """Turn one catalog special note into auditable, conservative clauses."""

    note = str(note or "").strip()
    parsed_clauses: list[dict[str, Any]] = []
    all_categories: set[str] = set()
    for text, start, end in _split_clauses(note):
        prices: list[dict[str, Any]] = []
        for match in PRICE_RE.finditer(text):
            prices.append(
                {
                    "metric": _metric_for_price(text, match.start()),
                    "comparator": _COMPARATOR_SYMBOL[match.group("comparator")],
                    "threshold_won": _won_value(match.group("number"), match.group("unit")),
                    "verbatim": match.group(0),
                    "note_start": start + match.start(),
                    "note_end": start + match.end(),
                }
            )
        categories = _condition_categories(text, bool(prices))
        all_categories.update(categories)
        restrictive = bool(re.search(r"(?:한함|한정|제외|예외|적용)", text))
        if re.search(r"(?:제외|예외)", text):
            effect = "exclusion"
        elif re.search(r"(?:한함|한정)", text):
            effect = "required_scope"
        elif restrictive:
            effect = "scope_reference"
        else:
            effect = "informative"
        pure_price = bool(PURE_PRICE_RE.fullmatch(re.sub(r"\s+", " ", text).strip()))
        # A full-clause match means the whole restriction is the price
        # predicate, even when its metric phrase (for example 공공입찰 금액)
        # also resembles a contract-scope keyword.
        requires_nonprice_fact = restrictive and (not prices or not pure_price)
        parsed_clauses.append(
            {
                "text": text,
                "note_start": start,
                "note_end": end,
                "effect": effect,
                "categories": categories,
                "price_conditions": prices,
                "pure_price_condition": pure_price,
                "requires_nonprice_fact": requires_nonprice_fact,
            }
        )

    restrictive_clauses = [
        clause for clause in parsed_clauses if clause["effect"] != "informative"
    ]
    if not note:
        kind = "none"
    elif restrictive_clauses:
        kind = "conditional"
    else:
        kind = "informative"
    result: dict[str, Any] = {
        "raw": note,
        "kind": kind,
        "categories": sorted(all_categories),
        "requires_notice_facts": any(
            clause["requires_nonprice_fact"] or clause["price_conditions"]
            for clause in restrictive_clauses
        ),
        "clauses": parsed_clauses,
    }
    if coordinate is not None:
        result["source_coordinate"] = {
            **dict(coordinate),
            "field": "특이사항",
            "start": 0,
            "end": len(note),
            "quote": note,
        }
    return result


def _source_context(text: str, start: int, end: int) -> tuple[int, int, str]:
    line_start = text.rfind("\n", max(0, start - 320), start)
    line_end = text.find("\n", end, min(len(text), end + 180))
    context_start = max(0, start - 240) if line_start < 0 else line_start + 1
    context_end = min(len(text), end + 100) if line_end < 0 else line_end
    return context_start, context_end, text[context_start:context_end]


def _nonprice_condition_evidence(
    clause_text: str, record: Mapping[str, Any]
) -> list[dict[str, Any]]:
    """Return strict source candidates for a supplied non-price predicate.

    This is intentionally narrow.  A public-body name in a notice header is
    not evidence that the delivered video contains an identifier.  The source
    must bind an institution-specific template/name/logo/character to nearby
    video or content work, matching the catalog note's own inclusive test.
    """

    if "제작 의뢰한 공공기관을 식별할 수 있는 정보가 포함된 영상" not in clause_text:
        return []

    evidence: list[dict[str, Any]] = []
    for doc_index, raw_doc in enumerate(record.get("docs") or []):
        text = str(raw_doc.get("text") or "")
        doc_id = str(raw_doc.get("doc_id") or f"D{doc_index}")
        doc_type = str(raw_doc.get("type") or "unknown")
        doc_sha256 = _sha256_bytes(text.encode("utf-8"))
        for match in COMMISSIONING_INSTITUTION_IDENTITY_RE.finditer(text):
            context_start = max(0, match.start() - 420)
            context_end = min(len(text), match.end() + 260)
            context = text[context_start:context_end]
            if not re.search(r"영상|동영상|콘텐츠|스토리보드", context, re.IGNORECASE):
                continue
            evidence.append(
                {
                    "predicate": "contains_commissioning_public_body_identifier",
                    "source_kind": "document",
                    "doc_index": doc_index,
                    "doc_id": doc_id,
                    "doc_type": doc_type,
                    "doc_sha256": doc_sha256,
                    "start": match.start(),
                    "end": match.end(),
                    "raw": match.group(0),
                    "context_start": context_start,
                    "context_end": context_end,
                    "context": context,
                }
            )
    return evidence


def _has_explicit_code_context(text: str, start: int, end: int) -> bool:
    immediate = text[max(0, start - 28) : min(len(text), end + 12)]
    raw = re.escape(text[start:end])
    if re.search(raw + r"\s*원(?:\b|정|\)|,|\.)", immediate):
        return False
    if re.search(r"(?:가격|금액|예산|사업비|기초금액)\s*[:：]?\s*$", text[max(0, start - 32) : start]):
        return False
    local = text[max(0, start - 180) : min(len(text), end + 72)]
    return bool(CODE_FIELD_RE.search(local))


def _fragmented_ten_digit_spans(text: str) -> Iterator[tuple[int, int, str]]:
    """Yield whitespace-fragmented ten-digit spans (for PDF extraction damage)."""

    for start, character in enumerate(text):
        if not character.isdigit() or (start > 0 and text[start - 1].isdigit()):
            continue
        position = start
        digits: list[str] = []
        used_space = False
        while position < len(text) and len(digits) < 10:
            current = text[position]
            if current.isdigit():
                digits.append(current)
                position += 1
                continue
            if current in " \t\u00a0" and digits:
                gap_start = position
                while position < len(text) and text[position] in " \t\u00a0":
                    position += 1
                if position - gap_start > 3 or position >= len(text) or not text[position].isdigit():
                    break
                used_space = True
                continue
            break
        if len(digits) != 10 or not used_space:
            continue
        lookahead = position
        while lookahead < len(text) and text[lookahead] in " \t\u00a0":
            lookahead += 1
        if lookahead < len(text) and text[lookahead].isdigit():
            continue
        yield start, position, "".join(digits)


def _document_tokens(record: Mapping[str, Any], catalog: CatalogIndex) -> list[dict[str, Any]]:
    tokens: list[dict[str, Any]] = []
    for doc_index, raw_doc in enumerate(record.get("docs") or []):
        text = str(raw_doc.get("text") or "")
        doc_id = str(raw_doc.get("doc_id") or f"D{doc_index}")
        doc_type = str(raw_doc.get("type") or "unknown")
        doc_sha256 = _sha256_bytes(text.encode("utf-8"))
        occupied: set[tuple[int, int]] = set()
        spans: list[tuple[int, int, str, str]] = [
            (match.start(), match.end(), match.group(0), "exact_10_digit")
            for match in STRICT_TEN_DIGIT_RE.finditer(text)
        ]
        for start, end, code in _fragmented_ten_digit_spans(text):
            if any(not (end <= left or start >= right) for left, right in occupied):
                continue
            context_start, context_end, context = _source_context(text, start, end)
            if _has_explicit_code_context(text, start, end):
                spans.append((start, end, code, "whitespace_normalized_10_digit"))
        for start, end, code, mention_form in sorted(spans):
            if (start, end) in occupied:
                continue
            occupied.add((start, end))
            context_start, context_end, context = _source_context(text, start, end)
            explicit = _has_explicit_code_context(text, start, end)
            tokens.append(
                {
                    "code": code,
                    "raw": text[start:end],
                    "mention_form": mention_form,
                    "catalog_registered": code in catalog,
                    "explicit_code_context": explicit,
                    "source_kind": "document",
                    "doc_index": doc_index,
                    "doc_id": doc_id,
                    "doc_type": doc_type,
                    "doc_sha256": doc_sha256,
                    "start": start,
                    "end": end,
                    "context_start": context_start,
                    "context_end": context_end,
                    "context": context,
                }
            )
    return tokens


def _meta_tokens(record: Mapping[str, Any], catalog: CatalogIndex) -> list[dict[str, Any]]:
    tokens: list[dict[str, Any]] = []
    meta = record.get("meta") or {}
    if not isinstance(meta, Mapping):
        return tokens
    for field, raw_value in meta.items():
        if not META_CODE_FIELD_RE.search(str(field)):
            continue
        value = str(raw_value or "")
        for match in STRICT_TEN_DIGIT_RE.finditer(value):
            code = match.group(0)
            tokens.append(
                {
                    "code": code,
                    "raw": code,
                    "mention_form": "exact_10_digit",
                    "catalog_registered": code in catalog,
                    "explicit_code_context": True,
                    "source_kind": "meta",
                    "field": str(field),
                    "path": f"meta.{field}",
                    "start": match.start(),
                    "end": match.end(),
                    "value": value,
                }
            )
    return tokens


def extract_code_mentions(
    record: Mapping[str, Any], catalog: CatalogIndex | None = None
) -> dict[str, Any]:
    """Extract product codes while retaining ambiguous ten-digit source tokens."""

    catalog = catalog or CatalogIndex.load()
    raw_tokens = _document_tokens(record, catalog) + _meta_tokens(record, catalog)
    explicitly_identified = {
        token["code"]
        for token in raw_tokens
        if token["catalog_registered"] or token["explicit_code_context"]
    }
    mentions: list[dict[str, Any]] = []
    ambiguous: list[dict[str, Any]] = []
    for token in raw_tokens:
        output = dict(token)
        if token["catalog_registered"]:
            output["identification_basis"] = "exact_catalog_match"
            mentions.append(output)
        elif token["explicit_code_context"]:
            output["identification_basis"] = "explicit_source_code_context"
            mentions.append(output)
        elif token["code"] in explicitly_identified:
            output["identification_basis"] = "same_code_explicit_elsewhere"
            mentions.append(output)
        else:
            output["identification_basis"] = "ambiguous_10_digit_number"
            ambiguous.append(output)

    mentions.sort(
        key=lambda row: (
            0 if row["source_kind"] == "document" else 1,
            int(row.get("doc_index", 10**9)),
            str(row.get("field", "")),
            int(row["start"]),
            row["code"],
        )
    )
    ambiguous.sort(
        key=lambda row: (
            int(row.get("doc_index", 10**9)), int(row["start"]), row["code"]
        )
    )
    return {
        "mentions": mentions,
        "ambiguous_ten_digit_tokens": ambiguous,
        "unique_codes": sorted({row["code"] for row in mentions}),
    }


def _meta_amount(
    record: Mapping[str, Any], metric: str
) -> tuple[int | None, dict[str, Any] | None, str | None]:
    meta = record.get("meta") or {}
    if not isinstance(meta, Mapping):
        return None, None, "meta_not_mapping"
    if metric == "total_amount":
        candidates = ("배정예산금액",)
    else:
        candidates = ("입찰추정가격",)
    for field in candidates:
        value = meta.get(field)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        if value < 0:
            continue
        integer = int(value)
        rendered = str(value)
        return (
            integer,
            {
                "source_kind": "meta",
                "field": field,
                "path": f"meta.{field}",
                "start": 0,
                "end": len(rendered),
                "quote": rendered,
            },
            None,
        )
    return None, None, f"missing_{candidates[0]}"


def _compare(value: int, comparator: str, threshold: int) -> bool:
    if comparator == "lt":
        return value < threshold
    if comparator == "le":
        return value <= threshold
    if comparator == "ge":
        return value >= threshold
    if comparator == "gt":
        return value > threshold
    raise ValueError(f"unsupported comparator: {comparator}")


def _evaluate_catalog_condition(
    condition: Mapping[str, Any], record: Mapping[str, Any]
) -> dict[str, Any]:
    if condition["kind"] in {"none", "informative"}:
        return {
            "decision": "applicable",
            "reason_code": "no_restrictive_special_condition",
            "reason": "The supplied catalog row has no unresolved restrictive condition.",
            "clause_evaluations": [],
        }

    evaluations: list[dict[str, Any]] = []
    decisive_failure = False
    unresolved = False
    for clause in condition["clauses"]:
        if clause["effect"] == "informative":
            evaluations.append(
                {
                    "text": clause["text"],
                    "status": "satisfied",
                    "reason_code": "informative_clause",
                }
            )
            continue

        price_results: list[dict[str, Any]] = []
        for predicate in clause["price_conditions"]:
            value, coordinate, missing = _meta_amount(record, predicate["metric"])
            if value is None:
                price_results.append(
                    {
                        **predicate,
                        "status": "unknown",
                        "reason_code": missing,
                    }
                )
            else:
                passed = _compare(value, predicate["comparator"], predicate["threshold_won"])
                price_results.append(
                    {
                        **predicate,
                        "status": "satisfied" if passed else "not_satisfied",
                        "observed_won": value,
                        "observed_coordinate": coordinate,
                    }
                )

        price_unknown = any(row["status"] == "unknown" for row in price_results)
        price_all_true = bool(price_results) and all(
            row["status"] == "satisfied" for row in price_results
        )
        price_any_false = any(row["status"] == "not_satisfied" for row in price_results)
        needs_other = bool(clause["requires_nonprice_fact"])
        nonprice_evidence = _nonprice_condition_evidence(clause["text"], record)
        effect = clause["effect"]

        if effect == "required_scope":
            if price_any_false:
                status = "not_satisfied"
                reason_code = "required_price_scope_not_met"
                decisive_failure = True
            elif price_unknown:
                status = "unknown"
                reason_code = "required_price_fact_missing"
                unresolved = True
            elif needs_other and not nonprice_evidence:
                status = "unknown"
                reason_code = "required_nonprice_fact_unresolved"
                unresolved = True
            elif needs_other:
                status = "satisfied"
                reason_code = "required_nonprice_scope_met"
            elif price_all_true:
                status = "satisfied"
                reason_code = "required_price_scope_met"
            else:
                status = "unknown"
                reason_code = "required_scope_unresolved"
                unresolved = True
        elif effect == "exclusion":
            if price_any_false and price_results:
                status = "satisfied"
                reason_code = "price_disproves_exclusion_trigger"
            elif price_unknown:
                status = "unknown"
                reason_code = "exclusion_price_fact_missing"
                unresolved = True
            elif needs_other:
                status = "unknown"
                reason_code = "exclusion_nonprice_fact_unresolved"
                unresolved = True
            elif price_all_true:
                status = "not_satisfied"
                reason_code = "catalog_exclusion_trigger_met"
                decisive_failure = True
            else:
                status = "unknown"
                reason_code = "catalog_exclusion_unresolved"
                unresolved = True
        else:
            status = "unknown"
            reason_code = "scope_reference_requires_adjudication"
            unresolved = True

        evaluations.append(
            {
                "text": clause["text"],
                "effect": effect,
                "categories": clause["categories"],
                "status": status,
                "reason_code": reason_code,
                "price_evaluations": price_results,
                "nonprice_evidence": nonprice_evidence,
            }
        )

    if decisive_failure:
        decision = "not_applicable"
        reason_code = "catalog_condition_not_met"
        reason = "An exact supplied-catalog scope condition is not met."
    elif unresolved:
        decision = "unknown"
        reason_code = "catalog_condition_unresolved"
        reason = "The catalog row needs a notice fact that is absent or not safely decidable."
    else:
        decision = "applicable"
        reason_code = "catalog_conditions_met"
        reason = "Every restrictive catalog condition was resolved from supplied facts."
    return {
        "decision": decision,
        "reason_code": reason_code,
        "reason": reason,
        "clause_evaluations": evaluations,
    }


def resolve_code(
    code: str,
    mentions: Sequence[Mapping[str, Any]],
    record: Mapping[str, Any],
    catalog: CatalogIndex,
) -> dict[str, Any]:
    row = catalog.get(code)
    if row is None:
        return {
            "code": code,
            "decision": "unknown",
            "reason_code": "code_not_in_supplied_catalog",
            "reason": "The source identifies a ten-digit product code absent from the supplied catalog.",
            "catalog_registered": False,
            "catalog_row": None,
            "mentions": [dict(mention) for mention in mentions],
            "condition_evaluation": None,
        }

    evaluation = _evaluate_catalog_condition(row["condition"], record)
    catalog_row = {key: row[key] for key in CATALOG_COLUMNS}
    catalog_row["catalog_coordinate"] = row["catalog_coordinate"]
    catalog_row["condition"] = row["condition"]
    return {
        "code": code,
        "decision": evaluation["decision"],
        "reason_code": evaluation["reason_code"],
        "reason": evaluation["reason"],
        "catalog_registered": True,
        "catalog_row": catalog_row,
        "mentions": [dict(mention) for mention in mentions],
        "condition_evaluation": evaluation,
    }


def resolve_record(
    record: Mapping[str, Any], catalog: CatalogIndex | None = None
) -> dict[str, Any]:
    """Resolve every source-identified code and a conservative record summary."""

    catalog = catalog or CatalogIndex.load()
    extraction = extract_code_mentions(record, catalog)
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for mention in extraction["mentions"]:
        grouped[mention["code"]].append(mention)
    code_facts = [
        resolve_code(code, grouped[code], record, catalog) for code in sorted(grouped)
    ]

    if len(code_facts) > 1:
        code_decisions = {str(fact["decision"]) for fact in code_facts}
        if code_decisions == {"applicable"}:
            summary_decision = "applicable"
            summary_reason_code = "all_identified_product_codes_applicable"
            summary_reason = (
                "Every source-identified product code is applicable under its supplied catalog row."
            )
        elif code_decisions == {"not_applicable"}:
            summary_decision = "not_applicable"
            summary_reason_code = "all_identified_product_codes_not_applicable"
            summary_reason = (
                "Every source-identified product code is outside its supplied catalog condition."
            )
        elif code_decisions == {"unknown"}:
            summary_decision = "unknown"
            summary_reason_code = "all_product_codes_unresolved"
            summary_reason = "Every identified product code still has an unresolved applicability premise."
        else:
            summary_decision = "unknown"
            summary_reason_code = "mixed_product_code_decisions"
            summary_reason = (
                "Identified product codes have different applicability decisions; scope requires adjudication."
            )
    elif len(code_facts) == 1:
        summary_decision = code_facts[0]["decision"]
        summary_reason_code = code_facts[0]["reason_code"]
        summary_reason = code_facts[0]["reason"]
    else:
        summary_decision = "unknown"
        summary_reason_code = "no_identified_product_code"
        summary_reason = "No catalog-exact or explicitly identified ten-digit product code was found."

    return {
        "schema_version": SCHEMA_VERSION,
        "id": str(record.get("id") or ""),
        "source_sha256": _sha256_object(record),
        "catalog": {
            "path": _relative_source_path(catalog.path),
            "sha256": catalog.sha256,
            "code_rows": len(catalog),
            "non_code_rows": len(catalog.non_code_rows),
            "total_rows": catalog.total_rows,
        },
        "summary": {
            "decision": summary_decision,
            "reason_code": summary_reason_code,
            "reason": summary_reason,
            "mixed_product_codes": len(code_facts) > 1,
            "unique_code_count": len(code_facts),
            "code_decisions": sorted({str(fact["decision"]) for fact in code_facts}),
        },
        "codes": code_facts,
        "ambiguous_ten_digit_tokens": extraction["ambiguous_ten_digit_tokens"],
    }


def read_jsonl_gz(path: pathlib.Path | str) -> Iterator[dict[str, Any]]:
    source = pathlib.Path(path)
    with gzip.open(source, "rt", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            row = json.loads(line)
            if not isinstance(row, dict):
                raise ValueError(f"{source}:{line_number}: expected object")
            yield row


def audit_records(
    records: Iterable[Mapping[str, Any]],
    *,
    catalog: CatalogIndex | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    """Return compact extraction/resolution statistics without writing labels."""

    catalog = catalog or CatalogIndex.load()
    summary_counts: Counter[str] = Counter()
    code_counts: Counter[str] = Counter()
    reason_counts: Counter[str] = Counter()
    condition_counts: Counter[str] = Counter()
    records_seen = 0
    records_with_codes = 0
    mixed_records = 0
    mentions = 0
    ambiguous = 0
    unique_catalog_codes: set[str] = set()
    for record in records:
        if limit is not None and records_seen >= limit:
            break
        result = resolve_record(record, catalog)
        records_seen += 1
        summary_counts[result["summary"]["decision"]] += 1
        if result["codes"]:
            records_with_codes += 1
        if result["summary"]["mixed_product_codes"]:
            mixed_records += 1
        ambiguous += len(result["ambiguous_ten_digit_tokens"])
        for fact in result["codes"]:
            code_counts[fact["decision"]] += 1
            reason_counts[fact["reason_code"]] += 1
            mentions += len(fact["mentions"])
            if fact["catalog_registered"]:
                unique_catalog_codes.add(fact["code"])
                condition_counts[fact["catalog_row"]["condition"]["kind"]] += 1
    return {
        "schema_version": f"{SCHEMA_VERSION}.audit",
        "records": records_seen,
        "records_with_identified_codes": records_with_codes,
        "mixed_product_records": mixed_records,
        "mentions": mentions,
        "ambiguous_ten_digit_tokens": ambiguous,
        "summary_decisions": dict(sorted(summary_counts.items())),
        "code_decisions": dict(sorted(code_counts.items())),
        "code_reason_counts": dict(sorted(reason_counts.items())),
        "registered_condition_kinds": dict(sorted(condition_counts.items())),
        "unique_registered_catalog_codes": len(unique_catalog_codes),
        "catalog_code_rows": len(catalog),
        "catalog_non_code_rows": len(catalog.non_code_rows),
        "catalog_total_rows": catalog.total_rows,
        "catalog_sha256": catalog.sha256,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=pathlib.Path, required=True)
    parser.add_argument("--catalog", type=pathlib.Path, default=DEFAULT_CATALOG_PATH)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--indent", type=int, default=2)
    args = parser.parse_args(argv)
    if args.limit is not None and args.limit < 0:
        parser.error("--limit must be non-negative")
    catalog = CatalogIndex.load(args.catalog)
    report = audit_records(read_jsonl_gz(args.input), catalog=catalog, limit=args.limit)
    print(json.dumps(report, ensure_ascii=False, indent=args.indent, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
