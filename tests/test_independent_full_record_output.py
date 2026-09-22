import copy
import hashlib
import importlib.util
import json
import pathlib

import pytest

from tools.independent_gold import full_record_context


ROOT = pathlib.Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "tools" / "independent_gold" / "full_record_output.py"
SPEC = importlib.util.spec_from_file_location("independent_full_record_output", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(MODULE)


def fixture_record():
    return {
        "id": "REC-1",
        "docs": [
            {
                "doc_id": "D0",
                "type": "공고문",
                "text": "공통 판정 근거\n기관 제한 근거\n예외 검토 문구",
            },
            {
                "doc_id": "D1",
                "type": "과업지시서",
                "text": "두 번째 문서의 직접 근거",
            },
        ],
        "meta": {},
        "input_completeness": {"fully_observed": True},
        "dropped_doc_counts": {},
    }


def registry(record=None):
    return full_record_context.source_span_registry_from_record(
        record or fixture_record()
    )


def decision(
    *,
    label=0,
    premises=("SRC-D0000-S000000",),
    evidence=None,
    completeness="sufficient",
    missing=None,
):
    return {
        "label": label,
        "confidence": "H",
        "rationale": "제공 원문과 항목 정의를 대조한 판정",
        "premise_span_ids": list(premises),
        "exception_analysis": None,
        "completeness": completeness,
        "material_missing_information": missing,
        "positive_evidence_span_id": evidence,
    }


def valid_output(record=None):
    record = record or fixture_record()
    decisions = {item: decision() for item in MODULE.ITEMS}
    decisions["v1"] = decision(label=1, evidence="SRC-D0000-S000000")
    # v10 is an absence-detection item: label 1 deliberately has no positive
    # witness, while its premise records what source scope was assessed.
    decisions["v10"] = decision(label=1)
    return {
        "source_span_ids": ["SRC-D0000-S000000"],
        "decisions": decisions,
    }


def normalize(value):
    return MODULE.normalize_output(value)


def nested_keys(value):
    if isinstance(value, dict):
        for key, child in value.items():
            yield key
            yield from nested_keys(child)
    elif isinstance(value, list):
        for child in value:
            yield from nested_keys(child)


def test_schema_has_fixed_required_v1_through_v24_and_no_property_names():
    schema = MODULE.output_schema()
    assert "propertyNames" not in set(nested_keys(schema))
    assert schema["additionalProperties"] is False
    assert schema["required"] == ["source_span_ids", "decisions"]
    assert schema["properties"]["source_span_ids"]["items"] == {
        "type": "string", "minLength": 1, "maxLength": 128
    }
    decisions = schema["properties"]["decisions"]
    assert decisions["additionalProperties"] is False
    assert decisions["required"] == list(MODULE.ITEMS)
    assert list(decisions["properties"]) == list(MODULE.ITEMS)
    assert len(decisions["properties"]) == 24
    assert set(decisions["properties"]["v1"]["required"]) == {
        "label",
        "confidence",
        "rationale",
        "premise_span_ids",
        "exception_analysis",
        "completeness",
        "material_missing_information",
        "positive_evidence_span_id",
    }


def test_parse_is_strict_json_and_rejects_duplicate_keys_or_wrappers():
    parsed = MODULE.parse_output(json.dumps(valid_output(), ensure_ascii=False))
    assert parsed["decisions"]["v1"]["label"] == 1
    with pytest.raises(MODULE.FullRecordOutputError, match="duplicate JSON"):
        MODULE.parse_output('{"source_span_ids": [], "source_span_ids": [], "decisions": {}}')
    with pytest.raises(MODULE.FullRecordOutputError, match="one valid JSON"):
        MODULE.parse_output("```json\n{}\n```")
    with pytest.raises(MODULE.FullRecordOutputError, match="non-standard JSON"):
        MODULE.parse_output('{"x": NaN}')
    with pytest.raises(MODULE.FullRecordOutputError, match="JSON object"):
        MODULE.parse_output("[]")


def test_valid_output_projects_exactly_24_canonical_cells_and_document_hash():
    record = fixture_record()
    raw = valid_output(record)
    normalized = normalize(raw)
    MODULE.validate_output(normalized, record)
    ledger = MODULE.canonical_ledger_projection(raw, record)

    assert ledger["schema_version"] == MODULE.LEDGER_SCHEMA_VERSION
    assert ledger["record_id"] == "REC-1"
    assert len(ledger["cells"]) == 24
    assert [cell["item"] for cell in ledger["cells"]] == list(MODULE.ITEMS)
    assert all(cell["record_id"] == "REC-1" for cell in ledger["cells"])
    assert ledger["source_spans"][0]["source_doc_sha256"] == hashlib.sha256(
        record["docs"][0]["text"].encode("utf-8")
    ).hexdigest()
    assert "source_spans" not in raw
    assert raw["source_span_ids"] == ["SRC-D0000-S000000"]


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (
            lambda value: value["source_span_ids"].__setitem__(0, {"span_id": "SRC-D0000-S000000", "quote": "조작"}),
            "invalid span ID",
        ),
        (
            lambda value: value["source_span_ids"].__setitem__(0, "SRC-D0000-S999999"),
            "not in allowed registry",
        ),
        (
            lambda value: value["source_span_ids"].append(value["source_span_ids"][0]),
            "duplicate span ID",
        ),
        (
            lambda value: value["decisions"]["v2"].update(
                premise_span_ids=["not-declared"]
            ),
            "unknown references",
        ),
    ],
)
def test_coordinate_uniqueness_and_reference_integrity_are_hard_failures(
    mutation, message
):
    raw = valid_output()
    mutation(raw)
    with pytest.raises(MODULE.FullRecordOutputError, match=message):
        MODULE.validate_output(normalize(raw), fixture_record())


def test_unused_span_and_evidence_not_in_premises_are_rejected():
    record = fixture_record()
    raw = valid_output(record)
    raw["source_span_ids"].append("SRC-D0001-S000000")
    with pytest.raises(MODULE.FullRecordOutputError, match="unused spans"):
        MODULE.validate_output(normalize(raw), record)

    raw = valid_output(record)
    raw["source_span_ids"].append("SRC-D0001-S000000")
    raw["decisions"]["v1"]["positive_evidence_span_id"] = "SRC-D0001-S000000"
    with pytest.raises(MODULE.FullRecordOutputError, match="must also appear"):
        MODULE.validate_output(normalize(raw), record)


def test_label_evidence_rules_cover_zero_unknown_positive_and_absence_items():
    raw = valid_output()
    raw["decisions"]["v2"]["positive_evidence_span_id"] = "SRC-D0000-S000000"
    with pytest.raises(MODULE.FullRecordOutputError, match="requires null"):
        MODULE.validate_output(normalize(raw), fixture_record())

    raw = valid_output()
    raw["decisions"]["v2"] = decision(label=1, evidence=None)
    with pytest.raises(MODULE.FullRecordOutputError, match="requires exact evidence"):
        MODULE.validate_output(normalize(raw), fixture_record())

    raw = valid_output()
    raw["decisions"]["v10"]["positive_evidence_span_id"] = "SRC-D0000-S000000"
    with pytest.raises(MODULE.FullRecordOutputError, match="requires null"):
        MODULE.validate_output(normalize(raw), fixture_record())

    raw = valid_output()
    raw["decisions"]["v2"] = decision(
        label="U",
        premises=("SRC-D0000-S000000",),
        evidence="SRC-D0000-S000000",
        completeness="unknown",
        missing="참가자격을 정한 별첨 문서가 제공되지 않음",
    )
    with pytest.raises(MODULE.FullRecordOutputError, match="requires null"):
        MODULE.validate_output(normalize(raw), fixture_record())


def test_binary_needs_premise_and_u_needs_material_specific_missing_information():
    raw = valid_output()
    raw["decisions"]["v2"]["premise_span_ids"] = []
    with pytest.raises(MODULE.FullRecordOutputError, match="binary conclusion"):
        MODULE.validate_output(normalize(raw), fixture_record())

    raw = valid_output()
    raw["decisions"]["v2"] = decision(
        label="U", premises=(), completeness="sufficient", missing=None
    )
    with pytest.raises(MODULE.FullRecordOutputError, match="U requires"):
        MODULE.validate_output(normalize(raw), fixture_record())

    raw = valid_output()
    raw["decisions"]["v2"] = decision(
        label="U", premises=(), completeness="unknown", missing="정보 부족"
    )
    with pytest.raises(MODULE.FullRecordOutputError, match="concrete material"):
        MODULE.validate_output(normalize(raw), fixture_record())

    raw = valid_output()
    raw["decisions"]["v2"] = decision(
        label="U",
        premises=(),
        completeness="incomplete_material",
        missing="지역제한 예외를 정한 별첨 제3장이 제공되지 않음",
    )
    MODULE.validate_output(normalize(raw), fixture_record())


def test_completeness_and_missing_information_are_coupled_both_ways():
    raw = valid_output()
    raw["decisions"]["v2"]["material_missing_information"] = "불필요한 값"
    with pytest.raises(MODULE.FullRecordOutputError, match="requires null"):
        MODULE.validate_output(normalize(raw), fixture_record())

    raw = valid_output()
    raw["decisions"]["v2"].update(
        completeness="incomplete_material", material_missing_information=None
    )
    with pytest.raises(MODULE.FullRecordOutputError, match="concrete material"):
        MODULE.validate_output(normalize(raw), fixture_record())

    raw = valid_output()
    raw["decisions"]["v2"].update(
        completeness="incomplete_material",
        material_missing_information="참가자격을 정한 별첨 문서가 제공되지 않음",
    )
    with pytest.raises(MODULE.FullRecordOutputError, match="binary conclusion requires"):
        MODULE.validate_output(normalize(raw), fixture_record())


def test_positive_evidence_reconstructed_span_is_under_500_character_limit():
    record = fixture_record()
    record["docs"][0]["text"] += "\n" + "가" * 501
    raw = valid_output(record)
    long_id = "SRC-D0000-S000001"
    raw["source_span_ids"].append(long_id)
    raw["decisions"]["v1"]["premise_span_ids"].append(long_id)
    raw["decisions"]["v1"]["positive_evidence_span_id"] = long_id
    ledger = MODULE.canonical_ledger_projection(normalize(raw), record)
    assert max(len(span["quote"]) for span in ledger["source_spans"]) <= 480


def test_allowed_registry_must_be_complete_and_match_organizer_source():
    record = fixture_record()
    raw = valid_output(record)
    source_registry = registry(record)

    normalized = normalize(raw)
    MODULE.validate_output(normalized, record, allowed_span_registry=source_registry)
    MODULE.validate_output(normalized, record)
    MODULE.validate_output(
        normalized,
        record,
        allowed_span_registry={
            key: {field: value[field] for field in ("doc_index", "start", "end", "quote")}
            for key, value in source_registry.items()
        },
    )

    changed_registry = copy.deepcopy(source_registry)
    changed_registry["SRC-D0000-S000000"]["end"] -= 1
    changed_registry["SRC-D0000-S000000"]["quote"] = changed_registry[
        "SRC-D0000-S000000"
    ]["quote"][:-1]
    with pytest.raises(MODULE.FullRecordOutputError, match="not the canonical organizer span"):
        MODULE.validate_output(
            normalized, record, allowed_span_registry=changed_registry
        )

    wrong_hash_registry = copy.deepcopy(source_registry)
    wrong_hash_registry["SRC-D0000-S000000"]["source_doc_sha256"] = "0" * 64
    with pytest.raises(MODULE.FullRecordOutputError, match="sha256: mismatch"):
        MODULE.validate_output(
            normalized, record, allowed_span_registry=wrong_hash_registry
        )

    with pytest.raises(MODULE.FullRecordOutputError, match="canonical ID set mismatch"):
        MODULE.validate_output(normalized, record, allowed_span_registry=[])


def test_whitespace_only_organizer_span_is_preserved_but_cannot_be_a_premise():
    record = fixture_record()
    record["docs"].append({"doc_id": "D2", "type": "빈 첨부", "text": "\n \t"})
    raw = valid_output(record)
    MODULE.validate_output(normalize(raw), record)

    empty_id = "SRC-D0002-S000000"
    raw["source_span_ids"].append(empty_id)
    raw["decisions"]["v1"]["premise_span_ids"].append(empty_id)
    with pytest.raises(MODULE.FullRecordOutputError, match="whitespace-only source"):
        MODULE.validate_output(normalize(raw), record)


def test_normalization_rejects_schema_shadowing_and_does_not_modify_span_ids():
    raw = valid_output()
    raw["unexpected"] = "shadow"
    with pytest.raises(MODULE.FullRecordOutputError, match="extra"):
        normalize(raw)

    raw = valid_output()
    raw["decisions"].pop("v24")
    with pytest.raises(MODULE.FullRecordOutputError, match="missing"):
        normalize(raw)

    raw = valid_output()
    raw["decisions"]["v1"]["rationale"] = "  설명  "
    normalized = normalize(raw)
    assert normalized["decisions"]["v1"]["rationale"] == "설명"
    assert normalized["source_span_ids"] == ["SRC-D0000-S000000"]

    raw = valid_output()
    raw["source_spans"] = [{"span_id": "SRC-D0000-S000000", "quote": "forged"}]
    with pytest.raises(MODULE.FullRecordOutputError, match="extra"):
        normalize(raw)

    raw = valid_output()
    raw["decisions"]["v2"]["label"] = 0.0
    with pytest.raises(MODULE.FullRecordOutputError, match="expected 0, 1"):
        normalize(raw)
