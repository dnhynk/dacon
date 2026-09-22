from __future__ import annotations

import importlib.util
import json
import pathlib
import types

import pytest


ROOT = pathlib.Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "tools" / "independent_gold" / "codex_cli_annotator.py"
SPEC = importlib.util.spec_from_file_location("independent_gold_codex_cli_annotator", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(MODULE)


def fixture_record():
    return {
        "id": "PPS-CODEX-FIXTURE-01",
        "docs": [
            {
                "doc_id": "notice",
                "type": "공고문",
                "text": (
                    "입찰참가자격\n최근 3년 수행실적이 3천만원 이상인 업체\n"
                    "납품 대상은 예시 제품과 동등 이상이어야 한다.\n끝"
                ),
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


@pytest.fixture(scope="module")
def catalogs():
    return (
        MODULE.catalog_facts.CatalogIndex.load(),
        MODULE.qualification_context.qualification_facts.CatalogReference.load(),
    )


def fake_cli_provenance():
    return {
        "requested_executable": "codex",
        "resolved_executable": "C:/verified/codex.exe",
        "executable_sha256": "c" * 64,
        "version_output": "codex-cli 0.test",
        "version_stderr_sha256": MODULE.sha256_text(""),
    }


def make_task(tmp_path, catalogs, group_name="v9"):
    fact_catalog, qualification_catalog = catalogs
    rubric = MODULE.RUBRIC_PATH.read_text(encoding="utf-8")
    staging = tmp_path / "stage"
    manifest = MODULE.build_run_manifest(
        input_path=ROOT / "data_open" / "dev.jsonl.gz",
        staging_dir=staging,
        model="gpt-independent-test",
        reasoning_effort="high",
        cli_provenance=fake_cli_provenance(),
        rubric=rubric,
        fact_catalog=fact_catalog,
        qualification_catalog=qualification_catalog,
    )
    record = fixture_record()
    prepared_facts = MODULE.fact_context.prepare_fact_inputs(
        record, catalog_index=fact_catalog
    )
    prepared_qualification = None
    if group_name in MODULE.qualification_context.GROUP_FAMILIES:
        prepared_qualification = MODULE.qualification_context.prepare_qualification_input(
            record, catalog=qualification_catalog
        )
    task = MODULE.prepare_group_task(
        record,
        group_name=group_name,
        rubric=rubric,
        staging_dir=staging,
        run_manifest=manifest,
        prepared_facts=prepared_facts,
        fact_catalog=fact_catalog,
        prepared_qualification=prepared_qualification,
        qualification_catalog=qualification_catalog,
    )
    return manifest, task


def make_lineaged_manifest(
    tmp_path,
    catalogs,
    *,
    role="verifier",
    model="gpt-6-astra",
    phase="selected_panel",
    groups=("v9",),
):
    fact_catalog, qualification_catalog = catalogs
    tmp_path.mkdir(parents=True, exist_ok=True)
    source = tmp_path / f"organizer-{role}-{phase}.jsonl"
    source.write_text(
        MODULE.canonical_json(fixture_record()) + "\n", encoding="utf-8"
    )
    selection = MODULE.select_record_ids(source, (), limit=None)
    plan = MODULE.build_cohort_plan(
        input_path=source,
        selection=selection,
        selected_groups=groups,
        phase=phase,
    )
    profile_name = MODULE.prompt_profiles.DEFAULT_PROFILE_BY_ROLE[role]
    profile = MODULE.prompt_profiles.get_profile(
        profile_name, annotator_role=role
    )
    identity = MODULE.build_model_identity(model=model)
    manifest = MODULE.build_run_manifest(
        input_path=source,
        staging_dir=tmp_path / f"stage-{role}-{phase}",
        model=model,
        reasoning_effort="high",
        cli_provenance=fake_cli_provenance(),
        rubric=MODULE.RUBRIC_PATH.read_text(encoding="utf-8"),
        fact_catalog=fact_catalog,
        qualification_catalog=qualification_catalog,
        annotator_role=role,
        prompt_profile=profile,
        phase=phase,
        cohort_plan=plan,
        model_identity=identity,
    )
    return source, plan, profile, manifest


def make_lineaged_task(tmp_path, catalogs, *, role="verifier", model="gpt-6-astra"):
    fact_catalog, qualification_catalog = catalogs
    source, plan, profile, manifest = make_lineaged_manifest(
        tmp_path, catalogs, role=role, model=model
    )
    record = fixture_record()
    prepared_facts = MODULE.fact_context.prepare_fact_inputs(
        record, catalog_index=fact_catalog
    )
    task = MODULE.prepare_group_task(
        record,
        group_name="v9",
        rubric=MODULE.RUBRIC_PATH.read_text(encoding="utf-8"),
        staging_dir=tmp_path / f"stage-{role}-selected_panel",
        run_manifest=manifest,
        prepared_facts=prepared_facts,
        fact_catalog=fact_catalog,
        prepared_qualification=None,
        qualification_catalog=qualification_catalog,
    )
    return source, plan, profile, manifest, task


def event_stream(final_json, *, extra_items=()):
    events = [
        {"type": "thread.started", "thread_id": "thread-ephemeral-test"},
        {"type": "turn.started"},
        *extra_items,
        {
            "type": "item.completed",
            "item": {"id": "answer", "type": "agent_message", "text": final_json},
        },
        {
            "type": "turn.completed",
            "usage": {
                "input_tokens": 100,
                "cached_input_tokens": 0,
                "output_tokens": 20,
                "reasoning_output_tokens": 5,
            },
        },
    ]
    return "\n".join(MODULE.canonical_json(event) for event in events) + "\n"


def invocation(final_json, *, extra_items=()):
    return {
        "returncode": 0,
        "stdout": event_stream(final_json, extra_items=extra_items),
        "stderr": "",
        "raw_final": final_json,
        "timeout_error": None,
        "elapsed_seconds": 1.25,
    }


def test_parser_requires_explicit_model_and_reasoning():
    parser = MODULE.build_parser()
    common = [
        "--input",
        str(ROOT / "data_open" / "dev.jsonl.gz"),
        "--staging-dir",
        "stage",
    ]
    with pytest.raises(SystemExit):
        parser.parse_args(common)
    with pytest.raises(SystemExit):
        parser.parse_args([*common, "--model", "explicit-model"])
    args = parser.parse_args(
        [
            *common,
            "--model",
            "explicit-model",
            "--reasoning-effort",
            "xhigh",
        ]
    )
    assert args.model == "explicit-model"
    assert args.reasoning_effort == "xhigh"
    assert args.execute is False
    assert args.full_source is False
    assert args.annotator_role is None
    assert args.prompt_profile is None
    assert args.phase is None


def test_parser_accepts_explicit_role_profile_and_phase():
    args = MODULE.build_parser().parse_args(
        [
            "--input",
            str(ROOT / "data_open" / "dev.jsonl.gz"),
            "--staging-dir",
            "stage",
            "--model",
            "gpt-6-astra",
            "--reasoning-effort",
            "xhigh",
            "--annotator-role",
            "verifier",
            "--prompt-profile",
            "verifier-source-falsification-v1",
            "--phase",
            "official_dev",
        ]
    )
    assert args.annotator_role == "verifier"
    assert args.prompt_profile == "verifier-source-falsification-v1"
    assert args.phase == "official_dev"
    assert args.model_identity_mode == "opaque_hosted_alias"


def test_prompt_profiles_are_separate_and_verifier_has_no_peer_vote_channel():
    candidate = MODULE.prompt_profiles.get_profile(
        "candidate-source-direct-v1", annotator_role="candidate"
    )
    verifier = MODULE.prompt_profiles.get_profile(
        "verifier-source-falsification-v1", annotator_role="verifier"
    )
    assert candidate.module_path != verifier.module_path
    assert candidate.template_sha256 != verifier.template_sha256
    assert candidate.builder_source_sha256 != verifier.builder_source_sha256
    assert candidate.protocol_version != verifier.protocol_version
    assert candidate.required_model_family == "openai:gpt-5.6"
    assert verifier.required_model_family == "openai:gpt-6"
    assert "candidate" not in verifier.module_path.read_text(encoding="utf-8").casefold()
    assert "candidate" not in verifier.template.casefold()
    assert verifier.renderer_signature == (
        "(messages: 'Sequence[Mapping[str, str]]', *, record_id: 'str', "
        "group_name: 'str', target_items: 'Sequence[str]') -> 'str'"
    )

    messages = [
        {"role": "system", "content": "RULEBOOK"},
        {"role": "user", "content": "SOURCE"},
    ]
    prompt = MODULE.prompt_profiles.render_profile_prompt(
        verifier,
        messages,
        record_id="R1",
        group_name="v9",
        target_items=("v9",),
    )
    assert prompt.startswith("BLIND SOURCE VERIFICATION")
    assert "RULEBOOK" in prompt and "SOURCE" in prompt
    for forbidden_argument in (
        "candidate_answer",
        "candidate_identity",
        "candidate_label",
        "candidate_confidence",
        "candidate_evidence",
        "peer_vote",
    ):
        with pytest.raises(TypeError, match="unexpected keyword argument"):
            MODULE.prompt_profiles.render_profile_prompt(
                verifier,
                messages,
                record_id="R1",
                group_name="v9",
                target_items=("v9",),
                **{forbidden_argument: "MUST-NOT-BE-VISIBLE"},
            )


def test_prompt_profile_role_mismatch_is_rejected():
    with pytest.raises(ValueError, match="is for role 'candidate'"):
        MODULE.prompt_profiles.get_profile(
            "candidate-source-direct-v1", annotator_role="verifier"
        )


def test_model_identity_does_not_invent_a_hosted_revision(tmp_path):
    opaque = MODULE.build_model_identity(model="gpt-6-astra")
    assert opaque == {
        "mode": "opaque_hosted_alias",
        "provider": "openai",
        "family": "openai:gpt-6",
        "family_source": "requested_model_id_prefix_registry_v1",
        "requested_model": "gpt-6-astra",
        "requested_revision": None,
        "resolved_revision": None,
        "official_snapshot_pin_available": False,
        "attestation": None,
        "attestation_verified": False,
        "qualification_eligible_from_identity_alone": False,
        "drift_canary_required": True,
    }
    with pytest.raises(ValueError, match="cannot claim a revision"):
        MODULE.build_model_identity(
            model="gpt-6-astra", model_revision="invented-revision"
        )

    pinned = MODULE.build_model_identity(
        model="gpt-6-astra",
        mode="pinned_snapshot",
        model_revision="requested-snapshot-2026-09",
    )
    assert pinned["requested_revision"] == "requested-snapshot-2026-09"
    assert pinned["official_snapshot_pin_available"] is True
    assert pinned["resolved_revision"] is None
    assert pinned["attestation"] is None
    assert pinned["attestation_verified"] is False
    assert pinned["qualification_eligible_from_identity_alone"] is False
    assert pinned["drift_canary_required"] is True


def test_cohort_plan_freezes_exact_ids_groups_and_group_row_topology(tmp_path):
    records = []
    for record_id in ("PPS-ONE", "PPS-TWO"):
        record = fixture_record()
        record["id"] = record_id
        records.append(record)
    source = tmp_path / "organizer.jsonl"
    source.write_text(
        "".join(MODULE.canonical_json(record) + "\n" for record in records),
        encoding="utf-8",
    )
    selection = MODULE.select_record_ids(source, ("PPS-TWO",), limit=None)
    plan = MODULE.build_cohort_plan(
        input_path=source,
        selection=selection,
        selected_groups=("v9", "v21-23"),
        phase="selected_panel",
    )
    assert plan["annotation_topology"] == "group_rows"
    assert plan["selection_mode"] == "cartesian_selected_ids_x_selected_groups"
    assert plan["requested_ids"] == ["PPS-TWO"]
    assert plan["selected_ids"] == ["PPS-TWO"]
    assert plan["selected_groups"] == ["v9", "v21-23"]
    assert plan["target_items_by_group"] == {
        "v9": ["v9"],
        "v21-23": ["v21", "v22", "v23"],
    }
    assert plan["selected_group_row_count"] == 2
    assert plan["selected_group_rows_sha256"] == MODULE.sha256_object(
        [["PPS-TWO", "v9"], ["PPS-TWO", "v21-23"]]
    )
    MODULE.validate_cohort_plan(
        plan, input_path=source, phase="selected_panel"
    )

    tampered = json.loads(json.dumps(plan))
    tampered["selected_groups"].append("v24")
    tampered["manifest_sha256"] = MODULE.sha256_object(
        MODULE._manifest_without_hash(tampered)
    )
    with pytest.raises(ValueError, match="target-item grouping mismatch"):
        MODULE.validate_cohort_plan(
            tampered, input_path=source, phase="selected_panel"
        )


def test_full_source_packet_is_exact_complete_and_hard_capped():
    record = fixture_record()
    packet = MODULE.build_full_source_packet(record, ("v20", "v24"))
    expected_chars = sum(len(doc["text"]) for doc in record["docs"])
    assert packet["packet_policy_version"] == MODULE.FULL_SOURCE_PACKET_VERSION
    assert packet["source_sha256"] == MODULE.sha256_object(record)
    assert packet["source_chars"] == expected_chars
    assert packet["selected_chars"] == expected_chars
    assert packet["covered_source_chars"] == expected_chars
    assert packet["full_source_visible"] is True
    assert packet["absence_warning"] is None
    assert packet["items"]["v20"]["absence_safe"] is True
    for index, segment in enumerate(packet["segments"]):
        source = record["docs"][index]
        assert segment["text"] == source["text"]
        assert segment["start"] == 0
        assert segment["end"] == len(source["text"])
        assert segment["source_doc_sha256"] == MODULE.sha256_text(source["text"])

    oversized = fixture_record()
    oversized["docs"][0]["text"] = "x" * (MODULE.FULL_SOURCE_MAX_CHARS + 1)
    with pytest.raises(ValueError, match="chunked exhaustive review is required"):
        MODULE.build_full_source_packet(oversized, ("v20",))


def test_full_source_flag_is_explicit():
    parser = MODULE.build_parser()
    args = parser.parse_args(
        [
            "--input",
            str(ROOT / "data_open" / "dev.jsonl.gz"),
            "--staging-dir",
            "stage",
            "--model",
            "explicit-model",
            "--reasoning-effort",
            "xhigh",
            "--full-source",
        ]
    )
    assert args.full_source is True


def test_command_uses_official_noninteractive_isolation_flags_and_no_shell():
    command = MODULE.build_codex_command(
        "codex",
        model="explicit-model",
        reasoning_effort="high",
        schema_path=pathlib.Path("schema.json"),
        raw_final_path=pathlib.Path("raw.json"),
    )
    assert command[:2] == ["codex", "exec"]
    for flag in (
        "--ephemeral",
        "--sandbox",
        "--ignore-user-config",
        "--ignore-rules",
        "--output-schema",
        "--model",
        "--json",
        "--output-last-message",
    ):
        assert flag in command
    assert command[command.index("--sandbox") + 1] == "read-only"
    assert command[command.index("--model") + 1] == "explicit-model"
    assert 'model_reasoning_effort="high"' in command
    assert 'approval_policy="never"' in command
    assert "--dangerously-bypass-approvals-and-sandbox" not in command
    assert command[-1] == "-"


def test_group_output_schema_is_bounded_and_supports_unknown():
    schema = MODULE.group_output_schema(("v10", "v11", "v12", "v13"))
    assert schema["additionalProperties"] is False
    assert schema["properties"]["labels"]["pattern"] == "^[01U]{4}$"
    assert schema["properties"]["confidence"]["pattern"] == "^[HML]{4}$"
    evidence = schema["properties"]["evidence"]
    assert evidence["additionalProperties"] is False
    assert evidence["required"] == [
        "v10",
        "v11",
        "v12",
        "v13",
    ]
    assert list(evidence["properties"]) == evidence["required"]
    assert evidence["properties"]["v10"]["anyOf"] == [
        {"type": "string", "maxLength": 500},
        {"type": "null"},
    ]


def test_nullable_wire_evidence_is_sparsified_without_hiding_contradictions():
    parsed = MODULE._normalize_codex_wire_output(
        {
            "labels": "0100",
            "confidence": "HHHH",
            "evidence": {"v1": None, "v2": "exact quote", "v3": None, "v4": None},
        },
        ("v1", "v2", "v3", "v4"),
    )
    assert parsed["evidence"] == {"v2": "exact quote"}
    contradictory = MODULE._normalize_codex_wire_output(
        {
            "labels": "0000",
            "confidence": "HHHH",
            "evidence": {"v1": None, "v2": None, "v3": "contradiction", "v4": None},
        },
        ("v1", "v2", "v3", "v4"),
    )
    with pytest.raises(ValueError, match="evidence key for v3 is forbidden"):
        MODULE.annotate_groups.normalize_group_decision(
            contradictory, ("v1", "v2", "v3", "v4")
        )


def test_prompt_forbids_all_tools_and_uses_only_embedded_payload():
    messages = [
        {"role": "system", "content": "RUBRIC"},
        {"role": "user", "content": "SOURCE AND CONTEXT"},
    ]
    prompt = MODULE.build_codex_prompt(
        messages,
        record_id="R1",
        group_name="v9",
        target_items=("v9",),
    )
    for phrase in (
        "Do not call or request any tool",
        "filesystem reads or writes",
        "web/search",
        "Do not read files",
        "Use only the embedded RUBRIC and SUPPLIED INPUT",
        "emit U",
    ):
        assert phrase in prompt
    assert "RUBRIC" in prompt
    assert "SOURCE AND CONTEXT" in prompt


def test_task_staging_has_exact_hashes_and_no_runtime_inputs(tmp_path, catalogs):
    manifest, task = make_task(tmp_path, catalogs)
    task_manifest = task["manifest"]
    assert task_manifest["record_source_sha256"] == MODULE.sha256_object(fixture_record())
    assert task_manifest["packet_sha256"] == MODULE.sha256_object(task["packet"])
    assert task_manifest["fact_context_sha256"] == task["fact_context"]["context_sha256"]
    assert task_manifest["law_context_sha256"] == task["law_context"]["context_sha256"]
    assert task_manifest["qualification_context_sha256"] is None
    assert task_manifest["prompt_sha256"] == MODULE.sha256_text(task["prompt"])
    assert task_manifest["run_manifest_sha256"] == manifest["manifest_sha256"]
    assert task_manifest["cli_may_read_outside_working_directory"] is True
    for name, expected in task_manifest["artifact_file_sha256"].items():
        assert MODULE.file_sha256(task["task_dir"] / name) == expected
    names = {path.name for path in task["task_dir"].iterdir()}
    assert names == {
        "prompt.txt",
        "output_schema.json",
        "packet.json",
        "fact_context.json",
        "law_context.json",
        "task_manifest.json",
    }


def test_lineaged_manifest_binds_tuple_sources_and_instance_cohort(tmp_path, catalogs):
    _, plan, profile, manifest = make_lineaged_manifest(tmp_path, catalogs)
    assert manifest["schema_version"] == MODULE.LINEAGED_RUNNER_SCHEMA_VERSION
    assert manifest["run_key"] == manifest["run_instance_sha256"]
    assert manifest["annotator_role"] == "verifier"
    assert manifest["pass_kind"] == "blind_first_pass"
    assert manifest["phase"] == "selected_panel"
    assert manifest["cohort_plan"] == plan
    assert manifest["prompt_lineage"]["profile"] == profile.name
    assert manifest["prompt_lineage"]["required_model_family"] == "openai:gpt-6"
    assert manifest["prompt_lineage"]["derived_from_vote_lineages"] == []
    assert manifest["model_identity"]["family"] == "openai:gpt-6"
    assert manifest["model_identity"]["mode"] == "opaque_hosted_alias"
    assert manifest["peer_visibility"] == {
        "peer_vote_inputs": [],
        "peer_identity_visible": False,
        "peer_label_visible": False,
        "peer_rationale_visible": False,
        "peer_confidence_visible": False,
        "peer_evidence_visible": False,
    }
    assert manifest["semantic_config"]["rubric_sha256"] == MODULE.sha256_text(
        MODULE.RUBRIC_PATH.read_text(encoding="utf-8")
    )
    assert manifest["semantic_config"]["groups"] == MODULE.annotate_groups.GROUPS
    assert set(manifest["semantic_config"]["output_schema_sha256_by_group"]) == set(
        MODULE.annotate_groups.GROUP_BY_NAME
    )
    assert manifest["semantic_config"]["law_context"][
        "reference_manifest_sha256"
    ] == MODULE.law_context.reference_manifest_sha256()
    assert manifest["semantic_config"]["fact_context"]["catalog_sha256"]
    assert manifest["semantic_config"]["qualification_context"]["catalog_sha256"]

    source_paths = {
        entry["path"] for entry in manifest["imported_source_bundle"]["files"]
    }
    assert {
        "tools/independent_gold/codex_cli_annotator.py",
        "tools/independent_gold/annotate_groups.py",
        "tools/independent_gold/catalog_facts.py",
        "tools/independent_gold/fact_context.py",
        "tools/independent_gold/legal_facts.py",
        "tools/independent_gold/qualification_context.py",
        "tools/independent_gold/qualification_facts.py",
        "tools/independent_gold/law_context.py",
        "tools/independent_gold/packetize.py",
        "tools/independent_gold/prompt_profiles/__init__.py",
        "tools/independent_gold/prompt_profiles/verifier_source_falsification_v1.py",
    } <= source_paths
    source_bundle_without_hash = {
        key: value
        for key, value in manifest["imported_source_bundle"].items()
        if key != "bundle_sha256"
    }
    assert manifest["imported_source_bundle"]["bundle_sha256"] == MODULE.sha256_object(
        source_bundle_without_hash
    )
    prompt_lineage_without_hash = {
        key: value
        for key, value in manifest["prompt_lineage"].items()
        if key != "lineage_sha256"
    }
    assert manifest["prompt_lineage"]["lineage_sha256"] == MODULE.sha256_object(
        prompt_lineage_without_hash
    )
    assert manifest["manifest_sha256"] == MODULE.sha256_object(
        MODULE._manifest_without_hash(manifest)
    )


def test_tuple_excludes_cohort_but_instance_binds_phase_and_exact_selection(
    tmp_path, catalogs
):
    _, selected_plan, _, selected = make_lineaged_manifest(
        tmp_path / "selected", catalogs, phase="selected_panel"
    )
    _, dev_plan, _, official_dev = make_lineaged_manifest(
        tmp_path / "dev", catalogs, phase="official_dev"
    )
    assert selected_plan["selected_ids"] == dev_plan["selected_ids"]
    assert selected["tuple_sha256"] == official_dev["tuple_sha256"]
    assert selected["run_instance_sha256"] != official_dev["run_instance_sha256"]

    _, _, _, candidate = make_lineaged_manifest(
        tmp_path / "candidate",
        catalogs,
        role="candidate",
        model="gpt-5.6-sol",
        phase="selected_panel",
    )
    assert candidate["tuple_sha256"] != selected["tuple_sha256"]
    assert candidate["prompt_lineage"]["lineage_sha256"] != selected[
        "prompt_lineage"
    ]["lineage_sha256"]
    assert candidate["model_identity"]["family"] != selected["model_identity"][
        "family"
    ]


def test_lineaged_manifest_rejects_profile_model_family_mismatch(tmp_path, catalogs):
    fact_catalog, qualification_catalog = catalogs
    source = tmp_path / "organizer.jsonl"
    source.write_text(MODULE.canonical_json(fixture_record()) + "\n", encoding="utf-8")
    selection = MODULE.select_record_ids(source, (), limit=None)
    plan = MODULE.build_cohort_plan(
        input_path=source,
        selection=selection,
        selected_groups=("v9",),
        phase="selected_panel",
    )
    verifier = MODULE.prompt_profiles.get_profile(
        "verifier-source-falsification-v1", annotator_role="verifier"
    )
    with pytest.raises(ValueError, match="required family"):
        MODULE.build_run_manifest(
            input_path=source,
            staging_dir=tmp_path / "stage",
            model="gpt-5.6-sol",
            reasoning_effort="high",
            cli_provenance=fake_cli_provenance(),
            rubric=MODULE.RUBRIC_PATH.read_text(encoding="utf-8"),
            fact_catalog=fact_catalog,
            qualification_catalog=qualification_catalog,
            annotator_role="verifier",
            prompt_profile=verifier,
            phase="selected_panel",
            cohort_plan=plan,
            model_identity=MODULE.build_model_identity(model="gpt-5.6-sol"),
        )


def test_lineaged_task_and_receipt_repeat_blind_lineage_fields(tmp_path, catalogs):
    _, plan, _, manifest, task = make_lineaged_task(tmp_path, catalogs)
    task_manifest = task["manifest"]
    assert task_manifest["schema_version"] == MODULE.LINEAGED_TASK_SCHEMA_VERSION
    assert task_manifest["annotator_role"] == "verifier"
    assert task_manifest["pass_kind"] == "blind_first_pass"
    assert task_manifest["phase"] == "selected_panel"
    assert task_manifest["tuple_sha256"] == manifest["tuple_sha256"]
    assert task_manifest["run_instance_sha256"] == manifest["run_instance_sha256"]
    assert task_manifest["cohort_plan_sha256"] == plan["manifest_sha256"]
    assert task_manifest["prompt_profile"] == "verifier-source-falsification-v1"
    assert task_manifest["prompt_lineage_sha256"] == manifest["prompt_lineage"][
        "lineage_sha256"
    ]
    assert task_manifest["peer_visibility"] == MODULE.blind_peer_visibility()
    assert task["prompt"].startswith("BLIND SOURCE VERIFICATION")
    assert "INDEPENDENT GOLD CANDIDATE ANNOTATION" not in task["prompt"]

    raw = '{"labels":"0","confidence":"H","evidence":{}}'
    receipt = MODULE.validate_invocation_result(
        task,
        run_manifest=manifest,
        attempt=1,
        invocation=invocation(raw),
    )
    assert receipt["status"] == "ok"
    assert receipt["runner_receipt_schema_version"] == (
        MODULE.LINEAGED_RECEIPT_SCHEMA_VERSION
    )
    for key in (
        "annotator_role",
        "pass_kind",
        "phase",
        "tuple_sha256",
        "run_instance_sha256",
        "cohort_plan_sha256",
        "prompt_profile",
        "prompt_lineage_sha256",
        "model_identity",
        "peer_visibility",
        "imported_source_bundle_sha256",
    ):
        assert receipt[key] == task_manifest[key]


def test_lineaged_task_must_belong_to_frozen_cohort(tmp_path, catalogs):
    fact_catalog, qualification_catalog = catalogs
    _, _, _, manifest = make_lineaged_manifest(tmp_path, catalogs, groups=("v9",))
    record = fixture_record()
    with pytest.raises(ValueError, match="group is outside the frozen cohort plan"):
        MODULE.prepare_group_task(
            record,
            group_name="v1-4",
            rubric=MODULE.RUBRIC_PATH.read_text(encoding="utf-8"),
            staging_dir=tmp_path / "stage-verifier-selected_panel",
            run_manifest=manifest,
            prepared_facts=MODULE.fact_context.prepare_fact_inputs(
                record, catalog_index=fact_catalog
            ),
            fact_catalog=fact_catalog,
            prepared_qualification=MODULE.qualification_context.prepare_qualification_input(
                record, catalog=qualification_catalog
            ),
            qualification_catalog=qualification_catalog,
        )


def test_valid_positive_keeps_raw_json_and_exact_source_coordinates(tmp_path, catalogs):
    manifest, task = make_task(tmp_path, catalogs)
    quote = "제조사·모델명 : Example Z9"
    raw = MODULE.canonical_json(
        {"labels": "1", "confidence": "H", "evidence": {"v9": quote}}
    )
    result = MODULE.validate_invocation_result(
        task,
        run_manifest=manifest,
        attempt=1,
        invocation=invocation(raw),
    )
    assert result["status"] == "ok"
    assert result["raw_content"] == raw
    assert result["raw_final_sha256"] == MODULE.sha256_text(raw)
    assert result["model_provenance"]["requested_model"] == "gpt-independent-test"
    assert result["model_provenance"]["declared_reasoning_effort"] == "high"
    assert result["event_audit"]["unsafe_items"] == []
    location = result["decisions"]["v9"]["evidence_locations"][0]
    source = fixture_record()["docs"][location["doc_index"]]["text"]
    assert source[location["start"] : location["end"]] == quote
    assert location["evidence_sha256"] == MODULE.sha256_text(quote)


def test_unknown_is_accepted_as_a_first_class_group_decision(tmp_path, catalogs):
    manifest, task = make_task(tmp_path, catalogs)
    raw = '{"labels":"U","confidence":"L","evidence":{}}'
    result = MODULE.validate_invocation_result(
        task,
        run_manifest=manifest,
        attempt=1,
        invocation=invocation(raw),
    )
    assert result["status"] == "ok"
    assert result["decisions"]["v9"]["label"] == "U"
    assert result["decisions"]["v9"]["confidence"] == "low"


@pytest.mark.parametrize(
    "item_type",
    ("command_execution", "file_change", "web_search", "mcp_tool_call", "tool_call"),
)
def test_any_observed_tool_event_disqualifies_otherwise_valid_result(
    tmp_path, catalogs, item_type
):
    manifest, task = make_task(tmp_path, catalogs)
    raw = '{"labels":"0","confidence":"H","evidence":{}}'
    tool_event = {
        "type": "item.completed",
        "item": {
            "id": "tool-1",
            "type": item_type,
            "command": "type ..\\secret.txt",
            "status": "failed",
        },
    }
    result = MODULE.validate_invocation_result(
        task,
        run_manifest=manifest,
        attempt=1,
        invocation=invocation(raw, extra_items=(tool_event,)),
    )
    assert result["status"] == "error"
    assert "tool_or_nonmessage_event_observed" in result["error"]
    assert result["event_audit"]["unsafe_items"][0]["item_type"] == item_type


def test_negative_label_with_evidence_key_is_a_hard_schema_contradiction(
    tmp_path, catalogs
):
    manifest, task = make_task(tmp_path, catalogs)
    raw = MODULE.canonical_json(
        {
            "labels": "0",
            "confidence": "H",
            "evidence": {"v9": "제조사·모델명 : Example Z9"},
        }
    )
    result = MODULE.validate_invocation_result(
        task,
        run_manifest=manifest,
        attempt=1,
        invocation=invocation(raw),
    )
    assert result["status"] == "error"
    assert "evidence key for v9 is forbidden for label=0" in result["error"]


def test_nonverbatim_positive_is_a_hard_error(tmp_path, catalogs):
    manifest, task = make_task(tmp_path, catalogs)
    raw = MODULE.canonical_json(
        {"labels": "1", "confidence": "H", "evidence": {"v9": "원문에 없는 모델"}}
    )
    result = MODULE.validate_invocation_result(
        task,
        run_manifest=manifest,
        attempt=1,
        invocation=invocation(raw),
    )
    assert result["status"] == "error"
    assert "evidence_not_verbatim_in_packet" in result["error"]


def test_checkpoint_resume_revalidates_raw_json_hash_and_decisions(tmp_path, catalogs):
    manifest, task = make_task(tmp_path, catalogs)
    raw = '{"labels":"0","confidence":"H","evidence":{}}'
    good = MODULE.validate_invocation_result(
        task,
        run_manifest=manifest,
        attempt=1,
        invocation=invocation(raw),
    )
    assert MODULE.reusable_checkpoint([good], task, run_manifest=manifest) is good
    tampered_hash = dict(good)
    tampered_hash["content_sha256"] = "0" * 64
    assert MODULE.reusable_checkpoint([tampered_hash], task, run_manifest=manifest) is None
    tampered_decision = json.loads(json.dumps(good))
    tampered_decision["decisions"]["v9"]["label"] = 1
    assert MODULE.reusable_checkpoint([tampered_decision], task, run_manifest=manifest) is None


def test_persisted_threat_model_and_manifest_disclose_residual_read_scope(tmp_path, catalogs):
    fact_catalog, qualification_catalog = catalogs
    staging = tmp_path / "stage"
    rubric = MODULE.RUBRIC_PATH.read_text(encoding="utf-8")
    manifest = MODULE.build_run_manifest(
        input_path=ROOT / "data_open" / "dev.jsonl.gz",
        staging_dir=staging,
        model="explicit-model",
        reasoning_effort="xhigh",
        cli_provenance=fake_cli_provenance(),
        rubric=rubric,
        fact_catalog=fact_catalog,
        qualification_catalog=qualification_catalog,
    )
    MODULE.persist_run_manifest(staging, manifest)
    threat = (staging / "THREAT_MODEL.md").read_text(encoding="utf-8")
    saved = json.loads((staging / "run_manifest.json").read_text(encoding="utf-8"))
    assert "not an operating-system security boundary" in threat
    assert "may be able to read files outside" in threat
    assert saved["threat_model"]["cli_may_read_outside_working_directory"] is True
    assert saved["execution_isolation"]["persistent_stage_is_cli_cwd"] is False
    assert saved["semantic_config"]["model"] == "explicit-model"
    assert saved["semantic_config"]["reasoning_effort"] == "xhigh"
    assert saved["manifest_sha256"] == MODULE.sha256_object(
        MODULE._manifest_without_hash(saved)
    )


def test_invoke_uses_fresh_external_temp_dir_and_stdin_only(monkeypatch):
    observed = {}
    raw = '{"labels":"0","confidence":"H","evidence":{}}'

    def fake_run(command, **kwargs):
        observed["command"] = command
        observed["kwargs"] = kwargs
        workdir = pathlib.Path(kwargs["cwd"])
        observed["workdir"] = workdir
        observed["preexisting"] = {path.name for path in workdir.iterdir()}
        raw_path = pathlib.Path(command[command.index("--output-last-message") + 1])
        raw_path.write_text(raw, encoding="utf-8")
        return types.SimpleNamespace(
            returncode=0,
            stdout=event_stream(raw),
            stderr="",
        )

    monkeypatch.setattr(MODULE.subprocess, "run", fake_run)
    result = MODULE.invoke_codex(
        executable="codex",
        model="explicit-model",
        reasoning_effort="high",
        prompt="ONLY THIS PROMPT",
        output_schema=MODULE.group_output_schema(("v9",)),
        timeout=10,
    )
    assert result["raw_final"] == raw
    assert observed["kwargs"]["input"] == "ONLY THIS PROMPT"
    assert observed["kwargs"]["shell"] is False
    assert observed["preexisting"] == {"output_schema.json"}
    assert not MODULE._is_relative_to(observed["workdir"], ROOT)
    assert not observed["workdir"].exists()


def test_prepare_only_never_crosses_the_codex_api_boundary(
    tmp_path, monkeypatch, catalogs
):
    source = tmp_path / "organizer.jsonl"
    source.write_text(MODULE.canonical_json(fixture_record()) + "\n", encoding="utf-8")
    monkeypatch.setattr(MODULE, "ALLOWED_RECORD_INPUTS", (source,))
    monkeypatch.setattr(MODULE, "resolve_codex_provenance", lambda value: fake_cli_provenance())

    def forbidden_call(**kwargs):
        raise AssertionError("prepare-only must not invoke Codex")

    monkeypatch.setattr(MODULE, "invoke_codex", forbidden_call)
    args = MODULE.build_parser().parse_args(
        [
            "--input",
            str(source),
            "--staging-dir",
            str(tmp_path / "stage"),
            "--model",
            "explicit-model",
            "--reasoning-effort",
            "high",
            "--group",
            "v9",
            "--id",
            "PPS-CODEX-FIXTURE-01",
            "--id",
            "PPS-CODEX-FIXTURE-01",
            "--limit",
            "1",
        ]
    )
    stats = MODULE.run(args)
    assert stats["execute"] is False
    assert stats["tasks_prepared"] == 1
    assert stats["calls_attempted"] == 0
    assert stats["tasks_ok"] == 0
    assert (tmp_path / "stage" / "run_manifest.json").is_file()


def test_explicit_verifier_prepare_only_writes_v2_lineage_without_a_model_call(
    tmp_path, monkeypatch, catalogs
):
    source = tmp_path / "organizer.jsonl"
    source.write_text(MODULE.canonical_json(fixture_record()) + "\n", encoding="utf-8")
    monkeypatch.setattr(MODULE, "ALLOWED_RECORD_INPUTS", (source,))
    monkeypatch.setattr(MODULE, "resolve_codex_provenance", lambda value: fake_cli_provenance())

    def forbidden_call(**kwargs):
        raise AssertionError("prepare-only must not invoke Codex")

    monkeypatch.setattr(MODULE, "invoke_codex", forbidden_call)
    stage = tmp_path / "stage"
    external_plan = tmp_path / "plans" / "selected-panel.json"
    args = MODULE.build_parser().parse_args(
        [
            "--input",
            str(source),
            "--staging-dir",
            str(stage),
            "--model",
            "gpt-6-astra",
            "--reasoning-effort",
            "high",
            "--annotator-role",
            "verifier",
            "--prompt-profile",
            "verifier-source-falsification-v1",
            "--phase",
            "selected_panel",
            "--cohort-plan",
            str(external_plan),
            "--group",
            "v9",
            "--id",
            "PPS-CODEX-FIXTURE-01",
            "--limit",
            "1",
        ]
    )
    stats = MODULE.run(args)
    assert stats["schema_version"] == MODULE.LINEAGED_RUNNER_SCHEMA_VERSION
    assert stats["execute"] is False
    assert stats["calls_attempted"] == 0
    assert stats["tasks_prepared"] == 1
    assert stats["annotator_role"] == "verifier"
    assert stats["phase"] == "selected_panel"
    manifest = json.loads((stage / "run_manifest.json").read_text(encoding="utf-8"))
    embedded_plan = json.loads((stage / "cohort_plan.json").read_text(encoding="utf-8"))
    copied_plan = json.loads(external_plan.read_text(encoding="utf-8"))
    assert embedded_plan == copied_plan == manifest["cohort_plan"]
    assert manifest["model_identity"]["mode"] == "opaque_hosted_alias"
    assert manifest["model_identity"]["resolved_revision"] is None
    assert manifest["model_identity"]["official_snapshot_pin_available"] is False
    assert manifest["model_identity"]["drift_canary_required"] is True
    task_manifest = json.loads(
        (
            stage
            / "tasks"
            / MODULE._task_slug("PPS-CODEX-FIXTURE-01")
            / "v9"
            / "task_manifest.json"
        ).read_text(encoding="utf-8")
    )
    assert task_manifest["run_instance_sha256"] == stats["run_instance_sha256"]
    assert task_manifest["tuple_sha256"] == stats["tuple_sha256"]


def test_partial_lineage_cli_flags_fail_before_provenance_or_staging(
    tmp_path, monkeypatch
):
    source = tmp_path / "organizer.jsonl"
    source.write_text(MODULE.canonical_json(fixture_record()) + "\n", encoding="utf-8")
    monkeypatch.setattr(MODULE, "ALLOWED_RECORD_INPUTS", (source,))

    def forbidden_provenance(value):
        raise AssertionError("incomplete lineage flags must fail before CLI provenance")

    monkeypatch.setattr(MODULE, "resolve_codex_provenance", forbidden_provenance)
    stage = tmp_path / "stage"
    args = MODULE.build_parser().parse_args(
        [
            "--input",
            str(source),
            "--staging-dir",
            str(stage),
            "--model",
            "gpt-6-astra",
            "--reasoning-effort",
            "high",
            "--annotator-role",
            "verifier",
        ]
    )
    with pytest.raises(ValueError, match="requires --prompt-profile, --phase"):
        MODULE.run(args)
    assert not stage.exists()


def test_repeated_id_filter_is_deduplicated_and_limit_applies_after_filter(tmp_path):
    records = []
    for record_id in ("SKIP-FIRST", "PPS-DEV-02", "PPS-DEV-03"):
        record = fixture_record()
        record["id"] = record_id
        records.append(record)
    source = tmp_path / "organizer.jsonl"
    source.write_text(
        "".join(MODULE.canonical_json(record) + "\n" for record in records),
        encoding="utf-8",
    )
    selection = MODULE.select_record_ids(
        source,
        ("PPS-DEV-02", "PPS-DEV-02", "PPS-DEV-03"),
        limit=1,
    )
    assert selection["requested_ids"] == ("PPS-DEV-02", "PPS-DEV-03")
    assert selection["matched_ids"] == ("PPS-DEV-02", "PPS-DEV-03")
    assert selection["selected_ids"] == ("PPS-DEV-02",)


def test_unknown_id_hard_fails_in_preflight_before_cli_or_staging(
    tmp_path, monkeypatch
):
    source = tmp_path / "organizer.jsonl"
    source.write_text(MODULE.canonical_json(fixture_record()) + "\n", encoding="utf-8")
    monkeypatch.setattr(MODULE, "ALLOWED_RECORD_INPUTS", (source,))

    def forbidden_provenance(value):
        raise AssertionError("unknown ID must fail before touching the Codex CLI")

    monkeypatch.setattr(MODULE, "resolve_codex_provenance", forbidden_provenance)
    stage = tmp_path / "stage"
    args = MODULE.build_parser().parse_args(
        [
            "--input",
            str(source),
            "--staging-dir",
            str(stage),
            "--model",
            "explicit-model",
            "--reasoning-effort",
            "high",
            "--id",
            "DOES-NOT-EXIST",
            "--group",
            "v1-4",
        ]
    )
    with pytest.raises(ValueError, match="unknown requested organizer IDs"):
        MODULE.run(args)
    assert not stage.exists()


def test_execute_checkpoint_is_resumable_without_second_call(
    tmp_path, monkeypatch, catalogs
):
    source = tmp_path / "organizer.jsonl"
    source.write_text(MODULE.canonical_json(fixture_record()) + "\n", encoding="utf-8")
    monkeypatch.setattr(MODULE, "ALLOWED_RECORD_INPUTS", (source,))
    monkeypatch.setattr(MODULE, "resolve_codex_provenance", lambda value: fake_cli_provenance())
    raw = '{"labels":"0","confidence":"H","evidence":{}}'
    calls = []

    def fake_invoke(**kwargs):
        calls.append(kwargs)
        return invocation(raw)

    monkeypatch.setattr(MODULE, "invoke_codex", fake_invoke)
    args = MODULE.build_parser().parse_args(
        [
            "--input",
            str(source),
            "--staging-dir",
            str(tmp_path / "stage"),
            "--model",
            "explicit-model",
            "--reasoning-effort",
            "high",
            "--group",
            "v9",
            "--id",
            "PPS-CODEX-FIXTURE-01",
            "--id",
            "PPS-CODEX-FIXTURE-01",
            "--limit",
            "1",
            "--execute",
        ]
    )
    first = MODULE.run(args)
    second = MODULE.run(args)
    assert first["calls_attempted"] == 1
    assert first["tasks_ok"] == 1
    assert first["requested_ids"] == ["PPS-CODEX-FIXTURE-01"]
    assert first["matched_records_before_limit"] == 1
    assert first["records_selected_after_limit"] == 1
    assert second["calls_attempted"] == 0
    assert second["tasks_resumed"] == 1
    assert second["tasks_ok"] == 1
    assert len(calls) == 1
    rows = list(MODULE.read_checkpoint(tmp_path / "stage" / "groups.jsonl"))
    assert len(rows) == 1
    assert rows[0]["status"] == "ok"


def test_only_exact_organizer_record_paths_are_admitted(tmp_path):
    with pytest.raises(ValueError, match="not an organizer input"):
        MODULE.require_allowed_input(tmp_path / "predictions.jsonl")
    assert MODULE.require_allowed_input(ROOT / "data_open" / "dev.jsonl.gz") == (
        ROOT / "data_open" / "dev.jsonl.gz"
    ).resolve()


def test_source_has_no_competition_runtime_or_saved_response_dependency():
    source = MODULE_PATH.read_text(encoding="utf-8").lower()
    forbidden = (
        "import " + "submission",
        "from " + "submission",
        "import " + "pps",
        "from " + "pps",
        "saved" + "_response",
        "development" + "_predictions",
        "os." + "walk(",
        "pathlib.path.rglob(",
    )
    assert not any(token in source for token in forbidden)
