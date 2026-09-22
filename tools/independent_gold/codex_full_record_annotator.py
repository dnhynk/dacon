"""Auditable one-call, 24-item Codex first-pass annotator.

This topology-B runner is independent of the competition submission runtime.
For each organizer record it constructs one lossless ``full_record_context``,
embeds the complete independent rubric, and makes at most one 24-item request
per attempt.  Candidate and verifier runs use separate frozen prompt profiles
and model families and have no peer-input channel.

Preparation is the default.  ``--execute`` is required for model calls.  Every
attempt is retained with raw JSONL events, and any observed tool/non-message
event makes the attempt invalid.  Hosted aliases remain opaque: the manifest
records the mandatory external drift-canary continuity policy and never claims
that a requested alias is an attested model revision.
"""

from __future__ import annotations

import argparse
import copy
import json
import math
import pathlib
import sys
from typing import Any, Iterable, Mapping, Sequence

try:
    from tools.independent_gold import codex_cli_annotator as base
    from tools.independent_gold import full_record_context
    from tools.independent_gold import full_record_output
    from tools.independent_gold import full_record_prompt_profiles as prompt_profiles
except ModuleNotFoundError:  # Direct execution from this directory.
    import codex_cli_annotator as base  # type: ignore[no-redef]
    import full_record_context  # type: ignore[no-redef]
    import full_record_output  # type: ignore[no-redef]
    import full_record_prompt_profiles as prompt_profiles  # type: ignore[no-redef]


ROOT = pathlib.Path(__file__).resolve().parents[2]
RUBRIC_PATH = pathlib.Path(__file__).with_name("rubric_v1.md")
GROUP_NAME = "v1-24"
TARGET_ITEMS = tuple(f"v{number}" for number in range(1, 25))
ANNOTATOR_ROLES = ("candidate", "verifier")
ANNOTATION_PHASES = base.ANNOTATION_PHASES
PASS_KIND = base.PASS_KIND

# The top-level lineage schemas intentionally remain v2-compatible with the
# grouped runner.  Topology-specific task and receipt fields are closed below.
RUNNER_SCHEMA_VERSION = base.LINEAGED_RUNNER_SCHEMA_VERSION
TASK_SCHEMA_VERSION = base.LINEAGED_TASK_SCHEMA_VERSION
RECEIPT_SCHEMA_VERSION = base.LINEAGED_RECEIPT_SCHEMA_VERSION
COHORT_PLAN_SCHEMA_VERSION = base.COHORT_PLAN_SCHEMA_VERSION
SOURCE_BUNDLE_SCHEMA_VERSION = base.SOURCE_BUNDLE_SCHEMA_VERSION
RESULT_SCHEMA_VERSION = "dacon.independent.codex_full_record_result.v2"
MAX_PROMPT_CHARS = 400_000

ROLE_MODEL = {
    "candidate": "gpt-5.6-sol",
    "verifier": "gpt-6-astra",
}

TASK_MANIFEST_KEYS = frozenset(
    {
        "schema_version",
        "run_key",
        "run_manifest_sha256",
        "record_id",
        "group",
        "target_items",
        "record_source_sha256",
        "full_record_context_sha256",
        "full_record_context_schema_version",
        "full_record_context_bounds",
        "rubric_sha256",
        "system_prompt_sha256",
        "prompt_sha256",
        "output_schema_sha256",
        "artifact_file_sha256",
        "semantic_inputs",
        "cli_execution_input",
        "threat_model_sha256",
        "cli_may_read_outside_working_directory",
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
        "manifest_sha256",
    }
)

RECEIPT_KEYS = frozenset(
    {
        "schema_version",
        "runner_receipt_schema_version",
        "status",
        "error",
        "validation_errors",
        "id",
        "group",
        "target_items",
        "run_key",
        "run_manifest_sha256",
        "task_manifest_sha256",
        "source_sha256",
        "full_record_context_sha256",
        "full_record_context_schema_version",
        "full_record_context_bounds",
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
        "ledger",
        "model_provenance",
        "event_audit",
        "usage",
        "attempt",
        "completed_utc",
        "elapsed_seconds",
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
    }
)


def _manifest_without_hash(value: Mapping[str, Any]) -> dict[str, Any]:
    return {key: child for key, child in value.items() if key != "manifest_sha256"}


def _write_json_immutable(path: pathlib.Path, value: Mapping[str, Any]) -> None:
    base._write_json_immutable(path, value)


def _lineage_projection(run_manifest: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "annotator_role": run_manifest["annotator_role"],
        "pass_kind": run_manifest["pass_kind"],
        "phase": run_manifest["phase"],
        "tuple_sha256": run_manifest["tuple_sha256"],
        "run_instance_sha256": run_manifest["run_instance_sha256"],
        "cohort_plan_sha256": run_manifest["cohort_plan"]["manifest_sha256"],
        "prompt_profile": run_manifest["prompt_lineage"]["profile"],
        "prompt_lineage_sha256": run_manifest["prompt_lineage"]["lineage_sha256"],
        "model_identity": run_manifest["model_identity"],
        "peer_visibility": run_manifest["peer_visibility"],
        "imported_source_bundle_sha256": run_manifest["imported_source_bundle"][
            "bundle_sha256"
        ],
    }


def build_cohort_plan(
    *,
    input_path: pathlib.Path,
    selection: Mapping[str, Sequence[str]],
    phase: str,
) -> dict[str, Any]:
    """Freeze one full-record row for each selected organizer ID."""

    if phase not in ANNOTATION_PHASES:
        raise ValueError(f"unknown annotation phase: {phase}")
    selected_ids = tuple(str(value) for value in selection.get("selected_ids") or ())
    requested_ids = tuple(str(value) for value in selection.get("requested_ids") or ())
    if not selected_ids or len(selected_ids) != len(set(selected_ids)):
        raise ValueError("selected cohort IDs must be non-empty and unique")
    rows = [[record_id, GROUP_NAME] for record_id in selected_ids]
    raw_partitioning = selection.get("partitioning")
    partitioning = (
        copy.deepcopy(dict(raw_partitioning))
        if isinstance(raw_partitioning, Mapping)
        else {
            "method": "organizer_order_stride_v1",
            "shard_index": 0,
            "shard_count": 1,
            "limit_after_sharding": None,
        }
    )
    plan: dict[str, Any] = {
        "schema_version": COHORT_PLAN_SCHEMA_VERSION,
        "phase": phase,
        "annotation_topology": "full_record_rows",
        "record_input_sha256": base.file_sha256(input_path),
        "input_record_count": len(tuple(selection.get("input_ids") or ())),
        "matched_record_count": len(tuple(selection.get("matched_ids") or ())),
        "requested_ids": list(requested_ids),
        "selected_ids": list(selected_ids),
        "selected_groups": [GROUP_NAME],
        "target_items_by_group": {GROUP_NAME: list(TARGET_ITEMS)},
        "selection_mode": "one_full_record_per_selected_id",
        "partitioning": partitioning,
        "selected_id_count": len(selected_ids),
        "selected_group_row_count": len(rows),
        "selected_ids_sha256": base.sha256_object(list(selected_ids)),
        "selected_group_rows_sha256": base.sha256_object(rows),
    }
    plan["manifest_sha256"] = base.sha256_object(_manifest_without_hash(plan))
    return plan


def validate_cohort_plan(
    plan: Mapping[str, Any], *, input_path: pathlib.Path, phase: str
) -> None:
    expected_keys = {
        "schema_version",
        "phase",
        "annotation_topology",
        "record_input_sha256",
        "input_record_count",
        "matched_record_count",
        "requested_ids",
        "selected_ids",
        "selected_groups",
        "target_items_by_group",
        "selection_mode",
        "partitioning",
        "selected_id_count",
        "selected_group_row_count",
        "selected_ids_sha256",
        "selected_group_rows_sha256",
        "manifest_sha256",
    }
    if set(plan) != expected_keys:
        raise ValueError("full-record cohort plan keys differ from contract")
    if plan.get("schema_version") != COHORT_PLAN_SCHEMA_VERSION:
        raise ValueError("cohort plan has the wrong schema version")
    if plan.get("phase") != phase:
        raise ValueError("cohort plan phase differs from run phase")
    if plan.get("annotation_topology") != "full_record_rows":
        raise ValueError("cohort plan is not full-record topology")
    if plan.get("selection_mode") != "one_full_record_per_selected_id":
        raise ValueError("cohort plan selection mode differs")
    if plan.get("record_input_sha256") != base.file_sha256(input_path):
        raise ValueError("cohort plan input hash differs")
    selected_ids = plan.get("selected_ids")
    if not isinstance(selected_ids, list) or not selected_ids or not all(
        isinstance(value, str) and value for value in selected_ids
    ):
        raise ValueError("cohort selected_ids must be non-empty strings")
    if len(selected_ids) != len(set(selected_ids)):
        raise ValueError("cohort selected_ids must be unique")
    requested_ids = plan.get("requested_ids")
    if not isinstance(requested_ids, list) or not all(
        isinstance(value, str) and value for value in requested_ids
    ):
        raise ValueError("cohort requested_ids must be strings")
    if len(requested_ids) != len(set(requested_ids)):
        raise ValueError("cohort requested_ids must be unique")
    for field in ("input_record_count", "matched_record_count"):
        value = plan.get(field)
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise ValueError(f"cohort {field} is invalid")
    if plan["matched_record_count"] > plan["input_record_count"]:
        raise ValueError("cohort matched count exceeds input count")
    if plan.get("selected_groups") != [GROUP_NAME]:
        raise ValueError("cohort must contain exactly v1-24")
    if plan.get("target_items_by_group") != {GROUP_NAME: list(TARGET_ITEMS)}:
        raise ValueError("cohort target items differ")
    partitioning = plan.get("partitioning")
    if not isinstance(partitioning, Mapping) or set(partitioning) != {
        "method",
        "shard_index",
        "shard_count",
        "limit_after_sharding",
    }:
        raise ValueError("cohort partitioning contract differs")
    shard_index = partitioning.get("shard_index")
    shard_count = partitioning.get("shard_count")
    limit_after = partitioning.get("limit_after_sharding")
    if partitioning.get("method") != "organizer_order_stride_v1":
        raise ValueError("cohort partitioning method differs")
    if (
        not isinstance(shard_index, int)
        or isinstance(shard_index, bool)
        or not isinstance(shard_count, int)
        or isinstance(shard_count, bool)
        or shard_count < 1
        or not 0 <= shard_index < shard_count
    ):
        raise ValueError("cohort shard coordinates are invalid")
    if limit_after is not None and (
        not isinstance(limit_after, int)
        or isinstance(limit_after, bool)
        or limit_after <= 0
    ):
        raise ValueError("cohort post-shard limit is invalid")
    rows = [[record_id, GROUP_NAME] for record_id in selected_ids]
    expected = {
        "selected_id_count": len(selected_ids),
        "selected_group_row_count": len(rows),
        "selected_ids_sha256": base.sha256_object(selected_ids),
        "selected_group_rows_sha256": base.sha256_object(rows),
    }
    for key, value in expected.items():
        if plan.get(key) != value:
            raise ValueError(f"cohort plan {key} mismatch")
    if plan.get("manifest_sha256") != base.sha256_object(
        _manifest_without_hash(plan)
    ):
        raise ValueError("cohort plan manifest hash mismatch")


def select_record_ids(
    path: pathlib.Path,
    requested: Sequence[str],
    *,
    limit: int | None,
    shard_index: int,
    shard_count: int,
) -> dict[str, Any]:
    """Select a deterministic organizer-order stride partition.

    Exact-ID filtering happens first, then the stable matched cohort is split
    by zero-based organizer-order position.  ``limit`` is applied only inside
    the selected shard, which makes smoke subsets explicit without changing
    another shard's membership.
    """

    if shard_count < 1 or not 0 <= shard_index < shard_count:
        raise ValueError("--shard-index must satisfy 0 <= index < --shard-count")
    if limit is not None and limit <= 0:
        raise ValueError("--limit must be positive")
    preflight = base.select_record_ids(path, requested, limit=None)
    matched = tuple(preflight["matched_ids"])
    sharded = matched[shard_index::shard_count]
    selected = sharded if limit is None else sharded[:limit]
    return {
        **preflight,
        "selected_ids": tuple(selected),
        "partitioning": {
            "method": "organizer_order_stride_v1",
            "shard_index": shard_index,
            "shard_count": shard_count,
            "limit_after_sharding": limit,
        },
    }


def build_prompt_lineage(
    profile: prompt_profiles.PromptProfile,
) -> dict[str, Any]:
    registry_path = pathlib.Path(prompt_profiles.__file__).resolve()
    value = {
        "profile": profile.name,
        "annotator_role": profile.annotator_role,
        "required_model_family": profile.required_model_family,
        "required_model": profile.required_model,
        "protocol_version": profile.protocol_version,
        "template_sha256": profile.template_sha256,
        "builder_source": {
            "path": base._relative_source_path(profile.module_path),
            "sha256": profile.builder_source_sha256,
        },
        "registry_source": {
            "path": base._relative_source_path(registry_path),
            "sha256": base.file_sha256(registry_path),
        },
        "renderer_signature": profile.renderer_signature,
        "derived_from_vote_lineages": [],
    }
    return {**value, "lineage_sha256": base.sha256_object(value)}


def build_imported_source_bundle(
    profile: prompt_profiles.PromptProfile,
) -> dict[str, Any]:
    paths = {
        *(path.resolve() for path in base.IMPORTED_SOURCE_PATHS),
        *(entry.module_path.resolve() for entry in prompt_profiles.PROFILES.values()),
        pathlib.Path(__file__).resolve(),
        pathlib.Path(base.__file__).resolve(),
        pathlib.Path(full_record_context.__file__).resolve(),
        pathlib.Path(full_record_output.__file__).resolve(),
        pathlib.Path(prompt_profiles.__file__).resolve(),
        profile.module_path.resolve(),
    }
    files = [
        {"path": base._relative_source_path(path), "sha256": base.file_sha256(path)}
        for path in sorted(paths, key=base._relative_source_path)
    ]
    bundle: dict[str, Any] = {
        "schema_version": SOURCE_BUNDLE_SCHEMA_VERSION,
        "files": files,
    }
    bundle["bundle_sha256"] = base.sha256_object(bundle)
    return bundle


def build_run_manifest(
    *,
    input_path: pathlib.Path,
    staging_dir: pathlib.Path,
    model: str,
    reasoning_effort: str,
    cli_provenance: Mapping[str, Any],
    rubric: str,
    fact_catalog: Any,
    qualification_catalog: Any,
    annotator_role: str,
    prompt_profile: prompt_profiles.PromptProfile,
    phase: str,
    cohort_plan: Mapping[str, Any],
    model_identity: Mapping[str, Any],
) -> dict[str, Any]:
    """Build the deterministic v2-style topology-B run lineage."""

    if annotator_role not in ANNOTATOR_ROLES:
        raise ValueError(f"unknown annotator role: {annotator_role}")
    if prompt_profile.annotator_role != annotator_role:
        raise ValueError("prompt profile role differs from annotator role")
    required_model = ROLE_MODEL[annotator_role]
    if model != required_model or prompt_profile.required_model != required_model:
        raise ValueError(
            f"{annotator_role} full-record runs require model {required_model!r}"
        )
    if not reasoning_effort.strip():
        raise ValueError("reasoning effort must be explicit")
    validate_cohort_plan(cohort_plan, input_path=input_path, phase=phase)
    if model_identity.get("requested_model") != model:
        raise ValueError("model identity differs from requested model")
    if model_identity.get("family") != prompt_profile.required_model_family:
        raise ValueError("model family differs from prompt-profile family")
    if model_identity.get("drift_canary_required") is not True:
        raise ValueError("model identity must retain the drift-canary requirement")

    output_schema = full_record_output.output_schema()
    semantic_config = {
        "model": model,
        "reasoning_effort": reasoning_effort,
        "annotation_topology": "one_call_per_record_all_24_items",
        "groups": [[GROUP_NAME, list(TARGET_ITEMS)]],
        "rubric_sha256": base.sha256_text(rubric),
        "prompt_protocol_version": prompt_profile.protocol_version,
        "prompt_profile": prompt_profile.name,
        "pass_kind": PASS_KIND,
        "source_context": {
            "schema_version": full_record_context.SCHEMA_VERSION,
            "full_supplied_source": True,
            "truncation_allowed": False,
            "max_source_chars": full_record_context.MAX_SOURCE_CHARS,
            "max_source_span_chars": full_record_context.MAX_SOURCE_SPAN_CHARS,
            "max_rendered_chars": full_record_context.MAX_RENDERED_CHARS,
            "fact_schema_version": base.fact_context.SCHEMA_VERSION,
            "qualification_schema_version": base.qualification_context.SCHEMA_VERSION,
            "law_schema_version": base.law_context.SCHEMA_VERSION,
            "fact_catalog_sha256": fact_catalog.sha256,
            "qualification_catalog_sha256": qualification_catalog.sha256,
            "law_reference_manifest_sha256": base.law_context.reference_manifest_sha256(),
        },
        "output": {
            "schema_version": full_record_output.SCHEMA_VERSION,
            "ledger_schema_version": full_record_output.LEDGER_SCHEMA_VERSION,
            "schema_sha256": base.sha256_object(output_schema),
            "id_only_registry_selection_required": True,
            "canonical_24_cell_ledger_required": True,
        },
        "prompt_max_chars": MAX_PROMPT_CHARS,
        "codex_command_template": base.command_template(model, reasoning_effort),
        "tool_event_policy": (
            "reject_every_item_type_except_agent_message_and_reasoning"
        ),
        "drift_canary_policy": {
            "required": True,
            "policy_version": "opaque-hosted-alias-continuity-v1",
            "before_qualification": True,
            "before_full_run": True,
            "predeclared_intervals_during_full_run": True,
            "after_full_run": True,
            "change_invalidates_since_last_passing_canary": True,
            "runner_does_not_claim_model_attestation": True,
        },
    }
    execution_isolation = {
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
    forbidden_inputs = [
        "competition runtime source",
        "production predictions",
        "saved model responses",
        "historical prediction artifacts",
        "organizer labels",
        "peer first-pass outputs",
    ]
    threat_model = {
        "path": "THREAT_MODEL.md",
        "sha256": base.sha256_text(base.THREAT_MODEL),
        "read_only_is_not_confidentiality_boundary": True,
        "cli_may_read_outside_working_directory": True,
        "external_container_required_for_high_assurance_secrecy": True,
    }
    prompt_lineage = build_prompt_lineage(prompt_profile)
    source_bundle = build_imported_source_bundle(prompt_profile)
    peer_visibility = base.blind_peer_visibility()
    tuple_payload = {
        "schema_version": RUNNER_SCHEMA_VERSION,
        "annotator_role": annotator_role,
        "pass_kind": PASS_KIND,
        "prompt_lineage": prompt_lineage,
        "model_identity": dict(model_identity),
        "semantic_config": semantic_config,
        "imported_source_bundle": source_bundle,
        "codex_cli": {
            "executable_sha256": cli_provenance["executable_sha256"],
            "version_output": cli_provenance["version_output"],
        },
        "execution_isolation": execution_isolation,
        "peer_visibility": peer_visibility,
    }
    tuple_sha256 = base.sha256_object(tuple_payload)
    run_instance_sha256 = base.sha256_object(
        {
            "tuple_sha256": tuple_sha256,
            "record_input_sha256": base.file_sha256(input_path),
            "phase": phase,
            "cohort_plan_sha256": cohort_plan["manifest_sha256"],
        }
    )
    manifest: dict[str, Any] = {
        "schema_version": RUNNER_SCHEMA_VERSION,
        "run_key": run_instance_sha256,
        "tuple_sha256": tuple_sha256,
        "run_instance_sha256": run_instance_sha256,
        "annotator_role": annotator_role,
        "pass_kind": PASS_KIND,
        "phase": phase,
        "cohort_plan": copy.deepcopy(dict(cohort_plan)),
        "prompt_lineage": prompt_lineage,
        "model_identity": copy.deepcopy(dict(model_identity)),
        "peer_visibility": peer_visibility,
        "imported_source_bundle": source_bundle,
        "record_input": {
            "path": str(input_path.resolve()),
            "sha256": base.file_sha256(input_path),
            "allowed_kind": input_path.name,
        },
        "staging_dir": str(staging_dir.resolve()),
        "runner_source": {
            "path": str(pathlib.Path(__file__).resolve()),
            "sha256": base.file_sha256(pathlib.Path(__file__).resolve()),
        },
        "semantic_config": semantic_config,
        "codex_cli": dict(cli_provenance),
        "execution_isolation": execution_isolation,
        "forbidden_annotation_inputs": forbidden_inputs,
        "threat_model": threat_model,
        "official_openai_docs": list(base.OFFICIAL_DOCS),
    }
    manifest["manifest_sha256"] = base.sha256_object(_manifest_without_hash(manifest))
    return manifest


def persist_run_manifest(
    staging_dir: pathlib.Path, manifest: Mapping[str, Any]
) -> None:
    staging_dir.mkdir(parents=True, exist_ok=True)
    base._write_immutable(staging_dir / "THREAT_MODEL.md", base.THREAT_MODEL)
    _write_json_immutable(staging_dir / "cohort_plan.json", manifest["cohort_plan"])
    _write_json_immutable(staging_dir / "run_manifest.json", manifest)


def build_prompt(
    *,
    profile: prompt_profiles.PromptProfile,
    rubric: str,
    context: Mapping[str, Any],
    record_id: str,
) -> str:
    rendered_context = full_record_context.render_full_record_context(context)
    messages = (
        {"role": "system", "content": rubric},
        {"role": "user", "content": rendered_context},
    )
    prompt = prompt_profiles.render_profile_prompt(
        profile,
        messages,
        record_id=record_id,
        group_name=GROUP_NAME,
        target_items=TARGET_ITEMS,
    )
    if len(prompt) > MAX_PROMPT_CHARS:
        raise ValueError(
            f"full-record prompt has {len(prompt)} chars; hard limit is "
            f"{MAX_PROMPT_CHARS}; truncation is forbidden"
        )
    return prompt


def prepare_full_record_task(
    record: Mapping[str, Any],
    *,
    rubric: str,
    staging_dir: pathlib.Path,
    run_manifest: Mapping[str, Any],
    fact_catalog: Any,
    qualification_catalog: Any,
) -> dict[str, Any]:
    """Stage exactly one complete 24-item task for ``record``."""

    record_id = str(record.get("id") or "")
    if not record_id:
        raise ValueError("record id must be non-empty")
    plan = run_manifest.get("cohort_plan") or {}
    if record_id not in plan.get("selected_ids", []):
        raise ValueError("record is outside the frozen cohort plan")
    role = str(run_manifest.get("annotator_role") or "")
    profile = prompt_profiles.get_profile(
        str((run_manifest.get("prompt_lineage") or {}).get("profile") or ""),
        annotator_role=role,
    )
    context = full_record_context.build_full_record_context(
        record,
        catalog_index=fact_catalog,
        qualification_catalog=qualification_catalog,
    )
    errors = full_record_context.validate_full_record_context(
        record, context, catalog_index=fact_catalog
    )
    if errors:
        raise ValueError(f"invalid full-record context: {errors[:5]}")
    prompt = build_prompt(
        profile=profile, rubric=rubric, context=context, record_id=record_id
    )
    output_schema = full_record_output.output_schema()
    task_dir = staging_dir / "tasks" / base._task_slug(record_id) / GROUP_NAME
    artifacts = {
        "prompt.txt": prompt,
        "output_schema.json": base.canonical_json(output_schema),
        "full_record_context.json": full_record_context.render_full_record_context(
            context
        ),
    }
    for name, content in artifacts.items():
        base._write_immutable(task_dir / name, content)

    task_manifest: dict[str, Any] = {
        "schema_version": TASK_SCHEMA_VERSION,
        "run_key": run_manifest["run_key"],
        "run_manifest_sha256": run_manifest["manifest_sha256"],
        "record_id": record_id,
        "group": GROUP_NAME,
        "target_items": list(TARGET_ITEMS),
        "record_source_sha256": base.sha256_object(record),
        "full_record_context_sha256": context["context_sha256"],
        "full_record_context_schema_version": context["schema_version"],
        "full_record_context_bounds": copy.deepcopy(context["bounds"]),
        "rubric_sha256": base.sha256_text(rubric),
        "system_prompt_sha256": base.sha256_text(rubric),
        "prompt_sha256": base.sha256_text(prompt),
        "output_schema_sha256": base.sha256_object(output_schema),
        "artifact_file_sha256": {
            name: base.sha256_text(content)
            for name, content in sorted(artifacts.items())
        },
        "semantic_inputs": ["full_record_context.json", "prompt.txt"],
        "cli_execution_input": {
            "stdin": "prompt.txt bytes",
            "filesystem": ["output_schema.json"],
            "persistent_task_directory_is_not_cli_cwd": True,
        },
        "threat_model_sha256": base.sha256_text(base.THREAT_MODEL),
        "cli_may_read_outside_working_directory": True,
        **_lineage_projection(run_manifest),
    }
    task_manifest["manifest_sha256"] = base.sha256_object(
        _manifest_without_hash(task_manifest)
    )
    if set(task_manifest) != TASK_MANIFEST_KEYS:
        raise AssertionError("internal full-record task-manifest key defect")
    _write_json_immutable(task_dir / "task_manifest.json", task_manifest)
    return {
        "task_dir": task_dir,
        "manifest": task_manifest,
        "record": record,
        "target_items": TARGET_ITEMS,
        "full_record_context": context,
        "prompt": prompt,
        "output_schema": output_schema,
    }


def _base_result(
    task: Mapping[str, Any],
    *,
    run_manifest: Mapping[str, Any],
    attempt: int,
    invocation: Mapping[str, Any],
    audit: Mapping[str, Any],
) -> dict[str, Any]:
    task_manifest = task["manifest"]
    raw_final = str(invocation.get("raw_final") or "")
    events = str(invocation.get("stdout") or "")
    stderr = str(invocation.get("stderr") or "")
    return {
        "schema_version": RESULT_SCHEMA_VERSION,
        "runner_receipt_schema_version": RECEIPT_SCHEMA_VERSION,
        "status": "error",
        "error": None,
        "validation_errors": [],
        "id": task_manifest["record_id"],
        "group": GROUP_NAME,
        "target_items": list(TARGET_ITEMS),
        "run_key": run_manifest["run_key"],
        "run_manifest_sha256": run_manifest["manifest_sha256"],
        "task_manifest_sha256": task_manifest["manifest_sha256"],
        "source_sha256": task_manifest["record_source_sha256"],
        "full_record_context_sha256": task_manifest["full_record_context_sha256"],
        "full_record_context_schema_version": task_manifest[
            "full_record_context_schema_version"
        ],
        "full_record_context_bounds": copy.deepcopy(
            task_manifest["full_record_context_bounds"]
        ),
        "rubric_sha256": task_manifest["rubric_sha256"],
        "system_prompt_sha256": task_manifest["system_prompt_sha256"],
        "prompt_sha256": task_manifest["prompt_sha256"],
        "output_schema_sha256": task_manifest["output_schema_sha256"],
        "request_sha256": base.sha256_object(
            {
                "prompt_sha256": task_manifest["prompt_sha256"],
                "output_schema_sha256": task_manifest["output_schema_sha256"],
                "command": run_manifest["semantic_config"][
                    "codex_command_template"
                ],
            }
        ),
        "raw_response_sha256": base.sha256_text(events),
        "event_stream_sha256": base.sha256_text(events),
        "stderr_sha256": base.sha256_text(stderr),
        "content_sha256": base.sha256_text(raw_final),
        "raw_final_sha256": base.sha256_text(raw_final),
        "raw_content": raw_final,
        "ledger": None,
        "model_provenance": {
            "endpoint_kind": "codex_cli_exec",
            "requested_model": run_manifest["semantic_config"]["model"],
            "declared_reasoning_effort": run_manifest["semantic_config"][
                "reasoning_effort"
            ],
            "resolved_model": None,
            "resolved_model_note": (
                "CLI event protocol does not attest a resolved alias"
            ),
            "codex_cli": run_manifest["codex_cli"],
            "command_template": run_manifest["semantic_config"][
                "codex_command_template"
            ],
            "session_ephemeral": True,
            "sandbox": "read-only",
            "ignore_user_config": True,
            "ignore_rules": True,
            "thread_ids": list(audit.get("thread_ids") or []),
        },
        "event_audit": {
            key: copy.deepcopy(audit.get(key))
            for key in (
                "event_count",
                "parse_errors",
                "unsafe_items",
                "service_errors",
                "turn_completed",
            )
        },
        "usage": dict(audit.get("usage") or {}),
        "attempt": attempt,
        "completed_utc": base.utc_now(),
        "elapsed_seconds": invocation.get("elapsed_seconds"),
        **_lineage_projection(run_manifest),
    }


def validate_invocation_result(
    task: Mapping[str, Any],
    *,
    run_manifest: Mapping[str, Any],
    attempt: int,
    invocation: Mapping[str, Any],
) -> dict[str, Any]:
    audit = base._event_audit(str(invocation.get("stdout") or ""))
    result = _base_result(
        task,
        run_manifest=run_manifest,
        attempt=attempt,
        invocation=invocation,
        audit=audit,
    )
    errors: list[str] = []
    if invocation.get("timeout_error"):
        errors.append(str(invocation["timeout_error"]))
    if invocation.get("returncode") != 0:
        errors.append(f"codex_exit_{invocation.get('returncode')}")
    if audit["parse_errors"]:
        errors.append("invalid_jsonl_event_stream")
    if audit["unsafe_items"]:
        errors.append("tool_or_nonmessage_event_observed")
    if audit["service_errors"]:
        errors.append("codex_service_error_event")
    if not audit["turn_completed"]:
        errors.append("turn_not_completed")
    if len(audit["thread_ids"]) != 1 or not audit["thread_ids"][0]:
        errors.append("thread_lineage_not_singular")

    raw_final = str(invocation.get("raw_final") or "")
    if not raw_final.strip():
        errors.append("missing_raw_final_json")
    if not audit["agent_messages"]:
        errors.append("missing_agent_message_event")
    elif raw_final.strip() != audit["agent_messages"][-1].strip():
        errors.append("raw_final_differs_from_last_agent_message")

    ledger = None
    try:
        parsed = full_record_output.parse_output(raw_final)
        normalized = full_record_output.normalize_output(parsed)
        registry = full_record_context.allowed_span_registry(
            task["full_record_context"]
        )
        full_record_output.validate_output(
            normalized, task["record"], allowed_span_registry=registry
        )
        ledger = full_record_output.canonical_ledger_projection(
            normalized, task["record"], allowed_span_registry=registry
        )
    except (TypeError, ValueError, full_record_output.FullRecordOutputError) as exc:
        errors.append(f"invalid_full_record_output: {type(exc).__name__}: {exc}")

    if errors:
        result["error"] = "; ".join(errors)
        result["validation_errors"] = errors
        result["ledger"] = ledger
    else:
        result["status"] = "ok"
        result["error"] = None
        result["validation_errors"] = []
        result["ledger"] = ledger
    if set(result) != RECEIPT_KEYS:
        raise AssertionError("internal full-record receipt key defect")
    return result


def reusable_checkpoint(
    rows: Iterable[Mapping[str, Any]],
    task: Mapping[str, Any],
    *,
    run_manifest: Mapping[str, Any],
) -> Mapping[str, Any] | None:
    manifest = task["manifest"]
    candidates = [
        row
        for row in rows
        if set(row) == RECEIPT_KEYS
        and row.get("schema_version") == RESULT_SCHEMA_VERSION
        and row.get("status") == "ok"
        and row.get("id") == manifest["record_id"]
        and row.get("group") == GROUP_NAME
        and row.get("run_key") == run_manifest["run_key"]
        and row.get("run_manifest_sha256") == run_manifest["manifest_sha256"]
        and row.get("task_manifest_sha256") == manifest["manifest_sha256"]
        and row.get("source_sha256") == manifest["record_source_sha256"]
    ]
    for row in reversed(candidates):
        raw = row.get("raw_content")
        if not isinstance(raw, str):
            continue
        if row.get("content_sha256") != base.sha256_text(raw):
            continue
        if row.get("raw_final_sha256") != base.sha256_text(raw):
            continue
        attempt = row.get("attempt")
        if not isinstance(attempt, int) or isinstance(attempt, bool) or attempt < 1:
            continue
        attempt_dir = task["task_dir"] / "attempts" / f"attempt-{attempt:03d}"
        try:
            persisted = json.loads(
                (attempt_dir / "receipt.json").read_text(encoding="utf-8")
            )
            events = (attempt_dir / "events.jsonl").read_text(encoding="utf-8")
        except (OSError, json.JSONDecodeError):
            continue
        audit = base._event_audit(events)
        if persisted != row or audit["parse_errors"] or audit["unsafe_items"]:
            continue
        if audit["service_errors"] or not audit["turn_completed"]:
            continue
        try:
            parsed = full_record_output.parse_output(raw)
            normalized = full_record_output.normalize_output(parsed)
            registry = full_record_context.allowed_span_registry(
                task["full_record_context"]
            )
            ledger = full_record_output.canonical_ledger_projection(
                normalized, task["record"], allowed_span_registry=registry
            )
        except (TypeError, ValueError, full_record_output.FullRecordOutputError):
            continue
        if ledger != row.get("ledger"):
            continue
        if any(row.get(key) != value for key, value in _lineage_projection(run_manifest).items()):
            continue
        return row
    return None


def checkpoint_offset_index(path: pathlib.Path) -> dict[str, list[int]]:
    """Index checkpoint rows by byte offset without retaining large ledgers.

    A 20,000-record receipt contains both the raw answer and canonical ledger;
    retaining every parsed row would consume memory proportional to the entire
    output file.  This preflight keeps only small integer offsets.  Malformed
    partial lines are ignored, matching the append-only resume policy.
    """

    result: dict[str, list[int]] = {}
    if not path.exists():
        return result
    with path.open("rb") as handle:
        while True:
            offset = handle.tell()
            line = handle.readline()
            if not line:
                break
            try:
                value = json.loads(line.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                continue
            if not isinstance(value, dict):
                continue
            record_id = value.get("id")
            if isinstance(record_id, (str, int)) and not isinstance(record_id, bool):
                result.setdefault(str(record_id), []).append(offset)
    return result


def checkpoint_rows_at(
    handle: Any, offsets: Sequence[int]
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if handle is None:
        return rows
    for offset in offsets:
        handle.seek(offset)
        line = handle.readline()
        try:
            value = json.loads(line.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
        if isinstance(value, dict):
            rows.append(value)
    return rows


def task_has_unsafe_or_unreadable_attempt(task_dir: pathlib.Path) -> bool:
    """Fail closed across resumes after any unsafe or unreadable event stream."""

    attempts_dir = task_dir / "attempts"
    if not attempts_dir.exists():
        return False
    for attempt_dir in sorted(child for child in attempts_dir.iterdir() if child.is_dir()):
        events_path = attempt_dir / "events.jsonl"
        try:
            events = events_path.read_text(encoding="utf-8")
        except OSError:
            return True
        audit = base._event_audit(events)
        if audit["parse_errors"] or audit["unsafe_items"]:
            return True
    return False


def run(args: argparse.Namespace) -> dict[str, Any]:
    input_path = base.require_allowed_input(args.input)
    staging_dir = base.require_safe_staging(args.staging_dir)
    checkpoint = (
        args.checkpoint.resolve()
        if args.checkpoint
        else staging_dir / "full_records.jsonl"
    )
    if any(
        base._is_relative_to(checkpoint, protected)
        for protected in (
            ROOT / "data_open",
            ROOT / "submission",
            ROOT / "pps",
            ROOT / "model",
            ROOT / "experiments",
        )
    ):
        raise ValueError("checkpoint is inside a protected input/runtime tree")
    if args.limit is not None and args.limit <= 0:
        raise ValueError("--limit must be positive")
    if args.retries <= 0:
        raise ValueError("--retries must be positive")
    if not math.isfinite(args.timeout) or args.timeout <= 0:
        raise ValueError("--timeout must be a finite positive number")

    profile = prompt_profiles.get_profile(
        args.prompt_profile, annotator_role=args.annotator_role
    )
    required_model = ROLE_MODEL[args.annotator_role]
    if args.model != required_model:
        raise ValueError(
            f"{args.annotator_role} full-record runs require --model {required_model}"
        )
    selection = select_record_ids(
        input_path,
        args.ids or (),
        limit=args.limit,
        shard_index=args.shard_index,
        shard_count=args.shard_count,
    )
    selected_ids = set(selection["selected_ids"])
    cohort_plan = build_cohort_plan(
        input_path=input_path, selection=selection, phase=args.phase
    )
    if args.cohort_plan:
        external_plan = args.cohort_plan.resolve()
        _write_json_immutable(external_plan, cohort_plan)
    model_identity = base.build_model_identity(
        model=args.model,
        mode=args.model_identity_mode,
        model_revision=args.model_revision,
        attestation_path=args.model_attestation,
    )
    rubric = RUBRIC_PATH.read_text(encoding="utf-8")
    fact_catalog = base.catalog_facts.CatalogIndex.load()
    qualification_catalog = (
        base.qualification_context.qualification_facts.CatalogReference.load()
    )
    cli_provenance = base.resolve_codex_provenance(args.codex_bin)
    run_manifest = build_run_manifest(
        input_path=input_path,
        staging_dir=staging_dir,
        model=args.model,
        reasoning_effort=args.reasoning_effort,
        cli_provenance=cli_provenance,
        rubric=rubric,
        fact_catalog=fact_catalog,
        qualification_catalog=qualification_catalog,
        annotator_role=args.annotator_role,
        prompt_profile=profile,
        phase=args.phase,
        cohort_plan=cohort_plan,
        model_identity=model_identity,
    )
    persist_run_manifest(staging_dir, run_manifest)
    checkpoint_index = checkpoint_offset_index(checkpoint)
    checkpoint_handle = checkpoint.open("rb") if checkpoint.exists() else None
    stats: dict[str, Any] = {
        "schema_version": run_manifest["schema_version"],
        "annotation_topology": "one_call_per_record_all_24_items",
        "run_key": run_manifest["run_key"],
        "run_manifest_sha256": run_manifest["manifest_sha256"],
        "execute": bool(args.execute),
        "annotator_role": args.annotator_role,
        "prompt_profile": profile.name,
        "phase": args.phase,
        "tuple_sha256": run_manifest["tuple_sha256"],
        "run_instance_sha256": run_manifest["run_instance_sha256"],
        "cohort_plan_sha256": cohort_plan["manifest_sha256"],
        "requested_ids": list(selection["requested_ids"]),
        "input_records": len(selection["input_ids"]),
        "matched_records_before_limit": len(selection["matched_ids"]),
        "records_selected_after_limit": len(selection["selected_ids"]),
        "partitioning": copy.deepcopy(selection["partitioning"]),
        "records_seen": 0,
        "tasks_prepared": 0,
        "tasks_resumed": 0,
        "calls_attempted": 0,
        "tasks_ok": 0,
        "tasks_error": 0,
        "checkpoint": str(checkpoint),
        "checkpoint_rows_indexed": sum(len(value) for value in checkpoint_index.values()),
    }
    try:
        for record in base.read_records(input_path):
            record_id = str(record["id"])
            if record_id not in selected_ids:
                continue
            stats["records_seen"] += 1
            task = prepare_full_record_task(
                record,
                rubric=rubric,
                staging_dir=staging_dir,
                run_manifest=run_manifest,
                fact_catalog=fact_catalog,
                qualification_catalog=qualification_catalog,
            )
            stats["tasks_prepared"] += 1
            if task_has_unsafe_or_unreadable_attempt(task["task_dir"]):
                stats["tasks_error"] += 1
                continue
            prior_rows = checkpoint_rows_at(
                checkpoint_handle, checkpoint_index.get(record_id, ())
            )
            if reusable_checkpoint(
                prior_rows, task, run_manifest=run_manifest
            ) is not None:
                stats["tasks_resumed"] += 1
                stats["tasks_ok"] += 1
                continue
            if not args.execute:
                continue
            final_result: Mapping[str, Any] | None = None
            for _ in range(args.retries):
                attempt = base._attempt_number(task["task_dir"])
                invocation = base.invoke_codex(
                    executable=cli_provenance["resolved_executable"],
                    model=args.model,
                    reasoning_effort=args.reasoning_effort,
                    prompt=task["prompt"],
                    output_schema=task["output_schema"],
                    timeout=args.timeout,
                )
                stats["calls_attempted"] += 1
                result = validate_invocation_result(
                    task,
                    run_manifest=run_manifest,
                    attempt=attempt,
                    invocation=invocation,
                )
                base.persist_attempt(
                    task, attempt=attempt, invocation=invocation, result=result
                )
                final_result = result
                if result["status"] == "ok":
                    break
                # A tool/non-message event is a terminal safety failure for this
                # staged task, not a transient format error retries may erase.
                if result["event_audit"]["unsafe_items"]:
                    break
            if final_result is None:
                raise AssertionError("execute mode made no attempt")
            if final_result["status"] == "ok":
                # The qualification checkpoint contains exactly one successful
                # terminal row per record. Failed attempts remain immutable in
                # attempts/ and are revalidated by the gate, but do not create
                # duplicate checkpoint identities across a later resume.
                base.append_checkpoint(checkpoint, final_result)
                stats["tasks_ok"] += 1
            else:
                stats["tasks_error"] += 1
    finally:
        if checkpoint_handle is not None:
            checkpoint_handle.close()
    if stats["records_seen"] != len(selection["selected_ids"]):
        raise ValueError("second input scan differed from the frozen cohort plan")
    if stats["tasks_prepared"] != len(selection["selected_ids"]):
        raise ValueError("full-record topology did not stage exactly one task per record")
    stats["completed_utc"] = base.utc_now()
    return stats


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=pathlib.Path, required=True)
    parser.add_argument("--staging-dir", type=pathlib.Path, required=True)
    parser.add_argument("--checkpoint", type=pathlib.Path)
    parser.add_argument("--model", required=True)
    parser.add_argument("--reasoning-effort", required=True)
    parser.add_argument("--codex-bin", default="codex")
    parser.add_argument("--annotator-role", choices=ANNOTATOR_ROLES, required=True)
    parser.add_argument(
        "--prompt-profile", choices=tuple(prompt_profiles.PROFILES), required=True
    )
    parser.add_argument("--phase", choices=ANNOTATION_PHASES, required=True)
    parser.add_argument("--cohort-plan", type=pathlib.Path)
    parser.add_argument(
        "--model-identity-mode",
        choices=("opaque_hosted_alias", "pinned_snapshot"),
        default="opaque_hosted_alias",
    )
    parser.add_argument("--model-revision")
    parser.add_argument("--model-attestation", type=pathlib.Path)
    parser.add_argument(
        "--id",
        "--record-id",
        action="append",
        dest="ids",
        help="repeatable exact organizer ID filter (use --record-id for smoke runs)",
    )
    parser.add_argument("--limit", type=int)
    parser.add_argument(
        "--shard-index",
        type=int,
        default=0,
        help="zero-based organizer-order stride partition index",
    )
    parser.add_argument(
        "--shard-count",
        type=int,
        default=1,
        help="number of deterministic organizer-order stride partitions",
    )
    parser.add_argument("--timeout", type=float, default=1800.0)
    parser.add_argument("--retries", type=int, default=1)
    parser.add_argument("--execute", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        stats = run(args)
    except (OSError, TypeError, ValueError) as exc:
        print(f"Full-record annotator failed: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(stats, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if stats["tasks_error"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "COHORT_PLAN_SCHEMA_VERSION",
    "GROUP_NAME",
    "MAX_PROMPT_CHARS",
    "RECEIPT_KEYS",
    "RECEIPT_SCHEMA_VERSION",
    "RESULT_SCHEMA_VERSION",
    "ROLE_MODEL",
    "RUNNER_SCHEMA_VERSION",
    "TASK_MANIFEST_KEYS",
    "TASK_SCHEMA_VERSION",
    "TARGET_ITEMS",
    "build_cohort_plan",
    "build_imported_source_bundle",
    "build_parser",
    "build_prompt",
    "build_prompt_lineage",
    "build_run_manifest",
    "checkpoint_offset_index",
    "checkpoint_rows_at",
    "prepare_full_record_task",
    "reusable_checkpoint",
    "run",
    "select_record_ids",
    "task_has_unsafe_or_unreadable_attempt",
    "validate_cohort_plan",
    "validate_invocation_result",
]
