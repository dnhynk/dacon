"""Strict post-hoc bridge from Codex CLI receipts to the dev diagnostic panel.

This module never invokes a model.  It accepts only an already frozen Codex
CLI checkpoint and proves a common semantic lineage through one or more paired
run manifests/checkpoints, deterministic task artifacts, raw event streams,
attempt receipts, and group decisions before reading official labels for
scoring.  Different staging paths may be combined only when every semantic and
model field is identical. Missing, duplicate, overlapping, or out-of-panel
record/group rows are fatal; an append-only retry log must be distilled into a
new, unambiguous checkpoint before it can be scored.

Passing this selected panel is only a screening result.  It cannot satisfy the
full official-dev gate and can never qualify a model for gold generation by
itself.
"""

from __future__ import annotations

import argparse
import json
import math
import pathlib
import sys
from collections import Counter
from statistics import mean
from typing import Any, Iterable, Mapping, Sequence

try:
    from tools.independent_gold import annotate_groups
    from tools.independent_gold import codex_cli_annotator as codex_runner
    from tools.independent_gold import qualification_panel
except ModuleNotFoundError:  # Direct ``python tools/.../codex_panel_bridge.py``.
    import annotate_groups  # type: ignore[no-redef]
    import codex_cli_annotator as codex_runner  # type: ignore[no-redef]
    import qualification_panel  # type: ignore[no-redef]


ROOT = pathlib.Path(__file__).resolve().parents[2]
DEFAULT_RECORDS = ROOT / "data_open" / "dev.jsonl.gz"
DEFAULT_LABELS = ROOT / "data_open" / "dev_labels.csv"

REPORT_SCHEMA = "dacon.independent.codex_panel_bridge.v2"
PANEL_MACRO_F1_MIN = 0.90
PANEL_ITEM_F1_MIN = 0.70

HEX64 = frozenset("0123456789abcdef")

RUN_MANIFEST_KEYS = frozenset(
    {
        "schema_version",
        "run_key",
        "record_input",
        "staging_dir",
        "runner_source",
        "semantic_config",
        "codex_cli",
        "execution_isolation",
        "forbidden_annotation_inputs",
        "threat_model",
        "official_openai_docs",
        "manifest_sha256",
    }
)

TASK_MANIFEST_KEYS = frozenset(
    {
        "schema_version",
        "run_key",
        "run_manifest_sha256",
        "record_id",
        "group",
        "target_items",
        "record_source_sha256",
        "packet_sha256",
        "fact_context_sha256",
        "qualification_context_sha256",
        "law_context_sha256",
        "rubric_sha256",
        "group_rubric_sha256",
        "prompt_sha256",
        "output_schema_sha256",
        "artifact_file_sha256",
        "semantic_inputs",
        "cli_execution_input",
        "threat_model_sha256",
        "cli_may_read_outside_working_directory",
        "manifest_sha256",
    }
)

CHECKPOINT_KEYS = frozenset(
    {
        "schema_version",
        "runner_receipt_schema_version",
        "id",
        "group",
        "target_items",
        "run_key",
        "run_manifest_sha256",
        "task_manifest_sha256",
        "source_sha256",
        "packet_sha256",
        "fact_context_sha256",
        "fact_context_schema_version",
        "fact_context_bounds",
        "qualification_context_sha256",
        "qualification_context_schema_version",
        "qualification_context_bounds",
        "qualification_catalog_sha256",
        "law_context_sha256",
        "law_context_schema_version",
        "rubric_sha256",
        "system_prompt_sha256",
        "prompt_sha256",
        "output_schema_sha256",
        "request_sha256",
        "raw_response_sha256",
        "event_stream_sha256",
        "stderr_sha256",
        "content_sha256",
        "raw_final_sha256",
        "raw_content",
        "model_provenance",
        "event_audit",
        "usage",
        "attempt",
        "completed_utc",
        "elapsed_seconds",
        "status",
        "decisions",
        "evidence_errors",
    }
)

ATTEMPT_FILES = frozenset({"events.jsonl", "stderr.txt", "raw_final.json", "receipt.json"})


class BridgeError(ValueError):
    """Raised when a panel, lineage, receipt, or evidence invariant fails."""


def _canonical(value: Any) -> str:
    return codex_runner.canonical_json(value)


def _is_sha256(value: Any) -> bool:
    return isinstance(value, str) and len(value) == 64 and set(value) <= HEX64


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise BridgeError(message)


def _require_exact_keys(value: Mapping[str, Any], expected: Iterable[str], context: str) -> None:
    actual = set(value)
    wanted = set(expected)
    _require(
        actual == wanted,
        f"{context}: keys differ; missing={sorted(wanted - actual)}, extra={sorted(actual - wanted)}",
    )


def _load_json(path: pathlib.Path, context: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise BridgeError(f"{context}: missing file {path}") from exc
    except json.JSONDecodeError as exc:
        raise BridgeError(f"{context}: invalid JSON in {path}: {exc.msg}") from exc
    _require(isinstance(value, dict), f"{context}: expected one JSON object")
    return value


def _manifest_hash(value: Mapping[str, Any]) -> str:
    return codex_runner.sha256_object(
        {key: child for key, child in value.items() if key != "manifest_sha256"}
    )


def _read_checkpoint(path: pathlib.Path) -> list[dict[str, Any]]:
    _require(path.is_file(), f"checkpoint: missing file {path}")
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                raise BridgeError(
                    f"checkpoint line {line_number}: invalid JSON: {exc.msg}"
                ) from exc
            _require(
                isinstance(value, dict),
                f"checkpoint line {line_number}: row is not an object",
            )
            value["__checkpoint_line__"] = line_number
            rows.append(value)
    _require(bool(rows), "checkpoint: no rows")
    return rows


def _checkpoint_identities(
    path: pathlib.Path,
    allowed_group_rows: set[tuple[str, str]],
) -> set[tuple[str, str]]:
    """Read only record/group identities before any batch is admitted.

    Full receipt validation happens later.  This first pass makes overlap,
    missing-row, and extra-row checks independent of batch ordering.
    """

    identities: set[tuple[str, str]] = set()
    for row in _read_checkpoint(path):
        line_number = int(row.pop("__checkpoint_line__"))
        key = (str(row.get("id") or ""), str(row.get("group") or ""))
        _require(
            key in allowed_group_rows,
            f"checkpoint {path} line {line_number}: extra group row {key}",
        )
        _require(
            key not in identities,
            f"checkpoint {path} line {line_number}: duplicate group row {key}",
        )
        identities.add(key)
    return identities


def _batch_paths(
    value: pathlib.Path | Sequence[pathlib.Path], context: str
) -> list[pathlib.Path]:
    if isinstance(value, pathlib.Path):
        paths = [value]
    else:
        paths = [pathlib.Path(path) for path in value]
    _require(bool(paths), f"{context}: at least one path is required")
    return paths


def _cross_batch_lineage(manifest: Mapping[str, Any]) -> dict[str, Any]:
    """Remove only fields that legitimately differ between staging batches."""

    return {
        key: value
        for key, value in manifest.items()
        if key not in {"staging_dir", "manifest_sha256"}
    }


def _panel_targets(
    manifest: Mapping[str, Any], records: Mapping[str, Mapping[str, Any]]
) -> tuple[dict[tuple[str, str], dict[str, str]], set[tuple[str, str]]]:
    """Validate the label-free panel projection and return cells/group rows."""

    _require(
        manifest.get("schema_version") == qualification_panel.MANIFEST_SCHEMA,
        "panel: wrong schema version",
    )
    _require(
        manifest.get("purpose") == "partial_official_dev_annotator_diagnostic_only",
        "panel: wrong purpose",
    )
    _require(
        manifest.get("qualified_for_gold_generation") is False,
        "panel: must hard-code qualified_for_gold_generation=false",
    )
    _require(
        manifest.get("is_full_official_dev_evaluation") is False,
        "panel: must hard-code is_full_official_dev_evaluation=false",
    )
    _require(_is_sha256(manifest.get("labels_sha256")), "panel: invalid labels SHA256")
    _require(
        manifest.get("records_sha256") is not None,
        "panel: missing records SHA256",
    )
    _require(
        manifest.get("manifest_sha256") == _manifest_hash(manifest),
        "panel: manifest hash mismatch",
    )
    items = manifest.get("items")
    _require(
        isinstance(items, Mapping) and set(items) == set(annotate_groups.ITEMS),
        "panel: items must be exactly v1..v24",
    )

    targets: dict[tuple[str, str], dict[str, str]] = {}
    role_counts: Counter[str] = Counter()
    expected_group_ids: dict[str, set[str]] = {
        name: set() for name, _, _ in annotate_groups.GROUPS
    }
    for item in annotate_groups.ITEMS:
        spec = items[item]
        _require(isinstance(spec, Mapping), f"panel {item}: spec is not an object")
        group_name = qualification_panel.ITEM_TO_GROUP[item]
        _require(spec.get("target_group") == group_name, f"panel {item}: wrong group")
        for field, role in (
            ("positive_candidates", "positive"),
            ("hard_negative_candidates", "hard_negative"),
        ):
            candidates = spec.get(field)
            _require(isinstance(candidates, list), f"panel {item}: {field} is not a list")
            expected_count = 2
            _require(
                len(candidates) == expected_count,
                f"panel {item}: {field} must contain exactly {expected_count} rows",
            )
            for candidate in candidates:
                _require(isinstance(candidate, Mapping), f"panel {item}: invalid candidate")
                record_id = str(candidate.get("id") or "")
                _require(record_id in records, f"panel {item}: unknown record {record_id}")
                _require(
                    candidate.get("source_sha256")
                    == codex_runner.sha256_object(records[record_id]),
                    f"panel {record_id}:{item}: source hash mismatch",
                )
                key = (record_id, item)
                _require(key not in targets, f"panel: duplicate target cell {record_id}:{item}")
                targets[key] = {"selection_role": role, "target_group": group_name}
                role_counts[role] += 1
                expected_group_ids[group_name].add(record_id)

    _require(len(targets) == 96, f"panel: expected 96 target cells, found {len(targets)}")
    _require(
        role_counts == Counter({"positive": 48, "hard_negative": 48}),
        f"panel: target roles differ: {dict(role_counts)}",
    )
    unique_ids = {record_id for record_id, _ in targets}

    counts = manifest.get("counts")
    _require(
        counts
        == {
            "unique_ids": len(unique_ids),
            "target_cells": 96,
            "positive_target_cells": 48,
            "hard_negative_target_cells": 48,
        },
        "panel: count registry mismatch",
    )

    expected_batches = [
        {
            "target_group": group_name,
            "ids": sorted(expected_group_ids[group_name]),
            "target_items": list(group_items),
        }
        for group_name, group_items, _ in annotate_groups.GROUPS
        if expected_group_ids[group_name]
    ]
    _require(
        manifest.get("execution_batches") == expected_batches,
        "panel: execution batches have missing, duplicate, or extra record/group rows",
    )
    required_group_rows = {
        (record_id, batch["target_group"])
        for batch in expected_batches
        for record_id in batch["ids"]
    }
    return targets, required_group_rows


def _validate_run_manifest(
    manifest: Mapping[str, Any],
    *,
    manifest_path: pathlib.Path,
    records_path: pathlib.Path,
    fact_catalog: Any,
    qualification_catalog: Any,
) -> pathlib.Path:
    _require_exact_keys(manifest, RUN_MANIFEST_KEYS, "run manifest")
    _require(
        manifest.get("schema_version") == codex_runner.RUNNER_SCHEMA_VERSION,
        "run manifest: wrong schema version",
    )
    _require(
        manifest.get("manifest_sha256") == _manifest_hash(manifest),
        "run manifest: hash mismatch",
    )

    staging_dir = pathlib.Path(str(manifest.get("staging_dir") or "")).resolve()
    _require(
        manifest_path.resolve() == staging_dir / "run_manifest.json",
        "run manifest: path is not the declared staging run_manifest.json",
    )
    record_input = manifest.get("record_input")
    _require(isinstance(record_input, Mapping), "run manifest: invalid record_input")
    _require_exact_keys(record_input, {"path", "sha256", "allowed_kind"}, "record_input")
    _require(
        pathlib.Path(str(record_input.get("path") or "")).resolve() == records_path.resolve(),
        "run manifest: organizer record path mismatch",
    )
    records_hash = codex_runner.file_sha256(records_path)
    _require(record_input.get("sha256") == records_hash, "run manifest: record SHA mismatch")
    _require(
        record_input.get("allowed_kind") == records_path.name,
        "run manifest: record kind mismatch",
    )

    runner_source = manifest.get("runner_source")
    _require(isinstance(runner_source, Mapping), "run manifest: invalid runner_source")
    _require_exact_keys(runner_source, {"path", "sha256"}, "runner_source")
    runner_path = pathlib.Path(codex_runner.__file__).resolve()
    _require(
        pathlib.Path(str(runner_source.get("path") or "")).resolve() == runner_path,
        "run manifest: runner source path mismatch",
    )
    _require(
        runner_source.get("sha256") == codex_runner.file_sha256(runner_path),
        "run manifest: runner source hash mismatch",
    )

    config = manifest.get("semantic_config")
    _require(isinstance(config, Mapping), "run manifest: invalid semantic_config")
    _require_exact_keys(
        config,
        {
            "model",
            "reasoning_effort",
            "rubric_sha256",
            "prompt_protocol_version",
            "output_schema_version",
            "groups",
            "source_packet",
            "fact_context",
            "qualification_context",
            "law_context",
            "codex_command_template",
            "tool_event_policy",
        },
        "semantic_config",
    )
    model = config.get("model")
    effort = config.get("reasoning_effort")
    _require(isinstance(model, str) and bool(model.strip()), "run manifest: missing model")
    _require(isinstance(effort, str) and bool(effort.strip()), "run manifest: missing effort")
    rubric = codex_runner.RUBRIC_PATH.read_text(encoding="utf-8")
    _require(
        config.get("rubric_sha256") == codex_runner.sha256_text(rubric),
        "run manifest: rubric hash mismatch",
    )
    _require(
        config.get("prompt_protocol_version") == codex_runner.PROMPT_PROTOCOL_VERSION,
        "run manifest: prompt protocol mismatch",
    )
    _require(
        config.get("output_schema_version") == codex_runner.OUTPUT_SCHEMA_VERSION,
        "run manifest: output schema version mismatch",
    )
    _require(
        _canonical(config.get("groups")) == _canonical(annotate_groups.GROUPS),
        "run manifest: group registry mismatch",
    )
    source_packet = config.get("source_packet")
    bounded_packet = {
        "mode": "bounded_retrieval",
        "full_source_policy_version": None,
        "full_source_max_chars": None,
    }
    full_packet = {
        "mode": "full_source",
        "full_source_policy_version": codex_runner.FULL_SOURCE_PACKET_VERSION,
        "full_source_max_chars": codex_runner.FULL_SOURCE_MAX_CHARS,
    }
    _require(
        source_packet in (bounded_packet, full_packet),
        "run manifest: source-packet policy mismatch",
    )
    expected_fact = {
        "schema_version": codex_runner.fact_context.SCHEMA_VERSION,
        "max_chars": codex_runner.fact_context.DEFAULT_MAX_CHARS,
        "catalog_sha256": fact_catalog.sha256,
    }
    _require(config.get("fact_context") == expected_fact, "run manifest: fact context mismatch")
    expected_qualification = {
        "schema_version": codex_runner.qualification_context.SCHEMA_VERSION,
        "extractor_schema_version": (
            codex_runner.qualification_context.qualification_facts.SCHEMA_VERSION
        ),
        "max_chars": codex_runner.qualification_context.DEFAULT_MAX_CHARS,
        "catalog_sha256": qualification_catalog.sha256,
    }
    _require(
        config.get("qualification_context") == expected_qualification,
        "run manifest: qualification context mismatch",
    )
    expected_law = {
        "schema_version": codex_runner.law_context.SCHEMA_VERSION,
        "max_chars": codex_runner.law_context.DEFAULT_MAX_CHARS,
        "reference_manifest_sha256": codex_runner.law_context.reference_manifest_sha256(),
    }
    _require(config.get("law_context") == expected_law, "run manifest: law context mismatch")
    command = codex_runner.command_template(model, effort)
    _require(
        config.get("codex_command_template") == command,
        "run manifest: Codex command template mismatch",
    )
    _require(
        config.get("tool_event_policy")
        == "reject_every_item_type_except_agent_message_and_reasoning",
        "run manifest: tool-event policy mismatch",
    )

    cli = manifest.get("codex_cli")
    _require(isinstance(cli, Mapping), "run manifest: missing CLI provenance")
    _require_exact_keys(
        cli,
        {
            "requested_executable",
            "resolved_executable",
            "executable_sha256",
            "version_output",
            "version_stderr_sha256",
        },
        "CLI provenance",
    )
    _require(_is_sha256(cli.get("executable_sha256")), "CLI provenance: invalid executable hash")
    _require(_is_sha256(cli.get("version_stderr_sha256")), "CLI provenance: invalid stderr hash")
    _require(
        isinstance(cli.get("resolved_executable"), str)
        and bool(str(cli.get("resolved_executable")).strip()),
        "CLI provenance: missing resolved executable",
    )
    executable_path = pathlib.Path(str(cli["resolved_executable"])).resolve()
    _require(executable_path.is_file(), "CLI provenance: resolved executable is unavailable")
    _require(
        codex_runner.file_sha256(executable_path) == cli["executable_sha256"],
        "CLI provenance: executable hash mismatch",
    )
    _require(
        isinstance(cli.get("version_output"), str) and bool(str(cli.get("version_output")).strip()),
        "CLI provenance: missing version output",
    )

    expected_isolation = {
        "persistent_stage_is_cli_cwd": False,
        "cli_cwd": "fresh OS temporary directory outside repository",
        "stdin_semantic_payload_only": True,
        "shell": False,
        "session_ephemeral": True,
        "sandbox": "read-only",
        "ignore_user_config": True,
        "ignore_rules": True,
        "approval_policy_override": "never",
        "raw_jsonl_event_audit": True,
    }
    _require(
        manifest.get("execution_isolation") == expected_isolation,
        "run manifest: execution isolation mismatch",
    )
    _require(
        manifest.get("forbidden_annotation_inputs")
        == [
            "competition runtime source",
            "production predictions",
            "saved model responses",
            "historical prediction artifacts",
            "organizer labels",
        ],
        "run manifest: forbidden-input declaration mismatch",
    )
    _require(
        manifest.get("threat_model")
        == {
            "path": "THREAT_MODEL.md",
            "sha256": codex_runner.sha256_text(codex_runner.THREAT_MODEL),
            "read_only_is_not_confidentiality_boundary": True,
            "cli_may_read_outside_working_directory": True,
            "external_container_required_for_high_assurance_secrecy": True,
        },
        "run manifest: threat model mismatch",
    )
    _require(
        manifest.get("official_openai_docs") == list(codex_runner.OFFICIAL_DOCS),
        "run manifest: documentation provenance mismatch",
    )
    threat_path = staging_dir / "THREAT_MODEL.md"
    _require(threat_path.is_file(), "run manifest: missing staged threat model")
    _require(
        threat_path.read_text(encoding="utf-8") == codex_runner.THREAT_MODEL,
        "run manifest: staged threat model differs",
    )

    expected_run_key = codex_runner.sha256_object(
        {
            "runner_schema_version": codex_runner.RUNNER_SCHEMA_VERSION,
            "record_input_sha256": records_hash,
            "semantic_config": config,
            "cli_executable_sha256": cli["executable_sha256"],
            "cli_version_output": cli["version_output"],
        }
    )
    _require(manifest.get("run_key") == expected_run_key, "run manifest: run key mismatch")
    return staging_dir


def _load_task_artifacts(
    task_dir: pathlib.Path,
    *,
    record: Mapping[str, Any],
    record_id: str,
    group_name: str,
    run_manifest: Mapping[str, Any],
    fact_catalog: Any,
) -> tuple[dict[str, Any], dict[str, Any]]:
    target_items, packet_budget = annotate_groups.GROUP_BY_NAME[group_name]
    task_manifest_path = task_dir / "task_manifest.json"
    task_manifest = _load_json(task_manifest_path, f"task {record_id}:{group_name}")
    _require_exact_keys(task_manifest, TASK_MANIFEST_KEYS, f"task {record_id}:{group_name}")
    _require(
        task_manifest.get("schema_version") == codex_runner.TASK_SCHEMA_VERSION,
        f"task {record_id}:{group_name}: wrong schema",
    )
    _require(
        task_manifest.get("manifest_sha256") == _manifest_hash(task_manifest),
        f"task {record_id}:{group_name}: manifest hash mismatch",
    )
    _require(
        task_manifest.get("run_key") == run_manifest["run_key"]
        and task_manifest.get("run_manifest_sha256") == run_manifest["manifest_sha256"],
        f"task {record_id}:{group_name}: run lineage mismatch",
    )
    _require(
        task_manifest.get("record_id") == record_id
        and task_manifest.get("group") == group_name
        and task_manifest.get("target_items") == list(target_items),
        f"task {record_id}:{group_name}: identity mismatch",
    )
    _require(
        task_manifest.get("record_source_sha256") == codex_runner.sha256_object(record),
        f"task {record_id}:{group_name}: source hash mismatch",
    )

    qualification_relevant = group_name in codex_runner.qualification_context.GROUP_FAMILIES
    artifact_names = {
        "prompt.txt",
        "output_schema.json",
        "packet.json",
        "fact_context.json",
        "law_context.json",
        *( ["qualification_context.json"] if qualification_relevant else [] ),
    }
    hashes = task_manifest.get("artifact_file_sha256")
    _require(isinstance(hashes, Mapping), f"task {record_id}:{group_name}: invalid artifact hashes")
    _require(
        set(hashes) == artifact_names,
        f"task {record_id}:{group_name}: artifact hash set mismatch",
    )
    for name in artifact_names:
        artifact = task_dir / name
        _require(artifact.is_file(), f"task {record_id}:{group_name}: missing {name}")
        _require(
            codex_runner.file_sha256(artifact) == hashes[name],
            f"task {record_id}:{group_name}: {name} hash mismatch",
        )

    prompt = (task_dir / "prompt.txt").read_text(encoding="utf-8")
    packet = _load_json(task_dir / "packet.json", f"task {record_id}:{group_name} packet")
    fact = _load_json(
        task_dir / "fact_context.json", f"task {record_id}:{group_name} fact context"
    )
    law = _load_json(task_dir / "law_context.json", f"task {record_id}:{group_name} law context")
    qualification = (
        _load_json(
            task_dir / "qualification_context.json",
            f"task {record_id}:{group_name} qualification context",
        )
        if qualification_relevant
        else None
    )
    output_schema = _load_json(
        task_dir / "output_schema.json", f"task {record_id}:{group_name} output schema"
    )

    if run_manifest["semantic_config"]["source_packet"]["mode"] == "full_source":
        expected_packet = codex_runner.build_full_source_packet(
            record,
            target_items,
            max_chars=codex_runner.FULL_SOURCE_MAX_CHARS,
        )
    else:
        expected_packet = codex_runner.packetize_record(
            record, max_chars=packet_budget, target_items=target_items
        )
    _require(packet == expected_packet, f"task {record_id}:{group_name}: packet is not deterministic")
    fact_errors = codex_runner.fact_context.validate_fact_context(
        record, fact, catalog_index=fact_catalog
    )
    _require(not fact_errors, f"task {record_id}:{group_name}: fact errors {fact_errors[:3]}")
    if qualification is not None:
        qualification_errors = (
            codex_runner.qualification_context.validate_qualification_context(record, qualification)
        )
        _require(
            not qualification_errors,
            f"task {record_id}:{group_name}: qualification errors {qualification_errors[:3]}",
        )
    law_errors = codex_runner.law_context.validate_law_context(law)
    _require(not law_errors, f"task {record_id}:{group_name}: law errors {law_errors[:3]}")
    expected_law = codex_runner.law_context.build_law_context(
        group_name, max_chars=codex_runner.law_context.DEFAULT_MAX_CHARS
    )
    _require(law == expected_law, f"task {record_id}:{group_name}: law context mismatch")

    rubric = codex_runner.RUBRIC_PATH.read_text(encoding="utf-8")
    group_rubric = annotate_groups.extract_group_rubric(rubric, target_items)
    messages = annotate_groups.build_group_messages(
        record,
        packet,
        group_rubric,
        group_name,
        fact_context_payload=fact,
        qualification_context_payload=qualification,
        law_context_payload=law,
    )
    expected_prompt = codex_runner.build_codex_prompt(
        messages,
        record_id=record_id,
        group_name=group_name,
        target_items=target_items,
    )
    expected_schema = codex_runner.group_output_schema(target_items)
    _require(prompt == expected_prompt, f"task {record_id}:{group_name}: prompt mismatch")
    _require(output_schema == expected_schema, f"task {record_id}:{group_name}: output schema mismatch")

    expected_semantic_inputs = [
        "packet.json",
        "fact_context.json",
        *( ["qualification_context.json"] if qualification_relevant else [] ),
        "law_context.json",
        "prompt.txt",
    ]
    expected_fields = {
        "packet_sha256": codex_runner.sha256_object(packet),
        "fact_context_sha256": fact.get("context_sha256"),
        "qualification_context_sha256": (
            qualification.get("context_sha256") if qualification is not None else None
        ),
        "law_context_sha256": law.get("context_sha256"),
        "rubric_sha256": codex_runner.sha256_text(rubric),
        "group_rubric_sha256": codex_runner.sha256_text(group_rubric),
        "prompt_sha256": codex_runner.sha256_text(prompt),
        "output_schema_sha256": codex_runner.sha256_object(output_schema),
    }
    for field, expected in expected_fields.items():
        _require(
            task_manifest.get(field) == expected,
            f"task {record_id}:{group_name}: {field} mismatch",
        )
    _require(
        task_manifest.get("semantic_inputs") == expected_semantic_inputs,
        f"task {record_id}:{group_name}: semantic input list mismatch",
    )
    _require(
        task_manifest.get("cli_execution_input")
        == {
            "stdin": "prompt.txt bytes",
            "filesystem": ["output_schema.json"],
            "persistent_task_directory_is_not_cli_cwd": True,
        },
        f"task {record_id}:{group_name}: CLI input boundary mismatch",
    )
    _require(
        task_manifest.get("threat_model_sha256")
        == codex_runner.sha256_text(codex_runner.THREAT_MODEL)
        and task_manifest.get("cli_may_read_outside_working_directory") is True,
        f"task {record_id}:{group_name}: threat-model lineage mismatch",
    )
    return task_manifest, {
        "packet": packet,
        "fact": fact,
        "qualification": qualification,
        "law": law,
        "prompt": prompt,
        "output_schema": output_schema,
        "group_rubric": group_rubric,
    }


def _validate_checkpoint_receipt(
    row: Mapping[str, Any],
    *,
    checkpoint_line: int,
    task_dir: pathlib.Path,
    task_manifest: Mapping[str, Any],
    artifacts: Mapping[str, Any],
    run_manifest: Mapping[str, Any],
    record: Mapping[str, Any],
    record_id: str,
    group_name: str,
) -> dict[str, Any]:
    context = f"checkpoint line {checkpoint_line} ({record_id}:{group_name})"
    _require_exact_keys(row, CHECKPOINT_KEYS, context)
    target_items = tuple(annotate_groups.GROUP_BY_NAME[group_name][0])
    _require(row.get("schema_version") == annotate_groups.GROUP_SCHEMA_VERSION, f"{context}: wrong group schema")
    _require(
        row.get("runner_receipt_schema_version") == codex_runner.RECEIPT_SCHEMA_VERSION,
        f"{context}: wrong receipt schema",
    )
    _require(row.get("status") == "ok", f"{context}: status is not ok")
    _require(row.get("evidence_errors") == [], f"{context}: evidence errors present")
    _require(
        row.get("id") == record_id
        and row.get("group") == group_name
        and row.get("target_items") == list(target_items),
        f"{context}: checkpoint identity mismatch",
    )
    _require(
        row.get("run_key") == run_manifest["run_key"]
        and row.get("run_manifest_sha256") == run_manifest["manifest_sha256"]
        and row.get("task_manifest_sha256") == task_manifest["manifest_sha256"],
        f"{context}: run/task lineage mismatch",
    )

    expected_direct = {
        "source_sha256": task_manifest["record_source_sha256"],
        "packet_sha256": task_manifest["packet_sha256"],
        "fact_context_sha256": task_manifest["fact_context_sha256"],
        "fact_context_schema_version": artifacts["fact"]["schema_version"],
        "fact_context_bounds": artifacts["fact"]["bounds"],
        "qualification_context_sha256": task_manifest["qualification_context_sha256"],
        "qualification_context_schema_version": (
            artifacts["qualification"]["schema_version"]
            if artifacts["qualification"] is not None
            else None
        ),
        "qualification_context_bounds": (
            artifacts["qualification"]["bounds"]
            if artifacts["qualification"] is not None
            else None
        ),
        "qualification_catalog_sha256": run_manifest["semantic_config"][
            "qualification_context"
        ]["catalog_sha256"],
        "law_context_sha256": task_manifest["law_context_sha256"],
        "law_context_schema_version": artifacts["law"]["schema_version"],
        "rubric_sha256": task_manifest["rubric_sha256"],
        "system_prompt_sha256": task_manifest["group_rubric_sha256"],
        "prompt_sha256": task_manifest["prompt_sha256"],
        "output_schema_sha256": task_manifest["output_schema_sha256"],
    }
    for field, expected in expected_direct.items():
        _require(row.get(field) == expected, f"{context}: {field} mismatch")
    expected_request = codex_runner.sha256_object(
        {
            "prompt_sha256": task_manifest["prompt_sha256"],
            "output_schema_sha256": task_manifest["output_schema_sha256"],
            "command": run_manifest["semantic_config"]["codex_command_template"],
        }
    )
    _require(row.get("request_sha256") == expected_request, f"{context}: request hash mismatch")

    attempt = row.get("attempt")
    _require(
        isinstance(attempt, int) and not isinstance(attempt, bool) and attempt >= 1,
        f"{context}: invalid attempt number",
    )
    attempts_dir = task_dir / "attempts"
    _require(attempts_dir.is_dir(), f"{context}: missing attempts directory")
    attempt_names = sorted(child.name for child in attempts_dir.iterdir() if child.is_dir())
    expected_attempt_name = f"attempt-{attempt:03d}"
    _require(
        attempt_names == [expected_attempt_name],
        f"{context}: ambiguous retry lineage {attempt_names}",
    )
    attempt_dir = attempts_dir / expected_attempt_name
    _require(
        {child.name for child in attempt_dir.iterdir() if child.is_file()} == ATTEMPT_FILES,
        f"{context}: attempt artifact set mismatch",
    )
    receipt = _load_json(attempt_dir / "receipt.json", context + " receipt")
    _require(receipt == row, f"{context}: checkpoint differs from immutable attempt receipt")
    events = (attempt_dir / "events.jsonl").read_text(encoding="utf-8")
    stderr = (attempt_dir / "stderr.txt").read_text(encoding="utf-8")
    raw_final = (attempt_dir / "raw_final.json").read_text(encoding="utf-8")
    expected_hashes = {
        "raw_response_sha256": codex_runner.sha256_text(events),
        "event_stream_sha256": codex_runner.sha256_text(events),
        "stderr_sha256": codex_runner.sha256_text(stderr),
        "content_sha256": codex_runner.sha256_text(raw_final),
        "raw_final_sha256": codex_runner.sha256_text(raw_final),
    }
    for field, expected in expected_hashes.items():
        _require(_is_sha256(row.get(field)), f"{context}: invalid {field}")
        _require(row.get(field) == expected, f"{context}: {field} mismatch")
    _require(row.get("raw_content") == raw_final, f"{context}: raw final mismatch")

    audit = codex_runner._event_audit(events)
    expected_event_audit = {
        "event_count": audit["event_count"],
        "parse_errors": audit["parse_errors"],
        "unsafe_items": audit["unsafe_items"],
        "service_errors": audit["service_errors"],
        "turn_completed": audit["turn_completed"],
    }
    _require(row.get("event_audit") == expected_event_audit, f"{context}: event audit mismatch")
    _require(not audit["parse_errors"], f"{context}: event parse errors")
    _require(not audit["unsafe_items"], f"{context}: prohibited tool/nonmessage event")
    _require(not audit["service_errors"], f"{context}: service error event")
    _require(audit["turn_completed"] is True, f"{context}: turn did not complete")
    _require(
        audit["agent_messages"] and raw_final.strip() == audit["agent_messages"][-1].strip(),
        f"{context}: final output differs from last agent message",
    )
    _require(row.get("usage") == audit["usage"], f"{context}: usage receipt mismatch")
    _require(
        len(audit["thread_ids"]) == 1 and bool(audit["thread_ids"][0]),
        f"{context}: thread lineage is not singular",
    )

    expected_provenance = {
        "endpoint_kind": "codex_cli_exec",
        "requested_model": run_manifest["semantic_config"]["model"],
        "declared_reasoning_effort": run_manifest["semantic_config"]["reasoning_effort"],
        "resolved_model": None,
        "resolved_model_note": "CLI event protocol does not attest a resolved alias",
        "codex_cli": run_manifest["codex_cli"],
        "command_template": run_manifest["semantic_config"]["codex_command_template"],
        "session_ephemeral": True,
        "sandbox": "read-only",
        "ignore_user_config": True,
        "ignore_rules": True,
        "thread_ids": audit["thread_ids"],
    }
    _require(row.get("model_provenance") == expected_provenance, f"{context}: model provenance mismatch")

    try:
        parsed = json.loads(raw_final)
        _require(isinstance(parsed, dict), f"{context}: final JSON is not an object")
        normalized_wire = codex_runner._normalize_codex_wire_output(parsed, target_items)
        decisions = annotate_groups.normalize_group_decision(normalized_wire, target_items)
        evidence_errors = annotate_groups.validate_evidence(
            decisions,
            artifacts["packet"],
            artifacts["fact"],
            artifacts["qualification"],
        )
    except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        raise BridgeError(f"{context}: invalid final decision: {exc}") from exc
    _require(not evidence_errors, f"{context}: evidence errors {evidence_errors}")
    _require(
        row.get("decisions") == decisions,
        f"{context}: decisions differ from raw final JSON/evidence resolution",
    )

    # Resolve every stored positive coordinate back to the immutable organizer source.
    documents = record.get("docs") or []
    for item, cell in decisions.items():
        for index, location in enumerate(cell["evidence_locations"]):
            try:
                doc = documents[location["doc_index"]]
                source_text = str(doc.get("text") or "")
                quote = source_text[location["start"] : location["end"]]
            except (IndexError, KeyError, TypeError) as exc:
                raise BridgeError(f"{context}: {item} coordinate {index} invalid") from exc
            _require(quote == cell["evidence"], f"{context}: {item} coordinate quote mismatch")
            _require(
                location.get("doc_id") == doc.get("doc_id")
                and location.get("source_doc_sha256") == codex_runner.sha256_text(source_text)
                and location.get("evidence_sha256")
                == codex_runner.sha256_text(cell["evidence"]),
                f"{context}: {item} coordinate provenance mismatch",
            )
    _require(
        isinstance(row.get("completed_utc"), str) and bool(str(row.get("completed_utc")).strip()),
        f"{context}: missing completion time",
    )
    elapsed = row.get("elapsed_seconds")
    _require(
        isinstance(elapsed, (int, float))
        and not isinstance(elapsed, bool)
        and math.isfinite(float(elapsed))
        and float(elapsed) >= 0,
        f"{context}: invalid elapsed time",
    )
    return decisions


def _index_and_validate_checkpoint(
    checkpoint_path: pathlib.Path,
    *,
    required_group_rows: set[tuple[str, str]],
    staging_dir: pathlib.Path,
    run_manifest: Mapping[str, Any],
    records: Mapping[str, Mapping[str, Any]],
    fact_catalog: Any,
) -> dict[tuple[str, str], dict[str, Any]]:
    raw_rows = _read_checkpoint(checkpoint_path)
    indexed: dict[tuple[str, str], tuple[int, dict[str, Any]]] = {}
    for row in raw_rows:
        line_number = int(row.pop("__checkpoint_line__"))
        key = (str(row.get("id") or ""), str(row.get("group") or ""))
        _require(key in required_group_rows, f"checkpoint line {line_number}: extra group row {key}")
        _require(key not in indexed, f"checkpoint line {line_number}: duplicate group row {key}")
        indexed[key] = (line_number, row)
    missing = sorted(required_group_rows - set(indexed))
    _require(not missing, f"checkpoint: missing required group rows {missing[:5]}")
    _require(
        len(indexed) == len(required_group_rows),
        "checkpoint: required group-row cardinality mismatch",
    )

    verified: dict[tuple[str, str], dict[str, Any]] = {}
    for record_id, group_name in sorted(
        required_group_rows,
        key=lambda value: (value[0], qualification_panel.GROUP_ORDER[value[1]]),
    ):
        line_number, row = indexed[(record_id, group_name)]
        task_dir = staging_dir / "tasks" / codex_runner._task_slug(record_id) / group_name
        task_manifest, artifacts = _load_task_artifacts(
            task_dir,
            record=records[record_id],
            record_id=record_id,
            group_name=group_name,
            run_manifest=run_manifest,
            fact_catalog=fact_catalog,
        )
        verified[(record_id, group_name)] = _validate_checkpoint_receipt(
            row,
            checkpoint_line=line_number,
            task_dir=task_dir,
            task_manifest=task_manifest,
            artifacts=artifacts,
            run_manifest=run_manifest,
            record=records[record_id],
            record_id=record_id,
            group_name=group_name,
        )
    return verified


def _metric(counts: Mapping[str, int]) -> dict[str, Any]:
    tp = int(counts.get("tp", 0))
    fp = int(counts.get("fp", 0))
    fn = int(counts.get("fn", 0))
    denominator = 2 * tp + fp + fn
    return {
        **{name: int(counts.get(name, 0)) for name in ("tp", "fp", "fn", "tn", "u")},
        "positive_f1": 0.0 if denominator == 0 else 2 * tp / denominator,
    }


def _score_verified(
    targets: Mapping[tuple[str, str], Mapping[str, str]],
    verified: Mapping[tuple[str, str], Mapping[str, Mapping[str, Any]]],
    labels: Mapping[str, Mapping[str, int]],
) -> dict[str, Any]:
    totals: Counter[str] = Counter()
    by_item_counts: dict[str, Counter[str]] = {
        item: Counter() for item in annotate_groups.ITEMS
    }
    by_group_counts: dict[str, Counter[str]] = {
        name: Counter() for name, _, _ in annotate_groups.GROUPS
    }
    cells: list[dict[str, Any]] = []
    for (record_id, item), target in sorted(
        targets.items(), key=lambda value: (int(value[0][1][1:]), value[0][0])
    ):
        group_name = target["target_group"]
        decision = verified[(record_id, group_name)][item]
        predicted = decision["label"]
        gold = labels[record_id][item]
        if predicted == "U":
            outcome = "u"
        elif predicted == 1 and gold == 1:
            outcome = "tp"
        elif predicted == 1 and gold == 0:
            outcome = "fp"
        elif predicted == 0 and gold == 1:
            outcome = "fn"
        else:
            outcome = "tn"
        totals[outcome] += 1
        by_item_counts[item][outcome] += 1
        by_group_counts[group_name][outcome] += 1
        cells.append(
            {
                "id": record_id,
                "item": item,
                "target_group": group_name,
                "selection_role": target["selection_role"],
                "gold_label": gold,
                "predicted_label": predicted,
                "outcome": outcome,
                "confidence": decision["confidence"],
                "evidence": decision["evidence"],
                "evidence_locations": decision["evidence_locations"],
            }
        )
    by_item = {item: _metric(by_item_counts[item]) for item in annotate_groups.ITEMS}
    by_group = {
        name: _metric(by_group_counts[name]) for name, _, _ in annotate_groups.GROUPS
    }
    macro = mean(metric["positive_f1"] for metric in by_item.values())
    minimum = min(metric["positive_f1"] for metric in by_item.values())
    no_u = totals["u"] == 0
    passed = macro >= PANEL_MACRO_F1_MIN and minimum >= PANEL_ITEM_F1_MIN and no_u
    return {
        "counts": _metric(totals),
        "panel_macro_positive_f1": macro,
        "minimum_item_positive_f1": minimum,
        "by_item": by_item,
        "by_group": by_group,
        "no_abstentions": no_u,
        "panel_diagnostic_pass": passed,
        "cells": cells,
    }


def bridge_score(
    *,
    panel_path: pathlib.Path,
    records_path: pathlib.Path,
    labels_path: pathlib.Path,
    run_manifest_path: pathlib.Path | Sequence[pathlib.Path],
    checkpoint_path: pathlib.Path | Sequence[pathlib.Path],
) -> dict[str, Any]:
    panel_manifest = _load_json(panel_path, "panel")
    records = qualification_panel.read_records(records_path)
    _require(
        panel_manifest.get("records_sha256") == codex_runner.file_sha256(records_path),
        "panel: organizer record SHA mismatch",
    )
    targets, required_group_rows = _panel_targets(panel_manifest, records)

    # All candidate artifacts are frozen and checked before official labels are
    # parsed below.  This function never invokes or resumes the annotator.
    fact_catalog = codex_runner.catalog_facts.CatalogIndex.load()
    qualification_catalog = (
        codex_runner.qualification_context.qualification_facts.CatalogReference.load()
    )
    run_manifest_paths = _batch_paths(run_manifest_path, "run manifests")
    checkpoint_paths = _batch_paths(checkpoint_path, "checkpoints")
    _require(
        len(run_manifest_paths) == len(checkpoint_paths),
        "batch inputs: --run-manifest and --checkpoint counts must be equal",
    )

    batches: list[dict[str, Any]] = []
    reference_lineage: dict[str, Any] | None = None
    for index, (manifest_path, batch_checkpoint_path) in enumerate(
        zip(run_manifest_paths, checkpoint_paths, strict=True), 1
    ):
        run_manifest = _load_json(manifest_path, f"batch {index} run manifest")
        staging_dir = _validate_run_manifest(
            run_manifest,
            manifest_path=manifest_path,
            records_path=records_path,
            fact_catalog=fact_catalog,
            qualification_catalog=qualification_catalog,
        )
        lineage = _cross_batch_lineage(run_manifest)
        if reference_lineage is None:
            reference_lineage = lineage
        else:
            _require(
                lineage == reference_lineage,
                f"batch {index}: mixed semantic lineage/run key/configuration",
            )
        batches.append(
            {
                "index": index,
                "manifest_path": manifest_path,
                "checkpoint_path": batch_checkpoint_path,
                "run_manifest": run_manifest,
                "staging_dir": staging_dir,
            }
        )

    claimed_group_rows: set[tuple[str, str]] = set()
    for batch in batches:
        identities = _checkpoint_identities(
            batch["checkpoint_path"], required_group_rows
        )
        overlap = sorted(claimed_group_rows & identities)
        _require(
            not overlap,
            f"batch {batch['index']}: overlapping/duplicate group rows {overlap[:5]}",
        )
        claimed_group_rows.update(identities)
        batch["group_rows"] = identities
    missing = sorted(required_group_rows - claimed_group_rows)
    _require(not missing, f"batch union: missing required group rows {missing[:5]}")
    _require(
        claimed_group_rows == required_group_rows,
        "batch union: group-row coverage differs from the panel",
    )

    verified: dict[tuple[str, str], dict[str, Any]] = {}
    for batch in batches:
        batch_verified = _index_and_validate_checkpoint(
            batch["checkpoint_path"],
            required_group_rows=batch["group_rows"],
            staging_dir=batch["staging_dir"],
            run_manifest=batch["run_manifest"],
            records=records,
            fact_catalog=fact_catalog,
        )
        overlap = set(verified) & set(batch_verified)
        _require(
            not overlap,
            f"batch {batch['index']}: verified row overlap {sorted(overlap)[:5]}",
        )
        verified.update(batch_verified)

    run_manifest = batches[0]["run_manifest"]

    _require(
        panel_manifest.get("labels_sha256") == codex_runner.file_sha256(labels_path),
        "panel: official labels SHA mismatch",
    )
    labels = qualification_panel.read_labels(labels_path)
    _require(set(labels) == set(records), "official records/labels ID sets differ")
    expected_panel_manifest = qualification_panel.build_manifest(
        records_path,
        labels_path,
        positives_per_item=2,
        hard_negatives_per_item=2,
    )
    _require(
        panel_manifest == expected_panel_manifest,
        "panel: manifest is not the deterministic 2-positive/2-hard-negative official projection",
    )
    for (record_id, item), target in targets.items():
        expected = 1 if target["selection_role"] == "positive" else 0
        _require(
            labels[record_id][item] == expected,
            f"panel {record_id}:{item}: selection role disagrees with official label",
        )

    score = _score_verified(targets, verified, labels)
    batch_receipts = [
        {
            "batch_index": batch["index"],
            "run_manifest_path": str(batch["manifest_path"].resolve()),
            "run_manifest_sha256": batch["run_manifest"]["manifest_sha256"],
            "staging_dir": str(batch["staging_dir"]),
            "checkpoint_path": str(batch["checkpoint_path"].resolve()),
            "checkpoint_sha256": codex_runner.file_sha256(batch["checkpoint_path"]),
            "group_rows": len(batch["group_rows"]),
            "groups": sorted(
                {group_name for _, group_name in batch["group_rows"]},
                key=lambda name: qualification_panel.GROUP_ORDER[name],
            ),
        }
        for batch in batches
    ]
    report = {
        "schema_version": REPORT_SCHEMA,
        "purpose": "strict_posthoc_codex_cli_partial_panel_diagnostic",
        "qualified_for_gold_generation": False,
        "is_full_official_dev_evaluation": False,
        "prohibited_interpretation": (
            "Passing this selected panel does not satisfy or substitute for the full "
            "official-dev promotion gate."
        ),
        "information_boundary": {
            "model_invocations": 0,
            "labels_read_after_candidate_artifact_validation": True,
            "candidate_input_lineage": "organizer record plus independent contexts and supplied law",
            "competition_runtime_outputs_used": False,
        },
        "panel_manifest_sha256": panel_manifest["manifest_sha256"],
        "panel_file_sha256": codex_runner.file_sha256(panel_path),
        "records_sha256": panel_manifest["records_sha256"],
        "labels_sha256": panel_manifest["labels_sha256"],
        "run_key": run_manifest["run_key"],
        "run_manifest_sha256": (
            run_manifest["manifest_sha256"] if len(batches) == 1 else None
        ),
        "checkpoint_sha256": (
            codex_runner.file_sha256(checkpoint_paths[0]) if len(batches) == 1 else None
        ),
        "batch_lineages": batch_receipts,
        "candidate": {
            "model": run_manifest["semantic_config"]["model"],
            "reasoning_effort": run_manifest["semantic_config"]["reasoning_effort"],
            "cli_executable_sha256": run_manifest["codex_cli"]["executable_sha256"],
            "cli_version_output": run_manifest["codex_cli"]["version_output"],
        },
        "coverage": {
            "target_cells": len(targets),
            "unique_ids": len({record_id for record_id, _ in targets}),
            "required_group_rows": len(required_group_rows),
            "verified_group_rows": len(verified),
            "lineage_batches": len(batches),
            "missing_group_rows": 0,
            "duplicate_group_rows": 0,
            "extra_group_rows": 0,
        },
        "integrity": {
            "panel_manifest_valid": True,
            "run_manifest_valid": True,
            "semantic_lineage_identical_across_batches": True,
            "batch_group_rows_disjoint_and_complete": True,
            "task_manifests_and_artifacts_valid": True,
            "attempt_receipts_and_events_valid": True,
            "checkpoint_lineage_valid": True,
            "evidence_coordinates_valid": True,
        },
        "thresholds": {
            "panel_macro_positive_f1_min": PANEL_MACRO_F1_MIN,
            "every_item_positive_f1_min": PANEL_ITEM_F1_MIN,
            "abstentions_allowed": 0,
        },
        **score,
    }
    return report


def _write_json_immutable(path: pathlib.Path, value: Mapping[str, Any]) -> None:
    content = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if path.exists():
        _require(path.read_text(encoding="utf-8") == content, f"report already exists and differs: {path}")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--panel", type=pathlib.Path, required=True)
    parser.add_argument("--records", type=pathlib.Path, default=DEFAULT_RECORDS)
    parser.add_argument("--labels", type=pathlib.Path, default=DEFAULT_LABELS)
    parser.add_argument(
        "--run-manifest",
        type=pathlib.Path,
        action="append",
        required=True,
        help="repeat once per independently staged batch; pair by argument order",
    )
    parser.add_argument(
        "--checkpoint",
        type=pathlib.Path,
        action="append",
        required=True,
        help="repeat once per run manifest; pair by argument order",
    )
    parser.add_argument("--report", type=pathlib.Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        report = bridge_score(
            panel_path=args.panel,
            records_path=args.records,
            labels_path=args.labels,
            run_manifest_path=args.run_manifest,
            checkpoint_path=args.checkpoint,
        )
        _write_json_immutable(args.report, report)
        print(
            _canonical(
                {
                    "report": str(args.report),
                    "panel_diagnostic_pass": report["panel_diagnostic_pass"],
                    "panel_macro_positive_f1": report["panel_macro_positive_f1"],
                    "minimum_item_positive_f1": report["minimum_item_positive_f1"],
                    "counts": report["counts"],
                    "qualified_for_gold_generation": False,
                }
            )
        )
        return 0 if report["panel_diagnostic_pass"] else 1
    except (BridgeError, OSError, KeyError, TypeError, ValueError) as exc:
        print(f"Codex panel bridge failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
