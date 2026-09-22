from __future__ import annotations

import copy
import inspect
import json
import pathlib

import pytest

from tools.independent_gold import codex_cli_annotator as BASE
from tools.independent_gold import codex_full_dev_gate as GATE
from tools.independent_gold import codex_full_record_annotator as RUNNER


def fixture_record(index: int = 0):
    return {
        "id": f"FULL-RUNNER-{index:03d}",
        "docs": [
            {
                "doc_id": "D0",
                "type": "공고문",
                "text": (
                    f"용역 입찰공고 {index}\n"
                    "입찰참가자는 공고서와 과업지시서를 확인해야 합니다.\n"
                    "공동수급 구성원의 최소 지분율은 5퍼센트입니다.\n"
                ),
            }
        ],
        "meta": {
            "적용계약법": "지방계약법",
            "업무구분": "일반용역",
            "계약방법": "일반경쟁",
            "배정예산금액": 220_000_000,
            "입찰추정가격": 200_000_000,
            "정보화사업여부": "N",
        },
        "input_completeness": {"완전관측": True},
        "dropped_doc_counts": {},
    }


def write_records(path: pathlib.Path, count: int) -> list[dict]:
    records = [fixture_record(index) for index in range(count)]
    path.write_text(
        "".join(BASE.canonical_json(record) + "\n" for record in records),
        encoding="utf-8",
    )
    return records


def fake_cli(tmp_path: pathlib.Path):
    executable = tmp_path / "codex.exe"
    executable.write_bytes(b"synthetic full-record codex")
    return {
        "requested_executable": "codex",
        "resolved_executable": str(executable.resolve()),
        "executable_sha256": BASE.file_sha256(executable),
        "version_output": "codex-test-full-record",
        "version_stderr_sha256": BASE.sha256_text(""),
    }


@pytest.fixture(scope="module")
def catalogs():
    return (
        BASE.catalog_facts.CatalogIndex.load(),
        BASE.qualification_context.qualification_facts.CatalogReference.load(),
    )


def make_manifest(
    tmp_path: pathlib.Path,
    records_path: pathlib.Path,
    records: list[dict],
    catalogs,
    *,
    role: str = "candidate",
    phase: str = "selected_panel",
    selected: list[str] | None = None,
):
    fact_catalog, qualification_catalog = catalogs
    ids = [record["id"] for record in records]
    selected_ids = selected or ids
    selection = {
        "input_ids": ids,
        "matched_ids": ids,
        "requested_ids": [],
        "selected_ids": selected_ids,
        "partitioning": {
            "method": "organizer_order_stride_v1",
            "shard_index": 0,
            "shard_count": 1,
            "limit_after_sharding": None,
        },
    }
    plan = RUNNER.build_cohort_plan(
        input_path=records_path, selection=selection, phase=phase
    )
    profile = RUNNER.prompt_profiles.get_profile(
        RUNNER.prompt_profiles.DEFAULT_PROFILE_BY_ROLE[role],
        annotator_role=role,
    )
    model = RUNNER.ROLE_MODEL[role]
    stage = tmp_path / f"stage-{role}-{phase}"
    manifest = RUNNER.build_run_manifest(
        input_path=records_path,
        staging_dir=stage,
        model=model,
        reasoning_effort="xhigh",
        cli_provenance=fake_cli(tmp_path),
        rubric=RUNNER.RUBRIC_PATH.read_text(encoding="utf-8"),
        fact_catalog=fact_catalog,
        qualification_catalog=qualification_catalog,
        annotator_role=role,
        prompt_profile=profile,
        phase=phase,
        cohort_plan=plan,
        model_identity=BASE.build_model_identity(model=model),
    )
    RUNNER.persist_run_manifest(stage, manifest)
    return stage, manifest


def valid_raw(task, *, labels=None):
    labels = labels or {item: 0 for item in RUNNER.TARGET_ITEMS}
    span_id = next(iter(task["full_record_context"]["allowed_span_registry"]))
    decisions = {}
    for item in RUNNER.TARGET_ITEMS:
        label = labels[item]
        decisions[item] = {
            "label": label,
            "confidence": "H",
            "rationale": "공급된 원문과 규칙의 구성요건을 독립적으로 대조함",
            "premise_span_ids": [span_id],
            "exception_analysis": "적용 범위와 예외가 결론을 뒤집는지 검토함",
            "completeness": "sufficient",
            "material_missing_information": None,
            "positive_evidence_span_id": (
                span_id
                if label == 1 and item not in BASE.annotate_groups.ABSENCE_ITEMS
                else None
            ),
        }
    return BASE.canonical_json({"source_span_ids": [span_id], "decisions": decisions})


def invocation(raw: str, *, unsafe: bool = False):
    events = [
        {"type": "thread.started", "thread_id": "thread-full-record-test"},
        {"type": "turn.started"},
    ]
    if unsafe:
        events.append(
            {
                "type": "item.completed",
                "item": {"id": "tool", "type": "command_execution"},
            }
        )
    events.extend(
        [
            {
                "type": "item.completed",
                "item": {"id": "answer", "type": "agent_message", "text": raw},
            },
            {"type": "turn.completed", "usage": {"input_tokens": 1}},
        ]
    )
    return {
        "returncode": 0,
        "stdout": "".join(BASE.canonical_json(event) + "\n" for event in events),
        "stderr": "",
        "raw_final": raw,
        "timeout_error": None,
        "elapsed_seconds": 1.0,
    }


def prepare_one(tmp_path, catalogs, *, role="candidate"):
    records_path = tmp_path / "records.jsonl"
    records = write_records(records_path, 1)
    stage, manifest = make_manifest(
        tmp_path, records_path, records, catalogs, role=role
    )
    fact_catalog, qualification_catalog = catalogs
    task = RUNNER.prepare_full_record_task(
        records[0],
        rubric=RUNNER.RUBRIC_PATH.read_text(encoding="utf-8"),
        staging_dir=stage,
        run_manifest=manifest,
        fact_catalog=fact_catalog,
        qualification_catalog=qualification_catalog,
    )
    return records_path, records[0], stage, manifest, task


def test_role_profiles_are_distinct_blind_and_exact_model_bound(tmp_path, catalogs):
    records_path = tmp_path / "records.jsonl"
    records = write_records(records_path, 1)
    candidate = RUNNER.prompt_profiles.get_profile(
        "candidate-full-record-source-direct-v2", annotator_role="candidate"
    )
    verifier = RUNNER.prompt_profiles.get_profile(
        "verifier-full-record-falsification-v2", annotator_role="verifier"
    )
    assert candidate.template_sha256 != verifier.template_sha256
    assert candidate.required_model == "gpt-5.6-sol"
    assert verifier.required_model == "gpt-6-astra"
    assert "peer" not in inspect.signature(candidate.render).parameters
    assert "peer" not in inspect.signature(verifier.render).parameters
    with pytest.raises(ValueError, match="not 'verifier'"):
        RUNNER.prompt_profiles.get_profile(candidate.name, annotator_role="verifier")

    fact_catalog, qualification_catalog = catalogs
    selection = {
        "input_ids": [records[0]["id"]],
        "matched_ids": [records[0]["id"]],
        "requested_ids": [],
        "selected_ids": [records[0]["id"]],
    }
    plan = RUNNER.build_cohort_plan(
        input_path=records_path, selection=selection, phase="selected_panel"
    )
    with pytest.raises(ValueError, match="require model"):
        RUNNER.build_run_manifest(
            input_path=records_path,
            staging_dir=tmp_path / "bad-stage",
            model="gpt-6-astra",
            reasoning_effort="xhigh",
            cli_provenance=fake_cli(tmp_path),
            rubric=RUNNER.RUBRIC_PATH.read_text(encoding="utf-8"),
            fact_catalog=fact_catalog,
            qualification_catalog=qualification_catalog,
            annotator_role="candidate",
            prompt_profile=candidate,
            phase="selected_panel",
            cohort_plan=plan,
            model_identity=BASE.build_model_identity(model="gpt-6-astra"),
        )


def test_deterministic_stride_partition_and_record_id_alias(tmp_path):
    records_path = tmp_path / "records.jsonl"
    records = write_records(records_path, 10)
    selection = RUNNER.select_record_ids(
        records_path,
        (),
        limit=2,
        shard_index=1,
        shard_count=3,
    )
    assert selection["selected_ids"] == (records[1]["id"], records[4]["id"])
    plan = RUNNER.build_cohort_plan(
        input_path=records_path, selection=selection, phase="selected_panel"
    )
    RUNNER.validate_cohort_plan(
        plan, input_path=records_path, phase="selected_panel"
    )
    assert plan["partitioning"] == {
        "method": "organizer_order_stride_v1",
        "shard_index": 1,
        "shard_count": 3,
        "limit_after_sharding": 2,
    }
    args = RUNNER.build_parser().parse_args(
        [
            "--input",
            str(records_path),
            "--staging-dir",
            str(tmp_path / "stage"),
            "--model",
            "gpt-5.6-sol",
            "--reasoning-effort",
            "xhigh",
            "--annotator-role",
            "candidate",
            "--prompt-profile",
            "candidate-full-record-source-direct-v2",
            "--phase",
            "selected_panel",
            "--record-id",
            records[0]["id"],
            "--record-id",
            records[2]["id"],
        ]
    )
    assert args.ids == [records[0]["id"], records[2]["id"]]


def test_continuity_canary_is_a_distinct_run_phase_not_a_tuple_change(
    tmp_path, catalogs
):
    records_path = tmp_path / "records.jsonl"
    records = write_records(records_path, 1)
    fact_catalog, qualification_catalog = catalogs
    cli = fake_cli(tmp_path)
    profile = RUNNER.prompt_profiles.get_profile(
        "candidate-full-record-source-direct-v2", annotator_role="candidate"
    )
    model_identity = BASE.build_model_identity(model="gpt-5.6-sol")

    manifests = {}
    for phase in ("development_diagnostic", "continuity_canary"):
        selection = {
            "input_ids": [records[0]["id"]],
            "matched_ids": [records[0]["id"]],
            "requested_ids": [records[0]["id"]],
            "selected_ids": [records[0]["id"]],
        }
        plan = RUNNER.build_cohort_plan(
            input_path=records_path, selection=selection, phase=phase
        )
        manifests[phase] = RUNNER.build_run_manifest(
            input_path=records_path,
            staging_dir=tmp_path / phase,
            model="gpt-5.6-sol",
            reasoning_effort="xhigh",
            cli_provenance=cli,
            rubric=RUNNER.RUBRIC_PATH.read_text(encoding="utf-8"),
            fact_catalog=fact_catalog,
            qualification_catalog=qualification_catalog,
            annotator_role="candidate",
            prompt_profile=profile,
            phase=phase,
            cohort_plan=plan,
            model_identity=model_identity,
        )
    assert manifests["continuity_canary"]["phase"] == "continuity_canary"
    assert (
        manifests["continuity_canary"]["tuple_sha256"]
        == manifests["development_diagnostic"]["tuple_sha256"]
    )
    assert (
        manifests["continuity_canary"]["run_instance_sha256"]
        != manifests["development_diagnostic"]["run_instance_sha256"]
    )
    canary_manifest = manifests["continuity_canary"]
    canary_stage = tmp_path / "continuity_canary"
    RUNNER.persist_run_manifest(canary_stage, canary_manifest)
    task = RUNNER.prepare_full_record_task(
        records[0],
        rubric=RUNNER.RUBRIC_PATH.read_text(encoding="utf-8"),
        staging_dir=canary_stage,
        run_manifest=canary_manifest,
        fact_catalog=fact_catalog,
        qualification_catalog=qualification_catalog,
    )
    assert task["manifest"]["phase"] == "continuity_canary"


def test_task_is_one_lossless_24_item_context_and_strict_ledger(
    tmp_path, catalogs
):
    _, record, _, manifest, task = prepare_one(tmp_path, catalogs)
    assert task["target_items"] == RUNNER.TARGET_ITEMS
    assert task["full_record_context"]["bounds"]["truncated"] is False
    assert set(task["manifest"]["artifact_file_sha256"]) == {
        "prompt.txt",
        "output_schema.json",
        "full_record_context.json",
    }
    assert manifest["peer_visibility"] == BASE.blind_peer_visibility()
    assert manifest["model_identity"]["drift_canary_required"] is True
    assert manifest["semantic_config"]["drift_canary_policy"]["required"] is True
    assert "submission" not in " ".join(
        row["path"] for row in manifest["imported_source_bundle"]["files"]
    )

    raw = valid_raw(task)
    result = RUNNER.validate_invocation_result(
        task,
        run_manifest=manifest,
        attempt=1,
        invocation=invocation(raw),
    )
    assert result["status"] == "ok"
    assert result["ledger"]["schema_version"] == (
        "dacon.independent.full_record_cell_ledger.v1"
    )
    assert [cell["item"] for cell in result["ledger"]["cells"]] == list(
        RUNNER.TARGET_ITEMS
    )

    parsed = json.loads(raw)
    parsed["source_span_ids"][0] = "SRC-D0000-S999999"
    tampered = RUNNER.validate_invocation_result(
        task,
        run_manifest=manifest,
        attempt=2,
        invocation=invocation(BASE.canonical_json(parsed)),
    )
    assert tampered["status"] == "error"
    assert "not in allowed registry" in tampered["error"]


def test_tool_event_is_hard_failure_even_with_valid_output(tmp_path, catalogs):
    _, _, _, manifest, task = prepare_one(tmp_path, catalogs)
    result = RUNNER.validate_invocation_result(
        task,
        run_manifest=manifest,
        attempt=1,
        invocation=invocation(valid_raw(task), unsafe=True),
    )
    assert result["status"] == "error"
    assert "tool_or_nonmessage_event_observed" in result["validation_errors"]


def test_execute_mock_resume_and_checkpoint_are_one_row_per_record(
    tmp_path, monkeypatch, catalogs
):
    records_path = tmp_path / "records.jsonl"
    record = write_records(records_path, 1)[0]
    stage = tmp_path / "stage"
    cli = fake_cli(tmp_path)
    monkeypatch.setattr(BASE, "require_allowed_input", lambda path: path.resolve())
    monkeypatch.setattr(BASE, "resolve_codex_provenance", lambda executable: cli)
    calls = []

    span_id = "SRC-D0000-S000000"
    decision = {
        "label": 0,
        "confidence": "H",
        "rationale": "전체 원문과 구성요건을 대조한 이진 판정",
        "premise_span_ids": [span_id],
        "exception_analysis": "예외가 결론을 바꾸지 않음을 검토함",
        "completeness": "sufficient",
        "material_missing_information": None,
        "positive_evidence_span_id": None,
    }
    raw = BASE.canonical_json(
        {
            "source_span_ids": [span_id],
            "decisions": {
                item: copy.deepcopy(decision) for item in RUNNER.TARGET_ITEMS
            },
        }
    )

    def fake_invoke(**kwargs):
        calls.append(kwargs)
        return invocation(raw)

    monkeypatch.setattr(BASE, "invoke_codex", fake_invoke)
    argv = [
        "--input",
        str(records_path),
        "--staging-dir",
        str(stage),
        "--model",
        "gpt-5.6-sol",
        "--reasoning-effort",
        "xhigh",
        "--annotator-role",
        "candidate",
        "--prompt-profile",
        "candidate-full-record-source-direct-v2",
        "--phase",
        "development_diagnostic",
        "--execute",
    ]
    first = RUNNER.run(RUNNER.build_parser().parse_args(argv))
    second = RUNNER.run(RUNNER.build_parser().parse_args(argv))
    assert first["calls_attempted"] == 1
    assert second["calls_attempted"] == 0
    assert second["tasks_resumed"] == 1
    assert len(calls) == 1
    checkpoint = stage / "full_records.jsonl"
    rows = list(BASE.read_checkpoint(checkpoint))
    assert len(rows) == 1 and rows[0]["status"] == "ok"


def test_safe_retry_yields_one_qualifiable_checkpoint_row(
    tmp_path, monkeypatch, catalogs
):
    records_path, record, stage, manifest, task = prepare_one(tmp_path, catalogs)
    cli = manifest["codex_cli"]
    monkeypatch.setattr(BASE, "require_allowed_input", lambda path: path.resolve())
    monkeypatch.setattr(BASE, "resolve_codex_provenance", lambda executable: cli)
    raw = valid_raw(task)
    calls = [invocation("{}"), invocation(raw)]
    monkeypatch.setattr(BASE, "invoke_codex", lambda **kwargs: calls.pop(0))
    args = RUNNER.build_parser().parse_args(
        [
            "--input",
            str(records_path),
            "--staging-dir",
            str(stage),
            "--model",
            "gpt-5.6-sol",
            "--reasoning-effort",
            "xhigh",
            "--annotator-role",
            "candidate",
            "--prompt-profile",
            "candidate-full-record-source-direct-v2",
            "--phase",
            "selected_panel",
            "--retries",
            "2",
            "--execute",
        ]
    )
    stats = RUNNER.run(args)
    assert stats["calls_attempted"] == 2
    rows = list(BASE.read_checkpoint(stage / "full_records.jsonl"))
    assert len(rows) == 1
    assert rows[0]["status"] == "ok" and rows[0]["attempt"] == 2

    fact_catalog, _ = catalogs
    artifacts = GATE._validate_task_manifest(
        task["manifest"],
        run_manifest=manifest,
        record=record,
        record_id=record["id"],
        group_name=GATE.FULL_RECORD_GROUP,
        target_items=RUNNER.TARGET_ITEMS,
        task_dir=task["task_dir"],
        context="safe retry",
        fact_catalog=fact_catalog,
    )
    decisions = GATE._validate_receipt(
        rows[0],
        checkpoint_line=1,
        task_dir=task["task_dir"],
        task_manifest=task["manifest"],
        artifacts=artifacts,
        run_manifest=manifest,
        record=record,
        record_id=record["id"],
        group_name=GATE.FULL_RECORD_GROUP,
        target_items=RUNNER.TARGET_ITEMS,
    )
    assert len(decisions) == 24


def test_unsafe_attempt_stops_retries_and_stays_fail_closed_on_resume(
    tmp_path, monkeypatch, catalogs
):
    records_path, _, stage, manifest, task = prepare_one(tmp_path, catalogs)
    cli = manifest["codex_cli"]
    monkeypatch.setattr(BASE, "require_allowed_input", lambda path: path.resolve())
    monkeypatch.setattr(BASE, "resolve_codex_provenance", lambda executable: cli)
    calls = []

    def unsafe_call(**kwargs):
        calls.append(kwargs)
        return invocation(valid_raw(task), unsafe=True)

    monkeypatch.setattr(BASE, "invoke_codex", unsafe_call)
    args = RUNNER.build_parser().parse_args(
        [
            "--input",
            str(records_path),
            "--staging-dir",
            str(stage),
            "--model",
            "gpt-5.6-sol",
            "--reasoning-effort",
            "xhigh",
            "--annotator-role",
            "candidate",
            "--prompt-profile",
            "candidate-full-record-source-direct-v2",
            "--phase",
            "selected_panel",
            "--retries",
            "3",
            "--execute",
        ]
    )
    first = RUNNER.run(args)
    second = RUNNER.run(args)
    assert first["calls_attempted"] == 1
    assert second["calls_attempted"] == 0
    assert second["tasks_error"] == 1
    assert len(calls) == 1
    assert not (stage / "full_records.jsonl").exists()


def test_real_full_record_manifest_task_receipt_round_trip_and_tamper_gate(
    tmp_path, catalogs
):
    records_path = tmp_path / "dev.jsonl"
    records = write_records(records_path, GATE.OFFICIAL_DEV_ID_COUNT)
    stage, manifest = make_manifest(
        tmp_path,
        records_path,
        records,
        catalogs,
        phase="official_dev",
    )
    staging_dir, rows, identity = GATE._validate_run_manifest(
        manifest,
        manifest_path=stage / "run_manifest.json",
        records_path=records_path,
        organizer_ids=[record["id"] for record in records],
        topology=GATE.FULL_RECORD_TOPOLOGY,
        context="full-record header round trip",
    )
    assert staging_dir == stage.resolve()
    assert len(rows) == 200
    assert identity["prompt_profile"] == "candidate-full-record-source-direct-v2"

    fact_catalog, qualification_catalog = catalogs
    record = records[0]
    task = RUNNER.prepare_full_record_task(
        record,
        rubric=RUNNER.RUBRIC_PATH.read_text(encoding="utf-8"),
        staging_dir=stage,
        run_manifest=manifest,
        fact_catalog=fact_catalog,
        qualification_catalog=qualification_catalog,
    )
    raw = valid_raw(task)
    call = invocation(raw)
    receipt = RUNNER.validate_invocation_result(
        task, run_manifest=manifest, attempt=1, invocation=call
    )
    BASE.persist_attempt(task, attempt=1, invocation=call, result=receipt)
    artifacts = GATE._validate_task_manifest(
        task["manifest"],
        run_manifest=manifest,
        record=record,
        record_id=record["id"],
        group_name=GATE.FULL_RECORD_GROUP,
        target_items=RUNNER.TARGET_ITEMS,
        task_dir=task["task_dir"],
        context="full-record task round trip",
        fact_catalog=fact_catalog,
    )
    decisions = GATE._validate_receipt(
        receipt,
        checkpoint_line=1,
        task_dir=task["task_dir"],
        task_manifest=task["manifest"],
        artifacts=artifacts,
        run_manifest=manifest,
        record=record,
        record_id=record["id"],
        group_name=GATE.FULL_RECORD_GROUP,
        target_items=RUNNER.TARGET_ITEMS,
    )
    assert len(decisions) == 24

    tampered = copy.deepcopy(receipt)
    tampered["ledger"]["cells"][0]["label"] = 1
    (task["task_dir"] / "attempts" / "attempt-001" / "receipt.json").write_text(
        BASE.canonical_json(tampered) + "\n", encoding="utf-8"
    )
    with pytest.raises(GATE.FullDevGateError, match="ledger shadow"):
        GATE._validate_receipt(
            tampered,
            checkpoint_line=1,
            task_dir=task["task_dir"],
            task_manifest=task["manifest"],
            artifacts=artifacts,
            run_manifest=manifest,
            record=record,
            record_id=record["id"],
            group_name=GATE.FULL_RECORD_GROUP,
            target_items=RUNNER.TARGET_ITEMS,
        )


def test_source_has_no_competition_runtime_dependency():
    source = pathlib.Path(RUNNER.__file__).read_text(encoding="utf-8").casefold()
    forbidden = (
        "import " + "submission",
        "from " + "submission",
        "import " + "pps",
        "from " + "pps",
        "saved" + "_response",
        "gemma" + "_response",
    )
    assert not any(token in source for token in forbidden)
