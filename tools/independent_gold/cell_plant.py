"""Deterministic planted-violation notices for teacher recall measurement; never gold.

An official positive evidence clause from the organizer dev set is transplanted
into the participation-qualification section of an official all-zero dev notice
whose metadata satisfies the target item's necessary conditions.  The target
cell is positive by construction, so teacher recall can be measured without
consensus.  Synthetic records keep the host ID and organizer shape, carry no
label, and are written only under ``runs/``.  They are not organizer data and
must never enter prevalence estimates, gold, or production tuning.

Dev labels are read here only to choose sources and hosts and to define the
expected cell; they are never written into a synthetic record or a prompt.
"""

from __future__ import annotations

import argparse
import copy
import csv
import gzip
import hashlib
import json
import pathlib
import re
import sys
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from decimal import Decimal
from typing import Any


ROOT = pathlib.Path(__file__).resolve().parents[2]
DEV_INPUT = ROOT / "data_open" / "dev.jsonl.gz"
DEV_LABELS = ROOT / "data_open" / "dev_labels.csv"
SPLIT_PATH = ROOT / "artifacts" / "audit" / "split.json"
POOL_INPUT = ROOT / "data_open" / "train_unlabeled.jsonl.gz"
FAMILY_MANIFEST = ROOT / "runs" / "self_label_20000_20260918" / "audit_v3" / "notice_families.json"
SCHEMA_VERSION = "dacon.independent.cell_plant.v1"
ITEMS = tuple(f"v{number}" for number in range(1, 25))
NOTICE_THRESHOLD_WON = 230_000_000
LOWEST_REGION_CEILING_WON = 150_000_000
HIGHEST_REGION_CEILING_WON = 500_000_000
SMALL_QUOTE_AWARD = "소액수의견적"
NEGOTIATED_AWARD = "협상에의한계약"
PRICE_PREMISED_ITEMS = {"v2", "v3", "v5", "v6", "v7", "v8", "v14", "v15", "v16", "v17", "v18"}


def price_premise_is_valid(host: Mapping[str, Any]) -> bool:
    """Reject placeholder/unit amounts; do not silently repair invalid metadata."""
    meta = host["meta"]
    price, budget = meta.get("입찰추정가격"), meta.get("배정예산금액")
    if type(price) is not int or price <= 1 or (type(budget) is int and budget <= 1):
        return False
    text = re.sub(r"[^\S\n]+", "", _all_text(host))
    if not re.search(r"단가(?:계약|입찰)|(?:입찰방법|계약방법)[^\n]{0,30}단가|입찰단가", text, re.I):
        return True
    pattern = r"(추정가격|배정예산(?:금액)?|사업예산|총사업비|총사업금액|총구매예정금액)[^\d\n]{0,16}(\d[\d,]*(?:\.\d+)?)(억|천만|백만|만|천)?원"
    for line in text.splitlines():
        if re.search(r"단가|사용료|실적", line):
            continue
        for match in re.finditer(pattern, line):
            expected = price if match[1] == "추정가격" else budget
            multiplier = {None: 1, "억": 100_000_000, "천만": 10_000_000, "백만": 1_000_000, "만": 10_000, "천": 1000}[match[3]]
            if float(match[2].replace(",", "")) * multiplier == expected:
                return True
    return False


def requires_price_premise(item: str, operation: str = "") -> bool:
    return item in PRICE_PREMISED_ITEMS or (item == "v24" and operation != "swap_meta_license")


def plant_envelope(line: str) -> dict[str, Any]:
    """Decode only seal bookkeeping at the top level, never record/provenance."""
    allowed = {"plant_id", "host_id", "family_id", "split", "target_item", "operation", "near_miss_items", "wording_hashes"}
    decoder, result, index = json.JSONDecoder(), {}, 1
    while index < len(line):
        while index < len(line) and line[index] in " \r\n\t,":
            index += 1
        if line[index] == "}":
            break
        key, index = decoder.raw_decode(line, index)
        while line[index] in " \r\n\t:":
            index += 1
        if key in allowed:
            result[key], index = decoder.raw_decode(line, index)
            continue
        depth, quoted, escaped = 0, False, False
        while index < len(line):
            char = line[index]
            if quoted:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == '"':
                    quoted = False
            elif char == '"':
                quoted = True
            elif char in "[{":
                depth += 1
            elif char in "]}":
                if depth == 0:
                    break
                depth -= 1
            elif char == "," and depth == 0:
                break
            index += 1
    return result

# Items whose truth can change when a clause of the same predicate family is
# added.  They are excluded from false-positive scoring for that plant; v24 is
# always excluded because any added clause may create a document/meta conflict.
_TRACK = ("v2", "v3", "v4", "v8")
_REGION = ("v1", "v5", "v6", "v7", "v8")
_SIZE = tuple(f"v{number}" for number in range(10, 19))
AFFECTED_ITEMS: dict[str, tuple[str, ...]] = {
    "v1": ("v1", *_SIZE),
    "v2": _TRACK, "v3": _TRACK, "v4": _TRACK,
    "v5": _REGION, "v6": _REGION, "v7": _REGION,
    "v13": _SIZE, "v14": _SIZE,
    "v19": ("v19",), "v21": ("v21",), "v22": ("v22", "v23"),
}
PLANT_ITEMS = tuple(AFFECTED_ITEMS)

# Minimum prior-performance amount stated by each official v3 evidence clause,
# read from the clause text itself.  v3 holds only where it exceeds the host budget.
V3_THRESHOLD_WON = {
    "PPS-DEV-04": 455_000_000, "PPS-DEV-05": 500_000_000,
    "PPS-DEV-042": 100_000_000, "PPS-DEV-051": 300_000_000,
    "PPS-DEV-053": 100_000_000, "PPS-DEV-059": 200_000_000,
    "PPS-DEV-062": 300_000_000, "PPS-DEV-071": 500_000_000,
}
V3_BUDGET_MARGIN = 0.8

_GA = "가나다라마바사아자차카타파하"
_CIRCLED = "①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳"
_BULLETS = "○ㅇ◦❍•·□■▪-*o"
_HEADING_RE = re.compile(r"^\s*(?P<number>\d{1,2})\s*\.\s*(?P<title>\S.{0,40})$")
_TITLE_RE = re.compile(
    r"^(입찰|견적제출|견적입찰|견적)?참가자격(및[가-힣]{1,8})?(요건|조건|사항)?([:：(（].{0,30})?$"
)
_ITEM_RES = (
    ("ga", re.compile(rf"^(?P<indent>\s*)(?P<marker>[{_GA}])\s*\.\s*\S")),
    ("paren", re.compile(r"^(?P<indent>\s*)(?P<marker>\d{1,2})\s*\)\s*\S")),
    ("circled", re.compile(rf"^(?P<indent>\s*)(?P<marker>[{_CIRCLED}])\s*\S")),
    ("bullet", re.compile(rf"^(?P<indent>\s*)(?P<marker>[{re.escape(_BULLETS)}])\s+\S")),
)
_LEADING_MARKER_RE = re.compile(
    rf"^\s*(?:[{_GA}]\s*\.|\d{{1,2}}\s*[\.\)]|[{_CIRCLED}]|[{re.escape(_BULLETS)}])\s*"
)
_TOKEN_RE = re.compile(r"\[[^\[\]\n]{1,120}\]")
_REGION_SYMBOL_RE = re.compile(r"\br(\d+)\b")
_MAX_SECTION_LINES = 120


class PlantError(ValueError):
    """The host or clause cannot be planted without guessing."""


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _new_bytes(path: pathlib.Path, body: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as handle:
        handle.write(body)


def strip_leading_marker(clause: str) -> str:
    """Drop the source notice's own list marker and table pipes, nothing else."""

    text = clause.strip().strip("|").strip()
    stripped = _LEADING_MARKER_RE.sub("", text, count=1).strip().strip("|").strip()
    if not stripped:
        raise PlantError("clause is empty after marker removal")
    return stripped


def remap_region_symbols(clause: str, host_text: str) -> str:
    """Give anonymized region symbols inside clause tokens fresh host-unique names."""

    used = [
        int(number)
        for token in _TOKEN_RE.findall(host_text)
        for number in _REGION_SYMBOL_RE.findall(token)
    ]
    next_number = max(used, default=0) + 1
    mapping: dict[str, str] = {}

    def _token(match: re.Match[str]) -> str:
        nonlocal next_number

        def _symbol(symbol: re.Match[str]) -> str:
            nonlocal next_number
            old = symbol.group(0)
            if old not in mapping:
                mapping[old] = f"r{next_number}"
                next_number += 1
            return mapping[old]

        return _REGION_SYMBOL_RE.sub(_symbol, match.group(0))

    return _TOKEN_RE.sub(_token, clause)


def find_qualification_section(text: str) -> dict[str, Any]:
    """Locate the numbered participation-qualification section and its item style."""

    lines = text.split("\n")
    for index, line in enumerate(lines):
        heading = _HEADING_RE.match(line)
        if heading is None or not _TITLE_RE.match(re.sub(r"\s+", "", heading["title"])):
            continue
        next_number = str(int(heading["number"]) + 1)
        end = None
        for cursor in range(index + 1, min(len(lines), index + 1 + _MAX_SECTION_LINES)):
            following = _HEADING_RE.match(lines[cursor])
            if following is not None and following["number"] == next_number:
                end = cursor
                break
        if end is None:
            continue
        if any(re.match(r"^(?:붙임|첨부|별지)(?:\s|\d|[:：\[])|^\[\s*(?:붙임|첨부|별지)",
                        strip_leading_marker(line))
               for line in lines[index + 1:end] if line.strip()):
            raise PlantError("qualification section runs into an attachment/form list")
        style = None
        last = None
        for cursor in range(index + 1, end):
            for name, pattern in _ITEM_RES:
                match = pattern.match(lines[cursor])
                if match is None:
                    continue
                if style is None:
                    style = (name, match["indent"])
                if (name, match["indent"]) == style:
                    last = match["marker"]
                break
        if style is None or last is None:
            continue
        return {
            "heading_line": index, "end_line": end, "style": style[0],
            "indent": style[1], "last_marker": last,
        }
    raise PlantError("no numbered participation-qualification section with list items")


def next_marker(style: str, last_marker: str) -> str:
    if style == "ga":
        position = _GA.index(last_marker)
        if position + 1 >= len(_GA):
            raise PlantError("list marker sequence exhausted")
        return f"{_GA[position + 1]}."
    if style == "circled":
        position = _CIRCLED.index(last_marker)
        if position + 1 >= len(_CIRCLED):
            raise PlantError("list marker sequence exhausted")
        return _CIRCLED[position + 1]
    if style == "paren":
        return f"{int(last_marker) + 1})"
    if style == "bullet":
        return last_marker
    raise PlantError(f"unknown list style: {style}")


def plant_clause(host: Mapping[str, Any], clause: str) -> tuple[dict[str, Any], dict[str, Any]]:
    """Append one qualification item to the host's first 공고문; all else is unchanged."""

    record = copy.deepcopy(dict(host))
    for doc_index, document in enumerate(record["docs"]):
        if document.get("type") == "공고문":
            break
    else:
        raise PlantError("host has no 공고문")
    text = document["text"]
    section = find_qualification_section(text)
    body = remap_region_symbols(strip_leading_marker(clause), text)
    planted_line = f"{section['indent']}{next_marker(section['style'], section['last_marker'])} {body}"
    lines = text.split("\n")
    end = section["end_line"]
    insert_at = end
    while insert_at - 1 > section["heading_line"] and not lines[insert_at - 1].strip():
        insert_at -= 1
    new_lines = lines[:insert_at] + [planted_line] + lines[insert_at:]
    document["text"] = "\n".join(new_lines)
    start = len("\n".join(new_lines[:insert_at])) + (1 if insert_at else 0)
    if document["text"][start:start + len(planted_line)] != planted_line:
        raise PlantError("planted line offset is inconsistent")
    provenance = {
        "doc_index": doc_index, "start": start, "end": start + len(planted_line),
        "planted_line": planted_line, "planted_body": body,
        "context_before": lines[max(section["heading_line"], insert_at - 3):insert_at],
        "context_after": lines[insert_at:insert_at + 2],
        "host_source_sha256": sha256_text(canonical_json(host)),
        "planted_source_sha256": sha256_text(canonical_json(record)),
    }
    return record, provenance


# ---------------------------------------------------------------------------
# Edits of the host's own clauses.  The organizer dev positives show the same
# operations (runs/cell_label_20260919/edit_library_v1/EDIT_LIBRARY.md): delete
# the enterprise-size restriction, rewrite its scope, or make meta disagree with
# the document.  The violating wording is the host's natural wording, so every
# host contributes a different phrasing.
# ---------------------------------------------------------------------------

_SEP = r"[·ㆍ･․‧\.,/]"
_LAW_SUFFIX = r"(?:기본법|제품|\s*범위|청|중앙회|협동조합|은행|진흥|\s*공공구매|공공구매)"
# "중소기업자간 경쟁제품" names the product scheme, not this notice's size restriction.
_NOT_SCOPE = rf"(?!{_LAW_SUFFIX}|자\s*간)"
_SME_ENUM_RE = re.compile(rf"중기업\s*{_SEP}\s*소기업")
_SME_RE = re.compile(rf"중기업|중{_SEP}\s*소기업|중소기업{_NOT_SCOPE}")
_SMALL_RE = re.compile(rf"(?<!중)(?<!중{_SEP})소기업")
_SIZE_ANY_RE = re.compile(rf"{_SME_RE.pattern}|{_SMALL_RE.pattern}|소상공인")
_BLOCK_START_RE = re.compile(
    rf"^\s*(?:[{_GA}]\s*\.|\d{{1,2}}\s*[\.\)]|\(\d{{1,2}}\)|[{_CIRCLED}]|[{re.escape(_BULLETS)}◇◆▶※])"
)
_DELETE_GUARD_RE = re.compile(r"소재|주소|영업소|본점|실적|직접생산")
# A size mention restricts bidders only where it is worded as a requirement; a form field
# ("대기업( ), 중소기업( )") or a payment-scheme sentence is not a restriction.
_SIZE_GATE_RE = re.compile(r"소지한|소지하|로\s*제한|제한\s*입찰|제한\s*경쟁|에\s*한하|에\s*한함|이어야|여야\s*(?:합|함|하)|자격을\s*(?:구비|갖)")
# The SME portal check enforces the size certificate even when the line names no enterprise size.
_SIZE_PORTAL_RE = re.compile(r"smpp\.go\.kr|공공구매\s*종합\s*정보망|공공구매\s*정보망")
_EMBEDDED_ITEM_RE = re.compile(rf"\S\s+[{_GA}]\s*\.\s+\S")
_NOTE_START_RE = re.compile(r"^\s*[※\*＊]")
_FRAGMENT_START_RE = re.compile(r"^\s*(?:\d{5,}|[\)\]」』])")
# A validity note on "the certificate" belongs to the size clause even when it names no size.
_CERTIFICATE_NOTE_RE = re.compile(r"확인서[^\n]{0,80}(?:유효\s*기간|발급)")
# Protect legal/institutional names independently of the scope replacement regexes.
_SIZE_NAME_RE = re.compile(
    r"(?:중소기업|소기업)(?:\s*제품)?\s*구매촉진[^\n「」<>]{0,70}?법률"
    r"|중소기업(?:기본법|청|중앙회|협동조합|은행|진흥[^\s「」<>]*)"
)
_SIZE_PROCEDURE_RE = re.compile(
    r"(?:입찰|계약|견적)(?:방법|방식|구분|유형)[^\n]{0,100}중소기업자?간경쟁"
)


def _is_size_line(line: str) -> bool:
    if size_scope(line) or _SIZE_PORTAL_RE.search(line):
        return True
    return bool(_CERTIFICATE_NOTE_RE.search(line)) and "직접생산" not in line
# One physical line packing several numbered documents: deleting it would drop unrelated items.
_PACKED_ITEM_RE = re.compile(rf"[{_CIRCLED}]")
_EXCEPTION_RE = re.compile(r"제2조의3|우선조달.{0,12}예외")
# Fields of the 28 designated services in the supplied catalog; the wording of a task rarely
# repeats the catalog name, so the product class of such notices cannot be closed lexically.
_DESIGNATED_SERVICE_RE = re.compile(
    r"소프트웨어|S/?W\b|정보시스템|시스템\s*(?:개발|구축|유지)|홈페이지|인터넷\s*(?:지원|개발|서비스)"
    r"|데이터\s*(?:처리|분석|구축)|빅데이터|공간정보|정보인프라"
    r"|행사\s*(?:대행|기획|운영|용역)|축제|회의\s*(?:기획|대행|운영)|전시|부스|홍보관|디자인|동영상|영상\s*제작"
    r"|청소|(?:시설물?\s*)?경비\s*(?:용역|업무|서비스)|승강기|우편\s*발송|통학|통근|운송\s*(?:서비스|용역)|전세버스"
    r"|위탁\s*운영|운영\s*위탁|유수율|지질"
)
# A bid run on behalf of a private subsidy recipient may fall outside the public-buyer duty.
_PRIVATE_BUYER_RE = re.compile(r"보조사업자|보조금|민간\s*보조|간접\s*보조")
_SECTION_HEADING_RE = re.compile(r"^\s*\d{1,2}\s*\.\s*\S")
_RENUMBER_RES = {
    "ga": re.compile(rf"^(?P<indent>\s*)(?P<marker>[{_GA}])(?P<rest>\s*\..*)$", re.S),
    "paren": re.compile(r"^(?P<indent>\s*)(?P<marker>\d{1,2})(?P<rest>\s*\).*)$", re.S),
    "circled": re.compile(rf"^(?P<indent>\s*)(?P<marker>[{_CIRCLED}])(?P<rest>.*)$", re.S),
}
META_PRICE_FACTORS = (1.03, 1.05, 1.08)
_BAND_EDGES_WON = (
    50_000_000, 100_000_000, LOWEST_REGION_CEILING_WON, NOTICE_THRESHOLD_WON,
    330_000_000, HIGHEST_REGION_CEILING_WON,
)


def size_scope(line: str) -> str | None:
    """'sme', 'small', 'mixed', 'neutral' (소상공인 only) or None for one physical line."""

    line = _SIZE_NAME_RE.sub("", line)
    if not _SIZE_ANY_RE.search(line):
        return None
    has_sme = bool(_SME_RE.search(line))
    remainder = _SME_RE.sub("", _SME_ENUM_RE.sub("", line))
    has_small = bool(_SMALL_RE.search(remainder))
    if has_sme and has_small:
        return "mixed"
    return "sme" if has_sme else "small" if has_small else "neutral"


def host_size_scope(record: Mapping[str, Any]) -> str | None:
    """Uniform enterprise-size scope that the record states as a bidder requirement, else None."""

    lines = [line for document in record["docs"] for line in document["text"].split("\n")]
    scopes = {size_scope(line) for line in lines} - {None, "neutral"}
    if len(scopes) != 1 or "mixed" in scopes or not has_size_gate(record):
        return None
    return next(iter(scopes))


def has_size_gate(record: Mapping[str, Any]) -> bool:
    """True when a 공고문 list item both names an enterprise size and words it as a requirement."""

    try:
        _, document = _notice_document(record)
    except PlantError:
        return False
    lines = document["text"].split("\n")
    for start, end in _blocks(lines):
        block = lines[start:end]
        if any(size_scope(line) in {"sme", "small", "mixed"} for line in block) and _SIZE_GATE_RE.search(" ".join(block)):
            return True
    return False


def _notice_document(record: Mapping[str, Any]) -> tuple[int, dict[str, Any]]:
    for doc_index, document in enumerate(record["docs"]):
        if document.get("type") == "공고문":
            return doc_index, document
    raise PlantError("host has no 공고문")


def _blocks(lines: Sequence[str]) -> list[tuple[int, int]]:
    """List-item blocks: a marker line plus its unmarked continuation lines."""

    blocks: list[tuple[int, int]] = []
    start: int | None = None
    for index, line in enumerate(lines):
        if not line.strip() or _BLOCK_START_RE.match(line):
            if start is not None:
                blocks.append((start, index))
            start = index if line.strip() else None
    if start is not None:
        blocks.append((start, len(lines)))
    return blocks


def _shift_marker(style: str, marker: str) -> str:
    if style == "ga":
        return _GA[_GA.index(marker) - 1]
    if style == "circled":
        return _CIRCLED[_CIRCLED.index(marker) - 1]
    return str(int(marker) - 1)


def _renumber_after(lines: list[str], index: int, style: str, indent: str, removed_marker: str) -> None:
    """Close the gap a deleted list item leaves in the following sibling markers."""

    pattern = _RENUMBER_RES[style]
    expected = removed_marker
    for cursor in range(index, len(lines)):
        line = lines[cursor]
        if _SECTION_HEADING_RE.match(line):
            return
        if style != "ga" and _RENUMBER_RES["ga"].match(line):
            return
        match = pattern.match(line)
        if match is None or match["indent"] != indent:
            continue
        if match["marker"] in {_GA[0], _CIRCLED[0], "1"}:
            return
        shifted = _shift_marker(style, match["marker"])
        # A host whose own list already skips markers would show a wider gap after the deletion.
        if shifted != expected:
            raise PlantError("host list numbering is not consecutive")
        lines[cursor] = f"{indent}{shifted}{match['rest']}"
        expected = match["marker"]


def delete_size_restriction(host: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Remove every enterprise-size restriction item from the 공고문 (organizer operation O2)."""

    if not has_size_gate(host):
        raise PlantError("host states no enterprise-size restriction")
    record, provenance = _delete_clause_blocks(
        host, doomed_line=_is_size_line, guard=_DELETE_GUARD_RE,
        operation="delete_size_restriction", what="enterprise-size",
    )
    if _SIZE_PROCEDURE_RE.search(re.sub(r"[^\S\n]+", "", _all_text(record))):
        raise PlantError("enterprise-size competition procedure survives the deletion")
    return record, provenance


_LARGE_ENTERPRISE_RE = re.compile(r"대기업|중견기업|상호출자제한|중소\s*소프트웨어사업자의\s*사업\s*참여")
# Only the software-business rule counts: 상호출자제한 also appears in telecom-construction and
# joint-venture affiliate clauses of notices that buy no software at all (PPS-D-019131, -012478).
_LARGE_ENTERPRISE_GATE_RE = re.compile(
    r"중소\s*소프트웨어사업자의\s*사업\s*참여|소프트웨어\s*(?:산업\s*)?진흥법[^\n]{0,60}(?:대기업|참여\s*제한)"
    r"|(?:대기업|중견기업)[^\n]{0,80}(?:참여|입찰)[^\n]{0,20}(?:없|제한|불가)"
)
# The task itself must show software work once the declaration is gone (PPS-D-015223 bought sirens).
_SOFTWARE_OBJECT_RE = re.compile(
    r"소프트웨어|S/?W|정보시스템|시스템\s*(?:개발|구축|유지|운영|고도화)|유지\s*관리|유지\s*보수|홈페이지|플랫폼|앱\s*개발|전산"
)
_OTHER_QUALIFICATION_RE = re.compile(r"소재|주소|영업소|본점|실적")


def _is_direct_production_line(line: str) -> bool:
    return bool(re.search(r"직접\s*생산|직생", line))


def delete_direct_production(host: Mapping[str, Any], catalog: Any) -> tuple[dict[str, Any], dict[str, Any]]:
    """Remove the pre-bid direct-production requirement from a designated-product notice (v10).

    The notice must still identify a designated product afterwards (code or name outside the
    deleted clause); otherwise nothing in the text says the product is a competitive one.
    """

    try:
        from tools.independent_gold import catalog_facts
    except ModuleNotFoundError:  # Direct script invocation.
        import catalog_facts  # type: ignore[no-redef]
    record, provenance = _delete_clause_blocks(
        host, doomed_line=_is_direct_production_line, guard=_OTHER_QUALIFICATION_RE,
        operation="delete_direct_production", what="direct-production",
    )
    if catalog_facts.resolve_record(record, catalog)["summary"]["decision"] != "applicable":
        raise PlantError("the designated product is identified only inside the deleted clause")
    return record, provenance


def delete_large_enterprise_limit(host: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Remove the large-enterprise participation limit from a software-business notice (v20)."""

    # PPS-D-013854: a form field "대기업( ) 중소기업( )" is not a participation limit.
    if not _LARGE_ENTERPRISE_GATE_RE.search(_all_text(host)):
        raise PlantError("host states no large-enterprise restriction")
    # Judge the task by its title and object lines only: the limit clause and a bidder's
    # 소프트웨어사업자 registration both say "software" without the task being software work.
    _, document = _notice_document(host)
    lines = [line for line in document["text"].split("\n") if line.strip()]
    title = "\n".join(lines[:3] + [line for line in lines if _OBJECT_LINE_RE.search(line)])
    title = "\n".join(line for line in title.split("\n") if not _LARGE_ENTERPRISE_RE.search(line) and "사업자" not in line)
    if not _SOFTWARE_OBJECT_RE.search(title):
        raise PlantError("the task does not show software work by itself")
    return _delete_clause_blocks(
        host, doomed_line=lambda line: bool(_LARGE_ENTERPRISE_RE.search(line)),
        guard=_OTHER_QUALIFICATION_RE, operation="delete_large_enterprise_limit",
        what="large-enterprise",
    )


def _delete_clause_blocks(
    host: Mapping[str, Any], *, doomed_line: Any, guard: re.Pattern[str], operation: str, what: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Delete every 공고문 list item that states the restriction; fail closed unless the cut is clean."""

    record = copy.deepcopy(dict(host))
    doc_index, document = _notice_document(record)
    lines = document["text"].split("\n")
    doomed = [
        (start, end) for start, end in _blocks(lines)
        if any(doomed_line(line) for line in lines[start:end])
    ]
    if not doomed:
        raise PlantError(f"host states no {what} restriction")
    blocks = _blocks(lines)
    for position, block in enumerate(blocks[:-1]):
        follower = blocks[position + 1]
        # "※ 마감일 전일까지 발급된 것으로 유효기간 내" names nothing itself: it hangs on the clause above.
        if block in doomed and follower not in doomed and _NOTE_START_RE.match(lines[follower[0]]):
            raise PlantError("a note that depends on the restriction clause would be orphaned")
    removed: list[str] = []
    for start, end in reversed(doomed):
        block = lines[start:end]
        body = "\n".join(block)
        if (guard.search(body) or len(_PACKED_ITEM_RE.findall(body)) >= 3
                or any(_EMBEDDED_ITEM_RE.search(line) for line in block)):
            raise PlantError(f"{what} restriction shares a list item with another qualification")
        # PPS-D-010966: a line starting "4010171601)로 …" is the tail of a clause whose head the
        # extraction put elsewhere; cutting it leaves "…(세부품명번호 10자리:" dangling.
        if any(_FRAGMENT_START_RE.match(line) for line in block):
            raise PlantError(f"{what} restriction is split by the text extraction")
        following = next((line for line in lines[end:] if line.strip()), "")
        if following and not _BLOCK_START_RE.match(following) and not _SECTION_HEADING_RE.match(following):
            raise PlantError("the restriction clause continues past a blank line")
        removed[:0] = block
        del lines[start:end]
        for style, pattern in _RENUMBER_RES.items():
            match = pattern.match(block[0])
            if match is not None:
                _renumber_after(lines, start, style, match["indent"], match["marker"])
                break
    document["text"] = "\n".join(lines)
    if any(doomed_line(line) for other in record["docs"] for line in other["text"].split("\n")):
        raise PlantError(f"an {what} mention survives the deletion")
    return record, {
        "operation": operation, "doc_index": doc_index,
        "removed_lines": removed,
        "host_source_sha256": sha256_text(canonical_json(host)),
        "planted_source_sha256": sha256_text(canonical_json(record)),
    }


def rewrite_size_scope(
    host: Mapping[str, Any], direction: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Narrow 중소기업→소기업 or broaden 소기업→중소기업 everywhere (organizer operation O3)."""

    if direction not in {"narrow", "broaden"}:
        raise PlantError(f"unknown scope rewrite: {direction}")
    if host_size_scope(host) != ("sme" if direction == "narrow" else "small"):
        raise PlantError("host does not state one uniform scope to rewrite")
    if re.search(r"(?:소|중)\s*\n\s*(?:소\s*)?기업", _all_text(host)):
        raise PlantError("enterprise-size token is split across lines")
    if direction == "broaden":
        if (host.get("dropped_doc_counts") or re.search(
                r"혼합|품목|단가|내역서|구매목록|규격서|별첨|붙임|외\s*\d+\s*종|제2조의2|시행령[^\n]{0,20}제8조",
                _all_text(host))):
            raise PlantError("broadening requires a closed non-mixed object and size provision")
        for document in host["docs"]:
            lines = document["text"].split("\n")
            for start, end in _blocks(lines):
                block = _SIZE_NAME_RE.sub("", " ".join(lines[start:end]))
                subject = re.search(r"소상공인(?:으로서|인\s|에\s*한|만)", block)
                if subject and not _SMALL_RE.search(block[:subject.start()]):
                    raise PlantError("a micro-only subject or note would survive broadening")
    record = copy.deepcopy(dict(host))
    changed: list[dict[str, str]] = []
    for document in record["docs"]:
        new_lines = []
        for line in document["text"].split("\n"):
            names = []

            def protect(match: re.Match[str]) -> str:
                names.append(match.group(0))
                return f"\x00{len(names) - 1}\x00"

            editable = _SIZE_NAME_RE.sub(protect, line)
            if direction == "narrow":
                edited = _SME_ENUM_RE.sub("소기업", editable)
                edited = re.sub(rf"중{_SEP}\s*소기업", "소기업", edited)
                edited = re.sub(rf"중소기업{_NOT_SCOPE}", "소기업", edited)
                edited = re.sub(rf"중기업\s*{_SEP}?\s*", "", edited)
            else:
                edited = _SMALL_RE.sub("중소기업", editable)
            for index, name in enumerate(names):
                edited = edited.replace(f"\x00{index}\x00", name)
            if direction == "broaden":
                # Article 2(2) defines the small/medium distinction, not the full SME scope.
                edited = re.sub(r"(중소기업기본법[」>]?\s*제\s*2\s*조)\s*제\s*2\s*항", r"\1", edited)
                edited = re.sub(r"중소기업\s*(?:[·ㆍ‧․]|또는|및)\s*소상공인(?=\s*확인서)", "중소기업", edited)
                if re.search(r"제\s*2\s*항|소기업" + _NOT_SCOPE, _SIZE_NAME_RE.sub("", edited).replace("중소기업", "")):
                    raise PlantError("a narrow size provision or note survives broadening")
            if edited != line:
                changed.append({"from": line, "to": edited})
            new_lines.append(edited)
        document["text"] = "\n".join(new_lines)
        if direction == "broaden":
            def certificate(match):
                changed.append({"from": match[0], "to": "중소기업확인서"})
                return "중소기업확인서"
            document["text"] = re.sub(
                r"중소기업\s*(?:[·ㆍ‧․]|또는|및)\s*소\s*상\s*공\s*인\s*확\s*인\s*서",
                certificate, document["text"])
    meta_changes = {}
    if direction == "broaden":
        for field, value in record["meta"].items():
            if not isinstance(value, str) or size_scope(value) not in {"small", "mixed"}:
                continue
            names = []
            edited = _SMALL_RE.sub("중소기업", _SIZE_NAME_RE.sub(protect, value))
            for index, name in enumerate(names):
                edited = edited.replace(f"\x00{index}\x00", name)
            edited = re.sub(r"(중소기업기본법[」>]?\s*제\s*2\s*조)\s*제\s*2\s*항", r"\1", edited)
            edited = re.sub(r"중소기업\s*(?:[·ㆍ‧․]|또는|및)\s*소상공인(?=\s*확인서)", "중소기업", edited)
            if edited != value:
                record["meta"][field] = edited
                meta_changes[field] = {"from": value, "to": edited}
    if not changed or host_size_scope(record) != ("small" if direction == "narrow" else "sme"):
        raise PlantError("scope rewrite left an inconsistent enterprise-size statement")
    return record, {
        "operation": f"rewrite_size_scope:{direction}", "changed_lines": changed, "meta_changes": meta_changes,
        "host_source_sha256": sha256_text(canonical_json(host)),
        "planted_source_sha256": sha256_text(canonical_json(record)),
    }


def _band(price: int) -> int:
    return sum(price >= edge for edge in _BAND_EDGES_WON)


def diverge_meta_price(host: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Make meta 입찰추정가격 disagree with the price the 공고문 states (organizer operation O6)."""

    if not price_premise_is_valid(host):
        raise PlantError("price premise is placeholder or an unresolved unit amount")
    price = host["meta"].get("입찰추정가격")
    if type(price) is not int or price <= 0:
        raise PlantError("host meta price is not a positive integer")
    doc_index, document = _notice_document(host)
    stated = f"{price:,}"
    text = document["text"]
    position = text.find(stated)
    # The amount must be stated as the estimated price, not as a budget that happens to match.
    context_start = text.rfind("\n", 0, max(0, text.rfind("\n", 0, position))) + 1
    if position < 0 or "추정가격" not in re.sub(r"\s+", "", text[context_start:position]):
        raise PlantError("공고문 does not state the meta estimated price verbatim")
    order = sorted(META_PRICE_FACTORS, key=lambda factor: _rank("meta-price", host["id"], str(factor)))
    for factor in order:
        new_price = round(price * factor)
        if _band(new_price) == _band(price) and f"{new_price:,}" not in _all_text(host):
            break
    else:
        raise PlantError("no price factor keeps the legal price band")
    record = copy.deepcopy(dict(host))
    record["meta"]["입찰추정가격"] = new_price
    budget = record["meta"].get("배정예산금액")
    if type(budget) is int:
        record["meta"]["배정예산금액"] = round(budget * factor)
    start = document["text"].index(stated)
    line_start = document["text"].rfind("\n", 0, start) + 1
    line_end = document["text"].find("\n", start)
    return record, {
        "operation": "diverge_meta_price", "doc_index": doc_index, "factor": factor,
        "document_price": price, "meta_price": new_price,
        "evidence_line": document["text"][line_start:line_end if line_end >= 0 else None],
        "host_source_sha256": sha256_text(canonical_json(host)),
        "planted_source_sha256": sha256_text(canonical_json(record)),
    }


_OBJECT_LINE_RE = re.compile(r"건\s*명|용\s*역\s*명|사\s*업\s*명|공\s*고\s*명|품\s*명|과\s*업\s*명|입찰에\s*부치는")
_OBJECT_HEAD_LINES = 12


def _object_text(record: Mapping[str, Any]) -> str:
    """Where a notice names what it buys: its opening lines and its title/object lines.

    Words such as 홈페이지 or 전시 occur in almost every notice body; only here do they say
    that the contract itself is a designated service.
    """

    try:
        _, document = _notice_document(record)
    except PlantError:
        return ""
    lines = [line for line in document["text"].split("\n") if line.strip()]
    return "\n".join(lines[:_OBJECT_HEAD_LINES] + [line for line in lines if _OBJECT_LINE_RE.search(line)])


def product_class(record: Mapping[str, Any], catalog: Any) -> str | None:
    """'competitive' or 'general' only where supplied text and catalog agree; else None."""

    try:
        from tools.independent_gold import catalog_facts
    except ModuleNotFoundError:  # Direct script invocation.
        import catalog_facts  # type: ignore[no-redef]
    resolved = catalog_facts.resolve_record(record, catalog)
    decision = resolved["summary"]["decision"]
    text = _all_text(record)
    if decision == "applicable" and "직접생산" in text:
        return "competitive"
    # A registered code whose special condition is undecided (간장: 혼합간장만 지정), or a task in a
    # designated service field (SW 개발, 행사대행 …), leaves the product class open.
    if (any(code.get("catalog_registered") for code in resolved["codes"])
            or _DESIGNATED_SERVICE_RE.search(_object_text(record))):
        return None
    if decision != "applicable" and "직접생산" not in text and "경쟁제품" not in text:
        # A designated product named without its code leaves the product class open.
        names = getattr(catalog, "_cell_plant_names", None)
        if names is None:
            names = tuple(sorted({str(row["세부품명"]).strip() for row in catalog.rows} - {""}, key=len, reverse=True))
            names = tuple(name for name in names if len(name) >= 3)
            catalog._cell_plant_names = names
        if not any(name in text for name in names):
            return "general"
    return None


def size_edit_target(operation: str, product: str | None, price: int) -> str | None:
    """Item made positive by a size-clause edit under the host's product class and price band."""

    if product == "competitive":
        return {"delete": "v11", "narrow": "v13"}.get(operation)
    if product != "general":
        return None
    if operation == "delete":
        return "v18" if price < 100_000_000 else "v16" if price < NOTICE_THRESHOLD_WON else None
    if operation == "narrow":
        return "v15" if 100_000_000 <= price < NOTICE_THRESHOLD_WON else None
    if operation == "broaden":
        return "v17" if price < 100_000_000 else None
    return None


PROVINCES = (
    "서울특별시", "부산광역시", "대구광역시", "인천광역시", "광주광역시", "대전광역시", "울산광역시",
    "세종특별자치시", "경기도", "강원특별자치도", "강원도", "충청북도", "충청남도", "전북특별자치도",
    "전라북도", "전라남도", "경상북도", "경상남도", "제주특별자치도", "제주도",
)
_PROVINCE_RE = re.compile("|".join(PROVINCES))
_LOCATION_RE = re.compile(r"소재지|영업소|본점")
_SHARE_RE = re.compile(r"(최소\s*지분[율률]?[^\d\n%]{0,20}?)(\d{1,2}(?:\.\d)?)(\s*%)")
VIOLATING_SHARES = ("1", "2", "3")  # below every lawful floor, including the adjusted 4% one
_PLEDGE_RE = re.compile(r"(제조사|공급사|제조업체|공급업체|기술지원사)[^\n]{0,60}(확약서|협약서)")
_POST_AWARD_TIMING_RE = re.compile(r"계약\s*체결\s*시\s*(?:까지|에)?|계약\s*시\s*(?:까지|에)?")
_WINNER_RE = re.compile(r"낙찰자로\s*결정된\s*업체는|낙찰자는")
_PRE_BID_RE = re.compile(r"입찰")
_AMOUNT_RE = re.compile(r"\d{1,3}(?:,\d{3})+")
_KOREAN_AMOUNT_RE = re.compile(r"[일이삼사오육칠팔구십백천]+\s*[만억]\s*[일이삼사오육칠팔구십백천만\s]*원")
_TITLE_BAND_TAG_RE = re.compile(r"[억만]\s*원?\s*(?:미만|이상|이하|초과)\s*\)")
_ARITHMETIC_RE = re.compile(r"[×xX\*]\s*\d|\d\s*[×xX\*]|단가|=\s*금?\s*\d")
SHIFT_BANDS = {
    "v15": (110_000_000, 220_000_000),
    "v14": (250_000_000, 450_000_000),
    "v5": (550_000_000, 900_000_000),
}


def _qualification_lines(record: Mapping[str, Any]) -> tuple[int, dict[str, Any], list[str], dict[str, Any]]:
    doc_index, document = _notice_document(record)
    lines = document["text"].split("\n")
    return doc_index, document, lines, find_qualification_section(document["text"])


def rewrite_min_share(host: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Lower the stated joint-venture minimum share below every lawful floor (v21)."""

    text = _all_text(host)
    values = {match.group(2) for match in _SHARE_RE.finditer(text)}
    if len(values) != 1 or float(next(iter(values))) < 5:
        raise PlantError("host does not state one lawful minimum share")
    mode = str(host["meta"].get("공동도급구성방식"))
    if "공동이행" not in mode and not ("공동이행" in text and "분담이행" not in text and mode == "None"):
        raise PlantError("joint performance is not established")
    new_value = sorted(VIOLATING_SHARES, key=lambda value: _rank("share", host["id"], value))[0]
    record = copy.deepcopy(dict(host))
    changed: list[dict[str, str]] = []
    for document in record["docs"]:
        new_lines = []
        for line in document["text"].split("\n"):
            edited = _SHARE_RE.sub(lambda match: f"{match.group(1)}{new_value}{match.group(3)}", line)
            if edited != line:
                changed.append({"from": line, "to": edited})
            new_lines.append(edited)
        document["text"] = "\n".join(new_lines)
    return record, {
        "operation": "rewrite_min_share", "from_percent": next(iter(values)),
        "to_percent": new_value, "changed_lines": changed,
        "host_source_sha256": sha256_text(canonical_json(host)),
        "planted_source_sha256": sha256_text(canonical_json(record)),
    }


def single_province_restriction(record: Mapping[str, Any]) -> tuple[str, list[int]]:
    """The one 광역 that the qualification section's location clauses name, with their line numbers."""

    _, _, lines, section = _qualification_lines(record)
    hits = [
        index for index in range(section["heading_line"] + 1, section["end_line"])
        if _LOCATION_RE.search(lines[index]) and _PROVINCE_RE.search(lines[index])
    ]
    provinces = {name for index in hits for name in _PROVINCE_RE.findall(lines[index])}
    if len(provinces) != 1 or any("단위=기초" in lines[index] for index in hits):
        raise PlantError("qualification does not restrict bidders to exactly one 광역")
    return next(iter(provinces)), hits


def narrow_region(host: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Turn a lawful 광역 location restriction into a 기초-level one below every ceiling (v6)."""

    price = host["meta"].get("입찰추정가격")
    if type(price) is not int or price >= LOWEST_REGION_CEILING_WON:
        raise PlantError("price is not below every regional ceiling")
    if host["meta"].get("낙찰방법") == SMALL_QUOTE_AWARD:
        raise PlantError("small-quote procedure may lawfully restrict to a 기초 region")
    province, hits = single_province_restriction(host)
    record = copy.deepcopy(dict(host))
    doc_index, document = _notice_document(record)
    lines = document["text"].split("\n")
    token = remap_region_symbols(f"[지역:r1|단위=기초|광역={province}]", document["text"])
    changed = []
    for index in hits:
        edited = lines[index].replace(province, f"{province} {token}", 1)
        changed.append({"from": lines[index], "to": edited})
        lines[index] = edited
    document["text"] = "\n".join(lines)
    if record["meta"].get("제한지역코드목록") == province:
        record["meta"]["제한지역코드목록"] = f"[등록지역:r1|단위=기초|광역={province}]"
    return record, {
        "operation": "narrow_region", "doc_index": doc_index, "province": province,
        "changed_lines": changed,
        "host_source_sha256": sha256_text(canonical_json(host)),
        "planted_source_sha256": sha256_text(canonical_json(record)),
    }


def advance_pledge_timing(host: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Move a third-party pledge from contract signing to before the bid deadline (v19)."""

    record = copy.deepcopy(dict(host))
    doc_index, document = _notice_document(record)
    lines = document["text"].split("\n")
    changed = []
    for index, line in enumerate(lines):
        if not _PLEDGE_RE.search(line) or not _POST_AWARD_TIMING_RE.search(line) or _PRE_BID_RE.search(line):
            continue
        edited = _POST_AWARD_TIMING_RE.sub("입찰서 제출 마감일 전일까지 ", line)
        edited = re.sub(r" {2,}", " ", _WINNER_RE.sub("입찰참가자는", edited))
        changed.append({"from": line, "to": edited})
        lines[index] = edited
    if len(changed) != 1:
        raise PlantError("host does not state exactly one post-award third-party pledge")
    if any(_PLEDGE_RE.search(line) and (_POST_AWARD_TIMING_RE.search(line) or _WINNER_RE.search(line))
           for line in lines):
        raise PlantError("a post-award pledge statement survives the rewrite")
    document["text"] = "\n".join(lines)
    return record, {
        "operation": "advance_pledge_timing", "doc_index": doc_index, "changed_lines": changed,
        "host_source_sha256": sha256_text(canonical_json(host)),
        "planted_source_sha256": sha256_text(canonical_json(record)),
    }


def _shift_money_text(text: str, values: Mapping[int, int], roles: Mapping[str, int]) -> tuple[str, int]:
    """Resolve units before substitution; unrecognized contract amounts fail closed."""
    if re.search(r"\d[^\S\n]*(?:억|천만|백만|만)[^\S\n]*원|천\s+원", text):
        raise PlantError("scaled amount expression is not a supported won/thousand-won field")
    units = set(re.findall(r"단위\s*[:：=]\s*(천원|원)", text))
    if len(units) > 1:
        raise PlantError("multiple table amount units are unresolved")
    default_unit = next(iter(units), None)
    pattern = re.compile(r"(?<![\d.,])(?P<n>\d[\d,]*(?:\.\d+)?)(?P<gap>[^\S\n]*)(?P<unit>천원|원|억원|만원)?(?![\d,])")
    role_pattern = re.compile(r"추정가격|배정예산(?:금액)?|사업예산|기초금액|총사업비|총사업금액|부가가치세|부가세|VAT", re.I)
    changed, output = 0, []
    for line in text.split("\n"):
        def replace(match):
            nonlocal changed
            token, unit = match["n"], match["unit"]
            if not unit and not ("," in token or (default_unit and Decimal(token) >= 100)
                                 or (role_pattern.search(line) and Decimal(token) >= 100_000)):
                return match[0]
            if unit in {"억원", "만원"}:
                raise PlantError("scaled amount expression is not a supported won/thousand-won field")
            multiplier = 1000 if (unit or default_unit) == "천원" else 1
            amount = Decimal(token.replace(",", "")) * multiplier
            if amount != int(amount) or int(amount) not in values:
                raise PlantError("the notice states amounts other than this contract's price, tax and budget")
            prefix_roles = list(role_pattern.finditer(line[:match.start()]))
            if prefix_roles:
                role = prefix_roles[-1][0]
                expected = roles["price"] if role == "추정가격" else roles["tax"] if role.lower() in {"부가가치세", "부가세", "vat"} else roles["budget"]
                if amount != expected:
                    raise PlantError("host already has a document/meta amount-role mismatch")
            changed += 1
            rendered = format(Decimal(values[int(amount)]) / multiplier, ",f")
            if "." in rendered:
                rendered = rendered.rstrip("0").rstrip(".")
            return rendered + match["gap"] + (unit or "")
        output.append(pattern.sub(replace, line))
    return "\n".join(output), changed


def shift_price(host: Mapping[str, Any], target_item: str, *, exact_price: int | None = None,
                allow_title_tag: bool = False) -> tuple[dict[str, Any], dict[str, Any]]:
    """Move every stated amount and meta price into the band where the host's own clause violates."""

    if not price_premise_is_valid(host):
        raise PlantError("price premise is placeholder or an unresolved unit amount")
    low, high = SHIFT_BANDS[target_item] if exact_price is None else (exact_price, exact_price)
    meta = host["meta"]
    price, budget = meta.get("입찰추정가격"), meta.get("배정예산금액")
    if type(price) is not int or type(budget) is not int or price <= 0 or (exact_price is None and price >= low):
        raise PlantError("host price is not an integer below the target band")
    text = _all_text(host)
    if _KOREAN_AMOUNT_RE.search(text) or (not allow_title_tag and _TITLE_BAND_TAG_RE.search(text)):
        raise PlantError("amounts in words or a title band tag cannot be shifted consistently")
    if meta.get("낙찰방법") == NEGOTIATED_AWARD and "설명" in text:
        raise PlantError("a price shift could change the briefing period rule")
    if meta.get("낙찰방법") == SMALL_QUOTE_AWARD or meta.get("계약방법") == "수의계약":
        raise PlantError("a small-quote procedure cannot carry a price above its own ceiling")
    if any(f"{price:,}" in line and _ARITHMETIC_RE.search(line) for line in text.split("\n")):
        raise PlantError("the price is derived from a unit-price calculation that a shift would break")
    tax = (price + 5) // 10
    if budget != price + tax:
        raise PlantError("budget must equal estimated price plus rounded ten-percent VAT")
    fraction = int(_rank("shift", target_item, host["id"])[:8], 16) / 0xFFFFFFFF
    new_price = exact_price if exact_price is not None else int((low + fraction * (high - low)) // 1000 * 1000)
    new_tax = (new_price + 5) // 10
    new_budget = new_price + new_tax
    values = {price: new_price, tax: new_tax, budget: new_budget}
    record = copy.deepcopy(dict(host))
    changed = 0
    for document in record["docs"]:
        document["text"], count = _shift_money_text(document["text"], values, {"price": price, "tax": tax, "budget": budget})
        changed += count
    for field, value in record["meta"].items():
        if isinstance(value, str):
            record["meta"][field], count = _shift_money_text(value, values, {"price": price, "tax": tax, "budget": budget})
            changed += count
    if not changed:
        raise PlantError("no explicit monetary occurrence to shift")
    record["meta"]["입찰추정가격"] = new_price
    record["meta"]["배정예산금액"] = new_budget
    return record, {
        "operation": f"shift_price:{target_item}", "from_price": price, "to_price": new_price,
        "amount_occurrences": changed, "vat_won": new_tax, "budget_won": new_budget,
        "host_source_sha256": sha256_text(canonical_json(host)),
        "planted_source_sha256": sha256_text(canonical_json(record)),
    }


_WEEKDAYS = "월화수목금토일"
LOCAL_LAW = "지방계약법"
BRIEFING_OFFSETS_DAYS = (2, 3, 4)  # rule (A) needs the notice posted 7 days before the briefing
BRIEFING_TEMPLATES = (
    "제안요청서 설명회: 개최함 — 일시 {date} 14:00, 장소 발주기관 회의실",
    "사업설명회를 {date} 10:00에 발주기관 회의실에서 실시합니다.",
    "제안요청 설명회 개최: {date} 15:00 / 발주기관 대회의실(참석은 선택사항임)",
    "제안요청서 설명 일정은 {date} 11:00이며, 발주기관 회의실에서 진행합니다.",
    "사업설명회 안내 — {date} 16:00, 발주기관 회의실에서 개최 예정(자율 참석)",
)


def insert_briefing(host: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """State a briefing held too soon after the posting date of a local negotiated bid (v23)."""

    import datetime

    meta = host["meta"]
    if meta.get("적용계약법") != LOCAL_LAW or meta.get("낙찰방법") != NEGOTIATED_AWARD:
        raise PlantError("v23 applies to local negotiated contracts only")
    if "설명회" in _all_text(host):
        raise PlantError("host already states a briefing")
    try:
        posted = datetime.datetime.strptime(str(meta.get("공고게시일자")), "%Y%m%d").date()
        opening = datetime.datetime.strptime(str(meta.get("개찰예정일자")), "%Y%m%d").date()
    except ValueError as exc:
        raise PlantError("posting or opening date is not a date") from exc
    offsets = sorted(BRIEFING_OFFSETS_DAYS, key=lambda days: _rank("briefing", host["id"], str(days)))
    for days in offsets:
        date = posted + datetime.timedelta(days=days)
        if date.weekday() < 5 and date < opening - datetime.timedelta(days=2):
            break
    else:
        raise PlantError("no weekday between the posting and the opening fits a briefing")
    stated = f"{date.year}. {date.month}. {date.day}.({_WEEKDAYS[date.weekday()]})"
    template = sorted(BRIEFING_TEMPLATES, key=lambda text: _rank("briefing-text", host["id"], text))[0]
    record, provenance = plant_clause(host, template.format(date=stated))
    provenance.update({"operation": "insert_briefing", "posted": str(posted), "briefing": str(date)})
    return record, provenance


_TITLE_TAG_RE = re.compile(r"\((?P<method>[^()·ㆍ\n]{2,12})[·ㆍ]\s*(?P<band>\d+\s*(?:천만|억)\s*원?\s*미만)\)")
# The nearest band whose ceiling the price has already reached, largest first.
_LOWER_TAGS = (
    (1_000_000_000, "10억원미만"), (500_000_000, "5억원미만"), (300_000_000, "3억원미만"),
    (100_000_000, "1억원미만"), (50_000_000, "5천만원미만"), (20_000_000, "2천만원미만"),
)


def _tag_ceiling(band: str) -> int:
    number = int(re.match(r"\d+", band).group(0))
    return number * (100_000_000 if "억" in band else 10_000_000)


def retag_title_band(host: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Make the title's price-band tag claim a band below the meta estimated price (v24)."""

    if not price_premise_is_valid(host):
        raise PlantError("price premise is placeholder or an unresolved unit amount")
    price = host["meta"].get("입찰추정가격")
    text = _all_text(host)
    tags = {match.group(0): match for match in _TITLE_TAG_RE.finditer(text)}
    if type(price) is not int or len(tags) != 1:
        raise PlantError("host does not carry exactly one title band tag")
    tag, match = next(iter(tags.items()))
    if price >= _tag_ceiling(match["band"]):
        raise PlantError("the title band tag already disagrees with the price")
    for ceiling, label in _LOWER_TAGS:
        if price >= ceiling:
            break
    else:
        raise PlantError("price is below every lower band tag")
    new_tag = f"({match['method']}·{label})"
    record = copy.deepcopy(dict(host))
    for document in record["docs"]:
        document["text"] = document["text"].replace(tag, new_tag)
    return record, {
        "operation": "retag_title_band", "changed_lines": [{"from": tag, "to": new_tag}],
        "meta_price": price,
        "host_source_sha256": sha256_text(canonical_json(host)),
        "planted_source_sha256": sha256_text(canonical_json(record)),
    }


_LICENSE_CODE_RE = re.compile(r"\((\d{4})\)")


def swap_meta_license(host: Mapping[str, Any], donor: str) -> tuple[dict[str, Any], dict[str, Any]]:
    """Register a different licensed industry in meta than the one the 공고문 requires (v24)."""

    current = host["meta"].get("면허업종제한목록")
    if not isinstance(current, str):
        raise PlantError("host meta registers no licensed industry")
    codes = set(_LICENSE_CODE_RE.findall(current))
    _, document = _notice_document(host)
    stated = {code for code in codes if re.search(rf"(?<!\d){code}(?!\d)", document["text"])}
    donor_codes = set(_LICENSE_CODE_RE.findall(donor))
    if len(codes) != 1 or stated != codes or not donor_codes or donor_codes & codes:
        raise PlantError("the 공고문 does not state the single registered industry code")
    if any(re.search(rf"(?<!\d){code}(?!\d)", _all_text(host)) for code in donor_codes):
        raise PlantError("the donor industry code also occurs in the host")
    record = copy.deepcopy(dict(host))
    record["meta"]["면허업종제한목록"] = donor
    record["meta"]["업종제한여부"] = "Y"
    return record, {
        "operation": "swap_meta_license", "document_code": next(iter(codes)),
        "changed_lines": [{"from": current, "to": donor}],
        "host_source_sha256": sha256_text(canonical_json(host)),
        "planted_source_sha256": sha256_text(canonical_json(record)),
    }


# v1 has no natural lawful counterpart to move between contexts, so its clauses are templates
# modelled on the organizer positives (DEV-01, DEV-046, DEV-058); their wording is not natural.
V1_TEMPLATES = (
    ("institution", "본 용역은 「고등교육법」 제2조에 따른 대학 또는 「산업교육진흥 및 산학연협력촉진에 관한 법률」 제25조에 따른 산학협력단만 참여할 수 있습니다."),
    ("institution", "입찰참가자는 국·공립 연구기관 또는 정부출연 연구기관이어야 합니다."),
    ("institution", "관련 분야 연구 경험이 있는 대학교, 국공립연구기관에 한하여 입찰에 참가할 수 있습니다."),
    ("institution", "본 과업과 관련된 학과가 설치된 4년제 대학에 한하여 입찰에 참가할 수 있습니다."),
    ("facility", "전국 모든 광역자치단체에 자체 서비스센터(A/S센터)를 보유한 업체이어야 합니다."),
    ("facility", "전국 17개 시·도에 지사 또는 영업소를 각각 설치·운영하고 있는 업체"),
    ("facility", "서울특별시, 부산광역시, 대구광역시, 광주광역시, 대전광역시에 직영 서비스센터를 모두 보유한 업체"),
    ("headcount", "입찰공고일 현재 상시근로자 50명 이상을 직접 고용하고 있는 업체이어야 합니다."),
    ("headcount", "본사에 정규직 기술인력 30명 이상을 상시 보유한 업체에 한하여 참가할 수 있습니다."),
    ("facility", "입찰참가자는 전국 각 광역시·도에 직영 유지보수 센터를 갖추어야 합니다."),
    ("headcount", "상시 고용한 정규직 인원이 40명 이상인 업체만 입찰에 참여할 수 있습니다."),
)
_INSTITUTION_RE = re.compile(r"대학|연구기관|연구소|학회|산학협력")


def insert_institution_limit(host: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Add an arbitrary institution-type, nationwide-facility or headcount requirement (v1)."""

    service = host["meta"].get("업무구분") == "일반용역"
    choices = [
        (kind, text) for kind, text in V1_TEMPLATES
        if kind != "institution" or (service and not _INSTITUTION_RE.search(_all_text(host)))
    ]
    kind, text = sorted(choices, key=lambda choice: _rank("v1", host["id"], choice[1]))[0]
    record, provenance = plant_clause(host, text)
    provenance.update({"operation": f"insert_institution_limit:{kind}", "wording": "template"})
    return record, provenance


_SPEC_TYPES = ("규격서", "과업지시서")
_SPEC_ANCHOR_RE = re.compile(r"규격|사양|품명|구성")
_MODEL_LABEL_RE = re.compile(r"모델\s*명?\s*[:：]|제조사\s*[·ㆍ/및]?\s*모델|Model\s*[:：]", re.I)
_MODEL_TOKEN_RE = re.compile(r"[A-Za-z]{2,}[A-Za-z0-9\- ]*\d|[A-Za-z]{3,}\s*\([A-Za-z ]+\)")
_NOT_PRESCRIPTIVE_RE = re.compile(r"동등|동급|이상|참고|예시|기존|현재|보유|호환")


def spec_model_lines(record: Mapping[str, Any]) -> list[str]:
    """Labelled manufacturer/model lines of a natural specification, usable as v9 sources."""

    if re.search("동등|동급", _all_text(record)):
        return []
    lines = []
    for document in record["docs"]:
        if document.get("type") not in _SPEC_TYPES or re.search("동등|동급", document["text"]):
            continue
        for line in document["text"].split("\n"):
            text = line.strip()
            if (10 <= len(text) <= 120 and _MODEL_LABEL_RE.search(text) and _MODEL_TOKEN_RE.search(text)
                    and not _NOT_PRESCRIPTIVE_RE.search(text)):
                lines.append(text)
    return lines


def plant_spec_line(host: Mapping[str, Any], line: str) -> tuple[dict[str, Any], dict[str, Any]]:
    """Put a model-naming line into the host's specification, right after its first spec heading (v9)."""

    record = copy.deepcopy(dict(host))
    for doc_index, document in enumerate(record["docs"]):
        if document.get("type") in _SPEC_TYPES:
            break
    else:
        raise PlantError("host has no specification document")
    if re.search("동등|동급|모델", document["text"]):
        raise PlantError("host specification already discusses models or equivalents")
    if re.search("동등|동급", _all_text(record)):
        raise PlantError("another host document discusses equivalents")
    lines = document["text"].split("\n")
    anchors = [index for index, text in enumerate(lines[:40]) if _SPEC_ANCHOR_RE.search(text) and text.strip()]
    if not anchors:
        raise PlantError("host specification has no heading to attach the line to")
    body = strip_leading_marker(line)
    planted = f"- {body}"
    lines.insert(anchors[0] + 1, planted)
    document["text"] = "\n".join(lines)
    return record, {
        "operation": "plant_spec_line", "doc_index": doc_index, "planted_line": planted,
        "planted_body": body, "context_before": lines[max(0, anchors[0] - 1):anchors[0] + 1],
        "context_after": lines[anchors[0] + 2:anchors[0] + 4],
        "host_source_sha256": sha256_text(canonical_json(host)),
        "planted_source_sha256": sha256_text(canonical_json(record)),
    }


# ---------------------------------------------------------------------------
# Natural clause bank.  A clause that is lawful in its own notice becomes a
# violation in a host whose price band or product class differs; its wording is
# natural and unseen by the organizer dev set.
# ---------------------------------------------------------------------------

_BIDDER = r"(?:자|업체|법인|단체|기관|사업자)"
_TRACK_GATE_RE = re.compile(r"실적(?!\s*(?:관리|신고|등록)?\s*(?:지침|규정|기준))")
# A gate names the bidder who holds the record; proof-document and scoring lines do not.
_TRACK_VERB_RE = re.compile(rf"(?:있는|보유한|수행한|이행한|납품한|완료한)\s*{_BIDDER}")
_NOT_A_GATE_RE = re.compile(r"평가|배점|제안서|가점|우대|증명|첨부|영수증|제출")
_CLIENT_RE = re.compile(r"(국가기관|지방자치단체|지자체|공공기관|정부|공기업|준정부기관)[^\n]{0,30}(발주|시행|계약|납품)")
# Naming private clients too means any client counts: that is not a specific-client restriction.
_ANY_CLIENT_RE = re.compile(r"민간|기업체|일반\s*기업|법인|기타|복지\s*(?:시설|기관|사업)|대학|학교|병원|호텔|아파트")
_DANGLING_CRITERIA_RE = re.compile(r"(?:아래|다음|하기)\s*(?:의\s*)?(?:기준|조건|요건|실적)")
_CLIENT_LAW_RE = re.compile(r"집행\s*기준|계약[^\n]{0,30}(?:법률|법령|시행령)")
# The requirement itself, not a note about the certificate's validity period.
_DIRECT_GATE_RE = re.compile(r"직접생산확인[^\n]{0,120}(?:소지|보유)")
_PRODUCT_NAME_RE = re.compile(r"세부\s*품명\s*[:：]?\s*([가-힣A-Za-z]{2,20})")
# Attendance at a site/business briefing as a condition of bidding; a proposal presentation is not one.
_MANDATORY_BRIEFING_RE = re.compile(
    r"(?:현장|사업|제안요청서?|입찰)\s*설명회[^\n]{0,80}(?:참석|참가)[^\n]{0,50}"
    r"(?:한하여|한해|에\s*한|불참|미참석|아니한|않은|않는|없는)"
)
_PRESENTATION_RE = re.compile(r"제안서\s*설명회|제안\s*설명|발표|평가")
_REGION_GATE_RE = re.compile(rf"(?:소재지|영업소|본점)[^\n]{{0,120}}(?:둔|있는|소재하고|소재한|위치한)[^\n]{{0,12}}{_BIDDER}")
_WON_PATTERNS = (
    (re.compile(r"(\d+(?:\.\d+)?)\s*억\s*(?:(\d)\s*천\s*만\s*)?원"),
     lambda match: int(float(match.group(1)) * 100_000_000) + int(match.group(2) or 0) * 10_000_000),
    (re.compile(r"(?<![\d.,억])(\d+(?:\.\d+)?)\s*천\s*만\s*원"),
     lambda match: int(float(match.group(1)) * 10_000_000)),
    (re.compile(r"(\d{1,3}(?:,\d{3})+)\s*원"), lambda match: int(match.group(1).replace(",", ""))),
)
MAX_BANK_CLAUSE_CHARS = 400


def stated_won(text: str) -> int | None:
    """The single amount a clause states, or None when absent or ambiguous."""

    amounts = set()
    for pattern, convert in _WON_PATTERNS:
        amounts.update(convert(match) for match in pattern.finditer(text))
        # "1억 5천만원" is one amount: consume it before the bare 천만 pattern can re-read its tail.
        text = pattern.sub(" ", text)
    return next(iter(amounts)) if len(amounts) == 1 else None


def bank_clauses(record: Mapping[str, Any], product: str | None) -> list[dict[str, Any]]:
    """Qualification list items of a natural notice usable as transplant sources, by target item."""

    try:
        _, _, lines, section = _qualification_lines(record)
    except PlantError:
        return []
    price = record["meta"].get("입찰추정가격")
    offset = section["heading_line"] + 1
    found = []
    for start, end in _blocks(lines[offset:section["end_line"]]):
        text = "\n".join(lines[offset + start:offset + end]).strip()
        # An anonymized agency token names the source's buyer type, which the host does not share.
        if (len(text) > MAX_BANK_CLAUSE_CHARS or "[수요기관" in text or "[기관" in text
                or "삭제" in text or _DANGLING_CRITERIA_RE.search(text)):
            continue
        targets = []
        flat = text.replace("\n", " ")
        provinces = set(_PROVINCE_RE.findall(text))
        if _TRACK_GATE_RE.search(text) and _TRACK_VERB_RE.search(text) and not _NOT_A_GATE_RE.search(text):
            targets.append("v8")  # with a host that already restricts the bidder's region
            if type(price) is int and price >= NOTICE_THRESHOLD_WON:
                targets.append("v2")
            if (_CLIENT_RE.search(text) and not _ANY_CLIENT_RE.search(text)
                    and not _CLIENT_LAW_RE.search(text)):
                targets.append("v4")
            if stated_won(text) is not None:
                targets.append("v3")
        elif _REGION_GATE_RE.search(flat) and "단위=기초" not in text and provinces:
            if len(provinces) >= 2 and re.search("또는|및|,", text):
                targets.append("v7")
            elif len(provinces) == 1 and not _SIZE_ANY_RE.search(text) and "실적" not in text:
                targets.append("v5")  # lawful below the ceiling, a violation above it
        elif (product == "competitive" and _DIRECT_GATE_RE.search(flat)
              and not text.startswith("※") and not _SIZE_ANY_RE.search(text)):
            targets.append("v12")
        elif _MANDATORY_BRIEFING_RE.search(flat) and not _PRESENTATION_RE.search(flat):
            targets.append("v22")
        elif (not _NOTE_START_RE.match(text) and _SIZE_GATE_RE.search(flat)
              and not _DELETE_GUARD_RE.search(text) and not _EXCEPTION_RE.search(text)):
            scopes = {size_scope(line) for line in text.split("\n")} - {None, "neutral"}
            if scopes == {"sme"}:
                targets.append("v14")  # any SME-only limit at or above the notice threshold
            elif scopes == {"small"}:
                targets.append("v15")  # small-only limit in the 1억~threshold band
        for target in targets:
            found.append({"target_item": target, "source_id": record["id"], "clause": text,
                          "threshold_won": stated_won(text) if target == "v3" else None})
    return found


def clause_product_is_unrelated(clause: str, host_text: str) -> bool:
    """True only when the clause names its product and the host never mentions anything like it.

    A direct-production clause for printed matter planted into a notice that orders leaflets
    is a lawful requirement on a designated product, not a restriction on a general one.
    """

    match = _PRODUCT_NAME_RE.search(clause)
    if match is None:
        return False
    name = match.group(1)
    if len(name) < 3:
        return name not in host_text
    return not any(name[index:index + 3] in host_text for index in range(len(name) - 2))


def transplant_host_targets(record: Mapping[str, Any], product: str | None) -> dict[str, Any]:
    """Which transplant targets this natural notice can host, with the budget v3 must exceed."""

    try:
        _, _, lines, section = _qualification_lines(record)
    except PlantError:
        return {}
    meta = record["meta"]
    price, budget = meta.get("입찰추정가격"), meta.get("배정예산금액")
    if type(price) is not int or record.get("dropped_doc_counts"):
        return {}
    body = "\n".join(lines[section["heading_line"] + 1:section["end_line"]])
    small_quote = meta.get("낙찰방법") == SMALL_QUOTE_AWARD
    targets: dict[str, Any] = {}
    if "실적" not in body:
        targets["v4"] = True
        if price < NOTICE_THRESHOLD_WON and not small_quote:
            targets["v2"] = True
        if type(budget) is int:
            targets["v3"] = max(budget, round(price * 1.1))
    if not _LOCATION_RE.search(body) and price < LOWEST_REGION_CEILING_WON and not small_quote:
        targets["v7"] = True
    if product == "general":
        targets["v12"] = True
    text = _all_text(record)
    negotiated_quote = small_quote or meta.get("계약방법") == "수의계약"
    if "실적" not in body and not small_quote:
        try:
            single_province_restriction(record)
        except PlantError:
            pass
        else:
            targets["v8"] = True  # a region limit is already there; a performance gate makes it double
    if not _LOCATION_RE.search(body) and price >= SHIFT_BANDS["v5"][0] and not negotiated_quote:
        targets["v5"] = True
    if product == "general" and not _SIZE_ANY_RE.search(text) and not _PRIVATE_BUYER_RE.search(text):
        if price >= NOTICE_THRESHOLD_WON:
            targets["v14"] = True
        elif price >= 100_000_000:
            targets["v15"] = True
    if meta.get("낙찰방법") == NEGOTIATED_AWARD and "설명회" not in text:
        targets["v22"] = True
    if "확약" not in text and "협약서" not in text:
        targets["v19"] = True
    if not re.search("동등|동급", text) and any(document.get("type") in _SPEC_TYPES and not re.search("동등|동급|모델", document["text"])
           for document in record["docs"]) and meta.get("업무구분") == "물품(내자)":
        prefix = product_prefix(record)
        if prefix is not None:
            targets["v9"] = prefix  # a model line only makes sense in a specification of the same product class
    return {item: value for item, value in targets.items()
            if not requires_price_premise(item) or price_premise_is_valid(record)}


def product_prefix(record: Mapping[str, Any]) -> str | None:
    """First six digits (제품명번호) of the notice's registered 세부품명번호, if it registers one."""

    match = re.search(r"(?<!\d)(\d{6})\d{4}(?!\d)", str(record["meta"].get("세부품명번호목록") or ""))
    return match.group(1) if match else None


def load_dev() -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, str]]]:
    records: dict[str, dict[str, Any]] = {}
    with gzip.open(DEV_INPUT, "rt", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                row = json.loads(line)
                records[row["id"]] = row
    with DEV_LABELS.open(encoding="utf-8", newline="") as handle:
        labels = {row["id"]: row for row in csv.DictReader(handle)}
    if set(records) != set(labels):
        raise PlantError("dev records and labels differ")
    return records, labels


def _near_duplicates() -> set[frozenset[str]]:
    if not SPLIT_PATH.exists():
        return set()
    split = json.loads(SPLIT_PATH.read_text(encoding="utf-8"))
    return {frozenset(pair[:2]) for pair in split.get("near_duplicates", [])}


def _all_text(record: Mapping[str, Any]) -> str:
    return "\n".join(document["text"] for document in record["docs"])


def host_is_eligible(item: str, source_id: str, host: Mapping[str, Any]) -> bool:
    """Metadata/text necessary conditions under which the planted target cell is positive."""

    meta = host["meta"]
    price = meta.get("입찰추정가격")
    budget = meta.get("배정예산금액")
    if type(price) is not int or (requires_price_premise(item) and not price_premise_is_valid(host)):
        return False
    award = meta.get("낙찰방법")
    text = _all_text(host)
    if item == "v1":
        return meta.get("업무구분") == "일반용역"
    if item == "v2":
        return price < NOTICE_THRESHOLD_WON and award != SMALL_QUOTE_AWARD
    if item == "v3":
        ceiling = V3_THRESHOLD_WON[source_id] * V3_BUDGET_MARGIN
        return type(budget) is int and budget < ceiling and price * 1.1 < ceiling
    if item == "v4":
        return True
    if item == "v5":
        return price >= HIGHEST_REGION_CEILING_WON
    if item in {"v6", "v7"}:
        return price < LOWEST_REGION_CEILING_WON and award != SMALL_QUOTE_AWARD
    if item == "v13":
        return "경쟁제품" in text and "직접생산" in text
    if item == "v14":
        return price >= NOTICE_THRESHOLD_WON and not re.search("중소기업|소기업|소상공인", text)
    if item == "v19":
        return "확약" not in text
    if item == "v21":
        return "공동이행" in str(meta.get("공동도급구성방식")) and "지분" not in text
    if item == "v22":
        return award == NEGOTIATED_AWARD and "설명회" not in text
    raise PlantError(f"item is not plantable: {item}")


def official_zero_hosts(
    records: Mapping[str, Mapping[str, Any]], labels: Mapping[str, Mapping[str, str]],
) -> list[str]:
    hosts = []
    for record_id, record in records.items():
        if any(labels[record_id][item] != "0" for item in ITEMS):
            continue
        if record.get("dropped_doc_counts"):
            continue
        try:
            for document in record["docs"]:
                if document.get("type") == "공고문":
                    find_qualification_section(document["text"])
                    break
            else:
                continue
        except PlantError:
            continue
        hosts.append(record_id)
    return hosts


def official_sources(labels: Mapping[str, Mapping[str, str]]) -> list[tuple[str, str, str]]:
    sources = []
    for record_id, row in labels.items():
        for item in PLANT_ITEMS:
            evidence = row["e" + item[1:]]
            if row[item] == "1" and evidence.strip():
                sources.append((item, record_id, evidence))
    return sorted(sources, key=lambda source: (int(source[0][1:]), source[1]))


def _rank(*parts: str) -> str:
    return sha256_text("\0".join(parts))


def build_plants(
    *, hosts_per_source: int, hosts_per_single_source_item: int, controls: int,
    max_plants_per_host: int,
) -> dict[str, Any]:
    records, labels = load_dev()
    hosts = official_zero_hosts(records, labels)
    sources = official_sources(labels)
    near = _near_duplicates()
    per_item = Counter(item for item, _, _ in sources)
    usage: Counter[str] = Counter()
    rows: list[dict[str, Any]] = []
    skipped: list[dict[str, str]] = []
    for item, source_id, evidence in sources:
        wanted = hosts_per_single_source_item if per_item[item] == 1 else hosts_per_source
        chosen = 0
        for host_id in sorted(hosts, key=lambda candidate: _rank(item, source_id, candidate)):
            if chosen == wanted:
                break
            if host_id == source_id or frozenset((host_id, source_id)) in near:
                continue
            if usage[host_id] >= max_plants_per_host:
                continue
            if not host_is_eligible(item, source_id, records[host_id]):
                continue
            try:
                planted, provenance = plant_clause(records[host_id], evidence)
            except PlantError as exc:
                skipped.append({"item": item, "source_id": source_id, "host_id": host_id, "reason": str(exc)})
                continue
            excluded = sorted(set(AFFECTED_ITEMS[item]) | {"v24"}, key=lambda name: int(name[1:]))
            rows.append({
                "plant_id": _rank("plant", item, source_id, host_id)[:16],
                "kind": "planted", "target_item": item, "source_id": source_id,
                "host_id": host_id, "excluded_items": excluded,
                "expected_zero_items": [name for name in ITEMS if name not in excluded],
                "provenance": provenance, "record": planted,
            })
            usage[host_id] += 1
            chosen += 1
        if chosen < wanted:
            skipped.append({"item": item, "source_id": source_id, "host_id": "", "reason": f"only {chosen}/{wanted} eligible hosts"})
    for host_id in sorted(hosts, key=lambda candidate: _rank("control", candidate))[:controls]:
        rows.append({
            "plant_id": _rank("control", host_id)[:16], "kind": "control",
            "target_item": None, "source_id": None, "host_id": host_id,
            "excluded_items": [], "expected_zero_items": list(ITEMS),
            "provenance": None, "record": copy.deepcopy(records[host_id]),
        })
    manifest = {
        "schema_version": SCHEMA_VERSION, "purpose": "teacher_recall_measurement_only_not_gold",
        "dev_input_sha256": hashlib.sha256(DEV_INPUT.read_bytes()).hexdigest(),
        "dev_labels_sha256": hashlib.sha256(DEV_LABELS.read_bytes()).hexdigest(),
        "eligible_zero_hosts": len(hosts), "sources": len(sources),
        "planted": sum(row["kind"] == "planted" for row in rows),
        "controls": sum(row["kind"] == "control" for row in rows),
        "planted_by_item": dict(sorted(
            Counter(row["target_item"] for row in rows if row["kind"] == "planted").items(),
            key=lambda pair: int(pair[0][1:]),
        )),
        "distinct_hosts_used": len({row["host_id"] for row in rows if row["kind"] == "planted"}),
        "skipped": skipped,
        "rows_sha256": sha256_text(canonical_json(rows)),
    }
    return {"manifest": manifest, "rows": rows}


def _pool_records():
    with gzip.open(POOL_INPUT, "rt", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                yield json.loads(line)


def _family_lookup() -> dict[str, str]:
    if not FAMILY_MANIFEST.exists():
        return {}
    manifest = json.loads(FAMILY_MANIFEST.read_text(encoding="utf-8"))
    return {row["record_id"]: row["family_id"] for row in manifest["records"]}


_SIZE_OPERATIONS = {
    "delete": delete_size_restriction,
    "narrow": lambda host: rewrite_size_scope(host, "narrow"),
    "broaden": lambda host: rewrite_size_scope(host, "broaden"),
}


_PLACEHOLDER_LICENSE = "[가상업종(0000)]"  # feasibility probe only; a real donor is drawn when the row is built
_CLAUSE_OPERATIONS = {
    "diverge_meta_price": ("v24", diverge_meta_price),
    "retag_title_band": ("v24", retag_title_band),
    "swap_meta_license": ("v24", lambda host: swap_meta_license(host, _PLACEHOLDER_LICENSE)),
    "rewrite_min_share": ("v21", rewrite_min_share),
    "narrow_region": ("v6", narrow_region),
    "advance_pledge_timing": ("v19", advance_pledge_timing),
    "insert_briefing": ("v23", insert_briefing),
    "delete_large_enterprise_limit": ("v20", delete_large_enterprise_limit),
    "insert_institution_limit": ("v1", insert_institution_limit),
}
# Cells a host-clause edit or a transplant can change besides its target (v24 is always excluded).
EDIT_AFFECTED: dict[str, tuple[str, ...]] = {
    **{item: _SIZE for item in ("v10", "v11", "v12", "v13", "v14", "v15", "v16", "v17", "v18")},
    **{item: _REGION for item in ("v5", "v6", "v7")},
    **{item: _TRACK for item in ("v2", "v3", "v4")},
    "v1": ("v1", *_SIZE), "v8": tuple(sorted(set(_TRACK) | set(_REGION))), "v9": ("v9",),
    "v19": ("v19",), "v20": ("v20", *_SIZE), "v21": ("v21",), "v22": ("v22", "v23"),
    "v23": ("v22", "v23"), "v24": ("v24",),
}
TRANSPLANT_ITEMS = ("v2", "v3", "v4", "v5", "v7", "v8", "v9", "v12", "v14", "v15", "v19", "v22")
MAX_USES_PER_SOURCE_CLAUSE = 2
# Items whose natural source clauses are scarce may reuse a clause more often.
MAX_USES_BY_ITEM = {"v4": 5, "v22": 4, "v9": 4, "v19": 3}
# v12 pairs are checked against the host text only when the row is built, so pair generously.
PAIRING_OVERSUPPLY = {"v12": 3}
ATTACHMENT_DROP_RATE = 0.44  # lifts 공고문-only from the pool's 23% to the 57% seen in dev positives
SECOND_EDIT_RATE = 0.3  # about 39% of the dev positive notices carry more than one violation
SECOND_EDIT_TEMPLATE_RATE = 0.15
# v24 is never a second edit: it is excluded from every row's scoring anyway.
_SECOND_EDITS = ("rewrite_min_share", "advance_pledge_timing", "insert_briefing", "narrow_region",
                 "insert_institution_limit")


def small_quote_host(record: Mapping[str, Any]) -> bool:
    meta = record["meta"]
    return any("수의" in str(meta.get(field, "")) for field in ("낙찰방법", "계약방법"))


def feasible_pool_edits(record: Mapping[str, Any], catalog: Any, product: str | None = None) -> dict[str, str]:
    """Target item -> operation for every edit that succeeds on this natural pool notice."""

    price = record["meta"].get("입찰추정가격")
    if type(price) is not int or record.get("dropped_doc_counts"):
        return {}
    feasible: dict[str, str] = {}
    text = _all_text(record)
    sized = (bool(_SIZE_ANY_RE.search(text)) and not _EXCEPTION_RE.search(text)
             and not _PRIVATE_BUYER_RE.search(text))
    if sized:
        product = product or product_class(record, catalog)
        for operation, function in _SIZE_OPERATIONS.items():
            target = size_edit_target(operation, product, price)
            if target is None or (target in {"v11", "v13", "v18"} and small_quote_host(record)):
                continue
            try:
                function(record)
            except PlantError:
                continue
            feasible[target] = operation
    options: dict[str, list[str]] = {}
    for operation, (target, function) in _CLAUSE_OPERATIONS.items():
        try:
            function(record)
        except PlantError:
            continue
        options.setdefault(target, []).append(operation)
    for target, operations in options.items():
        # Several operations can reach the same item (three ways to v24): one per host, by hash.
        feasible[target] = sorted(operations, key=lambda name: _rank("operation", record["id"], name))[0]
    product = product or product_class(record, catalog)
    if product == "competitive" and not small_quote_host(record):
        try:
            delete_direct_production(record, catalog)
        except PlantError:
            pass
        else:
            feasible["v10"] = "delete_direct_production"
    shifts = []
    if sized and product == "general":
        scope = host_size_scope(record)
        if scope == "small" and price < 100_000_000:
            shifts.append("v15")
        if scope in {"small", "sme"}:
            shifts.append("v14")
    if price < LOWEST_REGION_CEILING_WON:
        try:
            single_province_restriction(record)
        except PlantError:
            pass
        else:
            shifts.append("v5")
    for target in shifts:
        if target in feasible:
            continue
        try:
            shift_price(record, target)
        except PlantError:
            continue
        feasible[target] = f"shift_price:{target}"
    return {item: operation for item, operation in feasible.items()
            if not requires_price_premise(item, operation) or price_premise_is_valid(record)}


def _apply_pool_edit(
    record: Mapping[str, Any], operation: str, catalog: Any = None, donors: Sequence[str] = (),
) -> tuple[dict[str, Any], dict[str, Any]]:
    if operation.startswith("shift_price:"):
        return shift_price(record, operation.split(":", 1)[1])
    if operation == "delete_direct_production":
        return delete_direct_production(record, catalog)
    if operation == "swap_meta_license":
        for donor in sorted(donors, key=lambda value: _rank("donor", record["id"], value)):
            try:
                return swap_meta_license(record, donor)
            except PlantError:
                continue
        raise PlantError("no natural donor industry fits this host")
    if operation in _CLAUSE_OPERATIONS:
        return _CLAUSE_OPERATIONS[operation][1](record)
    return _SIZE_OPERATIONS[operation](record)


HOLDOUT_FRACTION = 0.4


def _split_of(family: str) -> str:
    return "holdout" if int(_rank("split", family)[:8], 16) / 0x100000000 < HOLDOUT_FRACTION else "dev"


def clause_split(clause: str) -> str:
    """Split of a transplant clause by its wording, so boilerplate shared by agencies stays on one side."""

    return _split_of("clause:" + re.sub(r"\d+", "#", re.sub(r"\s+", "", strip_leading_marker(clause))))


def _normalized_wording(lines: Iterable[str]) -> str:
    return "".join(
        re.sub(r"\d+", "#", re.sub(r"\s+", "", _LEADING_MARKER_RE.sub("", line, count=1))) for line in lines
    )


def wording_key(row: Mapping[str, Any]) -> str | None:
    """Normalized wording of the clause that carries the violation, where the operation has one."""

    provenance = row["provenance"]
    if provenance.get("wording") == "template":
        return None
    if "planted_body" in provenance:
        lines = provenance["planted_body"].split("\n")
    elif "removed_lines" in provenance:
        lines = provenance["removed_lines"]
    elif "changed_lines" in provenance:
        lines = [change["from"] for change in provenance["changed_lines"]]
    else:
        return None
    return sha256_text(_normalized_wording(lines))


def separate_seen_wording(rows: list[dict[str, Any]]) -> None:
    """Holdout rows whose clause wording also occurs in dev leave the strict holdout (in place)."""

    for row in rows:
        row["wording_sha256"] = wording_key(row)
    seen = {row["wording_sha256"] for row in rows if row["split"] == "dev" and row["wording_sha256"]}
    for row in rows:
        if row["split"] == "holdout" and row["wording_sha256"] in seen:
            row["split"] = "holdout_seen_wording"


def pair_transplants(
    sources: Sequence[Mapping[str, Any]], host_targets: Mapping[str, Mapping[str, Any]],
    used_hosts: set[str], families: Mapping[str, str], quotas: Mapping[str, int] | int,
    retained: Sequence[Mapping[str, Any]] = (),
) -> dict[str, dict[str, Any]]:
    """host id -> source clause.  A holdout clause never meets a dev host, so holdout wording is unseen."""

    assigned: dict[str, dict[str, Any]] = {}
    retained_counts = Counter(row["target_item"] for row in retained)
    source_uses = Counter((row["target_item"], row["source_id"],
                           _normalized_wording(row["provenance"]["planted_body"].split("\n")))
                          for row in retained)
    for target in TRANSPLANT_ITEMS:
        wanted = quotas if isinstance(quotas, int) else quotas.get(target, 0)
        wanted += retained_counts[target]
        wanted *= PAIRING_OVERSUPPLY.get(target, 1)
        holdout_quota = round(wanted * HOLDOUT_FRACTION)
        for split, quota in (("dev", wanted - holdout_quota), ("holdout", holdout_quota)):
            quota = max(0, quota - sum(row["target_item"] == target and row["split"] == split for row in retained))
            clauses = sorted(
                (source for source in sources if source["target_item"] == target
                 and clause_split(source["clause"]) == split),
                key=lambda source: _rank("bank", target, source["source_id"], source["clause"]),
            )
            hosts = sorted(
                (host for host, targets in host_targets.items() if target in targets
                 and _split_of(families.get(host, host)) == split),
                key=lambda host: _rank("bank-host", target, host),
            )
            taken = 0
            for _ in range(MAX_USES_BY_ITEM.get(target, MAX_USES_PER_SOURCE_CLAUSE)):
                for source in clauses:
                    if taken == quota:
                        break
                    source_key = (target, source["source_id"], _normalized_wording(source["clause"].split("\n")))
                    if source_uses[source_key] >= MAX_USES_BY_ITEM.get(target, MAX_USES_PER_SOURCE_CLAUSE):
                        continue
                    source_family = families.get(source["source_id"], source["source_id"])
                    for host in hosts:
                        if host in used_hosts or host in assigned or host == source["source_id"]:
                            continue
                        if families.get(host, host) == source_family:
                            continue
                        if target == "v3" and host_targets[host]["v3"] >= source["threshold_won"] * V3_BUDGET_MARGIN:
                            continue
                        if target == "v9" and host_targets[host]["v9"] != source.get("product_prefix"):
                            continue
                        assigned[host] = dict(source)
                        source_uses[source_key] += 1
                        taken += 1
                        break
    return assigned


def drop_attachments(row: dict[str, Any]) -> None:
    """Silently keep only the 공고문, as the organizer's edited notices often do (in place)."""

    record = row["record"]
    edited_index = row["provenance"].get("doc_index", 0)
    if record["docs"][edited_index].get("type") != "공고문" or len(record["docs"]) == 1:
        return
    if int(_rank("attachments", row["plant_id"])[:8], 16) / 0x100000000 >= ATTACHMENT_DROP_RATE:
        return
    kept = [document for document in record["docs"] if document.get("type") == "공고문"]
    # A rewrite may have touched only an attachment even when provenance defaults to doc 0.
    # Keep it if dropping it would erase every surviving positive evidence span for an item.
    kept_indices = {i for i, document in enumerate(record["docs"]) if document.get("type") == "공고문"}
    for spans in (evidence_spans(row) if "target_item" in row else {}).values():
        if spans and not any(span["doc_index"] in kept_indices for span in spans):
            return
    row["provenance"]["attachments_removed"] = [
        document.get("type") for document in record["docs"] if document.get("type") != "공고문"
    ]
    row["provenance"]["doc_index"] = kept.index(record["docs"][edited_index])
    record["docs"] = kept


def add_second_edit(row: dict[str, Any]) -> None:
    """Give some notices a second, independent violation, as the organizer's designs do (in place)."""

    if int(_rank("second", row["plant_id"])[:8], 16) / 0x100000000 >= SECOND_EDIT_RATE:
        return
    taken = set(row["excluded_items"])
    # Edits of the host's own clauses first; the v1 template fits almost any host, so it is only an
    # occasional fallback and does not crowd out the other items.
    natural = sorted(_SECOND_EDITS[:-1], key=lambda name: _rank("second-op", row["plant_id"], name))
    fallback = int(_rank("second-v1", row["plant_id"])[:8], 16) / 0x100000000 < SECOND_EDIT_TEMPLATE_RATE
    for operation in natural + ([_SECOND_EDITS[-1]] if fallback else []):
        target, function = _CLAUSE_OPERATIONS[operation]
        if target in taken or target == row["target_item"]:
            continue
        if requires_price_premise(target, operation) and not price_premise_is_valid(row["record"]):
            continue
        try:
            record, provenance = function(row["record"])
        except PlantError:
            continue
        row["record"] = record
        row["extra_targets"] = [{"target_item": target, "operation": operation, "provenance": provenance}]
        excluded = set(row["excluded_items"]) | set(EDIT_AFFECTED[target]) | {"v24"}
        row["excluded_items"] = sorted(excluded, key=lambda name: int(name[1:]))
        row["expected_zero_items"] = [name for name in ITEMS if name not in excluded]
        return


def retain_dev_edits(
    preferred: Sequence[Mapping[str, Any]], hosts: Mapping[str, Mapping[str, Any]],
    candidates: Mapping[str, Mapping[str, str]], sources: Sequence[Mapping[str, Any]],
    host_targets: Mapping[str, Mapping[str, Any]], families: Mapping[str, str],
    catalog: Any, donors: Sequence[str],
) -> tuple[list[dict[str, Any]], Counter[str]]:
    """Replay previously measured dev edits under current filters; retain only identical inputs/keys."""
    retained, rejected = [], Counter()
    bank: dict[tuple[str, str], list[Mapping[str, Any]]] = {}
    for source in sources:
        bank.setdefault((source["target_item"], source["source_id"]), []).append(source)
    for old in preferred:
        if old["split"] != "dev":
            raise PlantError("only dev rows may be preferred")
        host_id, target, operation = old["host_id"], old["target_item"], old["operation"]
        host = hosts.get(host_id)
        try:
            if host is None or _split_of(families.get(host_id, host_id)) != "dev":
                raise PlantError("preferred host absent or not dev")
            if old["kind"] == "edited":
                if candidates.get(target, {}).get(host_id) != operation:
                    raise PlantError("edit no longer feasible")
                record, provenance = _apply_pool_edit(host, operation, catalog, donors)
            else:
                targets = host_targets.get(host_id, {})
                if target not in targets:
                    raise PlantError("transplant host no longer eligible")
                matches = []
                for source in bank.get((target, old["source_id"]), []):
                    if clause_split(source["clause"]) != "dev":
                        continue
                    if target == "v3" and targets[target] >= source["threshold_won"] * V3_BUDGET_MARGIN:
                        continue
                    if target == "v9" and targets[target] != source.get("product_prefix"):
                        continue
                    if target == "v12" and not clause_product_is_unrelated(source["clause"], _all_text(host)):
                        continue
                    planter = plant_spec_line if target == "v9" else plant_clause
                    record, provenance = planter(host, source["clause"])
                    if provenance["planted_body"] == old["provenance"]["planted_body"]:
                        matches.append((record, provenance))
                if not matches:
                    raise PlantError("transplant source or amount no longer eligible")
                record, provenance = matches[0]
            row = copy.deepcopy(dict(old))
            row.update(record=record, provenance=provenance, extra_targets=[])
            excluded = set(EDIT_AFFECTED[target]) | {"v24"}
            row["excluded_items"] = sorted(excluded, key=lambda name: int(name[1:]))
            row["expected_zero_items"] = [item for item in ITEMS if item not in excluded]
            add_second_edit(row)
            drop_attachments(row)
            if any(row[key] != old[key] for key in ("record", "extra_targets", "expected_zero_items")):
                raise PlantError("replayed input or constructed cells changed")
        except PlantError as exc:
            rejected[str(exc)] += 1
            continue
        retained.append(row)
    return retained, rejected


def build_pool_edits(*, per_item: int, preferred_dev: Sequence[Mapping[str, Any]] = ()) -> dict[str, Any]:
    """Edit natural pool notices; one variant per host, hosts chosen in hash order per item."""

    try:
        from tools.independent_gold import catalog_facts
    except ModuleNotFoundError:  # Direct script invocation.
        import catalog_facts  # type: ignore[no-redef]
    catalog = catalog_facts.CatalogIndex.load()
    candidates: dict[str, dict[str, str]] = {}
    sources: list[dict[str, Any]] = []
    host_targets: dict[str, dict[str, Any]] = {}
    donors: set[str] = set()
    preferred_ids = {row["host_id"] for row in preferred_dev}
    preferred_hosts = {}
    for record in _pool_records():
        if record["id"] in preferred_ids:
            preferred_hosts[record["id"]] = record
        product = product_class(record, catalog)
        feasible = feasible_pool_edits(record, catalog, product)
        for target, operation in feasible.items():
            candidates.setdefault(target, {})[record["id"]] = operation
        sources.extend(bank_clauses(record, product))
        if "v19" in feasible:
            # The rewritten pledge line is itself a natural-worded pre-bid pledge clause.
            clause = advance_pledge_timing(record)[1]["changed_lines"][0]["to"].strip()
            if len(clause) <= MAX_BANK_CLAUSE_CHARS:
                sources.append({"target_item": "v19", "source_id": record["id"], "clause": clause, "threshold_won": None})
        prefix = product_prefix(record)
        for line in spec_model_lines(record) if prefix is not None else ():
            sources.append({"target_item": "v9", "source_id": record["id"], "clause": line,
                            "threshold_won": None, "product_prefix": prefix})
        license_list = record["meta"].get("면허업종제한목록")
        if isinstance(license_list, str) and len(_LICENSE_CODE_RE.findall(license_list)) == 1 and len(license_list) <= 60:
            donors.add(license_list)
        targets = transplant_host_targets(record, product)
        if targets:
            host_targets[record["id"]] = targets
    families = _family_lookup()
    retained, rejected = retain_dev_edits(preferred_dev, preferred_hosts, candidates, sources,
                                          host_targets, families, catalog, sorted(donors))
    retained_ids = {row["host_id"] for row in retained}
    retained_counts = Counter(row["target_item"] for row in retained)
    chosen: dict[str, tuple[str, str]] = {}
    for target in sorted(candidates, key=lambda item: len(candidates[item])):
        taken = retained_counts[target]
        for host_id in sorted(candidates[target], key=lambda candidate: _rank("pool", target, candidate)):
            if taken == per_item:
                break
            if host_id not in chosen and host_id not in retained_ids:
                chosen[host_id] = (target, candidates[target][host_id])
                taken += 1
    edited_counts = Counter(target for target, _ in chosen.values())
    quotas = {target: max(0, per_item - edited_counts[target] - retained_counts[target]) for target in TRANSPLANT_ITEMS}
    transplants = pair_transplants(sources, host_targets, set(chosen) | retained_ids, families, quotas,
                                  [row for row in retained if row["kind"] == "transplanted"])
    rows: list[dict[str, Any]] = list(retained)
    unparsable: Counter[str] = Counter()
    built = Counter(edited_counts) + retained_counts
    for record in _pool_records():
        host_id = record["id"]
        family = families.get(host_id, host_id)
        if host_id in chosen:
            target, operation = chosen[host_id]
            try:
                edited, provenance = _apply_pool_edit(record, operation, catalog, sorted(donors))
            except PlantError:
                unparsable[f"{target}:{operation}"] += 1
                built[target] -= 1
                continue
            kind, source_id, split = "edited", None, _split_of(family)
        elif host_id in transplants:
            source = transplants[host_id]
            target, source_id = source["target_item"], source["source_id"]
            operation = f"transplant:{target}"
            if built[target] >= per_item:
                continue
            if target == "v12" and not clause_product_is_unrelated(source["clause"], _all_text(record)):
                unparsable["v12_related_or_unnamed_product"] += 1
                continue
            try:
                planter = plant_spec_line if target == "v9" else plant_clause
                edited, provenance = planter(record, source["clause"])
            except PlantError:
                unparsable[target] += 1
                continue
            built[target] += 1
            kind, split = "transplanted", clause_split(source["clause"])
        else:
            continue
        excluded = sorted(set(EDIT_AFFECTED[target]) | {"v24"}, key=lambda name: int(name[1:]))
        row = {
            "plant_id": _rank("pool-edit", target, host_id)[:16],
            "kind": kind, "operation": operation, "target_item": target, "extra_targets": [],
            "source_id": source_id, "host_id": host_id, "family_id": family, "split": split,
            "excluded_items": excluded,
            "expected_zero_items": [name for name in ITEMS if name not in excluded],
            # Natural pool notices may carry real violations: only the target cells are known.
            "zero_cells_vetted": False,
            "provenance": provenance, "record": edited,
        }
        add_second_edit(row)
        drop_attachments(row)
        rows.append(row)
    rows.sort(key=lambda row: (int(row["target_item"][1:]), row["plant_id"]))
    separate_seen_wording(rows)

    def _by_item(counter: Mapping[str, int]) -> dict[str, int]:
        return dict(sorted(counter.items(), key=lambda pair: int(pair[0][1:])))

    manifest = {
        "schema_version": SCHEMA_VERSION, "purpose": "synthetic_development_set_not_organizer_gold",
        "pool_input_sha256": hashlib.sha256(POOL_INPUT.read_bytes()).hexdigest(),
        "per_item": per_item, "holdout_fraction": HOLDOUT_FRACTION,
        "preferred_dev_requested": len(preferred_dev), "preferred_dev_retained": len(retained),
        "preferred_dev_rejected": dict(rejected),
        "feasible_hosts_by_item": _by_item({item: len(hosts) for item, hosts in candidates.items()}),
        "bank_clauses_by_item": _by_item(Counter(source["target_item"] for source in sources)),
        "rows_by_item": _by_item(Counter(row["target_item"] for row in rows)),
        "rows_by_item_and_operation": {
            f"{item}:{operation}": count for (item, operation), count in sorted(
                Counter((row["target_item"], row["operation"]) for row in rows).items(),
                key=lambda pair: (int(pair[0][0][1:]), pair[0][1]),
            )
        },
        "rows_with_second_violation": sum(bool(row["extra_targets"]) for row in rows),
        "rows_with_attachments_removed": sum("attachments_removed" in row["provenance"] for row in rows),
        "distinct_transplant_sources": len({row["source_id"] for row in rows if row["kind"] == "transplanted"}),
        "rows_not_built": dict(unparsable),
        "rows_by_split": dict(Counter(row["split"] for row in rows)),
        "rows_sha256": sha256_text(canonical_json(rows)),
    }
    return {"manifest": manifest, "rows": rows}


def review_markdown(rows: Iterable[Mapping[str, Any]]) -> str:
    parts = ["# Planted-clause insertion review", ""]
    for row in rows:
        if row.get("split", "dev") != "dev":
            continue
        provenance = row["provenance"]
        if row["kind"] == "edited":
            price = row["record"]["meta"].get("입찰추정가격")
            parts.append(f"## {row['plant_id']} {row['target_item']} {row['operation']} {row['host_id']} price={price}")
            parts.append("```")
            for line in provenance.get("removed_lines", [])[:6]:
                parts.append("- " + line)
            for change in provenance.get("changed_lines", [])[:3]:
                parts.extend(["- " + change["from"], "+ " + change["to"]])
            if "evidence_line" in provenance:
                parts.append(f"doc {provenance['document_price']:,} vs meta {provenance['meta_price']:,} | " + provenance["evidence_line"])
            if "to_price" in provenance:
                parts.append(f"price {provenance['from_price']:,} -> {provenance['to_price']:,}")
            parts.extend(["```", ""])
            continue
        if row["kind"] not in {"planted", "transplanted"}:
            continue
        parts.append(f"## {row['plant_id']} {row['target_item']} {row['source_id']} -> {row['host_id']}")
        parts.append("```")
        parts.extend(provenance["context_before"])
        parts.append(">>> " + provenance["planted_line"])
        parts.extend(provenance["context_after"])
        parts.append("```")
        parts.append("")
    return "\n".join(parts)



# v10 additions are opt-in; v9 generation and its conservative filters stay intact.
V10_SEED = "cell-v10-families-20260919-sealed-01"
ABSENCE_ITEMS = {"v10", "v11", "v16", "v18", "v20"}
NEAR_MISS_ITEMS = ("v2", "v3", "v5", "v10", "v11", "v12", "v13", "v15", "v16", "v17", "v18", "v19", "v21", "v22", "v23", "v24")
WRITER_PROMPT = """# 외부 조항 작성자 계약 v10
제공된 task의 item_definition과 legal_conditions를 적용한다. 주어진 mode의 label을 만드는
새 한국어 조항을 1~3문장으로 작성한다. 호스트 문장 또는 부분문장을 복사하지 않는다.
호스트 익명화 토큰은 정확히 재사용하고 새 실명·지역 심볼·기관 토큰을 만들지 않는다.
meta의 금액·법·계약방법·업종·지역·날짜와 모순되는 조항은 작성하지 않는다.
insert_after_line은 공고문 0-based 줄 번호이며 task가 제시한 후보 중 하나이다.
입력 발췌만으로 조달범위/법적 전제/기존 모순을 닫을 수 없거나, 삭제·금액변경·O7가
필요하면 clause를 만들지 말고 별도 abstentions 파일에 host_id,item,mode,reason을 기록한다.
부재탐지 항목은 단순 삽입으로 위반을 만들 수 없을 수 있다. O7는 건명·과업·코드·면허의
동시 정합 편집이 필요하므로 이 삽입 전용 형식에 억지로 작성하지 않는다.
출력은 written_clauses.jsonl이며 각 행은 item, mode(violation|near_miss), host_id,
clause, insert_after_line, why_label, label(1|0), writer_model, prompt_sha256만 포함한다.
writer_model은 실제 외부 모델명, prompt_sha256은 task의 고정 프롬프트 해시이다.
why_label에는 해당 법적 전제와 meta 및 기존 문서와 충돌하지 않는 이유를 쓴다.
공식 정답이나 독립 검수 결과로 주장하지 않는다. 자동 검사는 구조·토큰·명시적 충돌을
거부할 뿐 의미적 정답을 인증하지 않는다. 별도 의미 검수 후에만 평가 세트에 편입한다.
dev와 holdout 작업 묶음은 별도 작성 세션에서 처리하고, holdout 결과를 개발자에게 출력하지 않는다.
"""


def v10_split(family: str) -> str:
    return "holdout" if int(_rank(V10_SEED, family)[:8], 16) / 0x100000000 < HOLDOUT_FRACTION else "dev"


def evidence_spans(row: Mapping[str, Any]) -> dict[str, list[dict[str, Any]]]:
    """Re-locate final text, after second edits and document removal; never trust stale offsets."""
    result = {}
    targets = [] if row.get("near_miss_items") else [(row["target_item"], row["provenance"])]
    targets += [(extra["target_item"], extra["provenance"]) for extra in row.get("extra_targets", [])]
    for item, provenance in targets:
        if item in ABSENCE_ITEMS:
            continue
        phrases = [provenance.get("planted_body", ""), provenance.get("evidence_line", "")]
        phrases += [change["to"] for change in provenance.get("changed_lines", [])]
        phrases += [change["to"] for change in provenance.get("meta_changes", {}).values()]
        if provenance.get("operation", "").startswith("shift_price"):
            pattern = _LOCATION_RE if item == "v5" else _SIZE_ANY_RE
            phrases += [line for doc in row["record"]["docs"] for line in doc["text"].splitlines() if pattern.search(line)]
        if provenance.get("document_code"):
            phrases += [line for doc in row["record"]["docs"] for line in doc["text"].splitlines()
                        if provenance["document_code"] in line]
        spans = []
        for doc_index, document in enumerate(row["record"]["docs"]):
            for phrase in dict.fromkeys(phrases):
                if not phrase.strip():
                    continue
                start = document["text"].find(phrase)
                if start >= 0:
                    spans.append({"doc_index": doc_index, "doc_id": document.get("doc_id"),
                                  "start": start, "end": start + len(phrase), "text": phrase})
        if spans:
            result[item] = spans
    return result


def _changed_record(host: Mapping[str, Any], replacements: Mapping[str, str], operation: str):
    record = copy.deepcopy(dict(host))
    changes = []
    for document in record["docs"]:
        before = document["text"]
        for old, new in replacements.items():
            if old in document["text"]:
                document["text"] = document["text"].replace(old, new)
                changes.append({"from": old, "to": new})
        if before == document["text"]:
            continue
    if not changes or record == host:
        raise PlantError("edit makes no change")
    return record, {"operation": operation, "changed_lines": changes,
                    "host_source_sha256": sha256_text(canonical_json(host)),
                    "planted_source_sha256": sha256_text(canonical_json(record))}


def legal_near_miss(host: Mapping[str, Any], item: str, product: str | None, *, wording_partition: str = "dev"):
    """Conservative constructed zeros; reject hosts with unresolved competing obligations."""
    import datetime

    text, meta = _all_text(host), host["meta"]
    price = meta.get("입찰추정가격")
    if requires_price_premise(item) and not price_premise_is_valid(host):
        raise PlantError("price premise is placeholder or an unresolved unit amount")
    if type(price) is not int or host.get("dropped_doc_counts"):
        raise PlantError("incomplete host or price")
    _qualification_lines(host)
    if item == "v21":
        # Reuse the v9 joint-performance and unique-percentage feasibility gate.
        rewrite_min_share(host)
        law = meta.get("적용계약법")
        if law not in {LOCAL_LAW, "국가계약법"} or re.search(r"조정|주계약자|혼합|분담이행", text):
            raise PlantError("share law or adjusted mode is unresolved")
        floor = "5" if law == LOCAL_LAW else "10"
        replacements = {m.group(0): m.group(1) + (floor + ".0" if m.group(2) == floor else floor) + m.group(3)
                        for m in _SHARE_RE.finditer(text)}
        return _changed_record(host, replacements, "near_miss:lawful_share")
    if item == "v5":
        _, location_lines = single_province_restriction(host)
        if meta.get("적용계약법") != "국가계약법" or meta.get("업무구분") not in {"물품", "물품(내자)", "일반용역"}:
            raise PlantError("regional threshold requires national goods/services")
        _, document = _notice_document(host)
        if any(re.search(r"단위=기초|시·군|시군", document["text"].split("\n")[i]) for i in location_lines):
            raise PlantError("possibly narrower location restriction")
        tags = list(_TITLE_TAG_RE.finditer(text))
        safe_tags = bool(tags) and all(_tag_ceiling(m["band"]) > NOTICE_THRESHOLD_WON - 1
                                      and m["method"].strip() in str(meta.get("계약방법", "")) for m in tags)
        return shift_price(host, item, exact_price=NOTICE_THRESHOLD_WON - 1, allow_title_tag=safe_tags)
    if item == "v19":
        lines = [line for line in text.splitlines() if "확약서" in line or "협약서" in line]
        if len(lines) != 1 or not _PLEDGE_RE.search(lines[0]):
            raise PlantError("third-party pledge is not closed to one line")
        clause = strip_leading_marker(lines[0])
        if not re.search(r"제출|보유", clause):
            raise PlantError("pledge is not an obligation")
        return _changed_record(host, {lines[0]: "낙찰자는 제조사 물품공급 확약서를 계약 체결 시 발급받아 제출합니다."}, "near_miss:post_award_pledge")
    if item in {"v2", "v3"}:
        if "실적" in text:
            raise PlantError("an existing performance condition could survive")
        return plant_clause(host, "수행실적은 평가 배점에만 반영하며, 실적 유무 및 실적금액은 입찰참가자격을 제한하지 않습니다.")
    if item == "v22":
        if "설명회" in text or meta.get("낙찰방법") != NEGOTIATED_AWARD:
            raise PlantError("existing briefing or nonnegotiated host")
        return plant_clause(host, "설명회 참석 여부와 무관하게 응찰 가능합니다.")
    if item == "v23":
        if "설명회" in text or meta.get("적용계약법") != LOCAL_LAW or meta.get("낙찰방법") != NEGOTIATED_AWARD:
            raise PlantError("briefing law or existing date unresolved")
        def no_briefing():
            # Fixed, meaning-equivalent wording partitions; never chosen using predictions.
            clause = ("별도의 제안요청 설명회를 열지 않으며 사업 내용은 첨부 제안요청서로 안내합니다."
                      if wording_partition == "holdout" else
                      "제안요청서 설명회는 개최하지 않습니다. 제안서는 공고에 명시된 기한까지 제출하여야 합니다.")
            record, provenance = plant_clause(host, clause)
            provenance["operation"] = "near_miss:no_briefing"
            return record, provenance
        try:
            posted = datetime.datetime.strptime(str(meta.get("공고게시일자")), "%Y%m%d").date()
        except ValueError:
            return no_briefing()
        # Require an explicit proposal deadline, not the opening date as its proxy.
        matches = re.findall(r"제안서[^\n]{0,30}(?:마감|제출기한)[^\d\n]{0,10}(20\d{2})[.년/-]\s*(\d{1,2})[.월/-]\s*(\d{1,2})", text)
        if len(set(matches)) != 1:
            return no_briefing()
        deadline = datetime.date(*map(int, matches[0]))
        briefing = posted + datetime.timedelta(days=8)
        while briefing.weekday() >= 5:
            briefing += datetime.timedelta(days=1)
        required = 10 if price < 100_000_000 else 20 if price < 1_000_000_000 else 40
        if not price_premise_is_valid(host) or (deadline - briefing).days < required + 1:
            return no_briefing()
        return plant_clause(host, f"제안요청서 설명회는 {briefing:%Y. %m. %d.} 14:00에 개최하며 참석은 선택사항입니다.")
    if item == "v24":
        verify_comparison_amount_roles(host)
        tags = list(_TITLE_TAG_RE.finditer(text))
        if not tags or len({m.group(0) for m in tags}) != 1:
            raise PlantError("unique price band tag required")
        if not 0 < price + 1000 < _tag_ceiling(tags[0]["band"]):
            raise PlantError("price would leave title band")
        if tags[0]["method"].strip() not in str(meta.get("계약방법", "")):
            raise PlantError("title method and meta must agree")
        # Other explicit comparison axes must not already disagree.
        for field in ("면허업종제한목록", "제한지역코드목록"):
            value = meta.get(field)
            if value and str(value) not in text:
                raise PlantError("another comparison axis is not text-verified")
        return shift_price(host, item, exact_price=price + 1000, allow_title_tag=True)
    if (item == "v16" and product == "general" and 100_000_000 <= price < NOTICE_THRESHOLD_WON
            and not _SIZE_ANY_RE.search(text) and not _EXCEPTION_RE.search(text)
            and not _PRIVATE_BUYER_RE.search(text) and not small_quote_host(host)):
        clause = ("입찰참가자는 중소기업자에 한하며, 유효한 중소기업확인서를 보유하여야 합니다."
                  if wording_partition == "holdout" else
                  "중소기업확인서를 소지한 중소기업자만 입찰에 참가할 수 있습니다.")
        record, provenance = plant_clause(host, clause)
        provenance["operation"] = "near_miss:insert_sme_gate"
        return record, provenance
    scope = host_size_scope(host)
    if _EXCEPTION_RE.search(text) or _PRIVATE_BUYER_RE.search(text) or not has_size_gate(host):
        raise PlantError("size premise is not closed")
    if item in {"v10", "v11", "v12"}:
        valid = product == "competitive" and scope == "sme" and not small_quote_host(host)
        valid = valid and any(_is_direct_production_line(line) for line in text.splitlines())
    elif item in {"v13", "v18"}:
        valid = product == "general" and scope == "small" and small_quote_host(host) and price < 50_000_000
    elif item == "v17":
        valid = product == "general" and scope == "small" and price < 100_000_000
    elif item == "v15":
        valid = product == "general" and ((scope == "sme" and 100_000_000 <= price < NOTICE_THRESHOLD_WON)
                 or (scope == "small" and small_quote_host(host) and price < 50_000_000))
    elif item == "v16":
        valid = product == "general" and scope == "sme" and 100_000_000 <= price < NOTICE_THRESHOLD_WON
    else:
        valid = False
    if not valid:
        raise PlantError("no lawful size configuration")
    # Preserve the restriction and all notes; only expand a scope token into its equivalent wording.
    candidates = [line for line in text.splitlines() if _is_size_line(line)]
    replacements = {}
    for line in candidates:
        if scope == "sme":
            changed = re.sub(r"중소기업" + _NOT_SCOPE, "중·소기업", line)
            if changed == line:
                changed = re.sub(r"중[·ㆍ‧]소기업", "중소기업", line)
        else:
            changed = re.sub(r"소기업\s*(?:[·ㆍ]|및)\s*소상공인", "소기업 또는 소상공인", line)
            if changed == line:
                changed = re.sub(r"소기업\s*또는\s*소상공인", "소기업·소상공인", line)
        if changed != line:
            replacements[line] = changed
    return _changed_record(host, replacements, "near_miss:equivalent_size_scope")


def verify_comparison_amount_roles(host: Mapping[str, Any]) -> None:
    """A matching number is insufficient: estimated price and VAT-inclusive budget have distinct roles."""
    known = 0
    pattern = re.compile(r"(추정가격|배정예산(?:금액)?|사업예산|기초금액|총사업비|총사업금액)[^\d\n]{0,24}([\d,]+)원")
    for document in host["docs"]:
        for line in document["text"].splitlines():
            compact = re.sub(r"\s+", "", line)
            for match in pattern.finditer(compact):
                field = "입찰추정가격" if match[1] == "추정가격" else "배정예산금액"
                if field == "배정예산금액" and re.search(r"부가(?:가치)?세별도|VAT별도", compact, re.I):
                    raise PlantError("budget role excludes VAT and is not comparable without interpretation")
                if int(match[2].replace(",", "")) != host["meta"].get(field):
                    raise PlantError("host already has a document/meta amount-role mismatch")
                known += 1
    if not known:
        raise PlantError("no explicit comparable estimated-price or budget role")


def retarget_procurement(host: Mapping[str, Any], item: str, catalog: Any):
    """O7 on a closed, single-notice simple object; complex scopes are writer work."""
    meta, text = host["meta"], _all_text(host)
    if _EXCEPTION_RE.search(text) or _PRIVATE_BUYER_RE.search(text):
        raise PlantError("O7 exception or private buyer scope is unresolved")
    if (len(host["docs"]) != 1 or host.get("dropped_doc_counts") or small_quote_host(host)
            or meta.get("업무구분") not in {"물품", "물품(내자)", "일반용역"}
            or meta.get("면허업종제한목록") or product_class(host, catalog) != "general"):
        raise PlantError("O7 requires a complete simple general host without conflicting licenses")
    price = meta.get("입찰추정가격")
    if type(price) is not int or not 100_000_000 <= price < 1_000_000_000:
        raise PlantError("O7 price outside bounded band")
    if item == "v20" and (type(meta.get("배정예산금액")) is not int
                           or not price <= meta["배정예산금액"] < 2_000_000_000
                           or re.search(r"장기계속|다년|연평균|차수", text)):
        raise PlantError("O7 software business amount or duration is unresolved")
    if re.search(r"직접생산|대기업|상호출자|세부품명|품명번호|과업지시서|제안요청서|별첨|붙임", text):
        raise PlantError("O7 has an existing product, restriction or delegated scope")
    _, document, lines, section = _qualification_lines(host)
    pattern = re.compile(r"(?P<head>^.*?(?:건\s*명|사업\s*명|용역\s*명|품\s*명|과업\s*개요)\s*[:：]\s*)(?P<value>[^|\n]+)$")
    object_lines = [(i, pattern.match(line)) for i, line in enumerate(lines[:section["heading_line"]]) if pattern.match(line)]
    if not object_lines:
        raise PlantError("O7 object is not an explicit single-line field")
    if item not in {"v10", "v11", "v20"}:
        raise PlantError("unsupported O7 target")
    old_names = {match["value"] for _, match in object_lines}
    if len(old_names) != 1 or any(meta.get(field) for field in meta if re.search(r"품명|품목|규격|업종|등록", field)):
        raise PlantError("O7 product or registration fields cannot be synchronized")
    # Only a closed field-based object is rewritable. Unknown prose/table rows can
    # contain a second purchase or registration premise even without a product label.
    old_name = next(iter(old_names))
    generic = re.compile(
        r"(?:입찰에 부치는 사항|입찰참가자격|입찰서 제출|시행령 제\d+조의 자격을 갖춘 자|"
        r"부정당업자[^\n]{0,30}(?:아닌|않은) 자|전자입찰|추정가격\s*[:：].*|배정예산\s*[:：].*)$")
    object_indices = {i for i, _ in object_lines}
    task_replacements = {}
    for i, line in enumerate(lines):
        if i in object_indices or not line.strip():
            continue
        plain = _LEADING_MARKER_RE.sub("", line).strip()
        if generic.fullmatch(plain):
            continue
        if re.fullmatch(r"(?:구매규격|과업내용)\s*[:：]\s*" + re.escape(old_name) + r"\s*(?:납품|수행)", plain):
            task_replacements[line] = line  # Render after selecting the destination.
            continue
        if _is_size_line(line) and not re.search(r"품목|규격|업종|등록|과업|구매|납품", line):
            continue
        raise PlantError("O7 purchase specification, registration or task prose is not closed")
    if item == "v20":
        name, code = "소프트웨어 유지 및 기술지원 용역", None
    else:
        entries = [r for r in catalog.rows if not r["특이사항"].strip()
                   and (r["세부품명번호"].startswith("8") if meta["업무구분"] == "일반용역" else not r["세부품명번호"].startswith("8"))]
        if not entries:
            raise PlantError("no unconditional catalog destination")
        chosen = sorted(entries, key=lambda r: _rank("O7", host["id"], r["세부품명번호"]))[0]
        name, code = chosen["세부품명"], chosen["세부품명번호"]
    replacements = {match["value"]: name for _, match in object_lines}
    for line in task_replacements:
        replacements[line] = re.sub(r"(?:납품|수행)$", "수행" if item == "v20" or meta["업무구분"] == "일반용역" else "납품", line.replace(old_name, name))
    replacements = dict(sorted(replacements.items(), key=lambda pair: len(pair[0]), reverse=True))
    # Replacing repeated exact object names synchronizes headers and overview.
    record, provenance = _changed_record(host, replacements, "retarget_procurement:" + item)
    if item == "v20" and record["meta"].get("업무구분") != "일반용역":
        provenance["meta_changes"] = {"업무구분": {"from": record["meta"].get("업무구분"), "to": "일반용역"}}
        record["meta"]["업무구분"] = "일반용역"
    for field, value in record["meta"].items():
        if isinstance(value, str):
            for old, new in replacements.items():
                value = value.replace(old, new)
            record["meta"][field] = value
    doc = record["docs"][0]
    new_lines = doc["text"].split("\n")
    if code:
        new_lines.insert(object_lines[-1][0] + 1, f"세부품명번호: {code} / 품명: {name}")
        record["meta"]["세부품명번호"] = code
    doc["text"] = "\n".join(new_lines)
    if item == "v11" and has_size_gate(record):
        record, removed = delete_size_restriction(record)
        provenance["size_deletion"] = removed
    if item == "v11" and has_size_gate(record):
        raise PlantError("size requirement survives O7")
    provenance.update({"catalog_code": code, "object_name": name,
                       "planted_source_sha256": sha256_text(canonical_json(record))})
    return record, provenance


def _validate_written_premises(item: str, mode: str, clause: str, host: Mapping[str, Any]) -> None:
    """Insertion cannot repeal a surviving cumulative requirement in another clause."""
    text = _all_text(host)
    if item == "v21" and mode == "violation":
        share = re.compile(r"(?:지분|출자|분담|참여비율|구성비율)[^\n]{0,45}?(\d+(?:\.\d+)?)\s*%[^\n]{0,15}(?:이상|최소|하한)")
        if (re.search(r"지분|출자|분담|참여비율|구성비율", text)
                or host["meta"].get("공동도급구성방식") != "공동이행"):
            raise PlantError("written share cannot override an existing cumulative share premise")
        matches = list(share.finditer(clause))
        floor = 5 if host["meta"].get("적용계약법") == LOCAL_LAW else 10
        if not matches or any(not 0 < float(m[1]) < floor for m in matches):
            raise PlantError("written share does not state a below-floor participation path")
    if item == "v9" and mode == "near_miss":
        # Insertion-only intake cannot prove that equivalence binds each existing
        # material/component brand. Such hosts require a whole-specification edit.
        if (len(host["docs"]) != 1 or host.get("dropped_doc_counts") or
                re.search(r"브랜드|상표|제조사|제조업체|모델|제품명|품번|소재|규격|품목|내역|별첨|붙임", text, re.I)):
            raise PlantError("written equivalence cannot close existing component-specific obligations")
        if not re.search(r"동등(?:품|이상)|동급\s*이상", clause):
            raise PlantError("written near-miss lacks an explicit equivalent-product path")
    if item == "v4" and mode == "violation":
        if not _CLIENT_RE.search(clause) or _ANY_CLIENT_RE.search(clause) or _CLIENT_LAW_RE.search(clause):
            raise PlantError("written performance clause does not close the restricted-client premise")


def validate_written_clause(entry: Mapping[str, Any], host: Mapping[str, Any], task: Mapping[str, Any]) -> None:
    if requires_price_premise(entry.get("item", ""), "written") and not price_premise_is_valid(host):
        raise PlantError("price premise is placeholder or an unresolved unit amount")
    required = {"item", "mode", "host_id", "clause", "insert_after_line", "why_label", "label", "writer_model", "prompt_sha256"}
    if set(entry) != required:
        raise PlantError("written clause fields differ from contract")
    if (entry["item"] not in ITEMS or entry["mode"] not in {"violation", "near_miss"}
            or entry["item"] != task["item"] or entry["mode"] != task["mode"] or entry["host_id"] != host["id"]
            or entry["host_id"] != task["host_id"] or entry["label"] != (1 if entry["mode"] == "violation" else 0)
            or type(entry["label"]) is not int):
        raise PlantError("written clause target, host or label mismatch")
    if entry["prompt_sha256"] != sha256_text(WRITER_PROMPT) or entry["prompt_sha256"] != task["prompt_sha256"]:
        raise PlantError("writer prompt hash mismatch")
    if not all(isinstance(entry[k], str) and entry[k].strip() for k in ("clause", "why_label", "writer_model")):
        raise PlantError("empty clause, rationale or writer provenance")
    clause = entry["clause"].strip()
    sentences = [s for s in re.split(r"(?<=[.!?。])(?:\s+|(?=[가-힣]))|\n+", clause) if s.strip()]
    if not 1 <= len(sentences) <= 3 or len(clause) > 1500:
        raise PlantError("written clause must contain one to three sentences")
    text = _all_text(host)
    _validate_written_premises(entry["item"], entry["mode"], clause, host)
    normalized = re.sub(r"\s+", "", clause)
    if normalized in re.sub(r"\s+", "", text):
        raise PlantError("writer copied host wording")
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            plain = re.sub(r"\s+", "", strip_leading_marker(line))
        except PlantError:
            # A list marker or table separator contains no host sentence to copy.
            continue
        if len(plain) >= 16 and plain in normalized:
            raise PlantError("writer copied a host sentence")
    tokens = set(_TOKEN_RE.findall(clause))
    if not tokens <= set(_TOKEN_RE.findall(text)):
        raise PlantError("new or changed anonymization token")
    if set(_REGION_SYMBOL_RE.findall(clause)) - set(_REGION_SYMBOL_RE.findall(text)):
        raise PlantError("new anonymization symbol")
    for law in (LOCAL_LAW, "국가계약법"):
        if law in clause and host["meta"].get("적용계약법") not in (None, law):
            raise PlantError("clause contradicts contract law meta")
    for field in ("입찰추정가격", "배정예산금액"):
        for match in re.finditer(("추정가격" if field == "입찰추정가격" else "배정예산") + r"\s*[:：은는]?\s*([\d,.]+\s*(?:억|천만|백만|만)?\s*원)", clause):
            amount = stated_won(match[1])
            if amount is None:
                amount = int(re.sub(r"[^\d]", "", match[1]))
            if amount != host["meta"].get(field):
                raise PlantError("clause contradicts amount meta")
    for method in ("수의계약", "일반경쟁", "제한경쟁"):
        if method in clause and method not in str(host["meta"].get("계약방법", "")):
            raise PlantError("clause contradicts contract method meta")
    for code in _LICENSE_CODE_RE.findall(clause):
        if code not in str(host["meta"].get("면허업종제한목록", "")):
            raise PlantError("clause contradicts license meta")
    for province in _PROVINCE_RE.findall(clause):
        if province not in text and province not in str(host["meta"].get("제한지역코드목록", "")):
            raise PlantError("clause introduces an unsupported region")
    for date in re.findall(r"20\d{2}[./-]\s*\d{1,2}[./-]\s*\d{1,2}", clause):
        digits = re.sub(r"\D", "", date)
        if date not in text and digits not in {str(v) for v in host["meta"].values()}:
            raise PlantError("clause introduces an unverified date")
    if type(entry["insert_after_line"]) is not int or entry["insert_after_line"] not in task["insert_after_line_candidates"]:
        raise PlantError("insertion position is not a task candidate")
    _, notice = _notice_document(host)
    if not 0 <= entry["insert_after_line"] < len(notice["text"].split("\n")):
        raise PlantError("insertion position is outside the host")
    if sha256_text(canonical_json(host)) != task["host_sha256"]:
        raise PlantError("writer host changed")


def plant_written_clause(entry: Mapping[str, Any], host: Mapping[str, Any], task: Mapping[str, Any]):
    validate_written_clause(entry, host, task)
    record = copy.deepcopy(dict(host))
    _, doc = _notice_document(record)
    lines = doc["text"].split("\n")
    lines.insert(entry["insert_after_line"] + 1, entry["clause"].strip())
    doc["text"] = "\n".join(lines)
    return {"record": record, "writer": dict(entry), "split": task["split"],
            "status": "structurally_valid_semantic_review_required", "eligible_for_scoring": False}


def _v10_row(host, record, provenance, item, family, *, near=False):
    excluded = set(EDIT_AFFECTED[item]) | {"v24"}
    return {"plant_id": _rank("v10", item, host["id"], str(near))[:16], "host_id": host["id"],
            "family_id": family, "source_id": None, "split": v10_split(family), "kind": "edited",
            "operation": provenance.get("operation", "near_miss:insert"), "target_item": item,
            "extra_targets": [], "near_miss_items": [item] if near else [],
            "excluded_items": sorted(excluded), "expected_zero_items": [v for v in ITEMS if v not in excluded],
            "zero_cells_vetted": False, "provenance": provenance, "record": record}


def writer_product_class(host: Mapping[str, Any], catalog: Any) -> str | None:
    """Catalog applicability is independent of an existing production-certificate clause."""
    try:
        from tools.independent_gold import catalog_facts
    except ModuleNotFoundError:
        import catalog_facts
    if catalog_facts.resolve_record(host, catalog)["summary"]["decision"] == "applicable":
        return "competitive"
    return product_class(host, catalog)


def writer_host_is_eligible(host: Mapping[str, Any], item: str, mode: str,
                            product: str | None) -> bool:
    """Necessary premises for insertion-only writing, using the deterministic edit gates.

    These are task selection conditions, not semantic certification of a future clause.
    Absence violations cannot be authored by inserting text over an existing requirement.
    """
    meta, text = host["meta"], _all_text(host)
    price = meta.get("입찰추정가격")
    if requires_price_premise(item) and not price_premise_is_valid(host):
        return False
    if (mode not in {"violation", "near_miss"} or type(price) is not int or price <= 0
            or host.get("dropped_doc_counts") or meta.get("적용계약법") not in {LOCAL_LAW, "국가계약법"}):
        return False
    try:
        _qualification_lines(host)
    except PlantError:
        return False
    targets = transplant_host_targets(host, product)
    goods_services = meta.get("업무구분") in {"물품", "물품(내자)", "일반용역"}
    if item == "v1":
        return host_is_eligible(item, "", host)
    if item in {"v2", "v3", "v4", "v7", "v8", "v9", "v22"}:
        return (item in targets and (item not in {"v2", "v3", "v7"} or goods_services)
                and (item not in {"v2", "v7", "v8"} or not small_quote_host(host)))
    if item in {"v5", "v6"}:
        return goods_services and host_is_eligible(item, "", host) and not small_quote_host(host)
    if item in {"v10", "v11", "v13"}:
        if product != "competitive" or small_quote_host(host) or _EXCEPTION_RE.search(text):
            return False
        if mode == "violation" and item == "v10":
            return not any(_is_direct_production_line(line) for line in text.splitlines())
        if mode == "violation" and item == "v11":
            return not has_size_gate(host)
        return True
    if item == "v12":
        return goods_services and "v12" in targets
    if item in {"v14", "v15", "v16", "v17", "v18"}:
        if not goods_services or product != "general" or _EXCEPTION_RE.search(text) or _PRIVATE_BUYER_RE.search(text):
            return False
        operation = {"v15": "narrow", "v16": "delete", "v17": "broaden", "v18": "delete"}.get(item)
        in_band = price >= NOTICE_THRESHOLD_WON if item == "v14" else size_edit_target(operation, product, price) == item
        if not in_band or (item == "v18" and small_quote_host(host)):
            return False
        return not (mode == "violation" and item in ABSENCE_ITEMS and has_size_gate(host))
    if item == "v19":
        return meta.get("업무구분") in {"물품", "물품(내자)"} and "v19" in targets
    if item == "v20":
        # Same procurement-object scope as delete_large_enterprise_limit, without
        # requiring a clause that an insertion-only writer would have to delete.
        _, notice = _notice_document(host)
        lines = [line for line in notice["text"].splitlines() if line.strip()]
        object_text = "\n".join(line for line in lines[:3] + [line for line in lines if _OBJECT_LINE_RE.search(line)]
                                if not _LARGE_ENTERPRISE_RE.search(line) and "사업자" not in line)
        budget = meta.get("배정예산금액")
        # Generic maintenance and an electronic bidding system are not an SW object.
        return bool(re.search(r"소프트웨어|S/?W\b|정보시스템|홈페이지|앱\s*개발|전산", object_text, re.I)
                    and meta.get("업무구분") == "일반용역" and type(budget) is int
                    and price <= budget < 2_000_000_000 and not re.search(r"장기계속|다년|연평균|차수", text)
                    and not (mode == "violation" and _LARGE_ENTERPRISE_GATE_RE.search(text)))
    if item == "v21":
        joint = "공동이행" in str(meta.get("공동도급구성방식"))
        return joint and not re.search(r"공동(?:수급|도급)[^\n]{0,20}(?:불허|불가|허용하지)|혼합|분담이행", text)
    if item == "v23":
        try:
            if mode == "near_miss":
                legal_near_miss(host, item, product)
            else:
                insert_briefing(host)
        except (PlantError, ValueError, TypeError):
            return False
        return True
    return item == "v24"


def build_pool_v10(*, per_item: int, near_per_item: int = 40, writer_per_mode: int = 40):
    try:
        from tools.independent_gold import catalog_facts
    except ModuleNotFoundError:
        import catalog_facts
    # Read development only, before decoding JSON. Never decode prior sealed rows.
    known_dev_families, known_dev_wording = set(), set()
    for version in ("v8", "v9"):
        path = ROOT / "runs" / "cell_label_20260919" / ("pool_edits_" + version) / "plants.jsonl"
        if not path.exists():
            continue
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                if not re.search(r'"split"\s*:\s*"dev"', line):
                    continue
                prior = json.loads(line)
                known_dev_families.add(prior["family_id"])
                word = wording_key(prior)
                if word:
                    known_dev_wording.add(word)
                for spans in evidence_spans(prior).values():
                    known_dev_wording.update(sha256_text(_normalized_wording([span["text"]])) for span in spans)
    split_for = lambda family: "dev" if family in known_dev_families else v10_split(family)
    # Regenerate from natural sources with all v9 filters.
    built = build_pool_edits(per_item=per_item)
    rows = built["rows"]
    for row in rows:
        row["split"] = split_for(row["family_id"])
        row["near_miss_items"] = []
    used = {row["host_id"] for row in rows}
    families, catalog = _family_lookup(), catalog_facts.CatalogIndex.load()
    near_counts, o7_counts, rejected = Counter(), Counter(), Counter()
    writer_counts, tasks = Counter(), []
    rubric = pathlib.Path(__file__).with_name("rubric_v1.md").read_text(encoding="utf-8")
    sections = {m[1]: m[0] for m in re.finditer(r"^### (v\d+)\b.*?(?=^### |\Z)", rubric.split("## 출력")[0], re.M | re.S)}
    for host in _pool_records():
        family = families.get(host["id"], host["id"])
        split = split_for(family)
        try:
            _, doc, lines, section = _qualification_lines(host)
        except PlantError:
            continue
        if host.get("dropped_doc_counts"):
            continue
        product = product_class(host, catalog)
        writer_product = writer_product_class(host, catalog)
        # Independent authoring bundles, never authored clauses. Both split quotas are explicit.
        for item in ITEMS:
            for mode in ("violation", "near_miss"):
                count_key = (item, mode, split)
                if writer_counts[count_key] >= writer_per_mode:
                    continue
                if not writer_host_is_eligible(host, item, mode, writer_product):
                    continue
                tasks.append({"item": item, "mode": mode, "label": int(mode == "violation"),
                              "host_id": host["id"], "family_id": family, "split": split,
                              "host_sha256": sha256_text(canonical_json(host)), "meta": host["meta"],
                              "item_definition": sections[item], "legal_conditions": sections[item],
                              "host_excerpt": [{"line": i, "text": line} for i, line in enumerate(lines)
                                               if i < 12 or section["heading_line"] <= i <= section["end_line"]],
                              "insert_after_line_candidates": [section["end_line"] - 1],
                              "prompt_sha256": sha256_text(WRITER_PROMPT),
                              "requires_premise_review": True,
                              "o7_followup": False, "premise_filter_version": 2})
                writer_counts[count_key] += 1
        if host["id"] in used:
            continue
        # Scarce O7 and near-miss configurations are allocated before broad insertions.
        options = [(item, False) for item in sorted(("v10", "v11", "v20"),
                   key=lambda item: (o7_counts[item], _rank("O7-allocation", host["id"], item)))
                   if o7_counts[item] < near_per_item]
        options += [(item, True) for item in ("v23", "v21", "v24", "v19", "v10", "v11", "v12", "v13", "v16", "v15", "v18", "v17", "v5", "v22", "v2", "v3") if near_counts[item] < near_per_item]
        for item, near in options:
            try:
                record, provenance = legal_near_miss(host, item, product) if near else retarget_procurement(host, item, catalog)
            except (PlantError, ValueError) as exc:
                rejected[f"{item}:{'near_miss' if near else 'O7'}:{exc}"] += 1
                continue
            row = _v10_row(host, record, provenance, item, family, near=near)
            row["split"] = split
            rows.append(row)
            (near_counts if near else o7_counts)[item] += 1
            used.add(host["id"])
            break
    # Include all target wording, including templates and second edits, in split isolation.
    for row in rows:
        row["evidence_spans"] = evidence_spans(row)
        wording = set()
        for spans in row["evidence_spans"].values():
            wording.update(sha256_text(_normalized_wording([span["text"]])) for span in spans)
        primary = wording_key(row)
        if primary:
            wording.add(primary)
        if row.get("near_miss_items"):
            provenance = row["provenance"]
            phrases = [provenance.get("planted_body", "")]
            phrases += [c["to"] for c in provenance.get("changed_lines", [])]
            wording.update(sha256_text(_normalized_wording([p])) for p in phrases if p)
        row["wording_hashes"] = sorted(wording)
    dev_wording = known_dev_wording | {w for row in rows if row["split"] == "dev" for w in row["wording_hashes"]}
    # Quarantine the whole family if any of its target wording was seen in dev.
    seen_families = {row["family_id"] for row in rows if row["split"] == "holdout" and set(row["wording_hashes"]) & dev_wording}
    for row in rows:
        if row["split"] == "holdout" and row["family_id"] in seen_families:
            row["split"] = "holdout_seen_wording"
    dev_families = {row["family_id"] for row in rows if row["split"] == "dev"}
    holdout_families = {row["family_id"] for row in rows if row["split"] == "holdout"}
    holdout_wording = {w for row in rows if row["split"] == "holdout" for w in row["wording_hashes"]}
    manifest = {"schema_version": "dacon.independent.cell_plant.v10", "seed": V10_SEED,
                "purpose": "constructed_synthetic_not_organizer_gold", "v9_filters_retained": True,
                "prior_dev_families_forced_dev": len(known_dev_families),
                "prior_dev_wording_excluded_from_holdout": len(known_dev_wording),
                "prior_dev_holdout_family_overlap": len(known_dev_families & holdout_families),
                "rows_by_split": dict(Counter(r["split"] for r in rows)),
                "violation_rows_by_item": dict(Counter(r["target_item"] for r in rows if not r["near_miss_items"])),
                "near_miss_rows_by_item": dict(near_counts), "o7_rows_by_item": dict(o7_counts),
                "writer_tasks_by_item_mode_split": {":".join(k): v for k, v in sorted(writer_counts.items())},
                "writer_tasks": len(tasks), "writer_prompt_sha256": sha256_text(WRITER_PROMPT),
                "dev_holdout_family_overlap": len(dev_families & holdout_families),
                "dev_holdout_wording_overlap": len(dev_wording & holdout_wording),
                "evidence_cells": sum(len(r["evidence_spans"]) for r in rows),
                "rows_sha256": sha256_text(canonical_json(rows)), "v9_generation": built["manifest"],
                "rejected_attempts": dict(rejected), "holdout_scored": False}
    return {"rows": rows, "manifest": manifest, "writer_tasks": tasks}


def intake_written(args: argparse.Namespace) -> dict[str, Any]:
    """Validate and plant writer outputs in a review quarantine, never silently assign gold."""
    if not args.writer_tasks:
        raise PlantError("written intake requires --writer-tasks")
    tasks = {}
    with args.writer_tasks.open(encoding="utf-8") as handle:
        for line in handle:
            task = json.loads(line)
            tasks[(task["host_id"], task["item"], task["mode"])] = task
    with args.written_clauses.open(encoding="utf-8") as handle:
        entries = [json.loads(line) for line in handle if line.strip()]
    hosts = {entry["host_id"] for entry in entries}
    source = {host["id"]: host for host in _pool_records() if host["id"] in hosts}
    planted, seen = [], set()
    for entry in entries:
        key = (entry["host_id"], entry["item"], entry["mode"])
        if key in seen or key not in tasks or entry["host_id"] not in source:
            raise PlantError("duplicate, unassigned or missing writer host")
        seen.add(key)
        planted.append(plant_written_clause(entry, source[entry["host_id"]], tasks[key]))
    output = args.output_dir.resolve()
    _new_bytes(output / "written_review_pending.jsonl", "".join(canonical_json(row) + "\n" for row in planted).encode("utf-8"))
    result = {"structurally_valid": len(planted), "semantic_review_required": len(planted), "admitted_to_scoring": 0}
    _new_bytes(output / "manifest.json", canonical_json(result).encode("utf-8"))
    return result


def build_pool_supplement(prior: pathlib.Path, *, per_item: int = 40) -> dict[str, Any]:
    """Regenerate repaired strata on prior dev hosts; read sealed bookkeeping only.

    No holdout source records are decoded, generated, or exported by this dev
    supplement. Existing family assignments win over a fresh hash assignment.
    """
    try:
        from tools.independent_gold import catalog_facts
    except ModuleNotFoundError:
        import catalog_facts
    dev_hosts, sealed_families, sealed_words = {}, set(), set()
    with prior.open(encoding="utf-8") as handle:
        for line in handle:
            envelope = plant_envelope(line)
            if envelope.get("split") == "dev":
                dev_hosts[envelope["host_id"]] = envelope["family_id"]
            else:
                sealed_families.add(envelope["family_id"])
                sealed_words.update(envelope.get("wording_hashes", []))
    catalog = catalog_facts.CatalogIndex.load()
    hosts, sources, rows, counts, rejected = [], [], [], Counter(), Counter()
    with gzip.open(POOL_INPUT, "rt", encoding="utf-8") as handle:
        for line in handle:
            identity = re.search(r'"id"\s*:\s*"([^"\\]+)"', line)
            if not identity or identity[1] not in dev_hosts or dev_hosts[identity[1]] in sealed_families:
                continue
            host = json.loads(line)
            if host["id"] != identity[1]:
                raise PlantError("source identity envelope mismatch")
            hosts.append(host)

    def accept(host, record, provenance, item, near=False):
        operation = provenance["operation"]
        key = operation + ":" + item
        if counts[key] >= per_item:
            return
        row = _v10_row(host, record, provenance, item, dev_hosts[host["id"]], near=near)
        row["split"] = "dev"
        row["plant_id"] = _rank("v10_supp1", item, host["id"], canonical_json(record))[:20]
        record["id"] = host["id"] + "--supp1-" + row["plant_id"]
        provenance["planted_source_sha256"] = sha256_text(canonical_json(record))
        row["evidence_spans"] = evidence_spans(row)
        phrases = [provenance.get("planted_body", "")]
        phrases += [change["to"] for change in provenance.get("changed_lines", [])]
        phrases += [span["text"] for spans in row["evidence_spans"].values() for span in spans]
        row["wording_hashes"] = sorted({sha256_text(_normalized_wording([phrase])) for phrase in phrases if phrase})
        if set(row["wording_hashes"]) & sealed_words:
            rejected[key + ":sealed_wording_collision"] += 1
            return
        row["construction_version"] = "v10_supp1_premise_checked_not_blind_vetted"
        rows.append(row)
        counts[key] += 1

    for host in hosts:
        product = product_class(host, catalog)
        sources.extend(source for source in bank_clauses(host, product) if source["target_item"] == "v4")
        options = [(item, False, lambda item=item: retarget_procurement(host, item, catalog))
                   for item in ("v10", "v11", "v20") if counts["retarget_procurement:" + item + ":" + item] < per_item]
        if (price_premise_is_valid(host) and product == "general"
                and type(host["meta"].get("입찰추정가격")) is int
                and host["meta"]["입찰추정가격"] < 100_000_000
                and not _EXCEPTION_RE.search(_all_text(host)) and not _PRIVATE_BUYER_RE.search(_all_text(host))
                and counts["broaden:v17"] < per_item):
            options.append(("v17", False, lambda: rewrite_size_scope(host, "broaden")))
        if counts["shift_price:v5:v5"] < per_item:
            options.append(("v5", True, lambda: legal_near_miss(host, "v5", product)))
        for item, near, function in options:
            try:
                record, provenance = function()
                if item == "v17":
                    provenance["operation"] = "broaden"
                accept(host, record, provenance, item, near)
            except PlantError as exc:
                rejected[item + ":" + str(exc)] += 1
    used_sources = Counter()
    for host in hosts:
        if counts["transplant:v4:v4"] >= per_item:
            break
        if "v4" not in transplant_host_targets(host, None):
            continue
        for source in sorted(sources, key=lambda source: _rank("supp1-v4", host["id"], source["source_id"])):
            if source["source_id"] == host["id"] or used_sources[source["clause"]] >= MAX_USES_BY_ITEM["v4"]:
                continue
            try:
                record, provenance = plant_clause(host, source["clause"])
                provenance.update(operation="transplant:v4", source_id=source["source_id"])
                previous = len(rows)
                accept(host, record, provenance, "v4")
                if len(rows) > previous:
                    used_sources[source["clause"]] += 1
                    break
            except PlantError as exc:
                rejected["v4:" + str(exc)] += 1
    manifest = {"schema_version": "dacon.independent.cell_plant.v10_supp1", "seed": V10_SEED,
                "purpose": "constructed_synthetic_dev_diagnostic_not_organizer_gold_not_blind_vetted",
                "prior_pool": str(prior), "split_policy": "prior dev families only; sealed family/wording collision rejected",
                "holdout_content_decoded": False, "holdout_generated": False, "holdout_scored": False,
                "rows_by_split": {"dev": len(rows)}, "rows_by_operation_item": dict(counts),
                "rejected_attempts": dict(rejected), "new_written_rows": 0,
                "written_status": "intake guards repaired; requires independent authors and semantic review",
                "family_overlap": 0, "wording_overlap": 0, "rows_sha256": sha256_text(canonical_json(rows))}
    return {"rows": rows, "manifest": manifest}


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=pathlib.Path, required=True)
    parser.add_argument("--hosts-per-source", type=int, default=3)
    parser.add_argument("--hosts-per-single-source-item", type=int, default=6)
    parser.add_argument("--controls", type=int, default=30)
    parser.add_argument("--max-plants-per-host", type=int, default=4)
    parser.add_argument("--prefer-dev-plants", type=pathlib.Path)
    parser.add_argument("--prefer-dev-key", type=pathlib.Path)
    parser.add_argument(
        "--pool-edits-per-item", type=int, default=None,
        help="edit natural pool notices (delete/rewrite size clause, meta price) instead of planting into dev hosts",
    )
    parser.add_argument("--v10", action="store_true")
    parser.add_argument("--supplement-of", type=pathlib.Path, help="prior plants.jsonl; repair strata on dev hosts only")
    parser.add_argument("--near-per-item", type=int, default=40)
    parser.add_argument("--writer-per-mode", type=int, default=40)
    parser.add_argument("--written-clauses", type=pathlib.Path)
    parser.add_argument("--writer-tasks", type=pathlib.Path)
    args = parser.parse_args(argv)
    output = args.output_dir.resolve()
    if output.exists() or ROOT / "runs" not in output.parents:
        print("plant output must be fresh under repository runs/", file=sys.stderr)
        return 2
    try:
        if args.written_clauses:
            print(canonical_json(intake_written(args)))
            return 0
        preferred = []
        if bool(args.prefer_dev_plants) != bool(args.prefer_dev_key):
            raise PlantError("preferred dev plants and key must be supplied together")
        if args.prefer_dev_key:
            key = json.loads(args.prefer_dev_key.read_text(encoding="utf-8"))
            if any(entry["split"] != "dev" for entry in key.values()):
                raise PlantError("preferred key must contain only dev")
            ids = {entry["plant_id"] for entry in key.values()}
            with args.prefer_dev_plants.open(encoding="utf-8") as handle:
                for line in handle:
                    # Inspect only the split marker before decoding a row; never decode v8 holdout.
                    if not re.search(r'"split"\s*:\s*"dev"', line):
                        continue
                    row = json.loads(line)
                    if row["plant_id"] in ids:
                        preferred.append(row)
        built = build_pool_supplement(args.supplement_of, per_item=args.pool_edits_per_item or 40) if args.supplement_of else build_pool_v10(per_item=args.pool_edits_per_item or 200, near_per_item=args.near_per_item, writer_per_mode=args.writer_per_mode) if args.v10 else build_pool_edits(per_item=args.pool_edits_per_item, preferred_dev=preferred) if args.pool_edits_per_item else build_plants(
            hosts_per_source=args.hosts_per_source,
            hosts_per_single_source_item=args.hosts_per_single_source_item,
            controls=args.controls, max_plants_per_host=args.max_plants_per_host,
        )
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f"planting refused: {exc}", file=sys.stderr)
        return 2
    if args.v10:
        _new_bytes(output / "WRITER_PROMPT.md", WRITER_PROMPT.encode("utf-8"))
        _new_bytes(output / "written_clauses.jsonl", b"")
        _new_bytes(output / "writer_tasks.jsonl", "".join(canonical_json(t) + "\n" for t in built["writer_tasks"]).encode("utf-8"))
        _new_bytes(output / "HOLDOUT_LEDGER.md", "# 봉인 holdout 집계 채점 원장\n\n| UTC 날짜 | 라운드 | 소스 fingerprint | 목적 |\n|---|---|---|---|\n".encode("utf-8"))
    body = "".join(canonical_json(row) + "\n" for row in built["rows"])
    _new_bytes(output / "plants.jsonl", body.encode("utf-8"))
    _new_bytes(output / "manifest.json", (canonical_json(built["manifest"]) + "\n").encode("utf-8"))
    if not args.v10:
        _new_bytes(output / "review.md", review_markdown(built["rows"]).encode("utf-8"))
    print(json.dumps({k: v for k, v in built["manifest"].items() if k != "skipped"}, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
