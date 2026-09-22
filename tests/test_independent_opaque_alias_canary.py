from __future__ import annotations

import copy
import json
import pathlib

import pytest

from tools.independent_gold import codex_cli_annotator as BASE
from tools.independent_gold import codex_full_record_annotator as RUNNER
from tools.independent_gold import full_record_context as CONTEXT
from tools.independent_gold import opaque_alias_canary as CANARY


def fixture_record(prefix: str, index: int) -> dict:
    return {
        "id": f"{prefix}-{index:03d}",
        "docs": [
            {
                "doc_id": "D0",
                "type": "공고문",
                "text": (
                    f"용역 입찰공고 {index}\n"
                    "입찰참가자는 공고서와 과업지시서를 확인해야 합니다.\n"
                    "공동수급 구성원의 최소 지분율은 5퍼센트입니다.\n"
                ),
            },
            {
                "doc_id": "D1",
                "type": "과업지시서",
                "text": (
                    "과업 수행범위와 납품 절차를 준수해야 합니다.\n"
                    "세부 일정은 계약담당자와 협의합니다.\n"
                ),
            },
        ],
        "meta": {
            "적용계약법": "지방계약법",
            "업무구분": "일반용역",
            "계약방법": "일반경쟁",
            "배정예산금액": 220_000_000 + index,
            "입찰추정가격": 200_000_000 + index,
            "정보화사업여부": "N",
        },
        "input_completeness": {"완전관측": True},
        "dropped_doc_counts": {},
    }


def write_records(path: pathlib.Path, records: list[dict]) -> None:
    path.write_text(
        "".join(BASE.canonical_json(record) + "\n" for record in records),
        encoding="utf-8",
    )


@pytest.fixture(scope="module")
def catalogs():
    return (
        BASE.catalog_facts.CatalogIndex.load(),
        BASE.qualification_context.qualification_facts.CatalogReference.load(),
    )


def make_plan(tmp_path: pathlib.Path, catalogs, *, interval_every: int = 1):
    dev_records = [fixture_record("DEV", index) for index in range(4)]
    full_records = [fixture_record("FULL", index) for index in range(4)]
    dev_path = tmp_path / "dev.jsonl"
    full_path = tmp_path / "unlabeled.jsonl"
    write_records(dev_path, dev_records)
    write_records(full_path, full_records)
    fact_catalog, qualification_catalog = catalogs
    fingerprints = {}
    for record in dev_records:
        context = CONTEXT.build_full_record_context(
            record,
            catalog_index=fact_catalog,
            qualification_catalog=qualification_catalog,
        )
        assert not CONTEXT.validate_full_record_context(
            record, context, catalog_index=fact_catalog
        )
        fingerprints[record["id"]] = {
            "full_record_context_sha256": context["context_sha256"],
            "full_record_context_schema_version": context["schema_version"],
            "full_record_context_bounds": context["bounds"],
        }
    plan = CANARY.build_plan_from_records(
        dev_records=dev_records,
        full_records=full_records,
        dev_input_path=dev_path,
        dev_input_sha256=BASE.file_sha256(dev_path),
        full_input_path=full_path,
        full_input_sha256=BASE.file_sha256(full_path),
        context_fingerprints=fingerprints,
        canary_count=1,
        interval_every=interval_every,
    )
    return plan, dev_records, dev_path


def fake_cli(tmp_path: pathlib.Path) -> dict:
    executable = tmp_path / "codex.exe"
    if not executable.exists():
        executable.write_bytes(b"opaque canary synthetic codex")
    return {
        "requested_executable": "codex",
        "resolved_executable": str(executable.resolve()),
        "executable_sha256": BASE.file_sha256(executable),
        "version_output": "codex-test-canary",
        "version_stderr_sha256": BASE.sha256_text(""),
    }


def invocation(raw: str, *, thread_id: str, unsafe: bool = False) -> dict:
    events = [
        {"type": "thread.started", "thread_id": thread_id},
        {"type": "turn.started"},
    ]
    if unsafe:
        events.append(
            {
                "type": "item.completed",
                "item": {"id": "unsafe", "type": "command_execution"},
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


def valid_raw(task: dict, *, positive: bool = False, span_offset: int = 0) -> str:
    registry = list(task["full_record_context"]["allowed_span_registry"].values())
    span_id = registry[span_offset % len(registry)]["span_id"]
    decisions = {}
    for item in RUNNER.TARGET_ITEMS:
        label = 1 if positive and item == "v9" else 0
        decisions[item] = {
            "label": label,
            "confidence": "H",
            "rationale": "공급 원문과 독립 규칙을 대조한 이진 판정",
            "premise_span_ids": [span_id],
            "exception_analysis": "예외가 결론을 바꾸지 않음을 확인함",
            "completeness": "sufficient",
            "material_missing_information": None,
            "positive_evidence_span_id": span_id if label == 1 else None,
        }
    return BASE.canonical_json({"source_span_ids": [span_id], "decisions": decisions})


def materialize_stage(
    tmp_path: pathlib.Path,
    plan: dict,
    dev_records: list[dict],
    dev_path: pathlib.Path,
    catalogs,
    *,
    role: str,
    name: str,
    positive: bool = False,
    span_offset: int = 0,
    unsafe: bool = False,
) -> pathlib.Path:
    role_plan = plan["roles"][role]
    selected_ids = role_plan["canary_ids"]
    selection = {
        "input_ids": [record["id"] for record in dev_records],
        "matched_ids": selected_ids,
        "requested_ids": selected_ids,
        "selected_ids": selected_ids,
        "partitioning": {
            "method": "organizer_order_stride_v1",
            "shard_index": 0,
            "shard_count": 1,
            "limit_after_sharding": None,
        },
    }
    cohort = RUNNER.build_cohort_plan(
        input_path=dev_path, selection=selection, phase=CANARY.CANARY_PHASE
    )
    profile = RUNNER.prompt_profiles.get_profile(
        role_plan["prompt_profile"], annotator_role=role
    )
    fact_catalog, qualification_catalog = catalogs
    stage = tmp_path / name
    manifest = RUNNER.build_run_manifest(
        input_path=dev_path,
        staging_dir=stage,
        model=role_plan["requested_model"],
        reasoning_effort=role_plan["reasoning_effort"],
        cli_provenance=fake_cli(tmp_path),
        rubric=RUNNER.RUBRIC_PATH.read_text(encoding="utf-8"),
        fact_catalog=fact_catalog,
        qualification_catalog=qualification_catalog,
        annotator_role=role,
        prompt_profile=profile,
        phase=CANARY.CANARY_PHASE,
        cohort_plan=cohort,
        model_identity=copy.deepcopy(role_plan["model_identity"]),
    )
    RUNNER.persist_run_manifest(stage, manifest)
    by_id = {record["id"]: record for record in dev_records}
    for row_index, record_id in enumerate(selected_ids):
        task = RUNNER.prepare_full_record_task(
            by_id[record_id],
            rubric=RUNNER.RUBRIC_PATH.read_text(encoding="utf-8"),
            staging_dir=stage,
            run_manifest=manifest,
            fact_catalog=fact_catalog,
            qualification_catalog=qualification_catalog,
        )
        raw = valid_raw(task, positive=positive, span_offset=span_offset)
        call = invocation(
            raw, thread_id=f"thread-{name}-{row_index}", unsafe=unsafe
        )
        receipt = RUNNER.validate_invocation_result(
            task, run_manifest=manifest, attempt=1, invocation=call
        )
        BASE.persist_attempt(task, attempt=1, invocation=call, result=receipt)
        BASE.append_checkpoint(stage / "full_records.jsonl", receipt)
    return stage


def observe(
    tmp_path: pathlib.Path,
    plan: dict,
    dev_records: list[dict],
    dev_path: pathlib.Path,
    catalogs,
    *,
    checkpoint_index: int,
    name: str,
    role: str = "candidate",
    positive: bool = False,
    span_offset: int = 0,
    unsafe: bool = False,
) -> dict:
    stage = materialize_stage(
        tmp_path,
        plan,
        dev_records,
        dev_path,
        catalogs,
        role=role,
        name=name,
        positive=positive,
        span_offset=span_offset,
        unsafe=unsafe,
    )
    checkpoint_id = plan["schedule"]["checkpoints"][checkpoint_index][
        "checkpoint_id"
    ]
    return CANARY.inspect_runner_checkpoint(
        plan,
        annotator_role=role,
        checkpoint_id=checkpoint_id,
        staging_dir=stage,
    )


def test_plan_is_label_free_role_separated_and_predeclares_all_boundaries(
    tmp_path, catalogs
):
    plan, _, _ = make_plan(tmp_path, catalogs)
    CANARY.validate_plan(plan)
    assert plan["selection"]["labels_consulted"] is False
    assert set(plan["roles"]["candidate"]["canary_ids"]).isdisjoint(
        plan["roles"]["verifier"]["canary_ids"]
    )
    assert plan["roles"]["candidate"]["model_identity"]["resolved_revision"] is None
    assert plan["roles"]["verifier"]["model_identity"]["resolved_revision"] is None
    kinds = [row["kind"] for row in plan["schedule"]["checkpoints"]]
    assert kinds == [
        "before_qualification",
        "pre_full_run",
        "interval",
        "interval",
        "interval",
        "post_run",
    ]
    command = CANARY.build_runner_command(
        plan,
        annotator_role="candidate",
        checkpoint_id=plan["schedule"]["checkpoints"][0]["checkpoint_id"],
        staging_dir=tmp_path / "command-stage",
    )
    assert "continuity_canary" in command
    assert "dev_labels.csv" not in " ".join(command)
    assert "--retries" in command and command[command.index("--retries") + 1] == "1"


def test_observe_pass_and_gapless_epoch_advance(tmp_path, catalogs):
    plan, records, dev_path = make_plan(tmp_path, catalogs)
    baseline = observe(
        tmp_path,
        plan,
        records,
        dev_path,
        catalogs,
        checkpoint_index=0,
        name="baseline",
    )
    assert baseline["status"] == "valid"
    epoch = CANARY.start_epoch(plan, baseline)
    assert epoch["status"] == "open"
    prefull = observe(
        tmp_path,
        plan,
        records,
        dev_path,
        catalogs,
        checkpoint_index=1,
        name="prefull",
    )
    assert prefull["comparison_sha256"] == baseline["comparison_sha256"]
    epoch = CANARY.advance_epoch(plan, epoch, prefull)
    assert epoch["status"] == "open"
    assert epoch["last_passing_checkpoint"]["kind"] == "pre_full_run"
    assert epoch["invalidation"] is None


def test_all_predeclared_passes_produce_complete_continuity_epoch(
    tmp_path, catalogs
):
    plan, records, dev_path = make_plan(tmp_path, catalogs)
    epoch = None
    for checkpoint_index in range(plan["schedule"]["checkpoint_count"]):
        result = observe(
            tmp_path,
            plan,
            records,
            dev_path,
            catalogs,
            checkpoint_index=checkpoint_index,
            name=f"complete-{checkpoint_index}",
        )
        epoch = (
            CANARY.start_epoch(plan, result)
            if epoch is None
            else CANARY.advance_epoch(plan, epoch, result)
        )
    assert epoch is not None
    assert epoch["status"] == "complete"
    assert epoch["continuity_qualified"] is True
    assert epoch["next_required_checkpoint_id"] is None
    assert epoch["invalidation"] is None


def test_label_drift_closes_epoch_and_invalidates_since_last_pass(
    tmp_path, catalogs
):
    plan, records, dev_path = make_plan(tmp_path, catalogs)
    baseline = observe(
        tmp_path,
        plan,
        records,
        dev_path,
        catalogs,
        checkpoint_index=0,
        name="drift-baseline",
    )
    epoch = CANARY.start_epoch(plan, baseline)
    changed = observe(
        tmp_path,
        plan,
        records,
        dev_path,
        catalogs,
        checkpoint_index=1,
        name="drift-prefull",
        positive=True,
    )
    closed = CANARY.advance_epoch(plan, epoch, changed)
    assert closed["status"] == "closed_drift"
    invalidation = closed["invalidation"]
    assert invalidation["global_ordinal_start_inclusive"] == 0
    assert invalidation["global_ordinal_end_exclusive"] == 4
    assert invalidation["task_count"] == 4
    assert invalidation["segments"][0]["record_ids"] == plan["workload_order"][
        "official_dev_qualification"
    ]
    assert any("labels" in row["path"] for row in invalidation["differences"])


def test_drift_after_prefull_invalidates_only_full_run_interval(tmp_path, catalogs):
    plan, records, dev_path = make_plan(tmp_path, catalogs)
    baseline = observe(
        tmp_path,
        plan,
        records,
        dev_path,
        catalogs,
        checkpoint_index=0,
        name="range-baseline",
    )
    epoch = CANARY.start_epoch(plan, baseline)
    prefull = observe(
        tmp_path,
        plan,
        records,
        dev_path,
        catalogs,
        checkpoint_index=1,
        name="range-prefull",
    )
    epoch = CANARY.advance_epoch(plan, epoch, prefull)
    changed = observe(
        tmp_path,
        plan,
        records,
        dev_path,
        catalogs,
        checkpoint_index=2,
        name="range-first-interval",
        positive=True,
    )
    closed = CANARY.advance_epoch(plan, epoch, changed)
    segment = closed["invalidation"]["segments"]
    assert len(segment) == 1
    assert segment[0]["workload"] == "unlabeled_full_run"
    assert segment[0]["ordinal_start_inclusive"] == 0
    assert segment[0]["ordinal_end_exclusive"] == 1
    assert segment[0]["record_ids"] == [
        plan["workload_order"]["unlabeled_full_run"][0]
    ]


def test_positive_evidence_coordinate_change_is_drift_even_when_labels_match(
    tmp_path, catalogs
):
    plan, records, dev_path = make_plan(tmp_path, catalogs)
    baseline = observe(
        tmp_path,
        plan,
        records,
        dev_path,
        catalogs,
        checkpoint_index=0,
        name="evidence-baseline",
        positive=True,
        span_offset=0,
    )
    epoch = CANARY.start_epoch(plan, baseline)
    changed = observe(
        tmp_path,
        plan,
        records,
        dev_path,
        catalogs,
        checkpoint_index=1,
        name="evidence-changed",
        positive=True,
        span_offset=1,
    )
    baseline_labels = baseline["comparison_projection"]["records"][0]["labels"]
    changed_labels = changed["comparison_projection"]["records"][0]["labels"]
    assert baseline_labels == changed_labels
    closed = CANARY.advance_epoch(plan, epoch, changed)
    assert closed["status"] == "closed_drift"
    paths = [row["path"] for row in closed["invalidation"]["differences"]]
    assert any("positive_evidence" in path for path in paths)


def test_interval_gap_and_exact_artifact_replay_are_rejected(tmp_path, catalogs):
    plan, records, dev_path = make_plan(tmp_path, catalogs)
    baseline = observe(
        tmp_path,
        plan,
        records,
        dev_path,
        catalogs,
        checkpoint_index=0,
        name="gap-baseline",
    )
    epoch = CANARY.start_epoch(plan, baseline)
    skipped = observe(
        tmp_path,
        plan,
        records,
        dev_path,
        catalogs,
        checkpoint_index=2,
        name="gap-skipped",
    )
    with pytest.raises(CANARY.CanaryError, match="checkpoint gap"):
        CANARY.advance_epoch(plan, epoch, skipped)

    replay = copy.deepcopy(baseline)
    replay["checkpoint"] = copy.deepcopy(plan["schedule"]["checkpoints"][1])
    replay["observed_utc"] = BASE.utc_now()
    replay["manifest_sha256"] = CANARY._manifest_hash(replay)
    CANARY.validate_result(replay, plan=plan)
    with pytest.raises(CANARY.CanaryError, match="replayed"):
        CANARY.advance_epoch(plan, epoch, replay)


def test_tamper_unsafe_wrong_role_and_tuple_all_fail_closed(tmp_path, catalogs):
    plan, records, dev_path = make_plan(tmp_path, catalogs)

    tamper_stage = materialize_stage(
        tmp_path,
        plan,
        records,
        dev_path,
        catalogs,
        role="candidate",
        name="tampered",
    )
    checkpoint_path = tamper_stage / "full_records.jsonl"
    row = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    row["ledger"]["cells"][0]["label"] = 1
    checkpoint_path.write_text(BASE.canonical_json(row) + "\n", encoding="utf-8")
    result = CANARY.inspect_runner_checkpoint(
        plan,
        annotator_role="candidate",
        checkpoint_id=plan["schedule"]["checkpoints"][0]["checkpoint_id"],
        staging_dir=tamper_stage,
    )
    assert result["status"] == "fail_closed"
    assert "mismatch" in result["failures"][0]["detail"]

    unsafe = observe(
        tmp_path,
        plan,
        records,
        dev_path,
        catalogs,
        checkpoint_index=0,
        name="unsafe",
        unsafe=True,
    )
    assert unsafe["status"] == "fail_closed"
    assert "unsafe event" in unsafe["failures"][0]["detail"]

    wrong_role_stage = materialize_stage(
        tmp_path,
        plan,
        records,
        dev_path,
        catalogs,
        role="verifier",
        name="wrong-role",
    )
    wrong_role = CANARY.inspect_runner_checkpoint(
        plan,
        annotator_role="candidate",
        checkpoint_id=plan["schedule"]["checkpoints"][0]["checkpoint_id"],
        staging_dir=wrong_role_stage,
    )
    assert wrong_role["status"] == "fail_closed"
    assert "wrong runner annotator role" in wrong_role["failures"][0]["detail"]

    tuple_stage = materialize_stage(
        tmp_path,
        plan,
        records,
        dev_path,
        catalogs,
        role="candidate",
        name="wrong-tuple",
    )
    manifest_path = tuple_stage / "run_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["tuple_sha256"] = "0" * 64
    manifest["manifest_sha256"] = CANARY._manifest_hash(manifest)
    manifest_path.write_text(BASE.canonical_json(manifest) + "\n", encoding="utf-8")
    wrong_tuple = CANARY.inspect_runner_checkpoint(
        plan,
        annotator_role="candidate",
        checkpoint_id=plan["schedule"]["checkpoints"][0]["checkpoint_id"],
        staging_dir=tuple_stage,
    )
    assert wrong_tuple["status"] == "fail_closed"
    assert "semantic tuple hash mismatch" in wrong_tuple["failures"][0]["detail"]


def test_positive_evidence_projection_includes_exact_quote_and_coordinates(
    tmp_path, catalogs
):
    plan, records, dev_path = make_plan(tmp_path, catalogs)
    result = observe(
        tmp_path,
        plan,
        records,
        dev_path,
        catalogs,
        checkpoint_index=0,
        name="positive-evidence",
        positive=True,
    )
    record = result["comparison_projection"]["records"][0]
    evidence = next(
        row["positive_evidence"]
        for row in record["positive_evidence"]
        if row["item"] == "v9"
    )
    assert evidence is not None
    source_record = next(
        row for row in records if row["id"] == record["record_id"]
    )
    text = source_record["docs"][evidence["doc_index"]]["text"]
    assert text[evidence["start"] : evidence["end"]] == evidence["quote"]
    assert len(evidence["source_doc_sha256"]) == 64


def test_manifest_tamper_and_immutable_write_fail(tmp_path, catalogs):
    plan, _, _ = make_plan(tmp_path, catalogs)
    path = tmp_path / "plan.json"
    CANARY._write_json_immutable(path, plan)
    CANARY._write_json_immutable(path, plan)
    changed = copy.deepcopy(plan)
    changed["selection"]["seed"] = "changed"
    changed["manifest_sha256"] = CANARY._manifest_hash(changed)
    with pytest.raises(ValueError, match="immutable staging artifact differs"):
        CANARY._write_json_immutable(path, changed)
    tampered = copy.deepcopy(plan)
    tampered["manifest_sha256"] = "0" * 64
    with pytest.raises(CANARY.CanaryError, match="plan hash mismatch"):
        CANARY.validate_plan(tampered)


def test_source_has_no_production_or_label_input_dependency():
    source = pathlib.Path(CANARY.__file__).read_text(encoding="utf-8").casefold()
    forbidden = (
        "import " + "submission",
        "from " + "submission",
        "import " + "pps",
        "from " + "pps",
        "dev_" + "labels.csv",
        "saved" + "_response",
        "production" + "_predictions",
    )
    assert not any(token in source for token in forbidden)
