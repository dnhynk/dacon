import argparse
import copy
import gzip
import json
import pathlib
import subprocess

import pytest

from tools.independent_gold import claude_full_record_annotator as claude
from tools.independent_gold import full_record_context


def record():
    return {
        "id": "SYN-CLAUDE-1",
        "docs": [{"doc_id": "D0", "type": "공고문", "text": "참가자격은 본문에 명시한다.\n"}],
        "meta": {},
        "input_completeness": {"완전관측": True},
        "dropped_doc_counts": {},
    }


def structured_output(*, span_id="SRC-D0000-S000000"):
    return {
        "source_span_ids": [span_id],
        "decisions": {
            item: {
                "label": 0,
                "confidence": "H",
                "rationale": "제공된 공고문과 항목 구성요건을 대조했다",
                "premise_span_ids": [span_id],
                "exception_analysis": None,
                "completeness": "sufficient",
                "material_missing_information": None,
                "positive_evidence_span_id": None,
            }
            for item in claude.ITEMS
        },
    }


def envelope(*, structured=None):
    return {
        "type": "result",
        "subtype": "success",
        "is_error": False,
        "result": "",
        "permission_denials": [],
        "usage": {
            "input_tokens": 1200,
            "output_tokens": 450,
            "cache_creation_input_tokens": 0,
            "cache_read_input_tokens": 0,
            "server_tool_use": {"web_search_requests": 0, "web_fetch_requests": 0},
        },
        "subagent_stats": {"spawned": 0},
        "modelUsage": {
            "claude-opus-5": {
                "canonicalModel": "claude-opus-5",
                "provider": "firstParty",
                "inputTokens": 1200,
                "outputTokens": 450,
                "cacheCreationInputTokens": 0,
                "cacheReadInputTokens": 0,
            }
        },
        "total_cost_usd": 0.03,
        "structured_output": structured if structured is not None else structured_output(),
    }


def encoded(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def test_rubric_projection_removes_only_production_identifiers():
    raw = claude.RUBRIC_PATH.read_text(encoding="utf-8")
    projected = claude.annotation_rubric(raw)
    assert "Gemma" not in projected
    assert "submission/" not in projected
    assert "### v24" in projected
    assert "### v16" in projected
    assert claude.annotation_rubric(raw) == projected
    with pytest.raises(ValueError, match="stale"):
        claude.annotation_rubric(raw.replace("Gemma", "OTHER", 1))


def test_command_disables_tools_and_uses_json_schema_without_shell(tmp_path):
    schema_json = claude.canonical_json(claude.full_record_output.output_schema())
    argv = claude.build_command(
        "claude.exe", schema_json=schema_json,
        system_prompt_path=tmp_path / "system_prompt.txt",
        max_budget_usd=2.0,
    )
    assert argv[:2] == ["claude.exe", "-p"]
    assert argv[argv.index("--tools") + 1] == ""
    assert argv[argv.index("--model") + 1] == "opus"
    assert argv[argv.index("--effort") + 1] == "high"
    assert argv[argv.index("--json-schema") + 1] == schema_json
    assert argv[argv.index("--output-format") + 1] == "json"
    assert argv[argv.index("--max-budget-usd") + 1] == "2.0"
    for flag in ("--safe-mode", "--strict-mcp-config", "--no-session-persistence"):
        assert flag in argv
    assert argv[argv.index("--disallowedTools") + 1] == "mcp__*"
    assert not any(token in argv for token in ("--resume", "--continue", "--agents"))


def test_invoke_uses_argv_stdin_isolated_cwd_and_no_shell(monkeypatch):
    seen = {}

    def fake_run(argv, **kwargs):
        seen["argv"] = argv
        seen.update(kwargs)
        system_path = pathlib.Path(argv[argv.index("--system-prompt-file") + 1])
        assert system_path.read_text(encoding="utf-8") == "SYSTEM"
        assert pathlib.Path(kwargs["cwd"]) == system_path.parent
        return subprocess.CompletedProcess(argv, 0, b'{"type":"result"}', b"")

    monkeypatch.setattr(claude.subprocess, "run", fake_run)
    returncode, stdout, stderr, failure = claude._invoke(
        executable="fake-claude", system="SYSTEM", prompt="SOURCE",
        schema_json="{}", timeout=30.0, max_budget_usd=2.0,
    )
    assert (returncode, stdout, stderr, failure) == (0, b'{"type":"result"}', b"", None)
    assert seen["shell"] is False
    assert seen["input"] == b"SOURCE"
    assert seen["capture_output"] is True
    assert seen["argv"][seen["argv"].index("--tools") + 1] == ""
    assert seen["argv"][seen["argv"].index("--max-budget-usd") + 1] == "2.0"


def test_envelope_projects_exact_organizer_spans_and_24_cells():
    source = record()
    context = {
        "allowed_span_registry": full_record_context.source_span_registry_from_record(source)
    }
    parsed, ledger, normalized, report = claude.validate_envelope(encoded(envelope()), source, context)
    assert parsed["modelUsage"]["claude-opus-5"]["canonicalModel"] == "claude-opus-5"
    assert len(ledger["cells"]) == 24
    assert ledger["source_spans"][0]["quote"] == source["docs"][0]["text"]
    assert all(cell["label"] == 0 for cell in ledger["cells"])
    assert normalized == parsed["structured_output"]
    assert report["changed"] is False


def test_envelope_accepts_fixed_classifier_but_attests_opus_body():
    source = record()
    context = {"allowed_span_registry": full_record_context.source_span_registry_from_record(source)}
    value = envelope()
    value["modelUsage"]["claude-haiku-4-5-20251001"] = {
        "canonicalModel": "claude-haiku-4-5", "provider": "firstParty",
        "inputTokens": 900, "outputTokens": 10,
        "cacheReadInputTokens": 0, "cacheCreationInputTokens": 0,
    }
    parsed, _, _, _ = claude.validate_envelope(encoded(value), source, context)
    assert "claude-haiku-4-5-20251001" in parsed["modelUsage"]
    value["modelUsage"]["claude-opus-5"]["outputTokens"] = 10
    with pytest.raises(ValueError, match="body model usage mismatch"):
        claude.validate_envelope(encoded(value), source, context)
    value["modelUsage"]["claude-opus-5"]["outputTokens"] = 450
    value["modelUsage"]["unexpected-model"] = {"canonicalModel": "unexpected-model"}
    with pytest.raises(ValueError, match="model identity"):
        claude.validate_envelope(encoded(value), source, context)


@pytest.mark.parametrize(
    "mutate,match",
    [
        (lambda e: e["usage"]["server_tool_use"].__setitem__("web_fetch_requests", 1), "server tool"),
        (lambda e: e["subagent_stats"].__setitem__("spawned", 1), "subagent"),
        (lambda e: e["permission_denials"].append({"tool": "Bash"}), "permission"),
        (lambda e: e["modelUsage"]["claude-opus-5"].__setitem__("canonicalModel", "other"), "canonical model"),
        (lambda e: e.pop("structured_output"), "structured_output"),
        (lambda e: e.update({"tool_uses": [{"name": "Bash"}]}), "tool/subagent"),
        (lambda e: e.update({"type": "tool_use"}), "successful"),
    ],
)
def test_envelope_fails_closed_on_unsafe_or_unattested_result(mutate, match):
    source = record()
    context = {
        "allowed_span_registry": full_record_context.source_span_registry_from_record(source)
    }
    value = envelope()
    mutate(value)
    with pytest.raises(ValueError, match=match):
        claude.validate_envelope(encoded(value), source, context)


def test_envelope_rejects_unknown_span_id():
    source = record()
    context = {
        "allowed_span_registry": full_record_context.source_span_registry_from_record(source)
    }
    with pytest.raises(ValueError, match="not in organizer registry"):
        claude.validate_envelope(
            encoded(envelope(structured=structured_output(span_id="SRC-D9999-S999999"))),
            source, context,
        )


def test_unused_declared_span_normalization_is_narrow_and_source_bound():
    source = record()
    source["docs"].append({"doc_id": "D1", "type": "첨부", "text": "선언만 된 부록 근거"})
    context = {"allowed_span_registry": full_record_context.source_span_registry_from_record(source)}
    value = envelope()
    value["structured_output"]["source_span_ids"].append("SRC-D0001-S000000")
    parsed, ledger, normalized, report = claude.validate_envelope(encoded(value), source, context)
    assert parsed["structured_output"]["source_span_ids"] == ["SRC-D0000-S000000", "SRC-D0001-S000000"]
    assert normalized["source_span_ids"] == ["SRC-D0000-S000000"]
    assert normalized["decisions"] == parsed["structured_output"]["decisions"]
    assert report["removed_unused_source_span_ids"] == ["SRC-D0001-S000000"]
    assert report["added_already_referenced_source_span_ids"] == []
    assert report["changed"] is True
    assert report["raw_structured_output_sha256"] != report["normalized_structured_output_sha256"]
    assert len(ledger["source_spans"]) == 1
    value["structured_output"]["source_span_ids"][-1] = "SRC-D9999-S999999"
    with pytest.raises(ValueError, match="not in organizer registry"):
        claude.validate_envelope(encoded(value), source, context)
    value["structured_output"]["source_span_ids"] = ["SRC-D0000-S000000"]
    value["structured_output"]["decisions"]["v1"]["premise_span_ids"] = ["SRC-D0001-S000000"]
    parsed, ledger, normalized, report = claude.validate_envelope(encoded(value), source, context)
    assert normalized["source_span_ids"] == ["SRC-D0000-S000000", "SRC-D0001-S000000"]
    assert normalized["decisions"] == parsed["structured_output"]["decisions"]
    assert report["added_already_referenced_source_span_ids"] == ["SRC-D0001-S000000"]
    assert len(ledger["source_spans"]) == 2
    value["structured_output"]["decisions"]["v1"]["premise_span_ids"] = ["SRC-D9999-S999999"]
    with pytest.raises(ValueError, match="outside organizer registry"):
        claude.validate_envelope(encoded(value), source, context)


def test_selection_rejects_duplicates_and_unknown_ids(tmp_path):
    source = tmp_path / "source.jsonl.gz"
    with gzip.open(source, "wt", encoding="utf-8") as handle:
        handle.write(json.dumps(record(), ensure_ascii=False) + "\n")
    assert claude.select_ids(source, [], shard_index=0, shard_count=1, limit=None) == (
        ["SYN-CLAUDE-1"], 1
    )
    with pytest.raises(ValueError, match="not found"):
        claude.select_ids(source, ["MISSING"], shard_index=0, shard_count=1, limit=None)
    with pytest.raises(ValueError, match="unique"):
        claude.select_ids(source, ["SYN-CLAUDE-1", "SYN-CLAUDE-1"], shard_index=0, shard_count=1, limit=None)
    with gzip.open(source, "at", encoding="utf-8") as handle:
        handle.write(json.dumps(record(), ensure_ascii=False) + "\n")
    with pytest.raises(ValueError, match="duplicate organizer ID"):
        claude.select_ids(source, [], shard_index=0, shard_count=1, limit=None)


def test_synthetic_execute_preserves_raw_bytes_and_provisional_ledger(tmp_path, monkeypatch):
    source_record = record()
    source_record["docs"].append({"doc_id": "D1", "type": "첨부", "text": "선언만 된 부록 근거"})
    source = tmp_path / "source.jsonl.gz"
    with gzip.open(source, "wt", encoding="utf-8") as handle:
        handle.write(json.dumps(source_record, ensure_ascii=False) + "\n")
    monkeypatch.setattr(claude, "_allowed_input", lambda path: path.resolve())
    monkeypatch.setattr(
        claude, "resolve_cli",
        lambda executable: {
            "requested_executable": executable,
            "resolved_executable": "fake-claude",
            "executable_sha256": "0" * 64,
            "version_output": "2.1.276 (Claude Code)",
            "version_stderr_sha256": "0" * 64,
        },
    )
    value = envelope()
    value["structured_output"]["source_span_ids"].append("SRC-D0001-S000000")
    raw = encoded(value)
    observed = {}

    def fake_invoke(**kwargs):
        observed.update(kwargs)
        return 0, raw, b"", None

    monkeypatch.setattr(claude, "_invoke", fake_invoke)
    output = tmp_path / "fresh"
    args = argparse.Namespace(
        input=source, output_dir=output, claude_bin="fake-claude",
        phase="development_diagnostic", record_id=["SYN-CLAUDE-1"],
        shard_index=0, shard_count=1, limit=None, timeout=30.0, max_budget_usd=2.0, context_mode="full", execute=True, continue_on_content_error=False,
    )
    stats = claude.run(args)
    assert stats["calls_attempted"] == stats["tasks_ok"] == 1
    assert stats["abstention_cells"] == 0
    assert "Gemma" not in observed["system"]
    assert "submission/" not in observed["system"]
    assert "peer" not in observed["prompt"].lower()
    assert observed["executable"] == "fake-claude"
    task_dir = next((output / "tasks").glob("*/v1-24"))
    assert (task_dir / "attempts" / "attempt-001" / "stdout.bin").read_bytes() == raw
    receipt = json.loads((task_dir / "attempts" / "attempt-001" / "receipt.json").read_text(encoding="utf-8"))
    assert receipt["status"] == "ok"
    assert receipt["stdout_sha256"] == claude.sha256_bytes(raw)
    assert receipt["requested_max_budget_usd"] == 2.0
    assert receipt["declared_span_normalization"]["removed_unused_source_span_ids"] == ["SRC-D0001-S000000"]
    assert receipt["cache_observation"]["body_cache_creation_input_tokens"] == 0
    assert receipt["cache_observation"]["body_cache_read_input_tokens"] == 0
    run_manifest = json.loads((output / "run_manifest.json").read_text(encoding="utf-8"))
    assert receipt["static_system_prefix_sha256"] == run_manifest["static_system_prefix_sha256"]
    row = json.loads((output / "full_records.jsonl").read_text(encoding="utf-8"))
    assert row["status"] == "provisional_unqualified"
    assert row["raw_structured_output"]["source_span_ids"] == ["SRC-D0000-S000000", "SRC-D0001-S000000"]
    assert row["structured_output"]["source_span_ids"] == ["SRC-D0000-S000000"]
    assert len(row["ledger"]["cells"]) == 24
    with pytest.raises(ValueError, match="fresh-only"):
        claude.run(args)
    args.output_dir = tmp_path / "over_budget"
    args.max_budget_usd = 0.01
    exceeded = claude.run(args)
    assert exceeded["tasks_error"] == 1
    assert not (args.output_dir / "full_records.jsonl").exists()
    failed_task = next((args.output_dir / "tasks").glob("*/v1-24"))
    failed_receipt = json.loads((failed_task / "attempts" / "attempt-001" / "receipt.json").read_text(encoding="utf-8"))
    assert failed_receipt["requested_max_budget_usd"] == 0.01
    assert "exceeds requested per-call budget" in failed_receipt["errors"][0]


def test_synthetic_tool_use_stops_and_preserves_failure_stdout(tmp_path, monkeypatch):
    source = tmp_path / "source.jsonl.gz"
    with gzip.open(source, "wt", encoding="utf-8") as handle:
        handle.write(json.dumps(record(), ensure_ascii=False) + "\n")
    monkeypatch.setattr(claude, "_allowed_input", lambda path: path.resolve())
    monkeypatch.setattr(
        claude, "resolve_cli",
        lambda executable: {
            "requested_executable": executable,
            "resolved_executable": "fake-claude",
            "executable_sha256": "0" * 64,
            "version_output": "2.1.276 (Claude Code)",
            "version_stderr_sha256": "0" * 64,
        },
    )
    value = envelope()
    value["usage"]["server_tool_use"]["web_search_requests"] = 1
    raw = encoded(value)
    monkeypatch.setattr(claude, "_invoke", lambda **kwargs: (0, raw, b"diagnostic", None))
    output = tmp_path / "fresh"
    args = argparse.Namespace(
        input=source, output_dir=output, claude_bin="fake-claude",
        phase="development_diagnostic", record_id=["SYN-CLAUDE-1"],
        shard_index=0, shard_count=1, limit=None, timeout=30.0, max_budget_usd=2.0, context_mode="full", execute=True, continue_on_content_error=False,
    )
    stats = claude.run(args)
    assert stats["calls_attempted"] == stats["tasks_error"] == 1
    assert stats["tasks_ok"] == 0
    assert not (output / "full_records.jsonl").exists()
    task_dir = next((output / "tasks").glob("*/v1-24"))
    receipt = json.loads((task_dir / "attempts" / "attempt-001" / "receipt.json").read_text(encoding="utf-8"))
    assert receipt["status"] == "error"
    assert "server tool" in receipt["errors"][0]
    assert (task_dir / "attempts" / "attempt-001" / "stdout.bin").read_bytes() == raw


def test_prepare_only_does_not_invoke_model(tmp_path, monkeypatch):
    source = tmp_path / "source.jsonl.gz"
    with gzip.open(source, "wt", encoding="utf-8") as handle:
        handle.write(json.dumps(record(), ensure_ascii=False) + "\n")
    monkeypatch.setattr(claude, "_allowed_input", lambda path: path.resolve())
    monkeypatch.setattr(
        claude, "resolve_cli",
        lambda executable: {
            "requested_executable": executable,
            "resolved_executable": "fake-claude",
            "executable_sha256": "0" * 64,
            "version_output": "2.1.276 (Claude Code)",
            "version_stderr_sha256": "0" * 64,
        },
    )
    monkeypatch.setattr(claude, "_invoke", lambda **kwargs: pytest.fail("model invoked"))
    output = tmp_path / "fresh"
    args = argparse.Namespace(
        input=source, output_dir=output, claude_bin="fake-claude",
        phase="development_diagnostic", record_id=["SYN-CLAUDE-1"],
        shard_index=0, shard_count=1, limit=None, timeout=30.0, max_budget_usd=2.0, context_mode="full", execute=False, continue_on_content_error=False,
    )
    stats = claude.run(args)
    assert stats["calls_attempted"] == 0
    assert stats["tasks_prepared"] == 1
    assert not (output / "full_records.jsonl").exists()
    manifest = json.loads((output / "run_manifest.json").read_text(encoding="utf-8"))
    assert manifest["source_context"]["truncation_allowed"] is False
    assert manifest["source_context"]["fact_catalog_sha256"]


def test_compact_context_is_reversible_and_preserves_all_source_material():
    source = record()
    full = full_record_context.build_full_record_context(source)
    compact = claude.compact_context(full)
    assert claude.expand_compact_context(compact) == full
    for key in (
        "organizer_record", "source_completeness", "allowed_span_registry",
        "law_contexts", "law_reference_registry", "target_items",
    ):
        assert compact[key] == full[key]
    assert compact["compact_projection"]["full_context_sha256"] == full["context_sha256"]
    assert compact["fact_shared_values"]
    assert compact["qualification_shared_values"]
    prompt = claude.user_prompt(full, context_mode="compact")
    assert json.dumps(source["docs"][0]["text"], ensure_ascii=False)[1:-1] in prompt
    assert "SRC-D0000-S000000" in prompt
    assert "_compact_ref" in prompt
    changed = copy.deepcopy(compact)
    changed["allowed_span_registry"].clear()
    with pytest.raises(ValueError, match="projection hash mismatch"):
        claude.expand_compact_context(changed)


def test_compact_prepare_saves_projected_and_full_context_without_model_call(tmp_path, monkeypatch):
    source = tmp_path / "source.jsonl.gz"
    with gzip.open(source, "wt", encoding="utf-8") as handle:
        handle.write(json.dumps(record(), ensure_ascii=False) + "\n")
    monkeypatch.setattr(claude, "_allowed_input", lambda path: path.resolve())
    monkeypatch.setattr(
        claude, "resolve_cli",
        lambda executable: {
            "requested_executable": executable,
            "resolved_executable": "fake-claude",
            "executable_sha256": "0" * 64,
            "version_output": "2.1.276 (Claude Code)",
            "version_stderr_sha256": "0" * 64,
        },
    )
    monkeypatch.setattr(claude, "_invoke", lambda **kwargs: pytest.fail("model invoked"))
    output = tmp_path / "compact_fresh"
    args = argparse.Namespace(
        input=source, output_dir=output, claude_bin="fake-claude",
        phase="development_diagnostic", record_id=["SYN-CLAUDE-1"],
        shard_index=0, shard_count=1, limit=None, timeout=30.0,
        max_budget_usd=2.0, context_mode="compact", execute=False, continue_on_content_error=False,
    )
    stats = claude.run(args)
    assert stats["calls_attempted"] == 0
    task_dir = next((output / "tasks").glob("*/v1-24"))
    full = json.loads((task_dir / "full_record_context.json").read_text(encoding="utf-8"))
    compact = json.loads((task_dir / "model_input_context.json").read_text(encoding="utf-8"))
    assert claude.expand_compact_context(compact) == full
    manifest = json.loads((task_dir / "task_manifest.json").read_text(encoding="utf-8"))
    assert manifest["context_mode"] == "compact"
    assert manifest["full_context_bytes"] - manifest["model_context_bytes"] == manifest["context_bytes_removed"]
    assert manifest["compact_projection_sha256"] == compact["compact_projection"]["projection_sha256"]
    assert stats["context_bytes_removed_total"] == manifest["context_bytes_removed"]


def test_opt_in_content_error_continues_with_raw_receipt_and_only_success_checkpoint(tmp_path, monkeypatch):
    first = record()
    second = copy.deepcopy(first)
    second["id"] = "SYN-CLAUDE-2"
    source = tmp_path / "two.jsonl.gz"
    with gzip.open(source, "wt", encoding="utf-8") as handle:
        for item in (first, second):
            handle.write(json.dumps(item, ensure_ascii=False) + "\n")
    monkeypatch.setattr(claude, "_allowed_input", lambda path: path.resolve())
    monkeypatch.setattr(claude, "resolve_cli", lambda executable: {
        "requested_executable": executable, "resolved_executable": "fake-claude",
        "executable_sha256": "0" * 64, "version_output": "2.1.276 (Claude Code)",
        "version_stderr_sha256": "0" * 64,
    })
    bad = envelope()
    bad["structured_output"]["decisions"]["v9"]["premise_span_ids"] = []
    responses = iter((encoded(bad), encoded(envelope())))
    monkeypatch.setattr(claude, "_invoke", lambda **kwargs: (0, next(responses), b"", None))
    output = tmp_path / "content_continue"
    args = argparse.Namespace(
        input=source, output_dir=output, claude_bin="fake-claude",
        phase="development_diagnostic", record_id=[], shard_index=0, shard_count=1,
        limit=None, timeout=30.0, max_budget_usd=2.0, context_mode="compact",
        execute=True, continue_on_content_error=True,
    )
    stats = claude.run(args)
    assert (stats["calls_attempted"], stats["tasks_error"], stats["content_errors"],
            stats["continued_content_errors"], stats["tasks_ok"]) == (2, 1, 1, 1, 1)
    first_task = next((output / "tasks").glob("SYN-CLAUDE-1-*/v1-24"))
    receipt = json.loads((first_task / "attempts" / "attempt-001" / "receipt.json").read_text(encoding="utf-8"))
    assert receipt["error_class"] == "content"
    assert receipt["continued_after_content_error"] is True
    assert "binary conclusion requires at least one premise span" in receipt["errors"][0]
    assert receipt["usage"] == bad["usage"]
    assert (first_task / "attempts" / "attempt-001" / "stdout.bin").read_bytes() == encoded(bad)
    rows = [json.loads(line) for line in (output / "full_records.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [row["id"] for row in rows] == ["SYN-CLAUDE-2"]


@pytest.mark.parametrize("failure_kind", ("tool", "model", "cost", "transport"))
def test_opt_in_content_error_never_continues_after_safety_failure(tmp_path, monkeypatch, failure_kind):
    first = record()
    second = copy.deepcopy(first)
    second["id"] = "SYN-CLAUDE-2"
    source = tmp_path / "two.jsonl.gz"
    with gzip.open(source, "wt", encoding="utf-8") as handle:
        for item in (first, second):
            handle.write(json.dumps(item, ensure_ascii=False) + "\n")
    monkeypatch.setattr(claude, "_allowed_input", lambda path: path.resolve())
    monkeypatch.setattr(claude, "resolve_cli", lambda executable: {
        "requested_executable": executable, "resolved_executable": "fake-claude",
        "executable_sha256": "0" * 64, "version_output": "2.1.276 (Claude Code)",
        "version_stderr_sha256": "0" * 64,
    })
    bad = envelope()
    if failure_kind == "tool":
        bad["usage"]["server_tool_use"]["web_search_requests"] = 1
    elif failure_kind == "model":
        bad["modelUsage"]["unknown-model"] = {"canonicalModel": "unknown-model"}
    elif failure_kind == "cost":
        bad["total_cost_usd"] = 2.5
    calls = []

    def fake_invoke(**kwargs):
        calls.append(kwargs)
        return (1 if failure_kind == "transport" else 0), encoded(bad), b"", None

    monkeypatch.setattr(claude, "_invoke", fake_invoke)
    output = tmp_path / "safety_stop"
    args = argparse.Namespace(
        input=source, output_dir=output, claude_bin="fake-claude",
        phase="development_diagnostic", record_id=[], shard_index=0, shard_count=1,
        limit=None, timeout=30.0, max_budget_usd=2.0, context_mode="compact",
        execute=True, continue_on_content_error=True,
    )
    stats = claude.run(args)
    assert len(calls) == stats["calls_attempted"] == stats["tasks_error"] == stats["safety_errors"] == 1
    assert stats["continued_content_errors"] == stats["tasks_ok"] == 0
    assert not (output / "full_records.jsonl").exists()
    first_task = next((output / "tasks").glob("SYN-CLAUDE-1-*/v1-24"))
    receipt = json.loads((first_task / "attempts" / "attempt-001" / "receipt.json").read_text(encoding="utf-8"))
    assert receipt["error_class"] == "safety"
    assert receipt["continued_after_content_error"] is False
