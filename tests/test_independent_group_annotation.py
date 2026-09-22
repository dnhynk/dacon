from __future__ import annotations

import gzip
import importlib.util
import json
import pathlib

import pytest


ROOT = pathlib.Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "tools" / "independent_gold" / "annotate_groups.py"
SPEC = importlib.util.spec_from_file_location("independent_gold_annotate_groups", MODULE_PATH)
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
                "text": "입찰참가자격\n최근 3년 수행실적이 3천만원 이상인 업체\n끝",
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


def fake_result(record, group_name, run_key="run"):
    items, _ = MODULE.GROUP_BY_NAME[group_name]
    decisions = {
        item: {
            "label": 0,
            "confidence": "high",
            "evidence": "",
            "evidence_locations": [],
        }
        for item in items
    }
    return {
        "schema_version": MODULE.GROUP_SCHEMA_VERSION,
        "id": record["id"],
        "group": group_name,
        "target_items": list(items),
        "status": "ok",
        "run_key": run_key,
        "source_sha256": MODULE.sha256_object(record),
        "packet_sha256": "p-" + group_name,
        "rubric_sha256": "r",
        "system_prompt_sha256": "s",
        "request_sha256": "q",
        "raw_response_sha256": "raw",
        "content_sha256": "content",
        "model_provenance": {"requested_model": "independent-model"},
        "attempt": 1,
        "usage": {},
        "completed_utc": "2026-01-01T00:00:00+00:00",
        "elapsed_seconds": 0.1,
        "decisions": decisions,
    }


def test_group_contract_and_budgets_are_exact():
    assert MODULE.GROUPS == (
        ("v1-4", ("v1", "v2", "v3", "v4"), 6000),
        ("v5-8", ("v5", "v6", "v7", "v8"), 4000),
        ("v9", ("v9",), 4000),
        ("v10-13", ("v10", "v11", "v12", "v13"), 4000),
        ("v14-19", ("v14", "v15", "v16", "v17", "v18", "v19"), 4000),
        ("v20", ("v20",), 4000),
        ("v21-23", ("v21", "v22", "v23"), 4000),
        ("v24", ("v24",), 6000),
    )
    assert tuple(item for _, items, _ in MODULE.GROUPS for item in items) == MODULE.ITEMS


def test_rubric_contains_common_and_only_target_item_sections():
    rubric = MODULE.RUBRIC_PATH.read_text(encoding="utf-8")
    prompt = MODULE.extract_group_rubric(rubric, ("v1", "v4"))
    assert "## 공통 원칙" in prompt
    assert "### v1 " in prompt
    assert "### v4 " in prompt
    assert "### v2 " not in prompt
    assert "### v10 " not in prompt
    assert "대상 순서는 정확히 v1,v4" in prompt


def test_json_schema_is_group_length_not_twenty_four():
    payload = MODULE.group_request_payload(
        "model", [{"role": "system", "content": "x"}], ("v21", "v22", "v23")
    )
    schema = payload["response_format"]["json_schema"]["schema"]
    assert schema["properties"]["labels"]["pattern"] == "^[01U]{3}$"
    assert schema["properties"]["confidence"]["pattern"] == "^[HML]{3}$"
    assert schema["properties"]["evidence"]["propertyNames"]["enum"] == [
        "v21",
        "v22",
        "v23",
    ]


def test_normalization_rejects_raw_label_evidence_contradictions_and_schema_shadowing():
    with pytest.raises(ValueError, match="evidence key for v3 is forbidden"):
        MODULE.normalize_group_decision(
            {
                "labels": "0000",
                "confidence": "HHHH",
                "evidence": {"v3": "실적 제한 문구"},
            },
            ("v1", "v2", "v3", "v4"),
        )
    with pytest.raises(ValueError, match="positive v3 requires non-empty evidence"):
        MODULE.normalize_group_decision(
            {"labels": "0010", "confidence": "HHHH", "evidence": {}},
            ("v1", "v2", "v3", "v4"),
        )
    with pytest.raises(ValueError, match="evidence key for v10 is forbidden"):
        MODULE.normalize_group_decision(
            {"labels": "1000", "confidence": "HHHH", "evidence": {"v10": "부재"}},
            ("v10", "v11", "v12", "v13"),
        )
    with pytest.raises(ValueError, match="fields must be exactly"):
        MODULE.normalize_group_decision(
            {
                "labels": "0000",
                "confidence": "HHHH",
                "evidence": {},
                "prediction": "0000",
            },
            ("v1", "v2", "v3", "v4"),
        )


def test_group_prompt_includes_hash_checked_supplied_law_not_a_label_vector():
    record = fixture_record()
    packet = MODULE.packetize_record(record, max_chars=6000, target_items=("v1", "v2", "v3", "v4"))
    law = MODULE.supplied_law_context.build_law_context("v1-4")
    messages = MODULE.build_group_messages(
        record, packet, "rubric", "v1-4", law_context_payload=law
    )
    user = messages[1]["content"]
    assert law["context_sha256"] in user
    assert "2억 3천만 원" in user
    assert '"labels"' not in MODULE.supplied_law_context.render_law_context(law)


def test_run_key_commits_to_law_context_configuration():
    common = dict(
        rubric_sha256="r" * 64,
        endpoint="http://localhost/v1/chat/completions",
        model="independent-model",
        model_revision="revision",
        seed=17,
        max_tokens=768,
    )
    with_law = MODULE.make_run_key(**common)
    without_law = MODULE.make_run_key(**common, use_law_context=False)
    assert with_law != without_law


def test_run_key_commits_to_qualification_context_schema_budget_and_catalog_hash():
    common = dict(
        rubric_sha256="r" * 64,
        endpoint="http://localhost/v1/chat/completions",
        model="independent-model",
        model_revision="revision",
        seed=17,
        max_tokens=768,
        qualification_catalog_sha256="a" * 64,
    )
    baseline = MODULE.make_run_key(**common)
    assert baseline != MODULE.make_run_key(
        **common, qualification_context_max_chars=13_999
    )
    assert baseline != MODULE.make_run_key(
        **{**common, "qualification_catalog_sha256": "b" * 64}
    )
    assert baseline != MODULE.make_run_key(
        **common, use_qualification_context=False
    )


def test_qualification_source_segment_is_valid_positive_evidence():
    record = fixture_record()
    quote = "최근 3년 수행실적이 3천만원 이상인 업체"
    text = record["docs"][0]["text"]
    start = text.index(quote)
    qualification = {
        "source_segments": [
            {
                "segment_id": "Q0",
                "doc_index": 0,
                "doc_id": "notice",
                "source_doc_sha256": MODULE.sha256_text(text),
                "start": start,
                "quote": quote,
            }
        ]
    }
    decisions = {
        "v12": {
            "label": 1,
            "confidence": "high",
            "evidence": quote,
            "evidence_locations": [],
        }
    }
    errors = MODULE.validate_evidence(
        decisions,
        {"segments": []},
        qualification_context_payload=qualification,
    )
    assert errors == []
    location = decisions["v12"]["evidence_locations"][0]
    assert location["origin"] == "qualification_context"
    assert text[location["start"] : location["end"]] == quote


def test_relevant_annotation_injects_and_receipts_qualification_context(monkeypatch):
    with gzip.open(ROOT / "data_open" / "dev.jsonl.gz", "rt", encoding="utf-8") as handle:
        record = json.loads(next(handle))
    captured = {}
    response = {
        "id": "response-qualification",
        "model": "independent-model",
        "choices": [
            {
                "finish_reason": "stop",
                "message": {
                    "content": '{"labels":"0000","confidence":"HHHH","evidence":{}}'
                },
            }
        ],
    }

    def fake_post(endpoint, payload, timeout, api_key):
        captured["payload"] = payload
        return response

    monkeypatch.setattr(MODULE, "post_json", fake_post)
    result = MODULE.annotate_group(
        record,
        group_name="v10-13",
        rubric=MODULE.RUBRIC_PATH.read_text(encoding="utf-8"),
        endpoint="http://localhost/v1/chat/completions",
        model="independent-model",
        retries=1,
        run_key="qualification-run",
        use_fact_context=False,
        use_law_context=False,
    )
    assert result["status"] == "ok"
    assert len(result["qualification_context_sha256"]) == 64
    assert (
        result["qualification_context_schema_version"]
        == MODULE.source_qualification_context.SCHEMA_VERSION
    )
    assert result["qualification_context_bounds"]["max_chars"] == 16_000
    assert len(result["qualification_catalog_sha256"]) == 64
    prompt = captured["payload"]["messages"][1]["content"]
    assert "QUALIFICATION_FACT_CONTEXT" in prompt
    assert result["qualification_context_sha256"] in prompt


def test_run_prepares_qualification_facts_once_and_only_routes_to_relevant_groups(
    tmp_path, monkeypatch
):
    record = fixture_record()
    source = tmp_path / "records.jsonl"
    output = tmp_path / "annotations.jsonl"
    groups = tmp_path / "groups.jsonl"
    source.write_text(MODULE.canonical_json(record) + "\n", encoding="utf-8")

    class FakeCatalog:
        sha256 = "c" * 64

    fake_catalog = FakeCatalog()
    prepared = {"record_id": record["id"], "sentinel": object()}
    prepare_calls = []
    routed = {}

    monkeypatch.setattr(
        MODULE.source_qualification_context.qualification_facts.CatalogReference,
        "load",
        lambda *args, **kwargs: fake_catalog,
    )

    def fake_prepare(value, *, catalog):
        prepare_calls.append((value["id"], catalog))
        return prepared

    def fake_annotate(value, *, group_name, run_key, prepared_qualification, **kwargs):
        routed[group_name] = prepared_qualification
        return fake_result(value, group_name, run_key=run_key)

    monkeypatch.setattr(
        MODULE.source_qualification_context, "prepare_qualification_input", fake_prepare
    )
    monkeypatch.setattr(MODULE, "annotate_group", fake_annotate)
    args = MODULE.build_parser().parse_args(
        [
            "--input",
            str(source),
            "--output",
            str(output),
            "--group-output",
            str(groups),
            "--endpoint",
            "http://localhost/v1/chat/completions",
            "--model",
            "independent-model",
            "--workers",
            "1",
            "--no-fact-context",
            "--no-law-context",
        ]
    )
    stats = MODULE.run(args)
    assert stats["group_calls"] == 8
    assert prepare_calls == [(record["id"], fake_catalog)]
    assert {
        group for group, value in routed.items() if value is prepared
    } == set(MODULE.source_qualification_context.GROUP_FAMILIES)
    assert all(
        value is None
        for group, value in routed.items()
        if group not in MODULE.source_qualification_context.GROUP_FAMILIES
    )


def test_annotation_retains_packet_hash_raw_hash_and_exact_coordinates(monkeypatch):
    record = fixture_record()
    quote = "제조사·모델명 : Example Z9"
    response = {
        "id": "response-1",
        "model": "different-family-model-rev",
        "created": 1,
        "system_fingerprint": "fp",
        "choices": [
            {
                "finish_reason": "stop",
                "message": {
                    "content": json.dumps(
                        {"labels": "1", "confidence": "H", "evidence": {"v9": quote}},
                        ensure_ascii=False,
                    )
                },
            }
        ],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5},
    }
    monkeypatch.setattr(MODULE, "post_json", lambda endpoint, payload, timeout, api_key: response)
    result = MODULE.annotate_group(
        record,
        group_name="v9",
        rubric=MODULE.RUBRIC_PATH.read_text(encoding="utf-8"),
        endpoint="http://localhost:8000/v1/chat/completions?secret=hidden",
        model="different-family-model",
        model_revision="revision-42",
        retries=1,
    )
    assert result["status"] == "ok"
    assert result["source_sha256"] == MODULE.sha256_object(record)
    assert len(result["packet_sha256"]) == 64
    assert result["raw_response_sha256"] == MODULE.sha256_text(MODULE.canonical_json(response))
    assert json.loads(result["raw_content"])["evidence"]["v9"] == quote
    assert result["model_provenance"]["declared_model_revision"] == "revision-42"
    assert "secret" not in result["model_provenance"]["endpoint_origin"]
    location = result["decisions"]["v9"]["evidence_locations"][0]
    source = record["docs"][location["doc_index"]]["text"]
    assert source[location["start"] : location["end"]] == quote


def test_hallucinated_positive_evidence_makes_group_error(monkeypatch):
    response = {
        "choices": [
            {
                "message": {
                    "content": '{"labels":"1","confidence":"H","evidence":{"v9":"없는 문구"}}'
                }
            }
        ]
    }
    monkeypatch.setattr(MODULE, "post_json", lambda endpoint, payload, timeout, api_key: response)
    result = MODULE.annotate_group(
        fixture_record(),
        group_name="v9",
        rubric=MODULE.RUBRIC_PATH.read_text(encoding="utf-8"),
        endpoint="http://localhost/v1/chat/completions",
        model="independent-model",
        retries=1,
    )
    assert result["status"] == "error"
    assert "evidence_not_verbatim_in_packet" in result["error"]
    assert result["raw_response_sha256"]
    assert "없는 문구" in result["raw_content"]


def test_merge_produces_exactly_twenty_four_cells_and_receipts():
    record = fixture_record()
    results = {
        group_name: fake_result(record, group_name) for group_name, _, _ in MODULE.GROUPS
    }
    merged = MODULE.merge_group_results(record, results, run_key="run")
    assert merged["status"] == "ok"
    assert tuple(merged["decisions"]) == MODULE.ITEMS
    assert len(merged["group_receipts"]) == 8
    assert merged["decisions"]["v20"]["group"] == "v20"
    assert merged["group_receipts"]["v9"]["raw_response_sha256"] == "raw"


def test_resume_loader_accepts_only_matching_valid_run(tmp_path):
    record = fixture_record()
    good = fake_result(record, "v9", run_key="wanted")
    wrong = fake_result(record, "v20", run_key="other")
    failed = fake_result(record, "v24", run_key="wanted")
    failed["status"] = "error"
    path = tmp_path / "groups.jsonl"
    path.write_text(
        "\n".join(MODULE.canonical_json(row) for row in (good, wrong, failed)) + "\n",
        encoding="utf-8",
    )
    loaded = MODULE.load_group_results(path, "wanted")
    assert set(loaded) == {("fixture", "v9")}


def test_read_records_supports_plain_jsonl(tmp_path):
    path = tmp_path / "records.jsonl"
    path.write_text(MODULE.canonical_json(fixture_record()) + "\n", encoding="utf-8")
    assert list(MODULE.read_records(path)) == [fixture_record()]


def test_run_checkpoints_merges_and_resumes_without_repeat(tmp_path, monkeypatch):
    record = fixture_record()
    source = tmp_path / "records.jsonl"
    output = tmp_path / "annotations.jsonl"
    groups = tmp_path / "groups.jsonl"
    source.write_text(MODULE.canonical_json(record) + "\n", encoding="utf-8")
    calls = []

    def fake_annotate(value, *, group_name, run_key, **kwargs):
        calls.append((value["id"], group_name))
        return fake_result(value, group_name, run_key=run_key)

    monkeypatch.setattr(MODULE, "annotate_group", fake_annotate)
    args = MODULE.build_parser().parse_args(
        [
            "--input",
            str(source),
            "--output",
            str(output),
            "--group-output",
            str(groups),
            "--endpoint",
            "http://localhost/v1/chat/completions",
            "--model",
            "independent-model",
            "--workers",
            "4",
        ]
    )
    first = MODULE.run(args)
    assert first["group_calls"] == 8
    assert first["merged_records"] == 1
    assert len(calls) == 8
    merged = json.loads(output.read_text(encoding="utf-8").strip())
    assert set(merged["decisions"]) == set(MODULE.ITEMS)
    assert len(merged["decisions"]) == 24

    second = MODULE.run(args)
    assert second["group_calls"] == 0
    assert second["merged_records"] == 0
    assert second["skipped_records"] == 1
    assert len(calls) == 8


def test_targeted_group_checkpoint_is_reused_by_full_run(tmp_path, monkeypatch):
    source_record = fixture_record()
    source = tmp_path / "records.jsonl"
    output = tmp_path / "annotations.jsonl"
    groups = tmp_path / "groups.jsonl"
    source.write_text(MODULE.canonical_json(source_record) + "\n", encoding="utf-8")
    calls = []

    def fake_annotate(value, *, group_name, run_key, **kwargs):
        calls.append(group_name)
        return fake_result(value, group_name, run_key=run_key)

    monkeypatch.setattr(MODULE, "annotate_group", fake_annotate)
    base = [
        "--input", str(source),
        "--output", str(output),
        "--group-output", str(groups),
        "--endpoint", "http://localhost/v1/chat/completions",
        "--model", "independent-model",
        "--workers", "1",
    ]
    targeted = MODULE.run(MODULE.build_parser().parse_args([*base, "--group", "v9"]))
    assert targeted["group_calls"] == 1
    assert targeted["merged_records"] == 0
    assert calls == ["v9"]

    full = MODULE.run(MODULE.build_parser().parse_args(base))
    assert full["group_calls"] == 7
    assert full["merged_records"] == 1
    assert calls.count("v9") == 1


def test_module_has_no_production_gemma_or_prediction_dependency():
    source = MODULE_PATH.read_text(encoding="utf-8").lower()
    forbidden = (
        "import submission",
        "from submission",
        "import pps",
        "from pps",
        "gem" + "ma",
        "saved" + "_response",
        "development" + "_predictions",
    )
    assert not any(token in source for token in forbidden)
