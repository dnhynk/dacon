"""CPU-only fixtures for the read-only stopped-Sol diagnostic."""

from __future__ import annotations

import pathlib

import pytest

from tests.test_independent_full_run_receipt_verify import _build_case
from tests.test_independent_pass_row_bridge import _archive_for_run
from tools.independent_gold import codex_cli_annotator as base
from tools.independent_gold import codex_full_record_annotator as runner
from tools.independent_gold import sol_partial_stop_audit as audit


@pytest.fixture(scope="module")
def catalogs():
    return (
        base.catalog_facts.CatalogIndex.load(),
        base.qualification_context.qualification_facts.CatalogReference.load(),
    )


def _case(tmp_path: pathlib.Path, catalogs, *, count: int = 3):
    case = _build_case(tmp_path, catalogs, count=count)
    archive = _archive_for_run(tmp_path, case["run_dir"], runner="codex")
    return case, archive


def _audit(case: dict, archive: pathlib.Path, catalogs):
    return audit.audit_run(
        run_dir=case["run_dir"], source_archive=archive,
        organizer_input=case["records_path"],
        order=list(case["records"]), records=case["records"], catalogs=catalogs,
        expected_shard_index=0, expected_shard_count=1,
    )


def _checkpoint_without_last(case: dict) -> None:
    checkpoint = case["run_dir"] / "full_records.jsonl"
    lines = checkpoint.read_bytes().splitlines(keepends=True)
    checkpoint.write_bytes(b"".join(lines[:-1]))


def test_complete_prefix_requires_archived_raw_replay_and_writes_nothing(tmp_path, catalogs):
    case, archive = _case(tmp_path, catalogs, count=2)
    before = {path: path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}

    report = _audit(case, archive, catalogs)

    assert report["verified_completed"] == list(case["records"])
    assert report["receipt_without_checkpoint"] == []
    assert report["prepared_without_receipt"] == []
    assert report["partial_or_corrupt_tail"] == []
    assert report["automatic_recall_authorized"] is False
    assert report["model_calls"] == report["output_files_created"] == 0
    assert {path: path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()} == before


def test_successful_receipt_without_checkpoint_is_not_completed(tmp_path, catalogs):
    case, archive = _case(tmp_path, catalogs)
    _checkpoint_without_last(case)
    ids = list(case["records"])

    report = _audit(case, archive, catalogs)

    assert report["verified_completed"] == ids[:2]
    assert report["receipt_without_checkpoint"] == [{
        "id": ids[2], "status": "ok", "raw_attempt_replay_verified": True,
    }]
    assert report["uncertain_ids"] == [ids[2]]
    assert report["automatic_recall_authorized"] is False


def test_prepared_without_receipt_is_uncertain_in_flight(tmp_path, catalogs):
    case, archive = _case(tmp_path, catalogs)
    _checkpoint_without_last(case)
    last_id = list(case["records"])[-1]
    attempt_dir = (case["run_dir"] / "tasks" / base._task_slug(last_id) /
                   runner.GROUP_NAME / "attempts" / "attempt-001")
    (attempt_dir / "receipt.json").unlink()

    report = _audit(case, archive, catalogs)

    assert report["verified_completed"] == list(case["records"])[:2]
    assert report["receipt_without_checkpoint"] == []
    assert report["prepared_without_receipt"] == [{
        "id": last_id, "attempt_dirs": ["attempt-001"],
        "task_artifacts_verified": True,
    }]
    assert report["automatic_recall_authorized"] is False


def test_partially_prepared_task_is_not_trusted(tmp_path, catalogs):
    case, archive = _case(tmp_path, catalogs)
    _checkpoint_without_last(case)
    last_id = list(case["records"])[-1]
    task_dir = case["run_dir"] / "tasks" / base._task_slug(last_id) / runner.GROUP_NAME
    (task_dir / "attempts" / "attempt-001" / "receipt.json").unlink()
    (task_dir / "prompt.txt").unlink()

    report = _audit(case, archive, catalogs)

    assert report["prepared_without_receipt"][0]["task_artifacts_verified"] is False
    assert any(issue["kind"] == "partial_or_corrupt_prepared_task"
               for issue in report["partial_or_corrupt_tail"])
    assert report["automatic_recall_authorized"] is False


def test_unterminated_tail_and_raw_tamper_fail_closed(tmp_path, catalogs):
    case, archive = _case(tmp_path, catalogs)
    checkpoint = case["run_dir"] / "full_records.jsonl"
    checkpoint.write_bytes(checkpoint.read_bytes() + b'{"id":"unfinished"')
    last_id = list(case["records"])[-1]
    raw = (case["run_dir"] / "tasks" / base._task_slug(last_id) /
           runner.GROUP_NAME / "attempts" / "attempt-001" / "raw_final.json")
    raw.write_bytes(raw.read_bytes() + b" ")

    report = _audit(case, archive, catalogs)

    assert report["verified_completed"] == list(case["records"])[:2]
    assert {issue["kind"] for issue in report["partial_or_corrupt_tail"]} >= {
        "unterminated_checkpoint_tail", "unverified_checkpoint_row",
        "unverified_receipt_raw_attempt", "unverified_checkpoint_receipt_state",
    }
    assert report["automatic_recall_authorized"] is False


def test_nonprefix_checkpoint_row_cannot_become_verified_prefix(tmp_path, catalogs):
    case, archive = _case(tmp_path, catalogs)
    checkpoint = case["run_dir"] / "full_records.jsonl"
    lines = checkpoint.read_bytes().splitlines(keepends=True)
    checkpoint.write_bytes(lines[0] + lines[2])

    report = _audit(case, archive, catalogs)

    assert report["verified_completed"] == list(case["records"])[:1]
    assert any(issue["kind"] == "nonprefix_checkpoint_row" for issue in report["partial_or_corrupt_tail"])
    assert [entry["id"] for entry in report["receipt_without_checkpoint"]] == [list(case["records"])[1]]
    assert report["automatic_recall_authorized"] is False


def test_multi_run_entrypoint_reads_organizer_without_creating_spool(tmp_path, catalogs):
    case, archive = _case(tmp_path, catalogs, count=2)
    before = {path: path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}

    report = audit.audit_runs(
        organizer_input=case["records_path"],
        runs_and_archives=[(case["run_dir"], archive)],
        expected_shard_indices=[0], expected_shard_count=1,
    )

    assert report["verified_completed_count"] == 2
    assert report["uncertain_count"] == 0
    assert report["automatic_recall_authorized"] is False
    assert {path: path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()} == before


def test_unanchored_source_archive_fails_closed(tmp_path, catalogs):
    case, archive = _case(tmp_path, catalogs)
    source_manifest = archive / "manifest.json"
    value = audit.bridge._canonical_file(source_manifest, "synthetic archive")
    value["run_manifest_sha256"] = None
    source_manifest.write_text(audit.bridge._canonical(value) + "\n", encoding="utf-8", newline="\n")

    with pytest.raises(audit.bridge.PassRowBridgeError, match="not anchored"):
        _audit(case, archive, catalogs)
