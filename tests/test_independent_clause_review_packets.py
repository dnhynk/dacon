from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import pathlib

import pytest


ROOT = pathlib.Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "tools" / "independent_gold" / "clause_review_packets.py"
SPEC = importlib.util.spec_from_file_location("independent_clause_review_packets", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(MODULE)


@pytest.fixture(scope="module")
def catalog():
    return MODULE.clause_bank.catalog_facts.CatalogIndex.load()


def record(record_id: str, text: str, *, complete: bool = True):
    fields = MODULE.clause_bank.template_clusters.legal_facts.META_FIELDS
    return {
        "id": record_id,
        "docs": [{"doc_id": "D0", "type": "공고문", "text": text}],
        "dropped_doc_counts": {} if complete else {"제안요청서": 1},
        "input_completeness": {
            "공고문_본문": True,
            "제안요청서": complete,
        },
        "assembly_policy_version": "fixture-v1",
        "meta": {
            fields["contract_law"]: "국가계약법",
            fields["work_type"]: "용역",
            fields["contract_method"]: "제한경쟁",
            fields["budget_won"]: 300_000_000,
            fields["estimated_price_won"]: 272_727_273,
            fields["catalog_codes"]: "1234567890",
        },
        "anon_applied": True,
    }


def source_records():
    return [
        record("R-3", "수행실적은 계약 후 3회 검토한다."),
        record("R-5", "수행실적은 계약 후 5회 검토한다."),
        record("R-7", "수행실적은 계약 후 7회 검토한다."),
        record(
            "R-CRITICAL",
            "소프트웨어사업 대기업 참여 제한의 예외를 검토한다.",
            complete=False,
        ),
        record(
            "R-CATALOG",
            "입찰참가자는 직접생산확인증명서를 제출하여야 한다. "
            "세부품명번호 1234567890을 확인한다.",
        ),
    ]


def build_bank(tmp_path: pathlib.Path, catalog, *, stem: str = "source"):
    bank = tmp_path / f"{stem}.bank.jsonl"
    manifest = tmp_path / f"{stem}.bank.manifest.json"
    MODULE.clause_bank.build_clause_bank(
        source_records(),
        output_jsonl=bank,
        manifest_output=manifest,
        catalog_index=catalog,
    )
    return bank, manifest


def packetize(
    tmp_path: pathlib.Path,
    bank: pathlib.Path,
    bank_manifest: pathlib.Path,
    *,
    stem: str = "review",
    max_occurrences: int = 2,
    max_bytes: int = 262_144,
):
    packets = tmp_path / f"{stem}.packets.jsonl"
    index = tmp_path / f"{stem}.index.jsonl"
    manifest = tmp_path / f"{stem}.manifest.json"
    summary = tmp_path / f"{stem}.md"
    payload = MODULE.build_review_packets(
        clause_bank_path=bank,
        clause_bank_manifest_path=bank_manifest,
        packets_output=packets,
        review_index_output=index,
        manifest_output=manifest,
        summary_output=summary,
        max_occurrences_per_packet=max_occurrences,
        max_packet_bytes=max_bytes,
    )
    packet_rows = [
        json.loads(line) for line in packets.read_text(encoding="utf-8").splitlines()
    ]
    index_rows = [
        json.loads(line) for line in index.read_text(encoding="utf-8").splitlines()
    ]
    return payload, packet_rows, index_rows, packets, index, manifest, summary


def test_packets_partition_every_occurrence_and_include_variant_representatives(
    tmp_path, catalog
):
    bank, bank_manifest = build_bank(tmp_path, catalog)
    source_rows = [
        json.loads(line) for line in bank.read_text(encoding="utf-8").splitlines()
    ]
    manifest, packets, index, packet_path, index_path, manifest_path, summary = packetize(
        tmp_path, bank, bank_manifest
    )

    expected_occurrences = {
        occurrence["occurrence_id"]: occurrence
        for row in source_rows
        for occurrence in row["occurrences"]
    }
    assigned = [
        occurrence
        for packet in packets
        for occurrence in packet["occurrences"]
    ]
    assert len(assigned) == len(expected_occurrences)
    assert {value["occurrence_id"] for value in assigned} == set(expected_occurrences)
    assert len({value["occurrence_id"] for value in assigned}) == len(assigned)
    for occurrence in assigned:
        assert occurrence == expected_occurrences[occurrence["occurrence_id"]]

    repeated_packets = [
        packet
        for packet in packets
        if packet["group"] == "v1-4"
        and packet["cluster"]["occurrence_count"] == 3
    ]
    assert len(repeated_packets) == 2
    assert len(repeated_packets[0]["raw_quote_variants"]) == 2
    for packet in repeated_packets:
        assert len(packet["occurrences"]) <= 2
        variant_ids = {
            occurrence["raw_quote_variant_id"] for occurrence in packet["occurrences"]
        }
        assert {value["variant_id"] for value in packet["raw_quote_variants"]} == variant_ids
        representatives = packet["representatives"]["raw_quote_variant_representatives"]
        assert {value["raw_quote_variant_id"] for value in representatives} == variant_ids
        occurrence_ids = {value["occurrence_id"] for value in packet["occurrences"]}
        assert {value["occurrence_id"] for value in representatives} <= occurrence_ids
        assert packet["review_contract"][
            "automatic_label_or_decision_propagation_allowed"
        ] is False
        assert packet["review_contract"][
            "representative_decision_applies_to_other_occurrences"
        ] is False

    assert manifest["statistics"]["occurrences"] == len(expected_occurrences)
    assert manifest["statistics"]["unique_occurrences_assigned"] == len(
        expected_occurrences
    )
    assert manifest["statistics"]["largest_packet_bytes"] <= 262_144
    assert manifest["outputs"]["packets_jsonl"]["sha256"] == hashlib.sha256(
        packet_path.read_bytes()
    ).hexdigest()
    assert manifest["outputs"]["review_index_jsonl"]["sha256"] == hashlib.sha256(
        index_path.read_bytes()
    ).hexdigest()
    assert json.loads(manifest_path.read_text(encoding="utf-8")) == manifest
    assert "자동 전파" in summary.read_text(encoding="utf-8")
    assert len(index) == len(packets)
    assert not list(tmp_path.glob(".*.tmp"))
    assert not list(tmp_path.glob(".*.sqlite3"))


def test_stratified_index_is_deterministic_and_places_critical_work_first(
    tmp_path, catalog
):
    bank, bank_manifest = build_bank(tmp_path, catalog)
    first = packetize(tmp_path, bank, bank_manifest, stem="first")
    second = packetize(tmp_path, bank, bank_manifest, stem="second")
    first_manifest, first_packets, first_index, first_path, first_index_path, *_ = first
    _, second_packets, second_index, second_path, second_index_path, *_ = second

    assert first_path.read_bytes() == second_path.read_bytes()
    assert first_index_path.read_bytes() == second_index_path.read_bytes()
    assert first_packets == second_packets
    assert first_index == second_index
    assert [row["queue_position"] for row in first_index] == list(
        range(1, len(first_index) + 1)
    )
    assert first_index[0]["stratum"]["highest_risk_tier"] == "critical"
    assert all(
        "cluster_size_band" in row["stratum"]
        and "raw_quote_variant_band" in row["stratum"]
        and "facet_axes" in row["stratum"]
        for row in first_index
    )
    assert first_manifest["review_only_contract"][
        "every_occurrence_assigned_exactly_once"
    ] is True
    assert first_manifest["review_only_contract"][
        "packets_are_final_record_decisions"
    ] is False


def test_catalog_scope_and_all_coordinate_provenance_survive_packetization(
    tmp_path, catalog
):
    bank, bank_manifest = build_bank(tmp_path, catalog)
    source_rows = [
        json.loads(line) for line in bank.read_text(encoding="utf-8").splitlines()
    ]
    _, packets, *_ = packetize(tmp_path, bank, bank_manifest)
    source_catalog = {
        row["clause_fingerprint"]: row
        for row in source_rows
        if row["group"] in MODULE.CATALOG_GROUPS
    }
    relevant = [
        packet
        for packet in packets
        if packet["group"] in MODULE.CATALOG_GROUPS
        and packet["catalog_scope_variants"]
    ]
    assert relevant
    for packet in relevant:
        source = source_catalog[packet["cluster"]["clause_fingerprint"]]
        assert packet["catalog_provenance"] == source["catalog_provenance"]
        assert packet["catalog_provenance_sha256"] == source[
            "catalog_provenance_sha256"
        ]
        scope_ids = {value["variant_id"] for value in packet["catalog_scope_variants"]}
        assert {
            value["catalog_scope_variant_id"]
            for value in packet["occurrences"]
            if value["catalog_scope_variant_id"] is not None
        } == scope_ids
        for occurrence in packet["occurrences"]:
            original = next(
                value
                for value in source["occurrences"]
                if value["occurrence_id"] == occurrence["occurrence_id"]
            )
            assert occurrence["source_sha256"] == original["source_sha256"]
            assert occurrence["coordinate"] == original["coordinate"]
            assert occurrence["coordinate_sha256"] == original["coordinate_sha256"]


def test_hidden_decision_fields_and_coordinate_corruption_fail_atomically(
    tmp_path, catalog
):
    bank, bank_manifest = build_bank(tmp_path, catalog)
    rows = [json.loads(line) for line in bank.read_text(encoding="utf-8").splitlines()]
    rows[0]["fingerprint_identity"]["hidden_prediction"] = {"v2": 1}
    poisoned = tmp_path / "poisoned.bank.jsonl"
    poisoned.write_text(
        "".join(MODULE.canonical_json(row) + "\n" for row in rows),
        encoding="utf-8",
        newline="\n",
    )
    with pytest.raises(ValueError, match="forbidden|schema keys differ"):
        packetize(tmp_path, poisoned, bank_manifest, stem="poisoned")
    assert not (tmp_path / "poisoned.packets.jsonl").exists()
    assert not (tmp_path / "poisoned.index.jsonl").exists()
    assert not (tmp_path / "poisoned.manifest.json").exists()

    clean_rows = [
        json.loads(line) for line in bank.read_text(encoding="utf-8").splitlines()
    ]
    clean_rows[0]["occurrences"][0]["coordinate"]["start"] += 1
    forged = tmp_path / "forged.bank.jsonl"
    forged.write_text(
        "".join(MODULE.canonical_json(row) + "\n" for row in clean_rows),
        encoding="utf-8",
        newline="\n",
    )
    with pytest.raises(ValueError, match="coordinate_sha256|identity hash"):
        packetize(tmp_path, forged, bank_manifest, stem="forged")
    assert not (tmp_path / "forged.packets.jsonl").exists()
    assert not (tmp_path / "forged.index.jsonl").exists()


def test_hidden_manifest_artifact_and_duplicate_json_key_are_rejected(tmp_path, catalog):
    bank, bank_manifest = build_bank(tmp_path, catalog)
    manifest = json.loads(bank_manifest.read_text(encoding="utf-8"))
    manifest["normalization_provenance"]["candidate_predictions"] = {"R-1": 1}
    poisoned_manifest = tmp_path / "poisoned.manifest.json"
    poisoned_manifest.write_text(
        json.dumps(manifest, ensure_ascii=False), encoding="utf-8"
    )
    with pytest.raises(ValueError, match="forbidden"):
        packetize(tmp_path, bank, poisoned_manifest, stem="manifest_poison")

    raw = bank.read_text(encoding="utf-8")
    first, rest = raw.split("\n", 1)
    duplicate = first[:-1] + ',"group":"v1-4"}\n' + rest
    duplicate_bank = tmp_path / "duplicate.bank.jsonl"
    duplicate_bank.write_text(duplicate, encoding="utf-8", newline="\n")
    with pytest.raises(ValueError, match="duplicate JSON key"):
        packetize(tmp_path, duplicate_bank, bank_manifest, stem="duplicate")


def test_byte_bound_rejects_an_oversized_singleton_without_publication(tmp_path, catalog):
    bank, bank_manifest = build_bank(tmp_path, catalog)
    with pytest.raises(ValueError, match="single occurrence packet exceeds"):
        packetize(
            tmp_path,
            bank,
            bank_manifest,
            stem="too_small",
            max_bytes=512,
        )
    assert not (tmp_path / "too_small.packets.jsonl").exists()
    assert not (tmp_path / "too_small.index.jsonl").exists()
    assert not (tmp_path / "too_small.manifest.json").exists()
    assert not list(tmp_path.glob(".too_small*.tmp"))
    assert not list(tmp_path.glob(".too_small*.sqlite3"))


def test_manifest_hash_mismatch_blocks_publication_after_stream_validation(
    tmp_path, catalog
):
    bank, bank_manifest = build_bank(tmp_path, catalog)
    manifest = json.loads(bank_manifest.read_text(encoding="utf-8"))
    manifest["outputs"]["clauses_jsonl"]["sha256"] = "0" * 64
    committed = {
        key: value
        for key, value in manifest.items()
        if key not in {"generated_at_utc", "content_sha256"}
    }
    manifest["content_sha256"] = MODULE.sha256_object(committed)
    forged_manifest = tmp_path / "wrong-hash.manifest.json"
    forged_manifest.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        packetize(tmp_path, bank, forged_manifest, stem="wrong_hash")
    assert not (tmp_path / "wrong_hash.packets.jsonl").exists()
    assert not (tmp_path / "wrong_hash.index.jsonl").exists()
    assert not (tmp_path / "wrong_hash.manifest.json").exists()


def test_module_has_no_submission_runtime_or_saved_decision_dependency():
    source = MODULE_PATH.read_text(encoding="utf-8").lower()
    forbidden = (
        "import " + "submission",
        "from " + "submission",
        "import pps",
        "from pps",
        "saved" + "_response",
        "dev_" + "labels",
        "train_" + "labels",
    )
    assert not any(token in source for token in forbidden)
