from __future__ import annotations

import copy
import gzip
import hashlib
import importlib.util
import json
import pathlib

import pytest


ROOT = pathlib.Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "tools" / "independent_gold" / "full_record_context.py"
SPEC = importlib.util.spec_from_file_location("independent_full_record_context", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(MODULE)


def fixture_record():
    paragraphs = []
    for number in range(55):
        paragraphs.append(
            f"제{number + 1}항 참가자는 공고문과 과업지시서를 확인하여야 합니다. "
            "문장 경계와 공백 경계를 보존하는 원문입니다.\n"
        )
    text = (
        "용역 입찰공고\n\n"
        "2. 입찰참가자격\n"
        "세부품명번호 8014199001로 입찰참가 등록한 중소기업이어야 합니다.\n"
        "직접생산확인증명서는 계약 체결 전에 제출합니다.\n\n"
        + "".join(paragraphs)
        + "공동이행 구성원의 최소 지분율은 5%입니다.\n"
    )
    return {
        "id": "fixture-full-record-context",
        "docs": [
            {"doc_id": "D0", "type": "공고문", "text": text},
            {"doc_id": "D1", "type": "빈첨부", "text": ""},
        ],
        "dropped_doc_counts": {},
        "input_completeness": {"완전관측": True, "무탈락": True},
        "assembly_policy_version": "fixture-v1",
        "anon_applied": True,
        "meta": {
            "적용계약법": "지방계약법",
            "업무구분": "일반용역",
            "계약방법": "제한경쟁",
            "낙찰방법": "협상에의한계약",
            "배정예산금액": 220_000_000,
            "입찰추정가격": 200_000_000,
            "세부품명번호목록": "기타행사기획및대행서비스[8014199001]",
            "정보화사업여부": "N",
            "공동도급구성방식": "공동이행",
            "공고게시일자": "20260101",
            "개찰예정일자": "20260120",
        },
    }


def load_dev(*record_ids):
    wanted = set(record_ids)
    found = {}
    with gzip.open(ROOT / "data_open" / "dev.jsonl.gz", "rt", encoding="utf-8") as handle:
        for line in handle:
            record = json.loads(line)
            if record["id"] in wanted:
                found[record["id"]] = record
    assert set(found) == wanted
    return found


@pytest.fixture(scope="module")
def built_fixture():
    record = fixture_record()
    return record, MODULE.build_full_record_context(record)


def test_builds_one_canonical_24_item_context_with_all_required_children(built_fixture):
    record, context = built_fixture
    rendered = MODULE.render_full_record_context(context)

    assert json.loads(rendered) == context
    assert context["schema_version"] == "dacon.independent.full_record_context.v1"
    assert context["group"] == "v1-24"
    assert context["target_items"] == [f"v{number}" for number in range(1, 25)]
    assert list(context["fact_contexts"]) == list(MODULE.GROUPS)
    assert list(context["qualification_contexts"]) == list(
        MODULE.QUALIFICATION_GROUPS
    )
    assert list(context["law_contexts"]) == list(MODULE.GROUPS)
    assert all(
        child["schema_version"] == "dacon.independent.fact_context.v4"
        for child in context["fact_contexts"].values()
    )
    assert all(
        child["schema_version"] == "dacon.independent.qualification_context.v4"
        for child in context["qualification_contexts"].values()
    )
    assert all(
        child["schema_version"] == "dacon.independent.law_context.v4"
        for child in context["law_contexts"].values()
    )
    assert len(rendered) == context["bounds"]["rendered_chars"]
    assert len(rendered) <= 300_000
    assert context["bounds"]["truncated"] is False
    assert MODULE.validate_full_record_context(record, context) == []


def test_source_registry_rebuild_from_organizer_record_matches_context(built_fixture):
    record, context = built_fixture
    reconstructed = MODULE.source_span_registry_from_record(record)
    assert reconstructed == context["allowed_span_registry"]
    first_id = next(iter(reconstructed))
    reconstructed[first_id]["quote"] += "조작"
    assert reconstructed != context["allowed_span_registry"]


def test_registry_is_gapless_exact_full_source_and_records_empty_documents(
    built_fixture,
):
    record, context = built_fixture
    registry = context["allowed_span_registry"]
    manifests = context["organizer_record"]["documents"]

    assert "text" not in context["organizer_record"]
    assert manifests[1]["chars"] == 0
    assert manifests[1]["span_ids"] == []
    for doc_index, document in enumerate(record["docs"]):
        spans = [registry[span_id] for span_id in manifests[doc_index]["span_ids"]]
        cursor = 0
        recovered = []
        for span in spans:
            assert span["doc_index"] == doc_index
            assert span["start"] == cursor
            assert span["end"] > span["start"]
            assert len(span["quote"]) <= 480
            assert document["text"][span["start"] : span["end"]] == span["quote"]
            assert span["source_doc_sha256"] == hashlib.sha256(
                document["text"].encode("utf-8")
            ).hexdigest()
            assert span["text_sha256"] == hashlib.sha256(
                span["quote"].encode("utf-8")
            ).hexdigest()
            cursor = span["end"]
            recovered.append(span["quote"])
        assert cursor == len(document["text"])
        assert "".join(recovered) == document["text"]

    nonfinal = [
        registry[span_id]
        for span_id in manifests[0]["span_ids"][:-1]
    ]
    assert nonfinal
    assert all(
        span["quote"][-1].isspace() or span["quote"][-1] in ".!?。！？"
        for span in nonfinal
    )


def test_allowed_span_registry_mapping_is_detached_and_deterministic(built_fixture):
    record, context = built_fixture
    detached = MODULE.allowed_span_registry(context)
    assert detached == context["allowed_span_registry"]
    first_id = next(iter(detached))
    detached[first_id]["quote"] += "변경"
    assert detached != context["allowed_span_registry"]

    rebuilt = MODULE.build_full_record_context(record)
    assert rebuilt == context
    assert MODULE.render(rebuilt) == MODULE.render_full_record_context(context)


def test_child_hash_omission_and_completeness_manifests_are_preserved(built_fixture):
    _, context = built_fixture
    manifest = context["child_context_manifest"]
    for group, child in context["fact_contexts"].items():
        row = manifest["fact_contexts"][group]
        assert row["context_sha256"] == child["context_sha256"]
        assert row["omissions"] == child["omissions"]
        assert row["source_completeness"] == child["source_completeness"]
    for group, child in context["qualification_contexts"].items():
        row = manifest["qualification_contexts"][group]
        assert row["context_sha256"] == child["context_sha256"]
        assert row["omissions"] == child["omissions"]
        assert row["source_completeness"] == child["source_completeness"]
    for group, child in context["law_contexts"].items():
        row = manifest["law_contexts"][group]
        assert row["context_sha256"] == child["child_context_sha256"]
        assert row["omissions"] == child["omissions"]


def test_law_references_are_deduplicated_by_source_identity_with_unions(built_fixture):
    _, context = built_fixture
    registry = context["law_reference_registry"]
    links = [
        (group, link)
        for group, child in context["law_contexts"].items()
        for link in child["reference_links"]
    ]
    assert len(links) > len(registry)
    identities = [MODULE.canonical_json(row["source_identity"]) for row in registry.values()]
    assert len(identities) == len(set(identities))

    groups_by_id = {registry_id: set() for registry_id in registry}
    items_by_id = {registry_id: set() for registry_id in registry}
    for group, link in links:
        registry_id = link["registry_id"]
        groups_by_id[registry_id].add(group)
        items_by_id[registry_id].update(link["support_items"])
    for registry_id, row in registry.items():
        assert set(row["applicable_groups"]) == groups_by_id[registry_id]
        assert set(row["applicable_items"]) == items_by_id[registry_id]
        source = row["source"]
        path = ROOT / source["relative_path"]
        text = path.read_text(encoding="utf-8")
        assert hashlib.sha256(text.encode()).hexdigest() == source["source_sha256"]
        assert text[source["start"] : source["end"]] == source["quote"]
        assert hashlib.sha256(source["quote"].encode()).hexdigest() == source[
            "quote_sha256"
        ]

    threshold = next(
        row
        for row in registry.values()
        if "national_notified_goods_services_threshold" in row["reference_ids"]
    )
    assert set(threshold["applicable_groups"]) == {
        "v1-4",
        "v5-8",
        "v10-13",
        "v14-19",
    }


def test_registry_and_child_tampering_are_detected(built_fixture):
    record, context = built_fixture
    tampered = copy.deepcopy(context)
    span_id = next(iter(tampered["allowed_span_registry"]))
    tampered["allowed_span_registry"][span_id]["quote"] += "변조"
    errors = MODULE.validate_full_record_context(record, tampered)
    assert any(error.startswith(f"span:{span_id}:content_mismatch") for error in errors)
    assert "context_hash_mismatch" in errors

    gap = copy.deepcopy(context)
    span = gap["allowed_span_registry"][span_id]
    span["start"] += 1
    errors = MODULE.validate_full_record_context(record, gap)
    assert any("gap_or_overlap" in error for error in errors)

    child_tamper = copy.deepcopy(context)
    child = child_tamper["fact_contexts"]["v1-4"]
    if child["source_segments"]:
        child["source_segments"][0]["quote"] += "변조"
        errors = MODULE.validate_full_record_context(record, child_tamper)
        assert any(error.startswith("fact:v1-4:") for error in errors)


def test_hard_limits_and_forbidden_answer_data_fail_without_truncation():
    too_large = fixture_record()
    too_large["id"] = "fixture-over-100k"
    too_large["docs"] = [
        {"doc_id": "D0", "type": "공고문", "text": "가" * 100_001}
    ]
    with pytest.raises(MODULE.FullRecordContextError, match="source_chars"):
        MODULE.build_full_record_context(too_large)

    contaminated = fixture_record()
    contaminated["labels"] = {"v1": 1}
    with pytest.raises(MODULE.FullRecordContextError, match="forbidden"):
        MODULE.build_full_record_context(contaminated)


def test_any_child_validation_error_aborts_build(monkeypatch):
    monkeypatch.setattr(
        MODULE.fact_context,
        "validate_fact_context",
        lambda *args, **kwargs: ["synthetic_validation_error"],
    )
    with pytest.raises(MODULE.FullRecordContextError, match="failed validation"):
        MODULE.build_full_record_context(fixture_record())


def test_dev11_and_dev13_complete_context_smoke_without_label_assertions():
    records = load_dev("PPS-DEV-11", "PPS-DEV-13")
    for record_id in ("PPS-DEV-11", "PPS-DEV-13"):
        record = records[record_id]
        context = MODULE.build_full_record_context(record)
        assert MODULE.validate_full_record_context(record, context) == []
        assert context["organizer_record"]["id"] == record_id
        assert context["target_items"] == [f"v{number}" for number in range(1, 25)]
        assert len(MODULE.render_full_record_context(context)) <= 300_000
        manifests = context["organizer_record"]["documents"]
        registry = context["allowed_span_registry"]
        for index, document in enumerate(record["docs"]):
            recovered = "".join(
                registry[span_id]["quote"] for span_id in manifests[index]["span_ids"]
            )
            assert recovered == document["text"]


def test_source_has_no_competition_runtime_or_saved_response_dependency():
    source = MODULE_PATH.read_text(encoding="utf-8").casefold()
    forbidden = (
        "import " + "submission",
        "from " + "submission",
        "import " + "pps",
        "from " + "pps",
        "saved" + "_response",
        "gemma" + "_response",
    )
    assert not any(token in source for token in forbidden)
