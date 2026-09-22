from __future__ import annotations

import copy
import gzip
import hashlib
import json
import pathlib

import pytest

from tools.independent_gold import codex_cli_annotator as BASE
from tools.independent_gold import codex_full_record_annotator as RUNNER
from tools.independent_gold import full_run_receipt_verify as VERIFY


ROOT = pathlib.Path(__file__).resolve().parents[1]


def _record(index: int) -> dict:
    return {
        "id": f"FULL-VERIFY-{index:03d}",
        "docs": [
            {
                "doc_id": "D0",
                "type": "공고문",
                "text": (
                    f"검증용 입찰공고 {index}\n"
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


def _write_input(path: pathlib.Path, records: list[dict]) -> None:
    with gzip.open(path, "wt", encoding="utf-8", newline="\n") as handle:
        for record in records:
            handle.write(BASE.canonical_json(record) + "\n")


def _fake_cli(tmp_path: pathlib.Path) -> dict:
    executable = tmp_path / "codex.exe"
    executable.write_bytes(b"strict full-run verifier fixture")
    return {
        "requested_executable": "codex",
        "resolved_executable": str(executable.resolve()),
        "executable_sha256": BASE.file_sha256(executable),
        "version_output": "codex-test-full-run-verifier",
        "version_stderr_sha256": BASE.sha256_text(""),
    }


def _raw_output(task: dict) -> str:
    span_id = next(iter(task["full_record_context"]["allowed_span_registry"]))
    decisions = {
        item: {
            "label": 0,
            "confidence": "H",
            "rationale": "공급된 원문과 독립 규칙의 구성요건을 대조함",
            "premise_span_ids": [span_id],
            "exception_analysis": "적용 범위 및 예외를 별도로 확인함",
            "completeness": "sufficient",
            "material_missing_information": None,
            "positive_evidence_span_id": None,
        }
        for item in RUNNER.TARGET_ITEMS
    }
    return BASE.canonical_json({"source_span_ids": [span_id], "decisions": decisions})


def _invocation(raw: str, *, unsafe: bool = False) -> dict:
    events = [
        {"type": "thread.started", "thread_id": "thread-full-run-verifier"},
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


@pytest.fixture(scope="module")
def catalogs():
    return (
        BASE.catalog_facts.CatalogIndex.load(),
        BASE.qualification_context.qualification_facts.CatalogReference.load(),
    )


def _build_case(
    tmp_path: pathlib.Path,
    catalogs,
    *,
    count: int = 1,
    selected_count: int | None = None,
    role: str = "candidate",
    phase: str = VERIFY.EXPECTED_PHASE,
    unsafe: bool = False,
    organizer_input: pathlib.Path | None = None,
) -> dict:
    tmp_path.mkdir(parents=True, exist_ok=True)
    fact_catalog, qualification_catalog = catalogs
    records = [_record(index) for index in range(count)]
    records_path = organizer_input or tmp_path / "organizer.jsonl.gz"
    if organizer_input is None:
        _write_input(records_path, records)
    selected_records = records[: selected_count if selected_count is not None else count]
    ids = [record["id"] for record in records]
    selected_ids = [record["id"] for record in selected_records]
    requested_ids = selected_ids if len(selected_ids) != len(ids) else []
    selection = {
        "input_ids": ids,
        "matched_ids": selected_ids if requested_ids else ids,
        "requested_ids": requested_ids,
        "selected_ids": selected_ids,
        "partitioning": {
            "method": "organizer_order_stride_v1",
            "shard_index": 0,
            "shard_count": 1,
            "limit_after_sharding": None,
        },
    }
    plan = RUNNER.build_cohort_plan(
        input_path=records_path,
        selection=selection,
        phase=phase,
    )
    profile = RUNNER.prompt_profiles.get_profile(
        RUNNER.prompt_profiles.DEFAULT_PROFILE_BY_ROLE[role],
        annotator_role=role,
    )
    model = RUNNER.ROLE_MODEL[role]
    run_dir = (tmp_path / f"run-{role}").resolve()
    manifest = RUNNER.build_run_manifest(
        input_path=records_path,
        staging_dir=run_dir,
        model=model,
        reasoning_effort="xhigh",
        cli_provenance=_fake_cli(tmp_path),
        rubric=RUNNER.RUBRIC_PATH.read_text(encoding="utf-8"),
        fact_catalog=fact_catalog,
        qualification_catalog=qualification_catalog,
        annotator_role=role,
        prompt_profile=profile,
        phase=phase,
        cohort_plan=plan,
        model_identity=BASE.build_model_identity(model=model),
    )
    RUNNER.persist_run_manifest(run_dir, manifest)
    receipts: dict[str, dict] = {}
    for record in selected_records:
        task = RUNNER.prepare_full_record_task(
            record,
            rubric=RUNNER.RUBRIC_PATH.read_text(encoding="utf-8"),
            staging_dir=run_dir,
            run_manifest=manifest,
            fact_catalog=fact_catalog,
            qualification_catalog=qualification_catalog,
        )
        raw = _raw_output(task)
        invocation = _invocation(raw, unsafe=unsafe)
        receipt = RUNNER.validate_invocation_result(
            task,
            run_manifest=manifest,
            attempt=1,
            invocation=invocation,
        )
        if unsafe:
            # A maliciously relabeled checkpoint still carries the audited
            # unsafe event and must never enter the review ledger.
            receipt["status"] = "ok"
            receipt["error"] = None
            receipt["validation_errors"] = []
        RUNNER.base.persist_attempt(
            task,
            attempt=1,
            invocation=invocation,
            result=receipt,
        )
        RUNNER.base.append_checkpoint(run_dir / VERIFY.CHECKPOINT_NAME, receipt)
        receipts[record["id"]] = receipt
    return {
        "records": {record["id"]: record for record in records},
        "records_path": records_path,
        "run_dir": run_dir,
        "receipts": receipts,
    }


def _verify(case: dict, **overrides):
    args = {
        "run_dirs": [case["run_dir"]],
        "role": "candidate",
        "organizer_input": case["records_path"],
        "records": case["records"],
        "policy_frozen_utc": "2000-01-01T00:00:00+00:00",
    }
    args.update(overrides)
    return VERIFY.verify_role_runs(**args)


def _frozen_archive(case: dict, output: pathlib.Path) -> pathlib.Path:
    manifest = json.loads(
        (case["run_dir"] / "run_manifest.json").read_text(encoding="utf-8")
    )
    bundle = manifest["imported_source_bundle"]
    for entry in bundle["files"]:
        source = ROOT / entry["path"]
        target = output / entry["path"]
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(source.read_bytes())
    rubric_relative = "tools/independent_gold/rubric_v1.md"
    rubric_target = output / rubric_relative
    rubric_target.parent.mkdir(parents=True, exist_ok=True)
    rubric_target.write_bytes(RUNNER.RUBRIC_PATH.read_bytes())
    archive_manifest = {
        "schema_version": VERIFY.SOURCE_ARCHIVE_SCHEMA_VERSION,
        "archive_kind": "mechanical_source_bytes_only_no_model_call",
        "bundle_sha256": bundle["bundle_sha256"],
        "files": bundle["files"],
        "rubric_sha256": hashlib.sha256(rubric_target.read_bytes()).hexdigest(),
        "run_manifest_sha256": manifest["manifest_sha256"],
    }
    (output / "manifest.json").write_text(
        BASE.canonical_json(archive_manifest) + "\n", encoding="utf-8"
    )
    return output


def test_verified_index_keeps_offsets_and_reloads_receipts(tmp_path, catalogs):
    case = _build_case(tmp_path, catalogs, count=2)
    index = _verify(case)

    assert set(index.locations) == set(case["records"])
    assert all(type(location[1]) is int for location in index.locations.values())
    assert index.load("FULL-VERIFY-001") == case["receipts"]["FULL-VERIFY-001"]
    assert index.descriptors[0]["checkpoint_rows"] == 2
    assert index.descriptors[0]["phase"] == "unlabeled_20000"
    json.dumps(index.descriptors, ensure_ascii=False)


def test_blank_or_duplicate_key_checkpoint_rows_are_rejected(tmp_path, catalogs):
    blank = _build_case(tmp_path / "blank", catalogs)
    with (blank["run_dir"] / VERIFY.CHECKPOINT_NAME).open("ab") as handle:
        handle.write(b"\n")
    with pytest.raises(VERIFY.FullRunReceiptError, match="blank row"):
        _verify(blank)

    duplicate = _build_case(tmp_path / "duplicate", catalogs)
    checkpoint = duplicate["run_dir"] / VERIFY.CHECKPOINT_NAME
    original = checkpoint.read_text(encoding="utf-8")
    record_id = next(iter(duplicate["records"]))
    checkpoint.write_text(
        "{\"id\":" + json.dumps(record_id) + "," + original[1:],
        encoding="utf-8",
        newline="\n",
    )
    with pytest.raises(VERIFY.FullRunReceiptError, match="duplicate JSON object key"):
        _verify(duplicate)


def test_context_and_raw_attempt_tampering_are_rejected(tmp_path, catalogs):
    context_case = _build_case(tmp_path / "context", catalogs)
    record_id = next(iter(context_case["records"]))
    task_dir = (
        context_case["run_dir"]
        / "tasks"
        / BASE._task_slug(record_id)
        / RUNNER.GROUP_NAME
    )
    context_path = task_dir / "full_record_context.json"
    context_path.write_bytes(context_path.read_bytes() + b" ")
    with pytest.raises(VERIFY.FullRunReceiptError, match="source context bytes"):
        _verify(context_case)

    raw_case = _build_case(tmp_path / "raw", catalogs)
    raw_id = next(iter(raw_case["records"]))
    raw_path = (
        raw_case["run_dir"]
        / "tasks"
        / BASE._task_slug(raw_id)
        / RUNNER.GROUP_NAME
        / "attempts"
        / "attempt-001"
        / "raw_final.json"
    )
    raw_path.write_bytes(raw_path.read_bytes() + b" ")
    with pytest.raises(VERIFY.FullRunReceiptError, match="content_sha256 mismatch"):
        _verify(raw_case)


def test_unsafe_events_are_rejected_even_if_checkpoint_claims_success(tmp_path, catalogs):
    case = _build_case(tmp_path, catalogs, unsafe=True)
    with pytest.raises(VERIFY.FullRunReceiptError, match="unsafe tool/nonmessage event"):
        _verify(case)


def test_canonical_ledger_is_replayed_from_raw_output(tmp_path, catalogs):
    case = _build_case(tmp_path, catalogs)
    record_id = next(iter(case["records"]))
    tampered = copy.deepcopy(case["receipts"][record_id])
    tampered["ledger"]["cells"][0]["label"] = 1
    task_dir = case["run_dir"] / "tasks" / BASE._task_slug(record_id) / RUNNER.GROUP_NAME
    persisted = task_dir / "attempts" / "attempt-001" / "receipt.json"
    persisted.write_text(BASE.canonical_json(tampered) + "\n", encoding="utf-8", newline="\n")
    checkpoint = case["run_dir"] / VERIFY.CHECKPOINT_NAME
    checkpoint.write_text(BASE.canonical_json(tampered) + "\n", encoding="utf-8", newline="\n")
    with pytest.raises(VERIFY.FullRunReceiptError, match="ledger replay mismatch"):
        _verify(case)


def test_role_phase_policy_freeze_and_complete_union_are_hard_boundaries(tmp_path, catalogs):
    valid = _build_case(tmp_path / "valid", catalogs)
    with pytest.raises(VERIFY.FullRunReceiptError, match="annotator role mismatch"):
        _verify(valid, role="verifier")
    with pytest.raises(VERIFY.FullRunReceiptError, match="completed before audit-policy freeze"):
        _verify(valid, policy_frozen_utc="2999-01-01T00:00:00+00:00")

    wrong_phase = _build_case(
        tmp_path / "phase", catalogs, phase="selected_panel"
    )
    with pytest.raises(VERIFY.FullRunReceiptError, match="phase mismatch"):
        _verify(wrong_phase)

    incomplete = _build_case(tmp_path / "coverage", catalogs, count=2, selected_count=1)
    with pytest.raises(VERIFY.FullRunReceiptError, match="selected run cohorts"):
        _verify(incomplete)


def test_load_detects_checkpoint_mutation_after_verification(tmp_path, catalogs):
    case = _build_case(tmp_path, catalogs)
    index = _verify(case)
    checkpoint = case["run_dir"] / VERIFY.CHECKPOINT_NAME
    raw = checkpoint.read_bytes()
    checkpoint.write_bytes(raw.replace(b'"status":"ok"', b'"status":"xx"', 1))
    with pytest.raises(VERIFY.FullRunReceiptError, match="changed after verification"):
        index.load(next(iter(case["records"])))


def test_verified_frozen_archive_matches_run_and_keeps_full_replay(tmp_path, catalogs):
    case = _build_case(tmp_path / "case", catalogs)
    archive = _frozen_archive(case, tmp_path / "archive")
    index = _verify(case, frozen_source_archive=archive)
    assert index.load(next(iter(case["records"]))) == next(iter(case["receipts"].values()))
    assert index.descriptors[0]["frozen_source_archive"]["path"] == str(archive.resolve())
    assert index.descriptors[0]["frozen_source_archive"]["bundle_sha256"] == json.loads(
        (archive / "manifest.json").read_text(encoding="utf-8")
    )["bundle_sha256"]

    record_id = next(iter(case["records"]))
    context_path = (
        case["run_dir"] / "tasks" / BASE._task_slug(record_id)
        / RUNNER.GROUP_NAME / "full_record_context.json"
    )
    context_path.write_bytes(context_path.read_bytes() + b" ")
    with pytest.raises(VERIFY.FullRunReceiptError, match="source context bytes"):
        _verify(case, frozen_source_archive=archive)


def test_frozen_archive_accepts_only_qualification_source_drift(
    tmp_path, catalogs, monkeypatch
):
    case = _build_case(tmp_path / "case", catalogs)
    archive = _frozen_archive(case, tmp_path / "archive")
    original = RUNNER.build_imported_source_bundle

    def changed(path_to_change):
        def rebuilt(profile):
            value = copy.deepcopy(original(profile))
            for entry in value["files"]:
                if entry["path"] == path_to_change:
                    entry["sha256"] = "0" * 64
            value["bundle_sha256"] = BASE.sha256_object(
                {"schema_version": value["schema_version"], "files": value["files"]}
            )
            return value

        return rebuilt

    monkeypatch.setattr(
        RUNNER,
        "build_imported_source_bundle",
        changed("tools/independent_gold/qualification_context.py"),
    )
    with pytest.raises(VERIFY.FullRunReceiptError, match="imported source bundle mismatch"):
        _verify(case)
    assert _verify(case, frozen_source_archive=archive).descriptors

    monkeypatch.setattr(
        RUNNER,
        "build_imported_source_bundle",
        changed("tools/independent_gold/fact_context.py"),
    )
    with pytest.raises(VERIFY.FullRunReceiptError, match="unsupported frozen/current source drift"):
        _verify(case, frozen_source_archive=archive)


def test_frozen_archive_rejects_byte_tamper_missing_file_and_false_anchor(
    tmp_path, catalogs
):
    case = _build_case(tmp_path / "case", catalogs)
    archive = _frozen_archive(case, tmp_path / "archive")
    source_file = archive / "tools/independent_gold/qualification_context.py"
    original = source_file.read_bytes()
    source_file.write_bytes(original + b" ")
    with pytest.raises(VERIFY.FullRunReceiptError, match="byte SHA-256 mismatch"):
        _verify(case, frozen_source_archive=archive)
    source_file.write_bytes(original)
    source_file.unlink()
    with pytest.raises(VERIFY.FullRunReceiptError, match="missing or symlinked"):
        _verify(case, frozen_source_archive=archive)
    source_file.write_bytes(original)
    manifest_path = archive / "manifest.json"
    value = json.loads(manifest_path.read_text(encoding="utf-8"))
    value["run_manifest_sha256"] = "f" * 64
    manifest_path.write_text(BASE.canonical_json(value) + "\n", encoding="utf-8")
    with pytest.raises(VERIFY.FullRunReceiptError, match="anchor manifest"):
        _verify(case, frozen_source_archive=archive)


def test_frozen_archive_rejects_path_escape(tmp_path, catalogs):
    case = _build_case(tmp_path / "case", catalogs)
    archive = _frozen_archive(case, tmp_path / "archive")
    manifest_path = archive / "manifest.json"
    value = json.loads(manifest_path.read_text(encoding="utf-8"))
    value["files"][0]["path"] = "tools/independent_gold/../../escape.py"
    manifest_path.write_text(BASE.canonical_json(value) + "\n", encoding="utf-8")
    with pytest.raises(VERIFY.FullRunReceiptError, match="unsafe path"):
        _verify(case, frozen_source_archive=archive)
