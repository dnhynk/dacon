"""Source-only routing of unlabeled notices into annotation priority tiers.

This is a *sampling plan*, never a label or a reason to assign a zero.  It reads
only organizer records and the source-only notice-family manifest.  A weak
lexical signal means "unknown value", not "no violation"; low-priority records
remain in the ledger for stratified sentinel sampling and later recall audits.
"""

from __future__ import annotations

import argparse
import collections
import hashlib
import json
import pathlib
import re
from typing import Any, Mapping, Sequence

from tools.independent_gold import notice_families, template_clusters


ROOT = pathlib.Path(__file__).resolve().parents[2]
SCHEMA = "dacon.independent.high_value_triage.v1"
PILOT_SCHEMA = "dacon.independent.high_value_pilot.v1"
PILOT_QUOTAS = {
    "priority_blind_annotation": 500,
    "single_or_weak_cue_sample": 200,
    "no_lexical_cue_sample": 25,
    "source_gap_review": 75,
}
PILOT_FAMILY_PAIRS = 100
SOURCE_PATTERNS = {
    "restricted_institution": r"대학|산학협력단|특정기관|공공기관|의료기관|병원",
    "performance": r"수행\s*실적|납품\s*실적|용역\s*실적|이행\s*실적|실적\s*(?:증명|제한|보유)|유사\s*사업",
    "region": r"주된\s*영업소|본점\s*소재지|본사\s*소재지|지역\s*제한|제한\s*지역|관할\s*구역",
    "brand_model": r"모델명|제조사|브랜드|상표|품번|[Pp]art\s*[Nn]umber|특정\s*모델",
    "direct_production": r"직접\s*생산|중소기업자간\s*경쟁제품|세부\s*품명",
    "small_business": r"소기업|소상공인|중소기업",
    "supply_pledge": r"물품\s*공급.{0,8}확약|기술\s*지원.{0,8}확약|공급\s*확약",
    "software": r"소프트웨어\s*사업|소프트웨어\s*진흥법|정보화\s*사업|[Ss][Ww]\s*사업|대기업\s*참여\s*제한",
    "joint_bid": r"공동\s*수급|공동\s*이행|분담\s*이행|공동\s*도급|지분\s*(?:율|참여)|출자\s*비율",
    "briefing": r"현장\s*설명|사업\s*설명회|과업\s*설명회|제안\s*요청.{0,6}설명회",
}
PATTERNS = {key: re.compile(value, re.IGNORECASE) for key, value in SOURCE_PATTERNS.items()}
COMPLEXITY_RE = re.compile(r"다만|예외|그러하지\s*아니|제외|또는|어느\s*하나")
PRICE_THRESHOLDS = (100_000_000, 230_000_000, 2_000_000_000, 4_000_000_000, 8_000_000_000)


def canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha_object(value: Any) -> str:
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def file_sha(path: pathlib.Path) -> str:
    return notice_families.file_sha256(path)


def source_features(record: Mapping[str, Any]) -> dict[str, Any]:
    """Extract routing cues; no decision or violation probability is returned."""

    template_clusters.assert_admissible_record(record)
    text = "\n".join(doc["text"] for doc in record["docs"])
    signals = sorted(key for key, pattern in PATTERNS.items() if pattern.search(text))
    meta = record["meta"]
    prices = [meta.get("배정예산금액"), meta.get("입찰추정가격")]
    price_boundaries = sorted({
        threshold
        for value in prices
        if isinstance(value, (int, float)) and not isinstance(value, bool)
        for threshold in PRICE_THRESHOLDS
        if abs(value - threshold) <= threshold * 0.05
    })
    complete = all(record["input_completeness"].values()) and not record["dropped_doc_counts"]
    cross_rule = any({left, right} <= set(signals) for left, right in (
        ("performance", "region"),
        ("direct_production", "small_business"),
        ("brand_model", "direct_production"),
        ("joint_bid", "briefing"),
    ))
    # A metadata/text difference is a *review cue*, never proof of v24.
    region_meta_without_clause = meta.get("지역제한여부") == "Y" and "region" not in signals
    high_signal = (
        len(signals) >= 3
        or cross_rule
        or (len(signals) >= 2 and bool(price_boundaries))
        or (len(signals) >= 1 and region_meta_without_clause)
    )
    return {
        "signals": signals,
        "signal_count": len(signals),
        "source_complete": complete,
        "source_chars": len(text),
        "document_count": len(record["docs"]),
        "price_boundaries_within_5pct_won": price_boundaries,
        "cross_rule_cue": cross_rule,
        "complexity_cue": bool(COMPLEXITY_RE.search(text)),
        "region_meta_without_clause_cue": region_meta_without_clause,
        "high_signal": high_signal,
    }


def _priority(features: Mapping[str, Any]) -> tuple[int, int, int, int]:
    # Used only to choose which sibling in a source-only family to inspect first.
    return (
        int(features["high_signal"]),
        int(features["cross_rule_cue"]) + bool(features["price_boundaries_within_5pct_won"]),
        features["signal_count"],
        -features["source_chars"],
    )


def build_plan(input_path: pathlib.Path, family_path: pathlib.Path) -> dict[str, Any]:
    input_path = input_path.resolve(strict=True)
    family_path = family_path.resolve(strict=True)
    family = json.loads(family_path.read_text(encoding="utf-8"))
    if family.get("schema_version") != notice_families.SCHEMA_VERSION:
        raise ValueError("unexpected family schema")
    if family.get("organizer_input_sha256") != file_sha(input_path):
        raise ValueError("family and organizer input hashes differ")
    if family.get("manifest_sha256") != sha_object({k: v for k, v in family.items() if k != "manifest_sha256"}):
        raise ValueError("family manifest content hash differs")
    family_rows = {row["record_id"]: row for row in family["records"]}
    family_sizes = {row["family_id"]: row["member_count"] for row in family["families"]}
    if len(family_rows) != 20_000 or len(family["records"]) != 20_000:
        raise ValueError("family manifest does not cover exactly 20,000 unique records")
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for record in template_clusters.read_jsonl_gz(input_path):
        record_id = record["id"]
        if record_id in seen or record_id not in family_rows:
            raise ValueError(f"duplicate or unregistered organizer ID: {record_id}")
        seen.add(record_id)
        source_hash = sha_object(record)
        family_row = family_rows[record_id]
        if family_row["source_sha256"] != source_hash:
            raise ValueError(f"family source hash differs for {record_id}")
        features = source_features(record)
        rows.append({
            "id": record_id,
            "source_sha256": source_hash,
            "family_id": family_row["family_id"],
            "family_size": family_sizes[family_row["family_id"]],
            "features": features,
        })
    if len(rows) != 20_000 or seen != set(family_rows):
        raise ValueError("20,000-record source coverage failed")

    by_family: dict[str, list[dict[str, Any]]] = collections.defaultdict(list)
    for row in rows:
        by_family[row["family_id"]].append(row)
    representative: dict[str, str] = {}
    for family_id, members in by_family.items():
        eligible = [row for row in members if row["features"]["source_complete"]]
        if eligible:
            representative[family_id] = min(
                eligible, key=lambda row: (tuple(-part for part in _priority(row["features"])), row["id"])
            )["id"]
    counts: collections.Counter[str] = collections.Counter()
    signal_counts: collections.Counter[str] = collections.Counter()
    for row in rows:
        features = row["features"]
        signal_counts.update(features["signals"])
        if not features["source_complete"]:
            tier = "source_gap_review"
        elif representative[row["family_id"]] != row["id"]:
            tier = "family_sibling_deferred"
        elif features["high_signal"]:
            tier = "priority_blind_annotation"
        elif features["signals"]:
            tier = "single_or_weak_cue_sample"
        else:
            tier = "no_lexical_cue_sample"
        row["tier"] = tier
        row["family_representative"] = representative.get(row["family_id"]) == row["id"]
        counts[tier] += 1
    plan = {
        "schema_version": SCHEMA,
        "input_sha256": file_sha(input_path),
        "family_manifest_sha256": file_sha(family_path),
        "source_only": True,
        "binary_answers_generated": False,
        "automatic_label_propagation_allowed": False,
        "final_exclusion_declared": False,
        "policy": {
            "priority_rule": "source-complete family representative with >=3 lexical cues, a cross-rule cue, 2 cues near a price boundary, or a metadata/text region cue",
            "deferred_rule": "source gaps and sibling notices need independent source review; weak/no lexical cues are sampled, never assumed negative",
            "weak_cue_warning": "lexical cues are routing proxies, not legal findings or a validated recall guarantee",
        },
        "counts": dict(sorted(counts.items())),
        "signal_counts": dict(sorted(signal_counts.items())),
        "rows": sorted(rows, key=lambda row: row["id"]),
    }
    plan["content_sha256"] = sha_object({k: v for k, v in plan.items() if k != "content_sha256"})
    return plan


def build_pilot(
    plan: Mapping[str, Any],
    *,
    quotas: Mapping[str, int] | None = None,
    family_pairs: int = PILOT_FAMILY_PAIRS,
) -> dict[str, Any]:
    """Draw a reproducible 1,000-slot value pilot, including sibling pairs.

    Priority and weak-cue strata are sampled independently.  The paired part
    tests whether family routing would hide different decisions; it does not
    assume members have the same answer.  Overlap reduces unique count below
    1,000, intentionally avoiding a second call on the same source record.
    """

    if plan.get("schema_version") != SCHEMA:
        raise ValueError("triage schema differs")
    if plan.get("content_sha256") != sha_object({k: v for k, v in plan.items() if k != "content_sha256"}):
        raise ValueError("triage plan hash differs")
    quotas = dict(PILOT_QUOTAS if quotas is None else quotas)
    if any(type(count) is not int or count < 0 for count in quotas.values()):
        raise ValueError("pilot quotas must be non-negative integers")
    if type(family_pairs) is not int or family_pairs < 0:
        raise ValueError("family_pairs must be a non-negative integer")
    rows = plan["rows"]
    by_tier: dict[str, list[Mapping[str, Any]]] = collections.defaultdict(list)
    by_family: dict[str, list[Mapping[str, Any]]] = collections.defaultdict(list)
    for row in rows:
        by_tier[row["tier"]].append(row)
        if row["features"]["source_complete"]:
            by_family[row["family_id"]].append(row)

    def order(value: str) -> str:
        return hashlib.sha256(f"dacon.value-pilot.v1:{value}".encode("utf-8")).hexdigest()

    selected: dict[str, dict[str, Any]] = {}

    def add(row: Mapping[str, Any], route: str) -> None:
        entry = selected.setdefault(row["id"], {
            "id": row["id"],
            "source_sha256": row["source_sha256"],
            "family_id": row["family_id"],
            "tier": row["tier"],
            "selection_routes": [],
        })
        entry["selection_routes"].append(route)

    for tier, quota in quotas.items():
        eligible = sorted(by_tier[tier], key=lambda row: (order(row["id"]), row["id"]))
        if len(eligible) < quota:
            raise ValueError(f"pilot quota exceeds {tier} population")
        for row in eligible[:quota]:
            add(row, f"stratum:{tier}")

    pair_families = [
        (family_id, members)
        for family_id, members in by_family.items()
        if len(members) >= 2 and any(row["family_representative"] for row in members)
    ]
    pair_families.sort(key=lambda pair: (order(pair[0]), pair[0]))
    if len(pair_families) < family_pairs:
        raise ValueError("too few paired families for pilot")
    for family_id, members in pair_families[:family_pairs]:
        representative = next(row for row in members if row["family_representative"])
        siblings = [row for row in members if row["id"] != representative["id"]]
        sibling = min(siblings, key=lambda row: (order(row["id"]), row["id"]))
        add(representative, "paired_family_representative")
        add(sibling, "paired_family_sibling")

    sampled = sorted(selected.values(), key=lambda row: row["id"])
    result = {
        "schema_version": PILOT_SCHEMA,
        "triage_content_sha256": plan["content_sha256"],
        "selection_seed": "dacon.value-pilot.v1",
        "requested_stratum_quotas": quotas,
        "requested_family_pairs": family_pairs,
        "unique_records": len(sampled),
        "sampled_tier_counts": dict(sorted(collections.Counter(row["tier"] for row in sampled).items())),
        "labels_or_model_responses_consumed": False,
        "binary_answers_generated": False,
        "purpose": "blind gold-yield, source-closure, model-disagreement, and sibling-variation measurement before setting a final retained percentage",
        "rows": sampled,
    }
    result["content_sha256"] = sha_object({k: v for k, v in result.items() if k != "content_sha256"})
    return result


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=pathlib.Path, default=ROOT / "data_open/train_unlabeled.jsonl.gz")
    parser.add_argument("--families", type=pathlib.Path, default=ROOT / "runs/self_label_20000_20260918/audit_v3/notice_families.json")
    parser.add_argument("--output", type=pathlib.Path, required=True)
    parser.add_argument("--pilot-output", type=pathlib.Path)
    args = parser.parse_args(argv)
    if args.output.exists():
        parser.error(f"fresh-only output already exists: {args.output}")
    if args.pilot_output and args.pilot_output.exists():
        parser.error(f"fresh-only pilot output already exists: {args.pilot_output}")
    plan = build_plan(args.input, args.families)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(plan, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
    pilot_summary = None
    if args.pilot_output:
        pilot = build_pilot(plan)
        args.pilot_output.parent.mkdir(parents=True, exist_ok=True)
        args.pilot_output.write_text(json.dumps(pilot, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
        pilot_summary = {"output": str(args.pilot_output), "unique_records": pilot["unique_records"], "sampled_tier_counts": pilot["sampled_tier_counts"], "content_sha256": pilot["content_sha256"]}
    print(canonical({"output": str(args.output), "counts": plan["counts"], "signal_counts": plan["signal_counts"], "content_sha256": plan["content_sha256"], "pilot": pilot_summary}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
