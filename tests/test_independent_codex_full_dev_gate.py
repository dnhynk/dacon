from __future__ import annotations

import csv
import json
import pathlib

import pytest

from tools.independent_gold import codex_cli_annotator as RUNNER
from tools.independent_gold import codex_full_dev_gate as GATE


QUOTE_PREFIX = "공식 원문 근거 "


def _label(record_index: int, item: str) -> int:
    return int((record_index + int(item[1:])) % 4 == 0)


def _write_inputs(root: pathlib.Path):
    records = []
    labels = {}
    for index in range(GATE.OFFICIAL_DEV_ID_COUNT):
        record_id = f"DEV-{index:03d}"
        quote = f"{QUOTE_PREFIX}{record_id}"
        record = {
            "id": record_id,
            "docs": [{"doc_id": "D0", "type": "공고문", "text": quote + "\n끝"}],
            "meta": {},
            "input_completeness": {"완전관측": True},
            "dropped_doc_counts": {},
        }
        records.append(record)
        labels[record_id] = {
            item: _label(index, item) for item in RUNNER.annotate_groups.ITEMS
        }
    records_path = root / "dev.jsonl"
    records_path.write_text(
        "".join(RUNNER.canonical_json(record) + "\n" for record in records),
        encoding="utf-8",
    )
    labels_path = root / "dev_labels.csv"
    with labels_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle, fieldnames=["id", *RUNNER.annotate_groups.ITEMS]
        )
        writer.writeheader()
        for record in records:
            writer.writerow({"id": record["id"], **labels[record["id"]]})
    return records_path, labels_path, {row["id"]: row for row in records}, labels


def _location(record):
    text = record["docs"][0]["text"]
    quote = text.splitlines()[0]
    return quote, {
        "origin": "source_packet",
        "segment_id": "segment-1",
        "doc_index": 0,
        "doc_id": "D0",
        "source_doc_sha256": RUNNER.sha256_text(text),
        "start": 0,
        "end": len(quote),
        "segment_start": 0,
        "segment_end": len(quote),
        "evidence_sha256": RUNNER.sha256_text(quote),
    }


def _decisions(record, target_items, labels, *, unknown=None, forged=None):
    result = {}
    quote, location = _location(record)
    for item in target_items:
        label = labels[record["id"]][item]
        if unknown == (record["id"], item):
            label = "U"
        needs_evidence = label == 1 and item not in RUNNER.annotate_groups.ABSENCE_ITEMS
        evidence = quote if needs_evidence else ""
        locations = [dict(location)] if needs_evidence else []
        if forged == (record["id"], item) and locations:
            locations[0]["start"] = 1
        result[item] = {
            "label": label,
            "confidence": "high",
            "evidence": evidence,
            "evidence_locations": locations,
        }
    return result


def _checkpoint(path: pathlib.Path, records, topology):
    item_map = GATE._group_items(topology)
    rows = [
        {"id": record_id, "group": group, "target_items": list(items)}
        for record_id in records
        for group, items in item_map.items()
    ]
    path.write_text(
        "".join(RUNNER.canonical_json(row) + "\n" for row in rows),
        encoding="utf-8",
    )
    return rows


def _minimal_manifest(path: pathlib.Path, *, instance="1" * 64):
    stage = path.parent / (path.stem + "-stage")
    value = {
        "manifest_sha256": "2" * 64,
        "run_instance_sha256": instance,
        "cohort_plan": {"manifest_sha256": "3" * 64},
    }
    path.write_text(RUNNER.canonical_json(value) + "\n", encoding="utf-8")
    return stage


def _install_synthetic_validators(
    monkeypatch,
    *,
    records,
    labels,
    topology,
    stage,
    state,
    unknown=None,
    forged=None,
):
    master = GATE._master_rows(list(records), topology)
    identity = {
        "tuple_sha256": "4" * 64,
        "annotator_role": "candidate",
        "prompt_profile": "candidate-source-direct-v1",
        "prompt_lineage_sha256": "5" * 64,
        "model_identity": {"family": "openai:gpt-5.6"},
    }

    def validate_manifest(manifest, **kwargs):
        state["manifests"] += 1
        return stage, master, identity

    def validate_receipts(*, indexed_rows, records, item_map, **kwargs):
        state["receipts"] += len(indexed_rows)
        result = {}
        for record_id, group in indexed_rows:
            result[(record_id, group)] = _decisions(
                records[record_id],
                item_map[group],
                labels,
                unknown=unknown,
                forged=forged,
            )
        state["candidate_validated"] = True
        return result

    monkeypatch.setattr(GATE, "_validate_run_manifest", validate_manifest)
    monkeypatch.setattr(GATE, "_validate_partition_receipts", validate_receipts)


@pytest.mark.parametrize(
    ("topology", "expected_rows"),
    ((GATE.GROUPED_TOPOLOGY, 1_600), (GATE.FULL_RECORD_TOPOLOGY, 200)),
)
def test_both_fixed_topologies_cover_exactly_200_ids_and_4800_cells(
    tmp_path, monkeypatch, topology, expected_rows
):
    records_path, labels_path, records, labels = _write_inputs(tmp_path)
    manifest_path = tmp_path / "run_manifest.json"
    stage = _minimal_manifest(manifest_path)
    checkpoint_path = tmp_path / "groups.jsonl"
    _checkpoint(checkpoint_path, records, topology)
    state = {"manifests": 0, "receipts": 0, "candidate_validated": False}
    _install_synthetic_validators(
        monkeypatch,
        records=records,
        labels=labels,
        topology=topology,
        stage=stage,
        state=state,
    )
    original_read_labels = GATE.qualification_panel.read_labels

    def read_labels_late(path):
        assert state["candidate_validated"] is True
        return original_read_labels(path)

    monkeypatch.setattr(GATE.qualification_panel, "read_labels", read_labels_late)
    report = GATE.full_dev_score(
        records_path=records_path,
        labels_path=labels_path,
        run_manifest_path=manifest_path,
        checkpoint_path=checkpoint_path,
        topology=topology,
    )
    assert report["official_dev_role_gate_pass"] is True
    assert report["macro_positive_f1"] == 1.0
    assert report["minimum_item_positive_f1"] == 1.0
    assert report["coverage"]["unique_ids"] == 200
    assert report["coverage"]["required_rows"] == expected_rows
    assert report["coverage"]["unique_cells"] == 4_800
    assert report["is_selected_panel"] is False
    assert report["qualified_for_declared_role"] is True
    assert report["qualified_for_gold_generation"] is False
    assert report["qualified_for_final_gold_generation"] is False
    assert state["receipts"] == expected_rows


@pytest.mark.parametrize("mutation", ("missing", "duplicate", "extra"))
def test_missing_duplicate_and_out_of_cohort_rows_fail_before_labels(
    tmp_path, monkeypatch, mutation
):
    records_path, labels_path, records, labels = _write_inputs(tmp_path)
    manifest_path = tmp_path / "run_manifest.json"
    stage = _minimal_manifest(manifest_path)
    checkpoint_path = tmp_path / "groups.jsonl"
    rows = _checkpoint(checkpoint_path, records, GATE.FULL_RECORD_TOPOLOGY)
    if mutation == "missing":
        rows = rows[1:]
    elif mutation == "duplicate":
        rows.append(dict(rows[0]))
    else:
        rows.append(
            {
                "id": "NOT-IN-DEV",
                "group": GATE.FULL_RECORD_GROUP,
                "target_items": list(RUNNER.annotate_groups.ITEMS),
            }
        )
    checkpoint_path.write_text(
        "".join(RUNNER.canonical_json(row) + "\n" for row in rows),
        encoding="utf-8",
    )
    state = {"manifests": 0, "receipts": 0, "candidate_validated": False}
    _install_synthetic_validators(
        monkeypatch,
        records=records,
        labels=labels,
        topology=GATE.FULL_RECORD_TOPOLOGY,
        stage=stage,
        state=state,
    )
    monkeypatch.setattr(
        GATE.qualification_panel,
        "read_labels",
        lambda path: pytest.fail("labels opened before hard coverage failure"),
    )
    with pytest.raises(GATE.FullDevGateError, match=mutation.split("_")[0]):
        GATE.full_dev_score(
            records_path=records_path,
            labels_path=labels_path,
            run_manifest_path=manifest_path,
            checkpoint_path=checkpoint_path,
            topology=GATE.FULL_RECORD_TOPOLOGY,
        )


@pytest.mark.parametrize("defect", ("unknown", "forged"))
def test_u_and_invalid_positive_evidence_fail_before_labels(tmp_path, monkeypatch, defect):
    records_path, labels_path, records, labels = _write_inputs(tmp_path)
    manifest_path = tmp_path / "run_manifest.json"
    stage = _minimal_manifest(manifest_path)
    checkpoint_path = tmp_path / "groups.jsonl"
    _checkpoint(checkpoint_path, records, GATE.FULL_RECORD_TOPOLOGY)
    first_positive = next(
        (record_id, item)
        for record_id, item_labels in labels.items()
        for item, value in item_labels.items()
        if value == 1 and item not in RUNNER.annotate_groups.ABSENCE_ITEMS
    )
    state = {"manifests": 0, "receipts": 0, "candidate_validated": False}
    _install_synthetic_validators(
        monkeypatch,
        records=records,
        labels=labels,
        topology=GATE.FULL_RECORD_TOPOLOGY,
        stage=stage,
        state=state,
        unknown=first_positive if defect == "unknown" else None,
        forged=first_positive if defect == "forged" else None,
    )
    monkeypatch.setattr(
        GATE.qualification_panel,
        "read_labels",
        lambda path: pytest.fail("labels opened before decision/evidence failure"),
    )
    pattern = "non-binary" if defect == "unknown" else "does not resolve"
    with pytest.raises(GATE.FullDevGateError, match=pattern):
        GATE.full_dev_score(
            records_path=records_path,
            labels_path=labels_path,
            run_manifest_path=manifest_path,
            checkpoint_path=checkpoint_path,
            topology=GATE.FULL_RECORD_TOPOLOGY,
        )


def test_selected_panel_phase_can_never_be_a_full_dev_plan(tmp_path):
    records_path, _, records, _ = _write_inputs(tmp_path)
    ids = list(records)
    group = RUNNER.annotate_groups.GROUPS[0][0]
    plan = {
        "schema_version": RUNNER.COHORT_PLAN_SCHEMA_VERSION,
        "phase": "selected_panel",
        "annotation_topology": "group_rows",
        "record_input_sha256": RUNNER.file_sha256(records_path),
        "input_record_count": 200,
        "matched_record_count": 200,
        "requested_ids": ids,
        "selected_ids": ids,
        "selected_groups": [group],
        "target_items_by_group": {
            group: list(RUNNER.annotate_groups.GROUP_BY_NAME[group][0])
        },
        "selection_mode": GATE.GROUPED_SELECTION_MODE,
        "selected_id_count": 200,
        "selected_group_row_count": 200,
        "selected_ids_sha256": RUNNER.sha256_object(ids),
        "selected_group_rows_sha256": RUNNER.sha256_object(
            [[record_id, group] for record_id in ids]
        ),
    }
    plan["manifest_sha256"] = GATE._manifest_hash(plan)
    with pytest.raises(GATE.FullDevGateError, match="phase"):
        GATE._validate_cohort_plan(
            plan,
            records_path=records_path,
            organizer_ids=ids,
            topology=GATE.GROUPED_TOPOLOGY,
            context="selected panel",
        )


def test_real_v2_manifest_builder_round_trips_through_gate_header(tmp_path):
    records_path, _, records, _ = _write_inputs(tmp_path)
    ids = list(records)
    group = RUNNER.annotate_groups.GROUPS[0][0]
    selection = {
        "input_ids": ids,
        "matched_ids": ids,
        "requested_ids": ids,
        "selected_ids": ids,
    }
    plan = RUNNER.build_cohort_plan(
        input_path=records_path,
        selection=selection,
        selected_groups=[group],
        phase="official_dev",
    )
    stage = tmp_path / "stage"
    executable = tmp_path / "codex.exe"
    executable.write_bytes(b"synthetic codex executable")
    cli = {
        "requested_executable": "codex",
        "resolved_executable": str(executable.resolve()),
        "executable_sha256": RUNNER.file_sha256(executable),
        "version_output": "codex-test",
        "version_stderr_sha256": RUNNER.sha256_text(""),
    }
    profile = RUNNER.prompt_profiles.get_profile(
        "candidate-source-direct-v1", annotator_role="candidate"
    )
    fact_catalog = RUNNER.catalog_facts.CatalogIndex.load()
    qualification_catalog = (
        RUNNER.qualification_context.qualification_facts.CatalogReference.load()
    )
    manifest = RUNNER.build_run_manifest(
        input_path=records_path,
        staging_dir=stage,
        model="gpt-5.6-sol",
        reasoning_effort="xhigh",
        cli_provenance=cli,
        rubric=RUNNER.RUBRIC_PATH.read_text(encoding="utf-8"),
        fact_catalog=fact_catalog,
        qualification_catalog=qualification_catalog,
        full_source=True,
        annotator_role="candidate",
        prompt_profile=profile,
        phase="official_dev",
        cohort_plan=plan,
        model_identity=RUNNER.build_model_identity(model="gpt-5.6-sol"),
    )
    RUNNER.persist_run_manifest(stage, manifest)
    staging_dir, rows, identity = GATE._validate_run_manifest(
        manifest,
        manifest_path=stage / "run_manifest.json",
        records_path=records_path,
        organizer_ids=ids,
        topology=GATE.GROUPED_TOPOLOGY,
        context="round trip",
    )
    assert staging_dir == stage.resolve()
    assert rows == {(record_id, group) for record_id in ids}
    assert identity["tuple_sha256"] == manifest["tuple_sha256"]
    assert identity["prompt_profile"] == "candidate-source-direct-v1"

    record = records[ids[0]]
    prepared_facts = RUNNER.fact_context.prepare_fact_inputs(
        record, catalog_index=fact_catalog
    )
    task = RUNNER.prepare_group_task(
        record,
        group_name=group,
        rubric=RUNNER.RUBRIC_PATH.read_text(encoding="utf-8"),
        staging_dir=stage,
        run_manifest=manifest,
        prepared_facts=prepared_facts,
        fact_catalog=fact_catalog,
        prepared_qualification=None,
        qualification_catalog=qualification_catalog,
        full_source=True,
    )
    target_items = tuple(task["target_items"])
    raw = RUNNER.canonical_json(
        {
            "labels": "0" * len(target_items),
            "confidence": "H" * len(target_items),
            "evidence": {item: None for item in target_items},
        }
    )
    events = "".join(
        RUNNER.canonical_json(event) + "\n"
        for event in (
            {"type": "thread.started", "thread_id": "thread-v2-round-trip"},
            {"type": "turn.started"},
            {
                "type": "item.completed",
                "item": {"id": "answer", "type": "agent_message", "text": raw},
            },
            {"type": "turn.completed", "usage": {}},
        )
    )
    invocation = {
        "returncode": 0,
        "stdout": events,
        "stderr": "",
        "raw_final": raw,
        "timeout_error": None,
        "elapsed_seconds": 1.0,
    }
    receipt = RUNNER.validate_invocation_result(
        task, run_manifest=manifest, attempt=1, invocation=invocation
    )
    assert receipt["status"] == "ok", receipt.get("error")
    RUNNER.persist_attempt(task, attempt=1, invocation=invocation, result=receipt)
    artifacts = GATE._validate_task_manifest(
        task["manifest"],
        run_manifest=manifest,
        record=record,
        record_id=record["id"],
        group_name=group,
        target_items=target_items,
        task_dir=task["task_dir"],
        context="round-trip task",
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
        group_name=group,
        target_items=target_items,
    )
    assert {cell["label"] for cell in decisions.values()} == {0}

    unsafe_events = "".join(
        RUNNER.canonical_json(event) + "\n"
        for event in (
            {"type": "thread.started", "thread_id": "thread-v2-round-trip"},
            {"type": "turn.started"},
            {
                "type": "item.completed",
                "item": {"id": "tool", "type": "command_execution"},
            },
            {
                "type": "item.completed",
                "item": {"id": "answer", "type": "agent_message", "text": raw},
            },
            {"type": "turn.completed", "usage": {}},
        )
    )
    unsafe = json.loads(json.dumps(receipt))
    unsafe_audit = RUNNER._event_audit(unsafe_events)
    unsafe["raw_response_sha256"] = RUNNER.sha256_text(unsafe_events)
    unsafe["event_stream_sha256"] = RUNNER.sha256_text(unsafe_events)
    unsafe["event_audit"] = {
        key: unsafe_audit[key]
        for key in (
            "event_count",
            "parse_errors",
            "unsafe_items",
            "service_errors",
            "turn_completed",
        )
    }
    attempt_dir = task["task_dir"] / "attempts" / "attempt-001"
    (attempt_dir / "events.jsonl").write_text(unsafe_events, encoding="utf-8")
    (attempt_dir / "receipt.json").write_text(
        RUNNER.canonical_json(unsafe) + "\n", encoding="utf-8"
    )
    with pytest.raises(GATE.FullDevGateError, match="unsafe tool"):
        GATE._validate_receipt(
            unsafe,
            checkpoint_line=1,
            task_dir=task["task_dir"],
            task_manifest=task["manifest"],
            artifacts=artifacts,
            run_manifest=manifest,
            record=record,
            record_id=record["id"],
            group_name=group,
            target_items=target_items,
        )


def test_item_floor_and_macro_threshold_are_both_enforced(tmp_path):
    _, _, records, labels = _write_inputs(tmp_path)
    cells = {}
    for index, record_id in enumerate(records):
        for item in RUNNER.annotate_groups.ITEMS:
            predicted = labels[record_id][item]
            if item == "v1" and index < 120:
                predicted = 0
            cells[(record_id, item)] = {"label": predicted}
    score = GATE._score_cells(cells, labels)
    assert score["macro_positive_f1"] > GATE.MACRO_POSITIVE_F1_MIN
    assert score["by_item"]["v1"]["positive_f1"] < GATE.ITEM_POSITIVE_F1_MIN
    assert score["official_dev_role_gate_pass"] is False


def test_unsafe_event_is_a_hard_failure():
    events = "\n".join(
        RUNNER.canonical_json(value)
        for value in (
            {"type": "thread.started", "thread_id": "thread-1"},
            {"type": "turn.started"},
            {
                "type": "item.completed",
                "item": {"id": "tool-1", "type": "command_execution"},
            },
            {"type": "turn.completed", "usage": {}},
        )
    )
    audit = RUNNER._event_audit(events)
    assert audit["unsafe_items"]


@pytest.mark.parametrize(
    "mixed_field",
    (
        "tuple_sha256",
        "annotator_role",
        "prompt_profile",
        "prompt_lineage_sha256",
        "model_identity",
        "run_instance_sha256",
    ),
)
def test_partition_tuple_role_profile_and_model_mismatch_is_hard_failure(
    tmp_path, monkeypatch, mixed_field
):
    records_path, labels_path, records, _ = _write_inputs(tmp_path)
    ids = list(records)
    paths = [tmp_path / "run-1.json", tmp_path / "run-2.json"]
    stages = [
        _minimal_manifest(paths[0], instance="1" * 64),
        _minimal_manifest(
            paths[1],
            instance=("1" * 64 if mixed_field == "run_instance_sha256" else "6" * 64),
        ),
    ]
    checkpoints = [tmp_path / "rows-1.jsonl", tmp_path / "rows-2.jsonl"]
    halves = (ids[:100], ids[100:])
    for checkpoint, selected in zip(checkpoints, halves, strict=True):
        checkpoint.write_text(
            "".join(
                RUNNER.canonical_json(
                    {
                        "id": record_id,
                        "group": GATE.FULL_RECORD_GROUP,
                        "target_items": list(RUNNER.annotate_groups.ITEMS),
                    }
                )
                + "\n"
                for record_id in selected
            ),
            encoding="utf-8",
        )
    base_identity = {
        "tuple_sha256": "4" * 64,
        "annotator_role": "candidate",
        "prompt_profile": "candidate-source-direct-v1",
        "prompt_lineage_sha256": "5" * 64,
        "model_identity": {"family": "openai:gpt-5.6"},
    }

    def validate_manifest(manifest, *, manifest_path, **kwargs):
        index = paths.index(manifest_path)
        identity = json.loads(json.dumps(base_identity))
        if index == 1 and mixed_field != "run_instance_sha256":
            identity[mixed_field] = (
                {"family": "different"}
                if mixed_field == "model_identity"
                else "different"
            )
        rows = {(record_id, GATE.FULL_RECORD_GROUP) for record_id in halves[index]}
        return stages[index], rows, identity

    monkeypatch.setattr(GATE, "_validate_run_manifest", validate_manifest)
    monkeypatch.setattr(
        GATE.qualification_panel,
        "read_labels",
        lambda path: pytest.fail("labels opened before lineage failure"),
    )
    pattern = (
        "duplicate run instance"
        if mixed_field == "run_instance_sha256"
        else f"mixed {mixed_field}"
    )
    with pytest.raises(GATE.FullDevGateError, match=pattern):
        GATE.full_dev_score(
            records_path=records_path,
            labels_path=labels_path,
            run_manifest_path=paths,
            checkpoint_path=checkpoints,
            topology=GATE.FULL_RECORD_TOPOLOGY,
        )


def test_module_has_no_competition_runtime_or_prediction_dependency():
    source = pathlib.Path(GATE.__file__).read_text(encoding="utf-8").lower()
    forbidden = (
        "import " + "submission",
        "from " + "submission",
        "import pps",
        "from pps",
        "saved" + "_response",
        "development" + "_predictions",
    )
    assert not any(token in source for token in forbidden)
