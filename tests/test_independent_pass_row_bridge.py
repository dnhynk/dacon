"""Zero-model-call checks for the independent provisional-pass adapter."""

from __future__ import annotations

import gzip
import json
import pathlib
import copy
import argparse
import shutil

import pytest

from tests.test_independent_full_run_receipt_verify import _build_case
from tests.test_independent_claude_full_record_annotator import record as claude_record
from tests.test_independent_claude_full_record_annotator import envelope as claude_envelope
from tools.independent_gold import codex_cli_annotator as codex_base
from tools.independent_gold import codex_full_record_annotator as codex_runner
from tools.independent_gold import claude_full_record_annotator as claude_runner
from tools.independent_gold import pass_row_bridge as bridge


def _archive_for_run(tmp_path: pathlib.Path, run_dir: pathlib.Path, *, runner: str,
                     anchor_run: bool = False) -> pathlib.Path:
    """Copy only synthetic fixture run's currently hashed source bytes."""
    manifest = json.loads((run_dir / "run_manifest.json").read_text(encoding="utf-8"))
    archive = tmp_path / "frozen_source"
    archive.mkdir()
    source_bundle = manifest["imported_source_bundle" if runner == "codex" else "source_bundle"]
    entries = list(source_bundle["files"])
    for entry in entries:
        source = codex_runner.ROOT / entry["path"]
        target = archive / entry["path"]
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        assert bridge._file_sha(target) == entry["sha256"]
    rubric = archive / "tools" / "independent_gold" / "rubric_v1.md"
    rubric.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(codex_runner.RUBRIC_PATH, rubric)
    source_archive_manifest = {
        "schema_version": "dacon.independent.source_archive.v1",
        "bundle_sha256": source_bundle["bundle_sha256"],
        "files": entries,
        "rubric_sha256": bridge._file_sha(rubric),
        "run_manifest_sha256": manifest["manifest_sha256"] if runner == "codex" or anchor_run else None,
        "archive_kind": "mechanical_source_bytes_only_no_model_call",
    }
    (archive / "manifest.json").write_text(
        bridge._canonical(source_archive_manifest) + "\n", encoding="utf-8", newline="\n"
    )
    return archive


@pytest.fixture(scope="module")
def catalogs():
    return (
        codex_base.catalog_facts.CatalogIndex.load(),
        codex_base.qualification_context.qualification_facts.CatalogReference.load(),
    )


def _bridge(case: dict, output: pathlib.Path, *, complete: bool = True) -> dict:
    return bridge.bridge_runs(
        records_path=case["records_path"],
        run_dirs=[case["run_dir"]],
        runner_kind="codex",
        role="candidate",
        output_path=output,
        require_complete=complete,
        require_official_input=False,
    )


def _attempt_dir(case: dict) -> pathlib.Path:
    record_id = next(iter(case["records"]))
    return (
        case["run_dir"]
        / "tasks"
        / codex_base._task_slug(record_id)
        / codex_runner.GROUP_NAME
        / "attempts"
        / "attempt-001"
    )


def _task_dir(case: dict) -> pathlib.Path:
    return _attempt_dir(case).parent.parent


def _build_claude_case(tmp_path: pathlib.Path, monkeypatch, *, recovery_subset: bool = False) -> dict:
    source_record = claude_record()
    source_record["docs"].append({"doc_id": "D1", "type": "첨부", "text": "별도 첨부의 실제 근거다.\n"})
    source = (tmp_path / "data_open" / "train_unlabeled.jsonl.gz") if recovery_subset else tmp_path / "organizer.jsonl.gz"
    source.parent.mkdir(parents=True, exist_ok=True)
    records = [source_record]
    if recovery_subset:
        first_record = copy.deepcopy(source_record)
        first_record["id"] = "SYNTHETIC-PREFIX-RECORD"
        records.insert(0, first_record)
    with gzip.open(source, "wt", encoding="utf-8", newline="\n") as handle:
        for value in records:
            handle.write(claude_runner.canonical_json(value) + "\n")
    executable = tmp_path / "claude.exe"
    executable.write_bytes(b"synthetic Claude CLI provenance")
    monkeypatch.setattr(claude_runner, "_allowed_input", lambda path: path.resolve())
    monkeypatch.setattr(claude_runner, "resolve_cli", lambda requested: {
        "requested_executable": requested,
        "resolved_executable": str(executable.resolve()),
        "executable_sha256": claude_runner.file_sha256(executable),
        "version_output": "2.1.276 (Claude Code)",
        "version_stderr_sha256": claude_runner.sha256_text(""),
    })
    wire = claude_envelope()
    # The source-bound declaration-only normalization may add an already
    # referenced real span, but it must never change a decision.
    first_item = claude_runner.ITEMS[0]
    wire["structured_output"]["decisions"][first_item]["premise_span_ids"] = ["SRC-D0001-S000000"]
    raw = claude_runner.canonical_json(wire).encode("utf-8")
    monkeypatch.setattr(claude_runner, "_invoke", lambda **kwargs: (0, raw, b"", None))
    output_dir = tmp_path / ("recovery_claude_synthetic" if recovery_subset else "claude_run")
    stats = claude_runner.run(argparse.Namespace(
        input=source, output_dir=output_dir, claude_bin="claude",
        phase="unlabeled_20000" if recovery_subset else "development_diagnostic",
        record_id=[source_record["id"]] if recovery_subset else None,
        shard_index=0, shard_count=1, limit=None,
        timeout=30.0, max_budget_usd=2.0, context_mode="compact", execute=True,
        continue_on_content_error=False,
    ))
    assert stats["tasks_ok"] == 1
    return {"records_path": source, "run_dir": output_dir, "raw": raw,
            "record": source_record, "records": records}


def test_codex_bridge_replays_raw_artifacts_and_emits_full_provisional_rows(tmp_path, catalogs):
    case = _build_case(tmp_path, catalogs, count=2)
    output = tmp_path / "candidate_pass.jsonl"

    result = _bridge(case, output)

    assert result["status"] == "provisional_pass_not_gold"
    assert result["model_calls"] == 0
    assert result["pass_records"] == 2
    rows = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]
    assert [row["record_id"] for row in rows] == list(case["records"])
    for row in rows:
        receipt = case["receipts"][row["record_id"]]
        assert row["schema_version"] == bridge.provisional_release.PASS_ROW_ROLE_SCHEMA
        assert row["source_sha256"] == codex_base.sha256_object(case["records"][row["record_id"]])
        assert row["annotator_role"] == "candidate"
        assert row["release_pass_role"] == "candidate"
        assert row["role_provenance"]["raw_runner_role"] == "candidate"
        assert row["role_provenance"]["release_pass_role"] == "candidate"
        assert row["lineage"]["provider"] == "openai"
        assert row["lineage"]["model_family"].startswith("openai:")
        assert row["lineage"]["prompt_sha256"] == receipt["prompt_sha256"]
        assert row["lineage"]["receipt_sha256"] == codex_base.sha256_object(receipt)
        assert row["lineage"]["peer_answer_visible"] is False
        assert row["lineage"]["production_output_visible"] is False
        assert row["ledger"] == receipt["ledger"]
        assert len(row["ledger"]["cells"]) == 24


def test_codex_bridge_supports_independent_verifier_role(tmp_path, catalogs):
    case = _build_case(tmp_path, catalogs, role="verifier")
    output = tmp_path / "verifier_pass.jsonl"
    result = bridge.bridge_runs(
        records_path=case["records_path"],
        run_dirs=[case["run_dir"]],
        runner_kind="codex",
        role="verifier",
        output_path=output,
        require_complete=True,
        require_official_input=False,
    )
    row = json.loads(output.read_text(encoding="utf-8"))
    assert result["annotator_role"] == row["annotator_role"] == row["release_pass_role"] == "verifier"
    assert row["lineage"]["model_family"].startswith("openai:")


def test_codex_raw_candidate_can_be_release_secondary_without_rewriting_prompt(tmp_path, catalogs):
    case = _build_case(tmp_path, catalogs, role="candidate")
    archive = _archive_for_run(tmp_path, case["run_dir"], runner="codex")
    output = tmp_path / "sol_release_secondary.jsonl"
    result = bridge.bridge_runs(
        records_path=case["records_path"], run_dirs=[case["run_dir"]],
        runner_kind="codex", role="verifier", output_path=output,
        source_archives=[archive], require_official_input=False,
    )
    row = json.loads(output.read_text(encoding="utf-8"))
    raw_manifest = json.loads((case["run_dir"] / "run_manifest.json").read_text(encoding="utf-8"))
    assert row["annotator_role"] == "candidate"
    assert row["release_pass_role"] == row["role_provenance"]["release_pass_role"] == "verifier"
    assert row["role_provenance"]["raw_runner_role"] == "candidate"
    assert row["role_provenance"]["raw_prompt_lineage"] == raw_manifest["prompt_lineage"]
    assert row["role_provenance"]["raw_run_manifest_sha256"] == raw_manifest["manifest_sha256"]
    assert row["lineage"]["prompt_sha256"] == case["receipts"][row["record_id"]]["prompt_sha256"]
    provenance = json.loads(pathlib.Path(result["provenance_output"]).read_text(encoding="utf-8"))
    assert provenance["runs"][0]["raw_runner_role"] == "candidate"
    assert provenance["runs"][0]["release_pass_role"] == "verifier"

    from tests.test_independent_provisional_release import role_bound_row, write_jsonl

    primary = tmp_path / "synthetic_claude_primary.jsonl"
    write_jsonl(primary, [
        role_bound_row(case["records"][row["record_id"]], runner="claude", release_role="candidate")
    ])
    release_dir = tmp_path / "sol_second_release"
    summary = bridge.provisional_release.build_release(
        records_path=case["records_path"], primary_path=primary,
        secondary_path=output, output_dir=release_dir, expected_count=1,
    )
    assert summary["primary_records"] == summary["secondary_records"] == 1
    assert summary["gold_cells"] == 0


def test_codex_bridge_rejects_self_consistent_false_checkpoint_and_receipt(tmp_path, catalogs):
    case = _build_case(tmp_path, catalogs)
    record_id = next(iter(case["records"]))
    forged = copy.deepcopy(case["receipts"][record_id])
    forged["ledger"]["cells"][0]["label"] = 1
    receipt_path = _attempt_dir(case) / "receipt.json"
    checkpoint = case["run_dir"] / "full_records.jsonl"
    wire = codex_base.canonical_json(forged) + "\n"
    receipt_path.write_text(wire, encoding="utf-8", newline="\n")
    checkpoint.write_text(wire, encoding="utf-8", newline="\n")
    output = tmp_path / "must_not_publish.jsonl"
    with pytest.raises(bridge.PassRowBridgeError, match="ledger replay mismatch"):
        _bridge(case, output)
    assert not output.exists()


def test_codex_bridge_rejects_unsafe_event_even_if_receipt_claims_success(tmp_path, catalogs):
    case = _build_case(tmp_path, catalogs, unsafe=True)
    output = tmp_path / "must_not_publish.jsonl"
    with pytest.raises(bridge.PassRowBridgeError, match="unsafe tool/nonmessage event"):
        _bridge(case, output)
    assert not output.exists()


@pytest.mark.parametrize(
    "tamper",
    ["raw_final", "prompt", "checkpoint", "run_manifest", "organizer_source", "missing_receipt"],
)
def test_codex_bridge_refuses_stale_or_tampered_lineage(tmp_path, catalogs, tamper):
    case = _build_case(tmp_path, catalogs)
    output = tmp_path / "must_not_publish.jsonl"
    if tamper == "raw_final":
        path = _attempt_dir(case) / "raw_final.json"
        path.write_bytes(path.read_bytes() + b" ")
    elif tamper == "prompt":
        path = _task_dir(case) / "prompt.txt"
        path.write_bytes(path.read_bytes() + b" ")
    elif tamper == "checkpoint":
        path = case["run_dir"] / "full_records.jsonl"
        row = json.loads(path.read_text(encoding="utf-8"))
        row["ledger"]["cells"][0]["label"] = 1
        path.write_text(codex_base.canonical_json(row) + "\n", encoding="utf-8", newline="\n")
    elif tamper == "run_manifest":
        path = case["run_dir"] / "run_manifest.json"
        manifest = json.loads(path.read_text(encoding="utf-8"))
        manifest["annotator_role"] = "verifier"
        path.write_text(codex_base.canonical_json(manifest) + "\n", encoding="utf-8", newline="\n")
    elif tamper == "organizer_source":
        with gzip.open(case["records_path"], "rt", encoding="utf-8") as handle:
            record = json.loads(handle.readline())
        record["docs"][0]["text"] += "변경"
        with gzip.open(case["records_path"], "wt", encoding="utf-8", newline="\n") as handle:
            handle.write(codex_base.canonical_json(record) + "\n")
    elif tamper == "missing_receipt":
        (_attempt_dir(case) / "receipt.json").unlink()

    with pytest.raises(bridge.PassRowBridgeError):
        _bridge(case, output)
    assert not output.exists()


def test_codex_bridge_refuses_incomplete_or_repeated_cohorts(tmp_path, catalogs):
    case = _build_case(tmp_path, catalogs, count=2, selected_count=1)
    output = tmp_path / "incomplete.jsonl"
    with pytest.raises(bridge.PassRowBridgeError, match="incomplete"):
        _bridge(case, output)
    assert not output.exists()

    repeated = tmp_path / "repeated.jsonl"
    with pytest.raises(bridge.PassRowBridgeError, match="duplicated"):
        bridge.bridge_runs(
            records_path=case["records_path"],
            run_dirs=[case["run_dir"], case["run_dir"]],
            runner_kind="codex",
            role="candidate",
            output_path=repeated,
            require_official_input=False,
        )
    assert not repeated.exists()


def test_codex_bridge_will_not_overwrite_existing_output(tmp_path, catalogs):
    case = _build_case(tmp_path, catalogs)
    output = tmp_path / "reserved.jsonl"
    output.write_bytes(b"user owned\n")
    with pytest.raises(bridge.PassRowBridgeError, match="already exists"):
        _bridge(case, output)
    assert output.read_bytes() == b"user owned\n"


def test_codex_archived_bridge_rebuilds_task_but_not_run_manifest(tmp_path, catalogs, monkeypatch):
    case = _build_case(tmp_path, catalogs, count=2)
    archive = _archive_for_run(tmp_path, case["run_dir"], runner="codex")
    output = tmp_path / "historical_candidate_pass.jsonl"
    verified_tasks = []
    original_verify_task = bridge.codex_verify._verify_task

    def current_manifest_must_not_run(*_args, **_kwargs):
        raise AssertionError("a historical run manifest must use its archived hash chain")

    def count_task_rebuild(*args, **kwargs):
        verified_tasks.append(kwargs["record"]["id"])
        return original_verify_task(*args, **kwargs)

    monkeypatch.setattr(codex_runner, "build_run_manifest", current_manifest_must_not_run)
    monkeypatch.setattr(bridge.codex_verify, "_verify_task", count_task_rebuild)
    result = bridge.bridge_runs(
        records_path=case["records_path"], run_dirs=[case["run_dir"]],
        runner_kind="codex", role="candidate", output_path=output,
        source_archives=[archive], require_official_input=False,
    )
    provenance = json.loads(pathlib.Path(result["provenance_output"]).read_text(encoding="utf-8"))
    assert result["pass_records"] == 2
    assert len(verified_tasks) == 2
    assert result["model_calls"] == 0
    assert provenance["status"] == "provisional_pass_not_gold"
    assert provenance["gold_qualification"] is False
    assert provenance["runs"][0]["source_mode"] == "archived_original_bytes"
    assert provenance["runs"][0]["source_archive"]["archive_binding"] == "exact_run"


def test_archived_bridge_allows_only_qualification_context_live_drift(tmp_path, catalogs, monkeypatch):
    case = _build_case(tmp_path, catalogs)
    archive = _archive_for_run(tmp_path, case["run_dir"], runner="codex")
    qualification_file = (codex_runner.ROOT / "tools" / "independent_gold" / "qualification_context.py").resolve()
    original_file_sha = bridge._file_sha

    def qualification_drift(path):
        if pathlib.Path(path).resolve() == qualification_file:
            return "f" * 64
        return original_file_sha(path)

    monkeypatch.setattr(bridge, "_file_sha", qualification_drift)
    output = tmp_path / "qualification_compatible_pass.jsonl"
    result = bridge.bridge_runs(
        records_path=case["records_path"], run_dirs=[case["run_dir"]],
        runner_kind="codex", role="candidate", output_path=output,
        source_archives=[archive], require_official_input=False,
    )
    provenance = json.loads(pathlib.Path(result["provenance_output"]).read_text(encoding="utf-8"))
    assert provenance["runs"][0]["source_archive"]["live_source_drift_allowed"] == [
        "tools/independent_gold/qualification_context.py"
    ]


def test_archived_bridge_rejects_other_live_source_drift(tmp_path, catalogs, monkeypatch):
    case = _build_case(tmp_path, catalogs)
    archive = _archive_for_run(tmp_path, case["run_dir"], runner="codex")
    run_manifest = json.loads((case["run_dir"] / "run_manifest.json").read_text(encoding="utf-8"))
    other_relative = next(
        entry["path"] for entry in run_manifest["imported_source_bundle"]["files"]
        if entry["path"] != "tools/independent_gold/qualification_context.py"
    )
    other_file = (codex_runner.ROOT / other_relative).resolve()
    original_file_sha = bridge._file_sha

    def other_drift(path):
        if pathlib.Path(path).resolve() == other_file:
            return "f" * 64
        return original_file_sha(path)

    monkeypatch.setattr(bridge, "_file_sha", other_drift)
    output = tmp_path / "must_not_publish.jsonl"
    with pytest.raises(bridge.PassRowBridgeError, match="outside qualification-context opt-in"):
        bridge.bridge_runs(
            records_path=case["records_path"], run_dirs=[case["run_dir"]],
            runner_kind="codex", role="candidate", output_path=output,
            source_archives=[archive], require_official_input=False,
        )
    assert not output.exists()


def test_codex_partial_archived_bridge_exports_only_verified_success(tmp_path, catalogs):
    case = _build_case(tmp_path, catalogs, count=2)
    archive = _archive_for_run(tmp_path, case["run_dir"], runner="codex")
    checkpoint = case["run_dir"] / "full_records.jsonl"
    complete_rows = checkpoint.read_bytes().splitlines(keepends=True)
    assert len(complete_rows) == 2
    checkpoint.write_bytes(complete_rows[0] + b'{"id":"unfinished"')
    output = tmp_path / "partial_candidate_pass.jsonl"

    result = bridge.bridge_runs(
        records_path=case["records_path"], run_dirs=[case["run_dir"]],
        runner_kind="codex", role="candidate", output_path=output,
        source_archives=[archive], allow_partial=True, require_official_input=False,
    )

    rows = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == result["pass_records"] == 1
    assert rows[0]["record_id"] == json.loads(complete_rows[0])["id"]
    provenance = json.loads(pathlib.Path(result["provenance_output"]).read_text(encoding="utf-8"))
    assert provenance["status"] == "partial_provisional_not_gold"
    assert provenance["runs"][0]["missing_selected_records"] == 1
    assert provenance["runs"][0]["ignored_truncated_checkpoint_tail"] is True
    assert provenance["inference_origin"] == "historical_runner_receipts_zero_new_model_calls"


def test_archived_bridge_rejects_wrong_or_mutated_source_bundle(tmp_path, catalogs):
    case = _build_case(tmp_path, catalogs)
    archive = _archive_for_run(tmp_path, case["run_dir"], runner="codex")
    archived_file = archive / json.loads((archive / "manifest.json").read_text())["files"][0]["path"]
    archived_file.write_bytes(archived_file.read_bytes() + b" ")
    output = tmp_path / "must_not_publish.jsonl"
    with pytest.raises(bridge.PassRowBridgeError, match="source archive byte hash differs"):
        bridge.bridge_runs(
            records_path=case["records_path"], run_dirs=[case["run_dir"]],
            runner_kind="codex", role="candidate", output_path=output,
            source_archives=[archive], allow_partial=True, require_official_input=False,
        )
    assert not output.exists()
    assert not pathlib.Path(str(output) + ".provenance.json").exists()


def test_partial_bridge_requires_frozen_source_archive(tmp_path, catalogs):
    case = _build_case(tmp_path, catalogs)
    output = tmp_path / "must_not_publish.jsonl"
    with pytest.raises(bridge.PassRowBridgeError, match="requires original source archives"):
        bridge.bridge_runs(
            records_path=case["records_path"], run_dirs=[case["run_dir"]],
            runner_kind="codex", role="candidate", output_path=output,
            allow_partial=True, require_official_input=False,
        )
    assert not output.exists()


def test_partial_bridge_rejects_archive_from_other_source_version(tmp_path, catalogs):
    case = _build_case(tmp_path, catalogs)
    archive = _archive_for_run(tmp_path, case["run_dir"], runner="codex")
    archive_manifest = json.loads((archive / "manifest.json").read_text(encoding="utf-8"))
    archive_manifest["bundle_sha256"] = "0" * 64
    (archive / "manifest.json").write_text(
        bridge._canonical(archive_manifest) + "\n", encoding="utf-8", newline="\n"
    )
    output = tmp_path / "must_not_publish.jsonl"
    with pytest.raises(bridge.PassRowBridgeError, match="source bundle hash differs"):
        bridge.bridge_runs(
            records_path=case["records_path"], run_dirs=[case["run_dir"]],
            runner_kind="codex", role="candidate", output_path=output,
            source_archives=[archive], allow_partial=True, require_official_input=False,
        )
    assert not output.exists()


def test_partial_bridge_still_replays_each_successful_raw_attempt(tmp_path, catalogs):
    case = _build_case(tmp_path, catalogs, count=2)
    archive = _archive_for_run(tmp_path, case["run_dir"], runner="codex")
    checkpoint = case["run_dir"] / "full_records.jsonl"
    checkpoint.write_bytes(checkpoint.read_bytes().splitlines(keepends=True)[0])
    record_id = json.loads(checkpoint.read_text(encoding="utf-8"))["id"]
    task = case["run_dir"] / "tasks" / codex_base._task_slug(record_id) / "v1-24"
    raw = task / "attempts" / "attempt-001" / "raw_final.json"
    raw.write_bytes(raw.read_bytes() + b" ")
    output = tmp_path / "must_not_publish.jsonl"
    with pytest.raises(bridge.PassRowBridgeError):
        bridge.bridge_runs(
            records_path=case["records_path"], run_dirs=[case["run_dir"]],
            runner_kind="codex", role="candidate", output_path=output,
            source_archives=[archive], allow_partial=True, require_official_input=False,
        )
    assert not output.exists()
    assert not pathlib.Path(str(output) + ".provenance.json").exists()


def test_partial_bridge_will_not_replace_existing_provenance(tmp_path, catalogs):
    case = _build_case(tmp_path, catalogs)
    archive = _archive_for_run(tmp_path, case["run_dir"], runner="codex")
    output = tmp_path / "candidate.jsonl"
    provenance = pathlib.Path(str(output) + ".provenance.json")
    provenance.write_bytes(b"user owned\n")
    with pytest.raises(bridge.PassRowBridgeError, match="already exists"):
        bridge.bridge_runs(
            records_path=case["records_path"], run_dirs=[case["run_dir"]],
            runner_kind="codex", role="candidate", output_path=output,
            source_archives=[archive], allow_partial=True, require_official_input=False,
        )
    assert provenance.read_bytes() == b"user owned\n"
    assert not output.exists()


def test_claude_v2_bridge_replays_envelope_and_declared_span_normalization(tmp_path, monkeypatch):
    case = _build_claude_case(tmp_path, monkeypatch)
    output = tmp_path / "claude_verifier_pass.jsonl"

    result = bridge.bridge_runs(
        records_path=case["records_path"], run_dirs=[case["run_dir"]],
        runner_kind="claude", role="verifier", output_path=output,
        require_complete=True, require_official_input=False,
    )

    assert result["model_calls"] == 0 and result["pass_records"] == 1
    row = json.loads(output.read_text(encoding="utf-8"))
    assert row["record_id"] == case["record"]["id"]
    assert row["annotator_role"] is None and row["release_pass_role"] == "verifier"
    assert row["lineage"]["provider"] == "anthropic"
    assert row["lineage"]["model_name"] == "claude-opus-5"
    assert len(row["ledger"]["cells"]) == 24
    checkpoint = json.loads((case["run_dir"] / "full_records.jsonl").read_text(encoding="utf-8"))
    assert checkpoint["declared_span_normalization"]["added_already_referenced_source_span_ids"] == ["SRC-D0001-S000000"]
    assert row["ledger"] == checkpoint["ledger"]


def test_claude_raw_blind_pass_can_be_release_primary_with_explicit_provenance(tmp_path, monkeypatch):
    case = _build_claude_case(tmp_path, monkeypatch)
    archive = _archive_for_run(tmp_path, case["run_dir"], runner="claude")
    output = tmp_path / "claude_release_primary.jsonl"
    result = bridge.bridge_runs(
        records_path=case["records_path"], run_dirs=[case["run_dir"]],
        runner_kind="claude", role="candidate", output_path=output,
        source_archives=[archive], require_official_input=False,
    )
    row = json.loads(output.read_text(encoding="utf-8"))
    raw_manifest = json.loads((case["run_dir"] / "run_manifest.json").read_text(encoding="utf-8"))
    declared = row["role_provenance"]
    assert row["annotator_role"] is None
    assert row["release_pass_role"] == declared["release_pass_role"] == "candidate"
    assert declared["raw_runner_kind"] == "claude" and declared["raw_runner_role"] is None
    assert declared["raw_pass_kind"] == raw_manifest["pass_kind"] == "blind_first_pass"
    assert declared["raw_prompt_lineage"]["prompt_protocol"] == raw_manifest["prompt_protocol"]
    assert declared["raw_prompt_lineage"]["static_system_prefix_sha256"] == raw_manifest["static_system_prefix_sha256"]
    assert row["lineage"]["provider"] == "anthropic"
    provenance = json.loads(pathlib.Path(result["provenance_output"]).read_text(encoding="utf-8"))
    assert provenance["runs"][0]["raw_runner_role"] is None
    assert provenance["runs"][0]["release_pass_role"] == "candidate"

    # The bridge row can enter the release builder as the real first vote;
    # the separate Sol row here is synthetic and makes no external call.
    from tests.test_independent_provisional_release import role_bound_row, write_jsonl

    secondary = tmp_path / "synthetic_sol_secondary.jsonl"
    write_jsonl(secondary, [role_bound_row(case["record"], runner="codex", release_role="verifier")])
    release_dir = tmp_path / "claude_first_release"
    summary = bridge.provisional_release.build_release(
        records_path=case["records_path"], primary_path=output,
        secondary_path=secondary, output_dir=release_dir, expected_count=1,
    )
    assert summary["primary_records"] == summary["secondary_records"] == 1
    assert summary["gold_cells"] == 0
    cells = [json.loads(line) for line in (release_dir / "provisional_cells.jsonl").read_text(encoding="utf-8").splitlines()]
    assert len(cells) == 24 and all(cell["tier"] != "missing" for cell in cells)


def test_claude_archived_partial_bridge_accepts_missing_summary_only_for_success_row(tmp_path, monkeypatch):
    case = _build_claude_case(tmp_path, monkeypatch)
    archive = _archive_for_run(tmp_path, case["run_dir"], runner="claude")
    (case["run_dir"] / "run_summary.json").unlink()
    output = tmp_path / "archived_claude_pass.jsonl"

    result = bridge.bridge_runs(
        records_path=case["records_path"], run_dirs=[case["run_dir"]],
        runner_kind="claude", role="verifier", output_path=output,
        source_archives=[archive], allow_partial=True, require_official_input=False,
    )

    row = json.loads(output.read_text(encoding="utf-8"))
    provenance = json.loads(pathlib.Path(result["provenance_output"]).read_text(encoding="utf-8"))
    assert result["pass_records"] == 1 and result["model_calls"] == 0
    assert row["record_id"] == case["record"]["id"]
    assert row["lineage"]["provider"] == "anthropic"
    assert len(row["ledger"]["cells"]) == 24
    assert provenance["runs"][0]["source_mode"] == "archived_original_bytes"
    assert provenance["gold_qualification"] is False


def test_claude_nonprefix_recovery_needs_exact_parent_plan_and_archive(tmp_path, monkeypatch):
    from tools.independent_gold import claude_recovery_plan as recovery

    case = _build_claude_case(tmp_path, monkeypatch, recovery_subset=True)
    archive = _archive_for_run(tmp_path, case["run_dir"], runner="claude", anchor_run=True)
    monkeypatch.setattr(claude_runner, "ROOT", tmp_path)
    manifest = json.loads((case["run_dir"] / "run_manifest.json").read_text(encoding="utf-8"))
    selected = [case["record"]["id"]]
    assert manifest["selected_ids"] == selected
    assert case["records"][0]["id"] != selected[0]
    without_plan = tmp_path / "must_not_publish.jsonl"
    with pytest.raises(bridge.PassRowBridgeError, match="requires its sealed parent-shard recovery plan"):
        bridge.bridge_runs(
            records_path=case["records_path"], run_dirs=[case["run_dir"]],
            runner_kind="claude", role="candidate", output_path=without_plan,
            source_archives=[archive], require_official_input=False,
        )
    assert not without_plan.exists()

    parent_proof = {"archive_binding": "exact_run", "verified_success_rows": 1}
    plan = {
        "schema_version": "dacon.independent.claude_manual_recovery_plan.v1",
        "recovery_output": str(case["run_dir"].resolve()),
        "organizer_input_path": str(case["records_path"].resolve()),
        "organizer_input_sha256_from_manifest": bridge._file_sha(case["records_path"]),
        "source_archive_proof": parent_proof,
        "source_run": str(tmp_path / "original_claude_shard"),
        "source_archive": str(tmp_path / "original_source_archive"),
        "recovery_ids": selected,
    }
    plan_path = tmp_path / "sealed_recovery_plan.json"
    plan_path.write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")

    def verified_plan(_plan_path, _recovery_run):
        assert pathlib.Path(_plan_path) == plan_path
        assert pathlib.Path(_recovery_run) == case["run_dir"]
        return {
            "valid": True, "recovery_ids": selected,
            "parent_source_archive_proof": parent_proof,
            "recovery_run_manifest_sha256": manifest["manifest_sha256"],
            "model_calls": 0,
        }

    monkeypatch.setattr(recovery, "verify_for_completed_recovery", verified_plan)
    output = tmp_path / "recovered_claude_primary.jsonl"
    result = bridge.bridge_runs(
        records_path=case["records_path"], run_dirs=[case["run_dir"]],
        runner_kind="claude", role="candidate", output_path=output,
        source_archives=[archive], recovery_plan_paths=[plan_path],
        require_official_input=False,
    )
    assert result["pass_records"] == 1 and result["model_calls"] == 0
    provenance = json.loads(pathlib.Path(result["provenance_output"]).read_text(encoding="utf-8"))
    assert provenance["runs"][0]["recovery_lineage"]["selection_mode"] == "explicit_parent_shard_missing_ids"
    assert provenance["runs"][0]["recovery_lineage"]["recovery_ids"] == selected

    archive_manifest_path = archive / "manifest.json"
    archive_manifest = json.loads(archive_manifest_path.read_text(encoding="utf-8"))
    archive_manifest["run_manifest_sha256"] = None
    archive_manifest_path.write_text(bridge._canonical(archive_manifest) + "\n", encoding="utf-8")
    with pytest.raises(bridge.PassRowBridgeError, match="not anchored to its exact run manifest"):
        bridge.bridge_runs(
            records_path=case["records_path"], run_dirs=[case["run_dir"]],
            runner_kind="claude", role="candidate",
            output_path=tmp_path / "must_not_publish_shared_archive.jsonl",
            source_archives=[archive], recovery_plan_paths=[plan_path],
            require_official_input=False,
        )


def test_claude_recovery_rejects_plan_selecting_another_id(tmp_path, monkeypatch):
    from tools.independent_gold import claude_recovery_plan as recovery

    case = _build_claude_case(tmp_path, monkeypatch, recovery_subset=True)
    archive = _archive_for_run(tmp_path, case["run_dir"], runner="claude", anchor_run=True)
    monkeypatch.setattr(claude_runner, "ROOT", tmp_path)
    manifest = json.loads((case["run_dir"] / "run_manifest.json").read_text(encoding="utf-8"))
    wrong_ids = [case["records"][0]["id"]]
    parent_proof = {"archive_binding": "exact_run"}
    plan = {
        "schema_version": "dacon.independent.claude_manual_recovery_plan.v1",
        "recovery_output": str(case["run_dir"].resolve()),
        "organizer_input_path": str(case["records_path"].resolve()),
        "organizer_input_sha256_from_manifest": bridge._file_sha(case["records_path"]),
        "source_archive_proof": parent_proof,
        "source_run": str(tmp_path / "original_claude_shard"),
        "source_archive": str(tmp_path / "original_source_archive"),
        "recovery_ids": wrong_ids,
    }
    plan_path = tmp_path / "wrong_recovery_plan.json"
    plan_path.write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(recovery, "verify_for_completed_recovery", lambda *_args: {
        "valid": True, "recovery_ids": wrong_ids,
        "parent_source_archive_proof": parent_proof,
        "recovery_run_manifest_sha256": manifest["manifest_sha256"],
    })
    output = tmp_path / "must_not_publish.jsonl"
    with pytest.raises(bridge.PassRowBridgeError, match="exact approved subset"):
        bridge.bridge_runs(
            records_path=case["records_path"], run_dirs=[case["run_dir"]],
            runner_kind="claude", role="candidate", output_path=output,
            source_archives=[archive], recovery_plan_paths=[plan_path],
            require_official_input=False,
        )
    assert not output.exists()


@pytest.mark.parametrize("tamper", ["stdout", "receipt", "checkpoint", "task_prompt", "source", "manifest"])
def test_claude_v2_bridge_refuses_raw_or_lineage_tampering(tmp_path, monkeypatch, tamper):
    case = _build_claude_case(tmp_path, monkeypatch)
    task_dir = next((case["run_dir"] / "tasks").glob("*/v1-24"))
    attempt = task_dir / "attempts" / "attempt-001"
    if tamper == "stdout":
        path = attempt / "stdout.bin"
        path.write_bytes(path.read_bytes() + b" ")
    elif tamper == "receipt":
        path = attempt / "receipt.json"
        receipt = json.loads(path.read_text(encoding="utf-8"))
        receipt["ledger_sha256"] = "0" * 64
        path.write_text(claude_runner.canonical_json(receipt) + "\n", encoding="utf-8", newline="\n")
    elif tamper == "checkpoint":
        path = case["run_dir"] / "full_records.jsonl"
        row = json.loads(path.read_text(encoding="utf-8"))
        row["ledger"]["cells"][0]["label"] = 1
        path.write_text(claude_runner.canonical_json(row) + "\n", encoding="utf-8", newline="\n")
    elif tamper == "task_prompt":
        path = task_dir / "prompt.txt"
        path.write_bytes(path.read_bytes() + b" ")
    elif tamper == "source":
        record = copy.deepcopy(case["record"])
        record["docs"][0]["text"] += "변조"
        with gzip.open(case["records_path"], "wt", encoding="utf-8", newline="\n") as handle:
            handle.write(claude_runner.canonical_json(record) + "\n")
    elif tamper == "manifest":
        path = case["run_dir"] / "run_manifest.json"
        manifest = json.loads(path.read_text(encoding="utf-8"))
        manifest["no_tools"] = False
        path.write_text(claude_runner.canonical_json(manifest) + "\n", encoding="utf-8", newline="\n")
    output = tmp_path / "must_not_publish.jsonl"
    with pytest.raises(bridge.PassRowBridgeError):
        bridge.bridge_runs(
            records_path=case["records_path"], run_dirs=[case["run_dir"]],
            runner_kind="claude", role="verifier", output_path=output,
            require_complete=True, require_official_input=False,
        )
    assert not output.exists()
