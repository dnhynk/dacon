import argparse
import copy
import gzip
import hashlib
import json
import pathlib

import pytest

from tools.independent_gold import claude_shard_supervisor as supervisor


def _record(number):
    return {
        "id": f"SYN-SUP-{number}",
        "docs": [{"doc_id": "D0", "type": "공고문", "text": f"공고 {number}: 참가자격은 본문에 명시한다."}],
        "meta": {},
        "input_completeness": {"완전관측": True},
        "dropped_doc_counts": {},
    }


def _model_result(*, content_error=False, server_tool=False):
    span = "SRC-D0000-S000000"
    decisions = {
        item: {
            "label": 0, "confidence": "H", "rationale": "제공된 공고문과 구성요건을 대조했다",
            "premise_span_ids": [] if content_error and item == "v9" else [span],
            "exception_analysis": None, "completeness": "sufficient",
            "material_missing_information": None, "positive_evidence_span_id": None,
        }
        for item in supervisor.runner.ITEMS
    }
    return {
        "type": "result", "subtype": "success", "is_error": False, "result": "",
        "permission_denials": [],
        "usage": {
            "input_tokens": 1200, "output_tokens": 450,
            "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0,
            "server_tool_use": {"web_search_requests": int(server_tool), "web_fetch_requests": 0},
        },
        "subagent_stats": {"spawned": 0},
        "modelUsage": {"claude-opus-5": {
            "canonicalModel": "claude-opus-5", "provider": "firstParty",
            "inputTokens": 1200, "outputTokens": 450,
            "cacheCreationInputTokens": 0, "cacheReadInputTokens": 0,
        }},
        "total_cost_usd": 0.03,
        "structured_output": {"source_span_ids": [span], "decisions": decisions},
    }


def _args(root, **overrides):
    values = dict(
        output_root=root, claude_bin="fake-claude", context_mode="compact",
        admission_report=root.parent / "admission" / "report.json",
        first_shard_index=0, last_shard_index=1, max_budget_usd=2.0,
        max_new_shards=None, poll_seconds=1.0, resume=False, execute=False,
        allow_external_overlap=False,
    )
    values.update(overrides)
    return argparse.Namespace(**values)


@pytest.fixture
def small_supervisor(tmp_path, monkeypatch):
    runs = tmp_path / "runs"
    runs.mkdir()
    source = tmp_path / "data" / "train_unlabeled.jsonl.gz"
    source.parent.mkdir()
    with gzip.open(source, "wt", encoding="utf-8") as handle:
        for number in range(4):
            handle.write(json.dumps(_record(number), ensure_ascii=False) + "\n")
    monkeypatch.setattr(supervisor, "ROOT", tmp_path)
    monkeypatch.setattr(supervisor, "INPUT", source)
    monkeypatch.setattr(supervisor, "EXTERNAL_SHARD_ROOT", runs / "external")
    monkeypatch.setattr(supervisor, "TOTAL_SHARDS", 2)
    monkeypatch.setattr(supervisor, "RECORDS_PER_SHARD", 2)
    monkeypatch.setattr(supervisor.runner, "_allowed_input", lambda path: path.resolve())
    monkeypatch.setattr(supervisor.runner, "resolve_cli", lambda executable: {
        "requested_executable": executable,
        "resolved_executable": "fake-claude",
        "executable_sha256": "0" * 64,
        "version_output": "2.1.276 (Claude Code)",
        "version_stderr_sha256": "0" * 64,
    })
    admission = runs / "admission"
    admission.mkdir()
    row_path = admission / "records.jsonl"
    row_path.write_text("".join(json.dumps({"id": _record(number)["id"], "status": "pass"}) + "\n"
                                for number in range(4)), encoding="utf-8")
    ids_digest = hashlib.sha256()
    for number in range(4):
        ids_digest.update((_record(number)["id"] + "\n").encode("utf-8"))
    software_paths = {
        "context_preflight": pathlib.Path(supervisor.context_preflight.__file__).resolve(),
        "full_record_context": pathlib.Path(supervisor.runner.full_record_context.__file__).resolve(),
        "fact_context": pathlib.Path(supervisor.runner.full_record_context.fact_context.__file__).resolve(),
        "qualification_context": pathlib.Path(supervisor.runner.full_record_context.qualification_context.__file__).resolve(),
        "law_context": pathlib.Path(supervisor.runner.full_record_context.law_context.__file__).resolve(),
    }
    report = {
        "schema_version": supervisor.context_preflight.SCHEMA_VERSION,
        "semantic_role": "organizer_source_admission_only_not_labels_or_model_staging",
        "status": "pass",
        "counts": {"expected_records": 4, "processed": 4, "unique_ids": 4, "passed": 4, "failed": 0},
        "errors": [], "fatal_error": None,
        "input": {
            "path": str(source.resolve()), "bytes": source.stat().st_size,
            "sha256_before": supervisor.runner.file_sha256(source),
            "sha256_after": supervisor.runner.file_sha256(source),
        },
        "id_order_sha256": ids_digest.hexdigest(),
        "context_schema_version": supervisor.runner.full_record_context.SCHEMA_VERSION,
        "software": {
            name: {
                "path": str(path),
                "sha256_before": supervisor.runner.file_sha256(path),
                "sha256_after": supervisor.runner.file_sha256(path),
            }
            for name, path in software_paths.items()
        },
        "record_rows": {"path": str(row_path), "sha256": supervisor.runner.file_sha256(row_path)},
    }
    (admission / "report.json").write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")
    return runs


def _fake_process(monkeypatch, responses):
    calls = []
    queue = iter(responses)

    def fake_invoke(**kwargs):
        calls.append(kwargs)
        payload = next(queue)
        if payload == "auth_error":
            return 1, b"", b"authentication failed", None
        return 0, json.dumps(payload, ensure_ascii=False).encode("utf-8"), b"", None

    monkeypatch.setattr(supervisor.runner, "_invoke", fake_invoke)

    class FakeProcess:
        def __init__(self, argv, **kwargs):
            parsed = supervisor.runner.build_parser().parse_args(argv[3:])
            stats = supervisor.runner.run(parsed)
            self.returncode = 1 if stats["tasks_error"] else 0

        def poll(self):
            return self.returncode

    monkeypatch.setattr(supervisor.subprocess, "Popen", FakeProcess)
    return calls


def test_dry_run_no_write_and_existing_external_shards_block_execution(small_supervisor):
    external = small_supervisor / "external"
    external.mkdir()
    (external / "provisional_claude_shard000_first10_v1").mkdir()
    supervisor.EXTERNAL_SHARD_ROOT = external
    root = small_supervisor / "fresh_supervisor"
    args = _args(root)
    result = supervisor.supervise(args)
    assert result["model_calls"] == 0
    assert result["external_overlap_indices"] == [0]
    assert not root.exists()
    args.execute = True
    with pytest.raises(supervisor.SupervisorError, match="disjoint range"):
        supervisor.supervise(args)
    assert not root.exists()


@pytest.mark.parametrize("failure", ("status", "count", "input", "software", "rows"))
def test_admission_report_must_cover_all_records_and_frozen_sources(small_supervisor, failure):
    root = small_supervisor / "admission_gate"
    report_path = small_supervisor / "admission" / "report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    if failure == "status":
        report["status"] = "fail_closed"
    elif failure == "count":
        report["counts"]["passed"] = 3
    elif failure == "input":
        report["input"]["sha256_after"] = "f" * 64
    elif failure == "software":
        report["software"]["qualification_context"]["sha256_after"] = "f" * 64
    else:
        report["record_rows"]["sha256"] = "f" * 64
    report_path.write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(supervisor.SupervisorError, match="admission"):
        supervisor.supervise(_args(root, execute=True))
    assert not root.exists()


def test_completed_shards_are_verified_and_never_recalled_on_resume(small_supervisor, monkeypatch):
    root = small_supervisor / "fresh_supervisor"
    calls = _fake_process(monkeypatch, [_model_result() for _ in range(4)])
    args = _args(root, execute=True, max_new_shards=1)
    first = supervisor.supervise(args)
    assert first["new_shards_executed"] == first["shards_verified_complete"] == 1
    assert len(calls) == 2
    args.resume = True
    second = supervisor.supervise(args)
    assert second["new_shards_executed"] == 1
    assert second["shards_verified_complete"] == 2
    assert len(calls) == 4
    third = supervisor.supervise(args)
    assert third["new_shards_executed"] == 0
    assert len(calls) == 4
    assert len((root / "events.jsonl").read_text(encoding="utf-8").splitlines()) >= 4


def test_content_error_can_complete_shard_but_only_success_rows_checkpoint(small_supervisor, monkeypatch):
    root = small_supervisor / "content_supervisor"
    calls = _fake_process(monkeypatch, [_model_result(content_error=True), _model_result()])
    result = supervisor.supervise(_args(root, execute=True, first_shard_index=0,
                                        last_shard_index=0))
    assert len(calls) == 2
    assert result["new_shards_executed"] == 1
    assert result["content_errors"] == 1
    assert result["successful_records"] == 1
    shard = root / "shards" / "shard-000-of-2"
    rows = [json.loads(line) for line in (shard / "full_records.jsonl").read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 1
    assert rows[0]["id"] == "SYN-SUP-2"


def test_partial_shard_and_auth_error_never_recall_or_advance(small_supervisor, monkeypatch):
    root = small_supervisor / "auth_supervisor"
    calls = _fake_process(monkeypatch, ["auth_error", _model_result()])
    with pytest.raises(supervisor.SupervisorError, match="fully attempted"):
        supervisor.supervise(_args(root, execute=True))
    assert len(calls) == 1
    assert not (root / "shards" / "shard-001-of-2").exists()
    with pytest.raises(supervisor.SupervisorError, match="fully attempted"):
        supervisor.supervise(_args(root, execute=True, resume=True))
    assert len(calls) == 1


def test_resume_rejects_source_fingerprint_drift(small_supervisor, monkeypatch):
    root = small_supervisor / "drift_supervisor"
    calls = _fake_process(monkeypatch, [_model_result(), _model_result()])
    supervisor.supervise(_args(root, execute=True, first_shard_index=0,
                               last_shard_index=0))
    assert len(calls) == 2
    original = supervisor.runner.source_bundle
    monkeypatch.setattr(supervisor.runner, "source_bundle", lambda: {
        **copy.deepcopy(original()), "bundle_sha256": "f" * 64,
    })
    with pytest.raises(supervisor.SupervisorError, match="differ from frozen plan"):
        supervisor.supervise(_args(root, execute=True, resume=True,
                                   first_shard_index=0, last_shard_index=0))
    assert len(calls) == 2
