import gzip
import hashlib
import importlib.util
import json
import pathlib

import pytest


ROOT = pathlib.Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "tools" / "independent_gold" / "packetize.py"
SPEC = importlib.util.spec_from_file_location("independent_gold_packetize", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(MODULE)


def fixture_record():
    return {
        "id": "fixture",
        "docs": [
            {
                "doc_id": "notice",
                "type": "공고문",
                "text": "머리말\n" + "가" * 500 + "최근 3년 수행실적이 3천만원 이상인 업체\n" + "끝" * 300,
            },
            {
                "doc_id": "spec",
                "type": "규격서",
                "text": "규격\n제조사·모델명 : Example Z9\n나머지",
            },
        ],
        "meta": {"계약방법": "일반경쟁"},
        "input_completeness": {"fully_observed": True},
        "dropped_doc_counts": {},
    }


def test_packet_preserves_exact_coordinates_and_hashes():
    record = fixture_record()
    packet = MODULE.packetize_record(record, max_chars=4_000)
    assert packet["selected_chars"] <= 4_000
    assert packet["schema_version"] == MODULE.SCHEMA_VERSION
    for segment in packet["segments"]:
        source = record["docs"][segment["doc_index"]]["text"]
        exact = source[segment["start"] : segment["end"]]
        assert segment["text"] == exact
        assert segment["text_sha256"] == hashlib.sha256(exact.encode("utf-8")).hexdigest()
        assert segment["source_doc_sha256"] == hashlib.sha256(source.encode("utf-8")).hexdigest()
        for anchor in segment["anchors"]:
            assert source[anchor["start"] : anchor["end"]] == anchor["keyword"]


def test_item_specific_windows_are_indexed_and_overlaps_are_merged():
    packet = MODULE.packetize_record(fixture_record(), max_chars=4_000)
    assert packet["items"]["v2"]["segment_ids"]
    assert packet["items"]["v3"]["segment_ids"]
    assert packet["items"]["v4"]["segment_ids"]
    assert packet["items"]["v9"]["segment_ids"]
    assert set(packet["items"]["v2"]["segment_ids"]) & set(
        packet["items"]["v3"]["segment_ids"]
    )
    intervals = [
        (segment["doc_index"], segment["start"], segment["end"])
        for segment in packet["segments"]
    ]
    for left, right in zip(intervals, intervals[1:]):
        if left[0] == right[0]:
            assert left[2] <= right[1]


def test_target_items_remove_unrelated_anchor_candidates():
    packet = MODULE.packetize_record(
        fixture_record(), max_chars=4_000, target_items=("v9",)
    )
    assert packet["target_items"] == ["v9"]
    assert set(packet["items"]) == {"v9"}
    assert packet["items"]["v9"]["segment_ids"]
    assert all(
        set(anchor["items"]) <= {"v9"}
        for segment in packet["segments"]
        for anchor in segment["anchors"]
    )
    assert all(set(segment["items"]) <= {"v9"} for segment in packet["segments"])


def test_target_items_are_validated():
    with pytest.raises(ValueError, match="must not be empty"):
        MODULE.packetize_record(fixture_record(), target_items=())
    with pytest.raises(ValueError, match="unknown target_items"):
        MODULE.packetize_record(fixture_record(), target_items=("v25",))


def test_truncated_packet_never_claims_absence_is_safe():
    record = fixture_record()
    packet = MODULE.packetize_record(record, max_chars=700)
    assert not packet["full_source_visible"]
    assert packet["absence_warning"]
    for item in MODULE.ABSENCE_ITEMS:
        assert packet["items"][item]["absence_safe"] is False


def test_rendered_packet_contains_verbatim_segments():
    packet = MODULE.packetize_record(fixture_record(), max_chars=4_000)
    rendered = MODULE.render_packet(packet)
    for segment in packet["segments"]:
        assert segment["text"] in rendered
        assert f"range=[{segment['start']},{segment['end']})" in rendered


def test_packetizer_has_no_production_runtime_dependency():
    source = MODULE_PATH.read_text(encoding="utf-8")
    forbidden = (
        "import submission",
        "from submission",
        "import pps",
        "from pps",
        "saved" + "_response",
        "development" + "_predictions",
    )
    assert not any(token in source for token in forbidden)


def test_official_dev_nonempty_positive_evidence_recall_is_complete():
    report = MODULE.audit_dev_evidence(
        ROOT / "data_open" / "dev.jsonl.gz",
        ROOT / "data_open" / "dev_labels.csv",
        max_chars=MODULE.DEFAULT_MAX_CHARS,
    )
    assert report["records"] == 200
    assert report["positive_nonempty_evidence"] > 0
    assert report["evidence_located_in_source"] == report["positive_nonempty_evidence"]
    assert report["evidence_covered"] == report["positive_nonempty_evidence"]
    assert report["evidence_recall"] == 1.0
    assert report["packet_length"]["max"] <= MODULE.DEFAULT_MAX_CHARS


def test_official_dev_group_safe_budgets_cover_every_positive_evidence():
    total_evidence = 0
    total_covered = 0
    for group_name, items in MODULE.ITEM_GROUPS.items():
        budget = MODULE.DEV_EVIDENCE_SAFE_GROUP_MAX_CHARS[group_name]
        assert 4_000 <= budget <= 6_000
        report = MODULE.audit_dev_evidence(
            ROOT / "data_open" / "dev.jsonl.gz",
            ROOT / "data_open" / "dev_labels.csv",
            max_chars=budget,
            target_items=items,
        )
        assert report["missed"] == []
        assert report["packet_length"]["max"] <= budget
        total_evidence += report["positive_nonempty_evidence"]
        total_covered += report["evidence_covered"]
    assert total_evidence == 54
    assert total_covered == 54


def test_packetize_cli_shape_on_first_official_record(tmp_path):
    source = ROOT / "data_open" / "dev.jsonl.gz"
    output = tmp_path / "packet.jsonl"
    assert MODULE.main(
        [
            "packetize",
            "--input",
            str(source),
            "--output",
            str(output),
            "--limit",
            "1",
        ]
    ) == 0
    rows = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 1
    with gzip.open(source, "rt", encoding="utf-8") as handle:
        original = json.loads(next(handle))
    assert rows[0]["id"] == original["id"]
    assert rows[0]["source_sha256"] == MODULE.sha256_object(original)
