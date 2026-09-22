"""Reproducible, label-free notice-template families for gold auditing.

Families are *sampling blocks*, not semantic or label-equivalence classes.  A
shared organizer notice template may contain a different decisive clause in a
particular member.  No prediction, model response, or development label is read.
"""

from __future__ import annotations

import argparse
import collections
import hashlib
import json
import pathlib
import re
import unicodedata
from typing import Any, Mapping, Sequence

try:
    from tools.independent_gold import template_clusters
except ModuleNotFoundError:  # Direct execution from this directory.
    import template_clusters  # type: ignore[no-redef]


ROOT = pathlib.Path(__file__).resolve().parents[2]
SCHEMA_VERSION = "dacon.independent.notice_families.v1"
ALGORITHM_ID = "organizer_notice_paragraph_graph"
ALGORITHM_VERSION = 1
EXPECTED_RECORDS = 20_000
MIN_PARAGRAPH_CHARS = 60
MAX_CANDIDATE_PARAGRAPH_FREQUENCY = 50
MIN_SHARED_RARE_PARAGRAPHS = 12
MIN_PARAGRAPH_CONTAINMENT = 0.8
MIN_PARAGRAPH_JACCARD = 0.6
MONEY_BOUNDS = (100_000_000, 230_000_000, 2_000_000_000, 4_000_000_000, 8_000_000_000)
SCOPE_META_KEYS = (
    "적용계약법",
    "업무구분",
    "계약방법",
    "낙찰방법",
    "지역제한여부",
    "업종제한여부",
    "정보화사업여부",
    "공동도급구성방식",
    "긴급공고여부",
)
ANON_RE = re.compile(r"\[([^\]\r\n]{1,220})\]")
NUMBER_RE = re.compile(r"\d+(?:[,.]\d+)*")
WHITESPACE_RE = re.compile(r"\s+")


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_object(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def file_sha256(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _money_band(value: Any) -> str | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    for bound in MONEY_BOUNDS:
        if value == bound:
            return f"eq_{bound}"
        if value < bound:
            return f"lt_{bound}"
    return f"ge_{MONEY_BOUNDS[-1]}"


def _scope(record: Mapping[str, Any]) -> dict[str, Any]:
    meta = record["meta"]
    return {
        "meta": {key: meta.get(key) for key in SCOPE_META_KEYS},
        "budget_band": _money_band(meta.get("배정예산금액")),
        "estimated_price_band": _money_band(meta.get("입찰추정가격")),
        "document_types": sorted(doc["type"] for doc in record["docs"]),
        "dropped_document_types": sorted(record["dropped_doc_counts"]),
        "source_complete": all(record["input_completeness"].values())
        and not record["dropped_doc_counts"],
    }


def _normalize_notice(text: str) -> str:
    normalized = unicodedata.normalize("NFKC", text).casefold()

    def replace_anon(match: re.Match[str]) -> str:
        # Retain the organizer's anonymized token *kind*, not its identity.
        kind = re.split(r"[(:|]", match.group(1), maxsplit=1)[0].strip()
        return f"<anon:{kind}>"

    normalized = ANON_RE.sub(replace_anon, normalized)
    normalized = NUMBER_RE.sub("<n>", normalized)
    return normalized


def _features(record: Mapping[str, Any]) -> tuple[str, frozenset[str], str]:
    scope_hash = sha256_object(_scope(record))
    notice_docs = [doc["text"] for doc in record["docs"] if doc["type"] == "공고문"]
    # No announcement is not evidence of a shared template: its paragraph set
    # stays empty, and the full organizer source still gives a stable identity.
    normalized_docs = [_normalize_notice(text) for text in notice_docs]
    paragraphs = {
        hashlib.sha256(paragraph.encode("utf-8")).hexdigest()
        for text in normalized_docs
        for raw in re.split(r"\n+", text)
        if len(paragraph := WHITESPACE_RE.sub(" ", raw).strip()) >= MIN_PARAGRAPH_CHARS
    }
    normalized_notice_hash = sha256_object(normalized_docs)
    if not normalized_docs:
        normalized_notice_hash = sha256_object(
            {
                "documents": [
                    {"type": doc["type"], "text": doc["text"]}
                    for doc in record["docs"]
                ],
                "meta": record["meta"],
                "input_completeness": record["input_completeness"],
            }
        )
    exact_signature = sha256_object(
        {"scope_sha256": scope_hash, "normalized_notice_sha256": normalized_notice_hash}
    )
    return scope_hash, frozenset(paragraphs), exact_signature


def _algorithm_manifest() -> dict[str, Any]:
    return {
        "id": ALGORITHM_ID,
        "version": ALGORITHM_VERSION,
        "module_path": "tools/independent_gold/notice_families.py",
        "module_sha256": file_sha256(pathlib.Path(__file__)),
        "source_guard_module_path": "tools/independent_gold/template_clusters.py",
        "source_guard_module_sha256": file_sha256(pathlib.Path(template_clusters.__file__)),
        "parameters": {
            "min_paragraph_chars": MIN_PARAGRAPH_CHARS,
            "max_candidate_paragraph_frequency": MAX_CANDIDATE_PARAGRAPH_FREQUENCY,
            "min_shared_rare_paragraphs": MIN_SHARED_RARE_PARAGRAPHS,
            "min_paragraph_containment": MIN_PARAGRAPH_CONTAINMENT,
            "min_paragraph_jaccard": MIN_PARAGRAPH_JACCARD,
            "money_bounds": list(MONEY_BOUNDS),
            "scope_meta_keys": list(SCOPE_META_KEYS),
        },
        "interpretation": "audit sampling blocks only; no label or semantic equivalence",
    }


def build_family_manifest(
    input_path: pathlib.Path, *, expected_records: int = EXPECTED_RECORDS
) -> dict[str, Any]:
    """Compute deterministic components from organizer text and metadata only."""

    if expected_records < 1:
        raise ValueError("expected_records must be positive")
    input_path = input_path.resolve()
    source_rows: list[dict[str, str]] = []
    feature_rows: list[tuple[str, frozenset[str], str]] = []
    seen_ids: set[str] = set()
    for record in template_clusters.read_jsonl_gz(input_path):
        record_id = record["id"]
        if record_id in seen_ids:
            raise ValueError(f"duplicate organizer record id: {record_id}")
        seen_ids.add(record_id)
        source_rows.append(
            {"record_id": record_id, "source_sha256": sha256_object(record)}
        )
        feature_rows.append(_features(record))
    if len(source_rows) != expected_records:
        raise ValueError(
            f"organizer record count {len(source_rows)} != expected {expected_records}"
        )

    order = sorted(range(len(source_rows)), key=lambda index: source_rows[index]["record_id"])
    source_rows = [source_rows[index] for index in order]
    feature_rows = [feature_rows[index] for index in order]
    count = len(source_rows)
    parent = list(range(count))

    def root(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    def union(left: int, right: int) -> None:
        first, second = root(left), root(right)
        if first != second:
            parent[max(first, second)] = min(first, second)

    # Exact normalized whole-announcement identity is sufficient even for a
    # short announcement with fewer than twelve long paragraphs.
    exact_index: dict[str, int] = {}
    inverted: dict[str, list[int]] = collections.defaultdict(list)
    for index, (scope_hash, paragraph_hashes, exact_signature) in enumerate(feature_rows):
        prior = exact_index.setdefault(exact_signature, index)
        if prior != index:
            union(prior, index)
        for paragraph_hash in sorted(paragraph_hashes):
            inverted[paragraph_hash].append(index)

    shared_rare: collections.Counter[tuple[int, int]] = collections.Counter()
    for members in inverted.values():
        if 2 <= len(members) <= MAX_CANDIDATE_PARAGRAPH_FREQUENCY:
            for offset, left in enumerate(members):
                for right in members[offset + 1 :]:
                    if feature_rows[left][0] == feature_rows[right][0]:
                        shared_rare[(left, right)] += 1

    accepted_edges = 0
    for (left, right), rare_count in sorted(shared_rare.items()):
        if rare_count < MIN_SHARED_RARE_PARAGRAPHS:
            continue
        left_set, right_set = feature_rows[left][1], feature_rows[right][1]
        common = len(left_set & right_set)
        if (
            common / min(len(left_set), len(right_set)) >= MIN_PARAGRAPH_CONTAINMENT
            and common / len(left_set | right_set) >= MIN_PARAGRAPH_JACCARD
        ):
            union(left, right)
            accepted_edges += 1

    members_by_root: dict[int, list[int]] = collections.defaultdict(list)
    for index in range(count):
        members_by_root[root(index)].append(index)
    family_ids_by_root: dict[int, str] = {}
    seen_family_ids: set[str] = set()
    for representative, members in members_by_root.items():
        content_signature = sha256_object(
            {
                "algorithm": ALGORITHM_ID,
                "member_exact_signatures": sorted(feature_rows[index][2] for index in members),
            }
        )
        family_id = f"f-{content_signature[:32]}"
        if family_id in seen_family_ids:
            raise ValueError(f"family id collision: {family_id}")
        seen_family_ids.add(family_id)
        family_ids_by_root[representative] = family_id

    records = [
        {**source_rows[index], "family_id": family_ids_by_root[root(index)]}
        for index in range(count)
    ]
    families = sorted(
        (
            {
                "family_id": family_ids_by_root[representative],
                "member_record_ids": [source_rows[index]["record_id"] for index in members],
                "member_count": len(members),
            }
            for representative, members in members_by_root.items()
        ),
        key=lambda row: row["family_id"],
    )
    sizes = [family["member_count"] for family in families]
    statistics = {
        "records": count,
        "families": len(families),
        "singleton_families": sum(size == 1 for size in sizes),
        "repeated_families": sum(size > 1 for size in sizes),
        "records_in_repeated_families": sum(size for size in sizes if size > 1),
        "largest_family": max(sizes),
        "accepted_near_duplicate_edges": accepted_edges,
        "size_distribution": {
            str(size): count for size, count in sorted(collections.Counter(sizes).items())
        },
    }
    payload: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "organizer_input_path": (
            input_path.relative_to(ROOT.resolve()).as_posix()
            if input_path.is_relative_to(ROOT.resolve())
            else str(input_path)
        ),
        "organizer_input_sha256": file_sha256(input_path),
        "algorithm": _algorithm_manifest(),
        "review_only_contract": {
            "labels_predictions_or_model_responses_consumed": False,
            "semantic_equivalence_claimed": False,
            "automatic_label_propagation_allowed": False,
            "source_for_family_assignment": "organizer notice text and metadata only",
        },
        "records": records,
        "families": families,
        "statistics": statistics,
    }
    payload["manifest_sha256"] = sha256_object(payload)
    return payload


def verify_family_manifest(
    manifest: Mapping[str, Any],
    input_path: pathlib.Path,
    *,
    expected_records: int = EXPECTED_RECORDS,
) -> dict[str, Any]:
    """Replay all 20k assignments and fail closed on any changed byte or row."""

    expected = build_family_manifest(input_path, expected_records=expected_records)
    if dict(manifest) != expected:
        raise ValueError("family manifest differs from independent organizer-source replay")
    return dict(expected["statistics"])


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("build", "verify"))
    parser.add_argument(
        "--input",
        type=pathlib.Path,
        default=ROOT / "data_open" / "train_unlabeled.jsonl.gz",
    )
    parser.add_argument(
        "--output",
        type=pathlib.Path,
        default=ROOT / "runs" / "self_label_20000_20260918" / "audit_v3" / "notice_families.json",
    )
    args = parser.parse_args(argv)
    if args.mode == "build":
        if args.output.exists():
            raise ValueError(f"refusing to overwrite existing family manifest: {args.output}")
        manifest = build_family_manifest(args.input)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )
        print(canonical_json({"output": str(args.output), **manifest["statistics"]}))
    else:
        manifest = json.loads(args.output.read_text(encoding="utf-8"))
        print(canonical_json(verify_family_manifest(manifest, args.input)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
