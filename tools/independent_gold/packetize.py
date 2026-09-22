"""Source-grounded, coordinate-preserving retrieval for independent annotation.

This module deliberately has no dependency on the competition runtime.  It
turns the organizer-provided record into a compact set of verbatim source
windows.  Every window carries document coordinates and hashes so a later
annotator can quote the source without losing provenance.

The packet is a *retrieval aid*, not proof that an absent phrase is absent.
``full_source_visible`` is therefore explicit and callers must not decide an
absence item from a truncated packet alone.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import math
import pathlib
import re
import statistics
import sys
from collections import defaultdict
from typing import Any, Iterable, Iterator, Mapping, Sequence


ROOT = pathlib.Path(__file__).resolve().parents[2]
ITEMS = tuple(f"v{i}" for i in range(1, 25))
ABSENCE_ITEMS = frozenset({"v10", "v11", "v16", "v18", "v20"})
SCHEMA_VERSION = "dacon.independent.source_packet.v1"
DEFAULT_MAX_CHARS = 16_000
ITEM_GROUPS: dict[str, tuple[str, ...]] = {
    "v1-4": ("v1", "v2", "v3", "v4"),
    "v5-8": ("v5", "v6", "v7", "v8"),
    "v9": ("v9",),
    "v10-13": ("v10", "v11", "v12", "v13"),
    "v14-19": ("v14", "v15", "v16", "v17", "v18", "v19"),
    "v20": ("v20",),
    "v21-23": ("v21", "v22", "v23"),
    "v24": ("v24",),
}
GROUP_BUDGET_CANDIDATES = (4_000, 4_500, 5_000, 5_500, 6_000)
# Smallest passing values on the explicit grid above for organizer dev evidence.
# This is a reproducible retrieval gate, not a claim that truncated text proves
# absence or that the development sample represents future data.
DEV_EVIDENCE_SAFE_GROUP_MAX_CHARS = {
    "v1-4": 6_000,
    "v5-8": 4_000,
    "v9": 4_000,
    "v10-13": 4_000,
    "v14-19": 4_000,
    "v20": 4_000,
    "v21-23": 4_000,
    "v24": 6_000,
}


# Broad retrieval vocabulary, derived only from the organizer item names and
# the organizer's positive dev evidence.  Terms intentionally overlap across
# related items: retrieval should favor recall and leave legal judgment to the
# independent annotators.
_KEYWORD_GROUPS: tuple[tuple[tuple[str, ...], tuple[str, ...], int], ...] = (
    (("입찰참가자격", "참가자격", "참여 가능", "참가 가능", "대학", "산학협력단",
      "특정기관", "기관만", "업체만", "법인만", "단체만"), ("v1",), 4),
    (("수행실적", "이행실적", "납품실적", "용역실적", "실적증명", "실적을",
      "실적이", "실적 보유", "실적을 보유", "실적제한"),
     ("v2", "v3", "v4", "v8"), 4),
    (("최근 3년", "최근 5년", "최근 7년", "최근 10년", "단일 건", "단일건",
      "단일 계약", "유사사업", "동종업종"), ("v2", "v3", "v4", "v8"), 3),
    (("주된 영업소", "본점 소재지", "본점소재지", "본사 소재지", "본사의 소재지",
      "관할구역", "지역제한", "제한지역", "소재지를", "소재지가"),
     ("v5", "v6", "v7", "v8", "v24"), 4),
    (("서울특별시", "부산광역시", "대구광역시", "인천광역시", "광주광역시",
      "대전광역시", "울산광역시", "세종특별자치시", "경기도", "강원특별자치도",
      "충청북도", "충청남도", "전북특별자치도", "전라남도", "경상북도",
      "경상남도", "제주특별자치도", "[지역:"), ("v5", "v6", "v7", "v8", "v24"), 2),
    (("제조사·모델명", "제조사", "모델명", "상표", "브랜드", "특정 모델",
      "Chipset", "Superchip", "Matrice", "배터리일 것", "제품명", "품번",
      "Part Number", "동등 이상 제품", "동등이상 제품"), ("v9",), 4),
    (("직접생산확인증명서", "직접생산 확인증명서", "직접생산확인", "직접생산 확인",
      "중소기업자간 경쟁제품", "중기간 경쟁제품"), ("v10", "v12", "v13"), 4),
    (("소기업·소상공인확인서", "소기업·소상공인 확인서", "소기업, 소상공인확인서",
      "소기업확인서", "소상공인확인서", "소기업자", "소상공인으로서"),
     ("v11", "v13", "v15", "v17", "v18"), 4),
    (("중소기업확인서", "중소기업 확인서", "중소기업자로서", "중소기업자 또는",
      "중소기업 또는", "중소기업기본법", "중소기업제품 공공구매"),
     ("v11", "v13", "v14", "v15", "v16", "v17", "v18"), 3),
    (("물품공급·기술지원 확약서", "물품공급·기술지원협약서", "물품공급 확약서",
      "물품공급확약서", "기술지원 확약서", "기술지원확약서", "확약서를",
      "공급확약서", "공급사", "기술지원사"), ("v19",), 4),
    (("소프트웨어사업", "소프트웨어 사업", "소프트웨어진흥법", "중소 소프트웨어사업자",
      "대기업 참여", "대기업인 소프트웨어", "상호출자제한", "사업 참여 제한",
      "SW사업", "정보화사업"), ("v20",), 4),
    (("공동수급", "공동이행", "분담이행", "공동도급", "공동협정서", "지분율",
      "지분참여", "출자비율", "최소 지분"), ("v21",), 4),
    (("현장설명회", "사업설명회", "과업설명회", "제안요청 설명회", "제안요청서 설명",
      "설명회 참석", "설명회에 참석", "설명회 불참", "설명회 미참석"),
     ("v22", "v23"), 4),
    (("설명회", "일시·장소", "일시 :", "장소 :"), ("v22", "v23"), 2),
    (("추정가격", "배정예산", "사업예산", "용역금액", "기초금액", "부가가치세",
      "계약방법", "일반경쟁", "제한경쟁", "수의계약", "협상에 의한 계약",
      "면허", "업종제한", "업종 제한", "낙찰방법", "낙찰하한율"), ("v24",), 3),
)


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_object(value: Any) -> str:
    rendered = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return sha256_text(rendered)


def _keyword_index() -> tuple[re.Pattern[str], dict[str, tuple[tuple[str, int], ...]]]:
    index: dict[str, set[tuple[str, int]]] = defaultdict(set)
    spelling: dict[str, str] = {}
    for terms, items, priority in _KEYWORD_GROUPS:
        for term in terms:
            folded = term.casefold()
            spelling.setdefault(folded, term)
            for item in items:
                index[folded].add((item, priority))
    alternatives = sorted(spelling.values(), key=lambda value: (-len(value), value.casefold()))
    pattern = re.compile("|".join(re.escape(value) for value in alternatives), re.IGNORECASE)
    frozen = {key: tuple(sorted(value)) for key, value in index.items()}
    return pattern, frozen


KEYWORD_PATTERN, KEYWORD_INDEX = _keyword_index()


def read_jsonl_gz(path: pathlib.Path) -> Iterator[dict[str, Any]]:
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            row = json.loads(line)
            if not isinstance(row, dict) or not row.get("id"):
                raise ValueError(f"{path}:{line_number}: invalid record")
            yield row


def _line_expanded_window(text: str, start: int, end: int, before: int, after: int) -> tuple[int, int]:
    """Expand around a match, preferring nearby line boundaries."""

    lo = max(0, start - before)
    hi = min(len(text), end + after)
    previous_newline = text.rfind("\n", max(0, lo - 160), lo)
    if previous_newline >= 0:
        lo = previous_newline + 1
    next_newline = text.find("\n", hi, min(len(text), hi + 240))
    if next_newline >= 0:
        hi = next_newline
    return lo, hi


def _context_bonus(context: str, items: set[str]) -> int:
    bonus = 0
    qualification = ("입찰참가", "참가자격", "업체", "이어야", "하여야", "제한")
    bonus += min(3, sum(token in context for token in qualification)) * 8
    if re.search(r"\d[\d,]*(?:\.\d+)?\s*(?:억|천만|백만|만원|원|%)", context):
        bonus += 10
    if items & {"v2", "v3", "v4", "v8"} and ("최근" in context or "단일" in context):
        bonus += 10
    if items & {"v22", "v23"} and ("일시" in context or "장소" in context):
        bonus += 8
    return bonus


def _document_bonus(doc_type: str, items: set[str]) -> int:
    bonus = 0
    if doc_type == "공고문":
        bonus += 8
    if "v9" in items and doc_type in {"규격서", "과업지시서", "제안요청서"}:
        bonus += 18
    if items & {"v22", "v23"} and doc_type in {"공고문", "제안요청서"}:
        bonus += 12
    return bonus


def _anchor_candidates(
    record: Mapping[str, Any],
    *,
    before: int,
    after: int,
    target_items: frozenset[str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    documents: list[dict[str, Any]] = []
    candidates: list[dict[str, Any]] = []
    for doc_index, raw_doc in enumerate(record.get("docs") or []):
        text = str(raw_doc.get("text") or "")
        doc = {
            "doc_index": doc_index,
            "doc_id": str(raw_doc.get("doc_id") or f"D{doc_index}"),
            "doc_type": str(raw_doc.get("type") or "unknown"),
            "text": text,
            "sha256": sha256_text(text),
        }
        documents.append(doc)
        for match in KEYWORD_PATTERN.finditer(text):
            specs = KEYWORD_INDEX.get(match.group(0).casefold(), ())
            if not specs:
                continue
            items = {item for item, _ in specs} & target_items
            if not items:
                continue
            priority = max(priority for _, priority in specs)
            lo, hi = _line_expanded_window(text, match.start(), match.end(), before, after)
            context = text[lo:hi]
            score = (
                priority * 100
                + min(len(match.group(0)), 30)
                + _context_bonus(context, items)
                + _document_bonus(doc["doc_type"], items)
            )
            candidates.append(
                {
                    "doc_index": doc_index,
                    "start": lo,
                    "end": hi,
                    "items": set(items),
                    "score": score,
                    "kind": "anchor",
                    "anchors": [
                        {
                            "keyword": match.group(0),
                            "start": match.start(),
                            "end": match.end(),
                            "items": sorted(items),
                            "priority": priority,
                        }
                    ],
                }
            )

        # Structural fallbacks make empty/sparse notices inspectable, but they
        # deliberately claim no item coverage and rank below semantic anchors.
        if text:
            fallback_end = min(len(text), max(480, min(before + after, 900)))
            candidates.append(
                {
                    "doc_index": doc_index,
                    "start": 0,
                    "end": fallback_end,
                    "items": set(),
                    "score": 20 + (8 if doc["doc_type"] == "공고문" else 0),
                    "kind": "document_head",
                    "anchors": [],
                }
            )
    return documents, candidates


def _merge_candidates(
    candidates: Sequence[dict[str, Any]],
    *,
    merge_gap: int,
    max_segment_chars: int,
) -> list[dict[str, Any]]:
    ordered = sorted(candidates, key=lambda row: (row["doc_index"], row["start"], row["end"]))
    merged: list[dict[str, Any]] = []
    for candidate in ordered:
        if merged:
            previous = merged[-1]
            union_end = max(previous["end"], candidate["end"])
            can_merge = (
                previous["doc_index"] == candidate["doc_index"]
                and candidate["start"] <= previous["end"] + merge_gap
                and union_end - previous["start"] <= max_segment_chars
            )
            if can_merge:
                previous["end"] = union_end
                previous["items"].update(candidate["items"])
                previous["score"] = max(previous["score"], candidate["score"])
                previous["score_sum"] += candidate["score"]
                previous["anchors"].extend(candidate["anchors"])
                if previous["kind"] != candidate["kind"]:
                    previous["kind"] = "mixed"
                continue
        row = dict(candidate)
        row["items"] = set(candidate["items"])
        row["anchors"] = list(candidate["anchors"])
        row["score_sum"] = candidate["score"]
        merged.append(row)
    return merged


def _candidate_rank(
    segment: Mapping[str, Any],
    uncovered: set[str],
    selected_docs: set[int],
    *,
    broad_packet: bool,
) -> float:
    length = max(1, int(segment["end"]) - int(segment["start"]))
    new_items = len(set(segment["items"]) & uncovered)
    diversity = 35 if int(segment["doc_index"]) not in selected_docs else 0
    distinct_strong = len(
        {
            str(anchor["keyword"]).casefold()
            for anchor in segment["anchors"]
            if int(anchor["priority"]) >= 4
        }
    )
    if broad_packet:
        # Across all 24 items, several independent anchors in one passage are
        # useful evidence that the passage is globally information-dense.
        semantic_score = float(segment["score_sum"])
    else:
        # Within one narrow group, repeated boilerplate must not crowd out a
        # single operative qualification sentence.
        semantic_score = float(segment["score"]) + min(distinct_strong, 3) * 18
    # New item coverage dominates, then semantic quality per source character.
    # Repetition does not increase rank: boilerplate may repeat the same region
    # token dozens of times, while one operative qualification sentence is the
    # evidence we must retain.
    return new_items * 100_000 + (semantic_score + diversity) * 1_000 / length


def _select_segments(
    segments: Sequence[dict[str, Any]], max_chars: int, *, broad_packet: bool
) -> list[dict[str, Any]]:
    remaining = set(range(len(segments)))
    selected: list[dict[str, Any]] = []
    selected_chars = 0
    covered_items: set[str] = set()
    selected_docs: set[int] = set()
    available_items = set().union(*(set(row["items"]) for row in segments)) if segments else set()

    while remaining:
        uncovered = available_items - covered_items
        feasible = [
            index
            for index in remaining
            if selected_chars + segments[index]["end"] - segments[index]["start"] <= max_chars
        ]
        if not feasible:
            break
        best = max(
            feasible,
            key=lambda index: (
                _candidate_rank(
                    segments[index], uncovered, selected_docs, broad_packet=broad_packet
                ),
                -segments[index]["start"],
                -segments[index]["doc_index"],
            ),
        )
        row = segments[best]
        selected.append(row)
        selected_chars += row["end"] - row["start"]
        covered_items.update(row["items"])
        selected_docs.add(row["doc_index"])
        remaining.remove(best)

    return sorted(selected, key=lambda row: (row["doc_index"], row["start"], row["end"]))


def _covered_source_chars(selected: Sequence[Mapping[str, Any]], documents: Sequence[Mapping[str, Any]]) -> int:
    by_doc: dict[int, list[tuple[int, int]]] = defaultdict(list)
    for row in selected:
        by_doc[int(row["doc_index"])].append((int(row["start"]), int(row["end"])))
    covered = 0
    for doc in documents:
        intervals = sorted(by_doc.get(int(doc["doc_index"]), []))
        end = 0
        for lo, hi in intervals:
            if hi <= end:
                continue
            covered += hi - max(lo, end)
            end = hi
    return covered


def packetize_record(
    record: Mapping[str, Any],
    *,
    max_chars: int = DEFAULT_MAX_CHARS,
    target_items: Iterable[str] | None = None,
    before: int = 420,
    after: int = 620,
    merge_gap: int = 96,
    max_segment_chars: int = 1_600,
) -> dict[str, Any]:
    """Build a bounded packet whose text is always verbatim source text."""

    if max_chars < 256:
        raise ValueError("max_chars must be at least 256")
    if before < 0 or after < 0 or merge_gap < 0:
        raise ValueError("window and merge sizes must be non-negative")
    if target_items is None:
        requested_items = ITEMS
    else:
        supplied = {str(item) for item in target_items}
        unknown = supplied - set(ITEMS)
        if unknown:
            raise ValueError(f"unknown target_items: {sorted(unknown)}")
        if not supplied:
            raise ValueError("target_items must not be empty")
        requested_items = tuple(item for item in ITEMS if item in supplied)
    target_set = frozenset(requested_items)
    max_segment_chars = max(128, min(max_segment_chars, max_chars))
    documents, candidates = _anchor_candidates(
        record, before=before, after=after, target_items=target_set
    )
    merged = _merge_candidates(
        candidates, merge_gap=merge_gap, max_segment_chars=max_segment_chars
    )
    selected = _select_segments(
        merged, max_chars, broad_packet=len(requested_items) == len(ITEMS)
    )
    # The cap above keeps retrieval candidates tractable.  Two capped
    # candidates may still overlap; coalesce the final selection once more so
    # packet text never pays twice for the same source characters.
    selected = _merge_candidates(selected, merge_gap=0, max_segment_chars=max_chars)

    output_segments: list[dict[str, Any]] = []
    item_segment_ids: dict[str, list[str]] = {item: [] for item in requested_items}
    selected_anchor_counts = {item: 0 for item in requested_items}
    total_anchor_counts = {item: 0 for item in requested_items}
    for row in merged:
        for anchor in row["anchors"]:
            for item in anchor["items"]:
                total_anchor_counts[item] += 1

    for ordinal, row in enumerate(selected, 1):
        doc = documents[row["doc_index"]]
        text = doc["text"][row["start"] : row["end"]]
        segment_id = f"S{ordinal:04d}"
        anchors = sorted(
            row["anchors"],
            key=lambda anchor: (anchor["start"], anchor["end"], anchor["keyword"]),
        )
        segment = {
            "segment_id": segment_id,
            "doc_index": row["doc_index"],
            "doc_id": doc["doc_id"],
            "doc_type": doc["doc_type"],
            "source_doc_sha256": doc["sha256"],
            "start": row["start"],
            "end": row["end"],
            "text": text,
            "text_sha256": sha256_text(text),
            "items": sorted(row["items"], key=lambda item: int(item[1:])),
            "kind": row["kind"],
            "anchors": anchors,
        }
        output_segments.append(segment)
        for item in row["items"]:
            item_segment_ids[item].append(segment_id)
        for anchor in anchors:
            for item in anchor["items"]:
                selected_anchor_counts[item] += 1

    source_chars = sum(len(doc["text"]) for doc in documents)
    selected_chars = sum(len(segment["text"]) for segment in output_segments)
    covered_chars = _covered_source_chars(selected, documents)
    full_source_visible = covered_chars == source_chars
    item_index = {
        item: {
            "segment_ids": item_segment_ids[item],
            "selected_anchor_count": selected_anchor_counts[item],
            "total_anchor_count": total_anchor_counts[item],
            "omitted_anchor_count": total_anchor_counts[item] - selected_anchor_counts[item],
            "absence_safe": full_source_visible if item in ABSENCE_ITEMS else None,
        }
        for item in requested_items
    }
    record_id = str(record.get("id") or "")
    if not record_id:
        raise ValueError("record has no id")
    return {
        "schema_version": SCHEMA_VERSION,
        "id": record_id,
        "source_sha256": sha256_object(record),
        "target_items": list(requested_items),
        "max_chars": max_chars,
        "source_chars": source_chars,
        "selected_chars": selected_chars,
        "covered_source_chars": covered_chars,
        "compression_ratio": round(selected_chars / source_chars, 8) if source_chars else 1.0,
        "full_source_visible": full_source_visible,
        "absence_warning": None if full_source_visible or not (target_set & ABSENCE_ITEMS) else (
            "Truncated retrieval cannot establish that required language is absent. "
            "Inspect the full supplied source before deciding v10/v11/v16/v18/v20."
        ),
        "meta": record.get("meta") or {},
        "input_completeness": record.get("input_completeness") or {},
        "dropped_doc_counts": record.get("dropped_doc_counts") or {},
        "documents": [
            {
                "doc_index": doc["doc_index"],
                "doc_id": doc["doc_id"],
                "doc_type": doc["doc_type"],
                "chars": len(doc["text"]),
                "sha256": doc["sha256"],
            }
            for doc in documents
        ],
        "items": item_index,
        "segments": output_segments,
    }


def render_packet(packet: Mapping[str, Any]) -> str:
    """Render packet segments while keeping provenance outside source quotes."""

    rendered: list[str] = []
    for segment in packet.get("segments") or []:
        items = ",".join(segment.get("items") or []) or "structure"
        rendered.append(
            f"<<<SOURCE segment={segment['segment_id']} doc_index={segment['doc_index']} "
            f"doc_id={segment['doc_id']} type={segment['doc_type']} "
            f"range=[{segment['start']},{segment['end']}) items={items} "
            f"sha256={segment['text_sha256']}>>>\n"
            f"{segment['text']}\n<<<END SOURCE>>>"
        )
    return "\n\n".join(rendered)


def _evidence_locations(record: Mapping[str, Any], evidence: str) -> list[tuple[int, int, int]]:
    locations: list[tuple[int, int, int]] = []
    if not evidence:
        return locations
    for doc_index, doc in enumerate(record.get("docs") or []):
        text = str(doc.get("text") or "")
        start = text.find(evidence)
        while start >= 0:
            locations.append((doc_index, start, start + len(evidence)))
            start = text.find(evidence, start + 1)
    return locations


def _location_covered(packet: Mapping[str, Any], location: tuple[int, int, int]) -> bool:
    doc_index, start, end = location
    return any(
        int(segment["doc_index"]) == doc_index
        and int(segment["start"]) <= start
        and int(segment["end"]) >= end
        for segment in packet.get("segments") or []
    )


def audit_dev_evidence(
    dev_path: pathlib.Path,
    labels_path: pathlib.Path,
    *,
    max_chars: int = DEFAULT_MAX_CHARS,
    target_items: Iterable[str] | None = None,
    before: int = 420,
    after: int = 620,
) -> dict[str, Any]:
    """Measure exact coverage of all non-empty organizer positive evidence."""

    if target_items is None:
        requested_items = ITEMS
    else:
        supplied = {str(item) for item in target_items}
        unknown = supplied - set(ITEMS)
        if unknown:
            raise ValueError(f"unknown target_items: {sorted(unknown)}")
        if not supplied:
            raise ValueError("target_items must not be empty")
        requested_items = tuple(item for item in ITEMS if item in supplied)
    with labels_path.open("r", encoding="utf-8-sig", newline="") as handle:
        labels = {str(row["id"]): row for row in csv.DictReader(handle)}
    packet_lengths: list[int] = []
    source_lengths: list[int] = []
    missed: list[dict[str, Any]] = []
    by_item = {
        item: {"evidence": 0, "located": 0, "covered": 0, "recall": None}
        for item in requested_items
    }
    records = 0
    for record in read_jsonl_gz(dev_path):
        records += 1
        record_id = str(record["id"])
        if record_id not in labels:
            raise ValueError(f"missing label row for {record_id}")
        packet = packetize_record(
            record,
            max_chars=max_chars,
            target_items=requested_items,
            before=before,
            after=after,
        )
        packet_lengths.append(int(packet["selected_chars"]))
        source_lengths.append(int(packet["source_chars"]))
        row = labels[record_id]
        for item in requested_items:
            evidence = str(row.get("e" + item[1:]) or "")
            if str(row.get(item)) != "1" or not evidence:
                continue
            by_item[item]["evidence"] += 1
            locations = _evidence_locations(record, evidence)
            if locations:
                by_item[item]["located"] += 1
            covered = any(_location_covered(packet, location) for location in locations)
            if covered:
                by_item[item]["covered"] += 1
            else:
                missed.append(
                    {
                        "id": record_id,
                        "item": item,
                        "evidence_sha256": sha256_text(evidence),
                        "evidence_chars": len(evidence),
                        "source_locations": [
                            {"doc_index": di, "start": lo, "end": hi}
                            for di, lo, hi in locations
                        ],
                    }
                )
    total_evidence = sum(int(row["evidence"]) for row in by_item.values())
    total_located = sum(int(row["located"]) for row in by_item.values())
    total_covered = sum(int(row["covered"]) for row in by_item.values())
    for row in by_item.values():
        row["recall"] = row["covered"] / row["evidence"] if row["evidence"] else None

    ordered_lengths = sorted(packet_lengths)
    p95_index = max(0, math.ceil(len(ordered_lengths) * 0.95) - 1) if ordered_lengths else 0
    packet_stats = {
        "min": min(packet_lengths, default=0),
        "mean": round(statistics.fmean(packet_lengths), 3) if packet_lengths else 0,
        "median": statistics.median(packet_lengths) if packet_lengths else 0,
        "p95": ordered_lengths[p95_index] if ordered_lengths else 0,
        "max": max(packet_lengths, default=0),
        "at_budget": sum(length == max_chars for length in packet_lengths),
    }
    source_total = sum(source_lengths)
    packet_total = sum(packet_lengths)
    return {
        "schema_version": "dacon.independent.packet_recall_audit.v1",
        "dev_path": str(dev_path),
        "labels_path": str(labels_path),
        "records": records,
        "target_items": list(requested_items),
        "max_chars": max_chars,
        "positive_nonempty_evidence": total_evidence,
        "evidence_located_in_source": total_located,
        "evidence_covered": total_covered,
        "evidence_recall": total_covered / total_evidence if total_evidence else 1.0,
        "source_chars_total": source_total,
        "packet_chars_total": packet_total,
        "corpus_compression_ratio": round(packet_total / source_total, 8) if source_total else 1.0,
        "packet_length": packet_stats,
        "by_item": by_item,
        "missed": missed,
    }


def audit_dev_item_groups(
    dev_path: pathlib.Path,
    labels_path: pathlib.Path,
    *,
    budget_candidates: Sequence[int] = GROUP_BUDGET_CANDIDATES,
    before: int = 420,
    after: int = 620,
) -> dict[str, Any]:
    """Find the first safe tested budget for every official item group.

    "Minimum" is intentionally scoped to the supplied ascending candidate
    grid.  This avoids claiming mathematical optimality from a development
    sample while still making the tested safety boundary reproducible.
    """

    budgets = tuple(sorted({int(value) for value in budget_candidates}))
    if not budgets or budgets[0] < 256:
        raise ValueError("budget_candidates must contain values >= 256")
    groups: dict[str, Any] = {}
    safe_total_evidence = 0
    safe_total_covered = 0
    safe_total_packet_chars = 0
    safe_total_source_chars = 0
    for group_name, items in ITEM_GROUPS.items():
        trials: list[dict[str, Any]] = []
        safe_report: dict[str, Any] | None = None
        for budget in budgets:
            report = audit_dev_evidence(
                dev_path,
                labels_path,
                max_chars=budget,
                target_items=items,
                before=before,
                after=after,
            )
            trial = {
                "max_chars": budget,
                "positive_nonempty_evidence": report["positive_nonempty_evidence"],
                "evidence_covered": report["evidence_covered"],
                "evidence_recall": report["evidence_recall"],
                "packet_chars_total": report["packet_chars_total"],
                "corpus_compression_ratio": report["corpus_compression_ratio"],
                "packet_length": report["packet_length"],
                "missed": report["missed"],
            }
            trials.append(trial)
            if not report["missed"] and (
                report["evidence_covered"] == report["positive_nonempty_evidence"]
            ):
                safe_report = report
                break
        if safe_report is None:
            groups[group_name] = {
                "items": list(items),
                "minimum_safe_tested_max_chars": None,
                "trials": trials,
            }
            continue
        safe_total_evidence += int(safe_report["positive_nonempty_evidence"])
        safe_total_covered += int(safe_report["evidence_covered"])
        safe_total_packet_chars += int(safe_report["packet_chars_total"])
        safe_total_source_chars += int(safe_report["source_chars_total"])
        groups[group_name] = {
            "items": list(items),
            "minimum_safe_tested_max_chars": safe_report["max_chars"],
            "positive_evidence_validation_available": bool(
                safe_report["positive_nonempty_evidence"]
            ),
            "absence_items": sorted(set(items) & ABSENCE_ITEMS, key=lambda item: int(item[1:])),
            "absence_decisions_require_full_source": bool(set(items) & ABSENCE_ITEMS),
            "safe_packet_length": safe_report["packet_length"],
            "safe_packet_chars_total": safe_report["packet_chars_total"],
            "safe_corpus_compression_ratio": safe_report["corpus_compression_ratio"],
            "positive_nonempty_evidence": safe_report["positive_nonempty_evidence"],
            "evidence_covered": safe_report["evidence_covered"],
            "trials": trials,
        }
    all_safe = all(
        group["minimum_safe_tested_max_chars"] is not None for group in groups.values()
    )
    return {
        "schema_version": "dacon.independent.group_packet_recall_audit.v1",
        "dev_path": str(dev_path),
        "labels_path": str(labels_path),
        "budget_candidates": list(budgets),
        "minimum_scope": "first passing value on the tested budget grid",
        "coverage_scope": (
            "Exact inclusion of non-empty positive dev evidence only. It does not validate "
            "absence decisions or establish unseen-data recall."
        ),
        "all_groups_safe": all_safe,
        "positive_nonempty_evidence": safe_total_evidence,
        "evidence_covered": safe_total_covered,
        "evidence_recall": (
            safe_total_covered / safe_total_evidence if safe_total_evidence else 1.0
        ),
        "safe_packet_chars_total_across_groups": safe_total_packet_chars,
        "source_chars_total_repeated_across_groups": safe_total_source_chars,
        "groups": groups,
    }


def _write_packets(args: argparse.Namespace) -> int:
    output: pathlib.Path = args.output
    output.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with output.open("w", encoding="utf-8", newline="\n") as handle:
        for record in read_jsonl_gz(args.input):
            if args.limit is not None and count >= args.limit:
                break
            packet = packetize_record(
                record,
                max_chars=args.max_chars,
                target_items=_parse_target_items(args.target_items),
                before=args.before,
                after=args.after,
            )
            handle.write(json.dumps(packet, ensure_ascii=False, sort_keys=True) + "\n")
            count += 1
            if count % 100 == 0:
                print(f"packetized {count}", file=sys.stderr, flush=True)
    print(json.dumps({"records": count, "output": str(output)}, ensure_ascii=False))
    return 0


def _audit(args: argparse.Namespace) -> int:
    report = audit_dev_evidence(
        args.dev,
        args.labels,
        max_chars=args.max_chars,
        target_items=_parse_target_items(args.target_items),
        before=args.before,
        after=args.after,
    )
    rendered = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    sys.stdout.write(rendered)
    return 0 if not report["missed"] else 2


def _audit_groups(args: argparse.Namespace) -> int:
    budgets = tuple(int(value) for value in args.budgets.split(",") if value.strip())
    report = audit_dev_item_groups(
        args.dev,
        args.labels,
        budget_candidates=budgets,
        before=args.before,
        after=args.after,
    )
    rendered = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    sys.stdout.write(rendered)
    return 0 if report["all_groups_safe"] and report["evidence_recall"] == 1.0 else 2


def _parse_target_items(value: str | None) -> tuple[str, ...] | None:
    if value is None:
        return None
    items = tuple(part.strip() for part in value.split(",") if part.strip())
    return items


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    make = subparsers.add_parser("packetize", help="write source packets as JSONL")
    make.add_argument("--input", type=pathlib.Path, required=True)
    make.add_argument("--output", type=pathlib.Path, required=True)
    make.add_argument("--max-chars", type=int, default=DEFAULT_MAX_CHARS)
    make.add_argument("--before", type=int, default=420)
    make.add_argument("--after", type=int, default=620)
    make.add_argument(
        "--target-items",
        help="comma-separated item subset, for example v1,v2,v3,v4",
    )
    make.add_argument("--limit", type=int)
    make.set_defaults(func=_write_packets)

    audit = subparsers.add_parser("audit-dev", help="measure official positive-evidence recall")
    audit.add_argument("--dev", type=pathlib.Path, default=ROOT / "data_open" / "dev.jsonl.gz")
    audit.add_argument(
        "--labels", type=pathlib.Path, default=ROOT / "data_open" / "dev_labels.csv"
    )
    audit.add_argument("--max-chars", type=int, default=DEFAULT_MAX_CHARS)
    audit.add_argument("--before", type=int, default=420)
    audit.add_argument("--after", type=int, default=620)
    audit.add_argument("--target-items", help="comma-separated item subset")
    audit.add_argument("--output", type=pathlib.Path)
    audit.set_defaults(func=_audit)

    groups = subparsers.add_parser(
        "audit-groups", help="find the first safe tested budget for each item group"
    )
    groups.add_argument("--dev", type=pathlib.Path, default=ROOT / "data_open" / "dev.jsonl.gz")
    groups.add_argument(
        "--labels", type=pathlib.Path, default=ROOT / "data_open" / "dev_labels.csv"
    )
    groups.add_argument(
        "--budgets",
        default=",".join(str(value) for value in GROUP_BUDGET_CANDIDATES),
        help="ascending comma-separated budget grid",
    )
    groups.add_argument("--before", type=int, default=420)
    groups.add_argument("--after", type=int, default=620)
    groups.add_argument("--output", type=pathlib.Path)
    groups.set_defaults(func=_audit_groups)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
