"""Post-hoc qualification gate over the complete official dev cohort.

The gate never invokes a model.  It validates frozen blind-first-pass artifacts
before it opens official labels.  Physical partitions may differ only in their
run instance and bound cohort slice; tuple, role, prompt profile, and model
identity must be identical.

Supported, mutually exclusive topologies are grouped_8 (200 IDs by eight item
groups) and full_record_24 (200 IDs by one v1-24 row).  Each must project
exactly 4,800 unique cells.  A selected panel can never pass this gate.
"""

from __future__ import annotations

import argparse
import json
import math
import pathlib
import sys
from collections import Counter
from statistics import mean
from typing import Any, Mapping, Sequence

try:
    from tools.independent_gold import annotate_groups
    from tools.independent_gold import codex_cli_annotator as codex_runner
    from tools.independent_gold import codex_full_record_annotator as full_runner
    from tools.independent_gold import codex_panel_bridge as bridge
    from tools.independent_gold import full_record_context
    from tools.independent_gold import full_record_output
    from tools.independent_gold import qualification_panel
except ModuleNotFoundError:  # Direct script execution.
    import annotate_groups  # type: ignore[no-redef]
    import codex_cli_annotator as codex_runner  # type: ignore[no-redef]
    import codex_full_record_annotator as full_runner  # type: ignore[no-redef]
    import codex_panel_bridge as bridge  # type: ignore[no-redef]
    import full_record_context  # type: ignore[no-redef]
    import full_record_output  # type: ignore[no-redef]
    import qualification_panel  # type: ignore[no-redef]


ROOT = pathlib.Path(__file__).resolve().parents[2]
DEFAULT_RECORDS = ROOT / "data_open" / "dev.jsonl.gz"
DEFAULT_LABELS = ROOT / "data_open" / "dev_labels.csv"

REPORT_SCHEMA = "dacon.independent.codex_full_dev_gate.v1"
OFFICIAL_DEV_ID_COUNT = 200
OFFICIAL_DEV_CELL_COUNT = 4_800
MACRO_POSITIVE_F1_MIN = 0.90
ITEM_POSITIVE_F1_MIN = 0.70

GROUPED_TOPOLOGY = "grouped_8"
FULL_RECORD_TOPOLOGY = "full_record_24"
TOPOLOGIES = (GROUPED_TOPOLOGY, FULL_RECORD_TOPOLOGY)
FULL_RECORD_GROUP = "v1-24"
GROUPED_SELECTION_MODE = "cartesian_selected_ids_x_selected_groups"
FULL_RECORD_SELECTION_MODE = "one_full_record_per_selected_id"

RUN_V2_KEYS = bridge.RUN_MANIFEST_KEYS | {
    "tuple_sha256",
    "run_instance_sha256",
    "annotator_role",
    "pass_kind",
    "phase",
    "cohort_plan",
    "prompt_lineage",
    "model_identity",
    "peer_visibility",
    "imported_source_bundle",
}
TASK_V2_KEYS = bridge.TASK_MANIFEST_KEYS | {
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
RECEIPT_V2_KEYS = bridge.CHECKPOINT_KEYS | {
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


class FullDevGateError(ValueError):
    """A qualification artifact violated a hard invariant."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise FullDevGateError(message)


def _canonical(value: Any) -> str:
    return codex_runner.canonical_json(value)


def _is_sha256(value: Any) -> bool:
    return bridge._is_sha256(value)


def _manifest_hash(value: Mapping[str, Any]) -> str:
    return codex_runner.sha256_object(
        {key: child for key, child in value.items() if key != "manifest_sha256"}
    )


def _load_json(path: pathlib.Path, context: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise FullDevGateError(f"{context}: missing file {path}") from exc
    except json.JSONDecodeError as exc:
        raise FullDevGateError(f"{context}: invalid JSON: {exc.msg}") from exc
    _require(isinstance(value, dict), f"{context}: expected one JSON object")
    return value


def _batch_paths(
    value: pathlib.Path | Sequence[pathlib.Path], context: str
) -> list[pathlib.Path]:
    paths = [value] if isinstance(value, pathlib.Path) else list(value)
    result = [pathlib.Path(path) for path in paths]
    _require(bool(result), f"{context}: at least one path is required")
    return result


def _group_items(topology: str) -> dict[str, tuple[str, ...]]:
    if topology == GROUPED_TOPOLOGY:
        return {name: tuple(items) for name, items, _ in annotate_groups.GROUPS}
    if topology == FULL_RECORD_TOPOLOGY:
        return {FULL_RECORD_GROUP: tuple(annotate_groups.ITEMS)}
    raise FullDevGateError(f"unknown topology: {topology}")


def _master_rows(record_ids: Sequence[str], topology: str) -> set[tuple[str, str]]:
    return {
        (record_id, group_name)
        for record_id in record_ids
        for group_name in _group_items(topology)
    }


def _validate_cohort_plan(
    plan: Mapping[str, Any],
    *,
    records_path: pathlib.Path,
    organizer_ids: Sequence[str],
    topology: str,
    context: str,
) -> set[tuple[str, str]]:
    _require(
        plan.get("schema_version")
        == getattr(
            codex_runner,
            "COHORT_PLAN_SCHEMA_VERSION",
            "dacon.independent.cohort_plan.v1",
        ),
        f"{context}: wrong cohort-plan schema",
    )
    _require(plan.get("phase") == "official_dev", f"{context}: wrong phase")
    _require(
        plan.get("record_input_sha256") == codex_runner.file_sha256(records_path),
        f"{context}: organizer input hash mismatch",
    )
    _require(
        plan.get("manifest_sha256") == _manifest_hash(plan),
        f"{context}: cohort-plan hash mismatch",
    )
    _require(
        plan.get("input_record_count") == OFFICIAL_DEV_ID_COUNT,
        f"{context}: input cohort is not exactly 200 records",
    )
    selected_ids = plan.get("selected_ids")
    selected_groups = plan.get("selected_groups")
    _require(isinstance(selected_ids, list), f"{context}: selected_ids is not a list")
    _require(
        isinstance(selected_groups, list),
        f"{context}: selected_groups is not a list",
    )
    ids = [str(value) for value in selected_ids]
    groups = [str(value) for value in selected_groups]
    _require(ids and len(ids) == len(set(ids)), f"{context}: duplicate/empty IDs")
    _require(
        groups and len(groups) == len(set(groups)),
        f"{context}: duplicate/empty groups",
    )
    organizer_set = set(organizer_ids)
    _require(not (set(ids) - organizer_set), f"{context}: out-of-cohort ID")
    order = {record_id: index for index, record_id in enumerate(organizer_ids)}
    _require(
        ids == sorted(ids, key=order.__getitem__),
        f"{context}: IDs do not preserve organizer order",
    )
    expected_groups = _group_items(topology)
    if topology == GROUPED_TOPOLOGY:
        _require(
            plan.get("annotation_topology") == "group_rows",
            f"{context}: grouped annotation topology mismatch",
        )
        _require(
            plan.get("selection_mode") == GROUPED_SELECTION_MODE,
            f"{context}: grouped selection mode mismatch",
        )
        _require(set(groups) <= set(expected_groups), f"{context}: unknown group")
        try:
            codex_runner.validate_cohort_plan(
                plan, input_path=records_path, phase="official_dev"
            )
        except (TypeError, ValueError) as exc:
            raise FullDevGateError(f"{context}: invalid runner cohort plan") from exc
    else:
        _require(
            plan.get("annotation_topology") == "full_record_rows",
            f"{context}: full-record annotation topology mismatch",
        )
        _require(
            plan.get("selection_mode") == FULL_RECORD_SELECTION_MODE,
            f"{context}: full-record selection mode mismatch",
        )
        _require(
            groups == [FULL_RECORD_GROUP],
            f"{context}: full-record plan must select only {FULL_RECORD_GROUP}",
        )
        partitioning = plan.get("partitioning")
        _require(
            isinstance(partitioning, Mapping)
            and partitioning.get("method") == "organizer_order_stride_v1",
            f"{context}: full-record partitioning policy mismatch",
        )
        shard_index = partitioning.get("shard_index")
        shard_count = partitioning.get("shard_count")
        limit_after = partitioning.get("limit_after_sharding")
        _require(
            isinstance(shard_index, int)
            and not isinstance(shard_index, bool)
            and isinstance(shard_count, int)
            and not isinstance(shard_count, bool)
            and shard_count >= 1
            and 0 <= shard_index < shard_count,
            f"{context}: invalid full-record shard coordinates",
        )
        _require(
            limit_after is None
            or (
                isinstance(limit_after, int)
                and not isinstance(limit_after, bool)
                and limit_after > 0
            ),
            f"{context}: invalid full-record post-shard limit",
        )
        requested = plan.get("requested_ids")
        _require(
            isinstance(requested, list)
            and all(isinstance(value, str) and value for value in requested)
            and len(requested) == len(set(requested)),
            f"{context}: invalid requested-ID filter",
        )
        requested_set = set(requested)
        _require(
            requested_set <= organizer_set,
            f"{context}: requested-ID filter contains out-of-cohort ID",
        )
        matched_ids = [
            record_id
            for record_id in organizer_ids
            if not requested_set or record_id in requested_set
        ]
        expected_partition = matched_ids[shard_index::shard_count]
        if limit_after is not None:
            expected_partition = expected_partition[:limit_after]
        _require(
            ids == expected_partition,
            f"{context}: selected IDs do not match deterministic shard",
        )
        _require(
            plan.get("matched_record_count") == len(matched_ids),
            f"{context}: matched record count mismatch",
        )
    _require(
        plan.get("target_items_by_group")
        == {group: list(expected_groups[group]) for group in groups},
        f"{context}: target-items-by-group mismatch",
    )
    rows = [(record_id, group_name) for record_id in ids for group_name in groups]
    _require(plan.get("selected_id_count") == len(ids), f"{context}: ID count mismatch")
    _require(
        plan.get("selected_group_row_count") == len(rows),
        f"{context}: row count mismatch",
    )
    _require(
        plan.get("selected_ids_sha256") == codex_runner.sha256_object(ids),
        f"{context}: selected-ID hash mismatch",
    )
    _require(
        plan.get("selected_group_rows_sha256")
        == codex_runner.sha256_object([[record_id, group] for record_id, group in rows]),
        f"{context}: selected-row hash mismatch",
    )
    return set(rows)


def _validate_source_bundle(bundle: Mapping[str, Any], context: str) -> None:
    _require(
        bundle.get("schema_version")
        == getattr(
            codex_runner,
            "SOURCE_BUNDLE_SCHEMA_VERSION",
            "dacon.independent.source_bundle.v1",
        ),
        f"{context}: wrong source-bundle schema",
    )
    files = bundle.get("files")
    _require(isinstance(files, list) and files, f"{context}: empty source bundle")
    seen: set[str] = set()
    for index, row in enumerate(files):
        _require(isinstance(row, Mapping), f"{context}: invalid source row {index}")
        path_text = str(row.get("path") or "")
        _require(path_text and path_text not in seen, f"{context}: duplicate source path")
        seen.add(path_text)
        source_path = (ROOT / pathlib.PurePosixPath(path_text)).resolve()
        try:
            source_path.relative_to(ROOT.resolve())
        except ValueError as exc:
            raise FullDevGateError(f"{context}: source path escapes repository") from exc
        _require(source_path.is_file(), f"{context}: missing source {path_text}")
        _require(
            row.get("sha256") == codex_runner.file_sha256(source_path),
            f"{context}: source hash mismatch for {path_text}",
        )
    payload = {key: value for key, value in bundle.items() if key != "bundle_sha256"}
    _require(
        bundle.get("bundle_sha256") == codex_runner.sha256_object(payload),
        f"{context}: bundle hash mismatch",
    )


def _validate_run_manifest(
    manifest: Mapping[str, Any],
    *,
    manifest_path: pathlib.Path,
    records_path: pathlib.Path,
    organizer_ids: Sequence[str],
    topology: str,
    context: str,
) -> tuple[pathlib.Path, set[tuple[str, str]], dict[str, Any]]:
    """Validate one v2 blind-run header without reading official labels."""

    topology_runner = (
        full_runner if topology == FULL_RECORD_TOPOLOGY else codex_runner
    )

    _require(
        set(manifest) == RUN_V2_KEYS,
        f"{context}: run-manifest keys differ; missing={sorted(RUN_V2_KEYS - set(manifest))}, "
        f"extra={sorted(set(manifest) - RUN_V2_KEYS)}",
    )
    _require(
        manifest.get("schema_version")
        == getattr(
            codex_runner,
            "LINEAGED_RUNNER_SCHEMA_VERSION",
            "dacon.independent.codex_cli_runner.v2",
        ),
        f"{context}: only lineaged runner v2 artifacts can qualify",
    )
    _require(
        manifest.get("manifest_sha256") == _manifest_hash(manifest),
        f"{context}: run manifest hash mismatch",
    )
    _require(
        manifest.get("annotator_role") in {"candidate", "verifier"},
        f"{context}: unsupported annotator role",
    )
    _require(
        manifest.get("pass_kind") == "blind_first_pass",
        f"{context}: pass is not blind_first_pass",
    )
    _require(manifest.get("phase") == "official_dev", f"{context}: wrong phase")
    for name in ("tuple_sha256", "run_instance_sha256", "run_key"):
        _require(_is_sha256(manifest.get(name)), f"{context}: invalid {name}")
    _require(
        manifest.get("run_key") == manifest.get("run_instance_sha256"),
        f"{context}: run key/run-instance mismatch",
    )

    staging_dir = pathlib.Path(str(manifest.get("staging_dir") or "")).resolve()
    _require(
        manifest_path.resolve() == staging_dir / "run_manifest.json",
        f"{context}: manifest path differs from staged run_manifest.json",
    )
    record_input = manifest.get("record_input")
    _require(isinstance(record_input, Mapping), f"{context}: invalid record_input")
    _require(
        pathlib.Path(str(record_input.get("path") or "")).resolve()
        == records_path.resolve(),
        f"{context}: organizer record path mismatch",
    )
    records_sha = codex_runner.file_sha256(records_path)
    _require(record_input.get("sha256") == records_sha, f"{context}: record hash mismatch")
    _require(
        record_input.get("allowed_kind") == records_path.name,
        f"{context}: record kind mismatch",
    )

    runner_source = manifest.get("runner_source")
    _require(isinstance(runner_source, Mapping), f"{context}: invalid runner source")
    runner_path = pathlib.Path(topology_runner.__file__).resolve()
    _require(
        pathlib.Path(str(runner_source.get("path") or "")).resolve() == runner_path,
        f"{context}: runner source path mismatch",
    )
    _require(
        runner_source.get("sha256") == codex_runner.file_sha256(runner_path),
        f"{context}: runner source hash mismatch",
    )

    plan = manifest.get("cohort_plan")
    _require(isinstance(plan, Mapping), f"{context}: missing embedded cohort plan")
    partition_rows = _validate_cohort_plan(
        plan,
        records_path=records_path,
        organizer_ids=organizer_ids,
        topology=topology,
        context=f"{context} cohort plan",
    )
    config = manifest.get("semantic_config")
    _require(isinstance(config, Mapping), f"{context}: invalid semantic config")
    _require(
        config.get("pass_kind") == "blind_first_pass",
        f"{context}: semantic pass kind mismatch",
    )
    _require(
        config.get("tool_event_policy")
        == "reject_every_item_type_except_agent_message_and_reasoning",
        f"{context}: unsafe tool-event policy",
    )
    profile_name = str(config.get("prompt_profile") or "")
    prompt_lineage = manifest.get("prompt_lineage")
    _require(isinstance(prompt_lineage, Mapping), f"{context}: missing prompt lineage")
    _require(
        profile_name and prompt_lineage.get("profile") == profile_name,
        f"{context}: prompt profile mismatch",
    )
    _require(
        prompt_lineage.get("annotator_role") == manifest.get("annotator_role"),
        f"{context}: prompt role mismatch",
    )
    lineage_payload = {
        key: value for key, value in prompt_lineage.items() if key != "lineage_sha256"
    }
    _require(
        prompt_lineage.get("lineage_sha256")
        == codex_runner.sha256_object(lineage_payload),
        f"{context}: prompt lineage hash mismatch",
    )
    _require(
        prompt_lineage.get("derived_from_vote_lineages") == [],
        f"{context}: first pass derives from peer votes",
    )
    try:
        profile = topology_runner.prompt_profiles.get_profile(
            profile_name, annotator_role=str(manifest.get("annotator_role") or "")
        )
    except (KeyError, ValueError) as exc:
        raise FullDevGateError(f"{context}: unknown/incompatible prompt profile") from exc
    _require(
        prompt_lineage == topology_runner.build_prompt_lineage(profile),
        f"{context}: prompt lineage differs from the reviewed profile",
    )
    peer = manifest.get("peer_visibility")
    _require(
        peer == codex_runner.blind_peer_visibility(),
        f"{context}: peer output or identity was visible",
    )

    model_identity = manifest.get("model_identity")
    _require(isinstance(model_identity, Mapping), f"{context}: missing model identity")
    _require(
        model_identity.get("requested_model") == config.get("model"),
        f"{context}: model identity/request mismatch",
    )
    _require(
        isinstance(model_identity.get("family"), str)
        and bool(str(model_identity.get("family")).strip()),
        f"{context}: missing model-family identity",
    )
    attestation = model_identity.get("attestation")
    attestation_path = None
    if attestation is not None:
        _require(isinstance(attestation, Mapping), f"{context}: invalid attestation")
        attestation_path = pathlib.Path(str(attestation.get("path") or ""))
    try:
        expected_model_identity = codex_runner.build_model_identity(
            model=str(config.get("model") or ""),
            mode=str(model_identity.get("mode") or ""),
            model_revision=model_identity.get("requested_revision"),
            attestation_path=attestation_path,
        )
    except (OSError, TypeError, ValueError) as exc:
        raise FullDevGateError(f"{context}: invalid model identity") from exc
    _require(
        model_identity == expected_model_identity,
        f"{context}: model identity differs from runner identity contract",
    )
    _require(
        model_identity.get("family") == prompt_lineage.get("required_model_family"),
        f"{context}: model family is incompatible with prompt profile",
    )
    source_bundle = manifest.get("imported_source_bundle")
    _require(isinstance(source_bundle, Mapping), f"{context}: missing source bundle")
    _validate_source_bundle(source_bundle, f"{context} source bundle")
    _require(
        source_bundle == topology_runner.build_imported_source_bundle(profile),
        f"{context}: imported source bundle differs from reviewed profile sources",
    )

    cli = manifest.get("codex_cli")
    _require(isinstance(cli, Mapping), f"{context}: missing CLI identity")
    executable = pathlib.Path(str(cli.get("resolved_executable") or "")).resolve()
    _require(executable.is_file(), f"{context}: resolved CLI executable is unavailable")
    _require(
        cli.get("executable_sha256") == codex_runner.file_sha256(executable),
        f"{context}: CLI executable hash mismatch",
    )
    _require(
        isinstance(cli.get("version_output"), str) and bool(cli.get("version_output")),
        f"{context}: missing CLI version identity",
    )
    isolation = manifest.get("execution_isolation")
    _require(
        isinstance(isolation, Mapping)
        and isolation.get("persistent_stage_is_cli_cwd") is False
        and isolation.get("stdin_semantic_payload_only") is True
        and isolation.get("shell") is False
        and isolation.get("session_ephemeral") is True
        and isolation.get("sandbox") == "read-only"
        and isolation.get("ignore_user_config") is True
        and isolation.get("ignore_rules") is True
        and isolation.get("approval_policy_override") == "never"
        and isolation.get("raw_jsonl_event_audit") is True,
        f"{context}: execution isolation mismatch",
    )
    forbidden = manifest.get("forbidden_annotation_inputs")
    _require(
        isinstance(forbidden, list)
        and "organizer labels" in forbidden
        and "production predictions" in forbidden
        and "competition runtime source" in forbidden,
        f"{context}: forbidden-input boundary is incomplete",
    )
    threat = manifest.get("threat_model")
    _require(isinstance(threat, Mapping), f"{context}: missing threat model")
    threat_path = staging_dir / str(threat.get("path") or "")
    _require(threat_path.is_file(), f"{context}: missing staged threat model")
    _require(
        threat.get("sha256") == codex_runner.sha256_text(codex_runner.THREAT_MODEL)
        and threat_path.read_text(encoding="utf-8") == codex_runner.THREAT_MODEL,
        f"{context}: threat model mismatch",
    )

    tuple_payload = {
        "schema_version": manifest["schema_version"],
        "annotator_role": manifest["annotator_role"],
        "pass_kind": manifest["pass_kind"],
        "prompt_lineage": prompt_lineage,
        "model_identity": model_identity,
        "semantic_config": config,
        "imported_source_bundle": source_bundle,
        "codex_cli": {
            "executable_sha256": cli.get("executable_sha256"),
            "version_output": cli.get("version_output"),
        },
        "execution_isolation": isolation,
        "peer_visibility": peer,
    }
    expected_tuple = codex_runner.sha256_object(tuple_payload)
    _require(
        manifest.get("tuple_sha256") == expected_tuple,
        f"{context}: frozen tuple hash mismatch",
    )
    expected_instance = codex_runner.sha256_object(
        {
            "tuple_sha256": expected_tuple,
            "record_input_sha256": records_sha,
            "phase": "official_dev",
            "cohort_plan_sha256": plan["manifest_sha256"],
        }
    )
    _require(
        manifest.get("run_instance_sha256") == expected_instance,
        f"{context}: run-instance hash mismatch",
    )
    if topology == GROUPED_TOPOLOGY:
        source_mode = (config.get("source_packet") or {}).get("mode")
        _require(
            source_mode in {"bounded_retrieval", "full_source"},
            f"{context}: unsupported source-packet mode",
        )
        try:
            rebuilt = codex_runner.build_run_manifest(
                input_path=records_path,
                staging_dir=staging_dir,
                model=str(config.get("model") or ""),
                reasoning_effort=str(config.get("reasoning_effort") or ""),
                cli_provenance=cli,
                rubric=codex_runner.RUBRIC_PATH.read_text(encoding="utf-8"),
                fact_catalog=codex_runner.catalog_facts.CatalogIndex.load(),
                qualification_catalog=(
                    codex_runner.qualification_context.qualification_facts.CatalogReference.load()
                ),
                full_source=source_mode == "full_source",
                annotator_role=str(manifest["annotator_role"]),
                prompt_profile=profile,
                phase="official_dev",
                cohort_plan=plan,
                model_identity=model_identity,
            )
        except (OSError, TypeError, ValueError) as exc:
            raise FullDevGateError(f"{context}: cannot rebuild run manifest") from exc
        _require(
            manifest == rebuilt,
            f"{context}: run manifest is not the deterministic reviewed-runner output",
        )
    else:
        try:
            full_runner.validate_cohort_plan(
                plan, input_path=records_path, phase="official_dev"
            )
            rebuilt = full_runner.build_run_manifest(
                input_path=records_path,
                staging_dir=staging_dir,
                model=str(config.get("model") or ""),
                reasoning_effort=str(config.get("reasoning_effort") or ""),
                cli_provenance=cli,
                rubric=full_runner.RUBRIC_PATH.read_text(encoding="utf-8"),
                fact_catalog=codex_runner.catalog_facts.CatalogIndex.load(),
                qualification_catalog=(
                    codex_runner.qualification_context.qualification_facts.CatalogReference.load()
                ),
                annotator_role=str(manifest["annotator_role"]),
                prompt_profile=profile,
                phase="official_dev",
                cohort_plan=plan,
                model_identity=model_identity,
            )
        except (OSError, TypeError, ValueError) as exc:
            raise FullDevGateError(
                f"{context}: cannot rebuild full-record run manifest"
            ) from exc
        _require(
            manifest == rebuilt,
            f"{context}: run manifest is not deterministic full-record runner output",
        )
    identity = {
        "tuple_sha256": expected_tuple,
        "annotator_role": manifest["annotator_role"],
        "prompt_profile": profile_name,
        "prompt_lineage_sha256": prompt_lineage["lineage_sha256"],
        "model_identity": model_identity,
    }
    return staging_dir, partition_rows, identity


def _read_checkpoint_identities(
    path: pathlib.Path,
    *,
    allowed_rows: set[tuple[str, str]],
    item_map: Mapping[str, tuple[str, ...]],
    context: str,
) -> tuple[set[tuple[str, str]], dict[tuple[str, str], tuple[int, dict[str, Any]]]]:
    indexed: dict[tuple[str, str], tuple[int, dict[str, Any]]] = {}
    for raw in bridge._read_checkpoint(path):
        line_number = int(raw.pop("__checkpoint_line__"))
        key = (str(raw.get("id") or ""), str(raw.get("group") or ""))
        _require(
            key in allowed_rows,
            f"{context} line {line_number}: extra/out-of-cohort row {key}",
        )
        _require(key not in indexed, f"{context} line {line_number}: duplicate row {key}")
        _require(
            raw.get("target_items") == list(item_map[key[1]]),
            f"{context} line {line_number}: target-item topology mismatch",
        )
        indexed[key] = (line_number, raw)
    missing = sorted(allowed_rows - set(indexed))
    _require(not missing, f"{context}: missing planned rows {missing[:5]}")
    _require(set(indexed) == allowed_rows, f"{context}: checkpoint/plan mismatch")
    return set(indexed), indexed


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


def _task_artifacts(
    task_dir: pathlib.Path,
    *,
    task_manifest: Mapping[str, Any],
    context: str,
) -> dict[str, Any]:
    hashes = task_manifest.get("artifact_file_sha256")
    _require(isinstance(hashes, Mapping), f"{context}: invalid artifact registry")
    group_name = str(task_manifest.get("group") or "")
    if group_name == FULL_RECORD_GROUP:
        expected = {
            "prompt.txt",
            "output_schema.json",
            "full_record_context.json",
        }
        _require(set(hashes) == expected, f"{context}: artifact set mismatch")
        for name, digest in hashes.items():
            artifact = task_dir / name
            _require(artifact.is_file(), f"{context}: missing {name}")
            _require(_is_sha256(digest), f"{context}: invalid hash for {name}")
            _require(
                codex_runner.file_sha256(artifact) == digest,
                f"{context}: artifact hash mismatch for {name}",
            )
        return {
            "prompt": (task_dir / "prompt.txt").read_text(encoding="utf-8"),
            "output_schema": _load_json(
                task_dir / "output_schema.json", context + " schema"
            ),
            "full_record_context": _load_json(
                task_dir / "full_record_context.json", context + " full context"
            ),
        }
    required = {
        "prompt.txt",
        "output_schema.json",
        "packet.json",
        "fact_context.json",
        "law_context.json",
    }
    needs_qualification = (
        group_name in codex_runner.qualification_context.GROUP_FAMILIES
    )
    expected = required | ({"qualification_context.json"} if needs_qualification else set())
    _require(set(hashes) == expected, f"{context}: artifact set mismatch")
    for name, digest in hashes.items():
        artifact = task_dir / name
        _require(artifact.is_file(), f"{context}: missing {name}")
        _require(_is_sha256(digest), f"{context}: invalid hash for {name}")
        _require(
            codex_runner.file_sha256(artifact) == digest,
            f"{context}: artifact hash mismatch for {name}",
        )
    return {
        "prompt": (task_dir / "prompt.txt").read_text(encoding="utf-8"),
        "output_schema": _load_json(task_dir / "output_schema.json", context + " schema"),
        "packet": _load_json(task_dir / "packet.json", context + " packet"),
        "fact": _load_json(task_dir / "fact_context.json", context + " facts"),
        "law": _load_json(task_dir / "law_context.json", context + " law"),
        "qualification": (
            _load_json(
                task_dir / "qualification_context.json",
                context + " qualification",
            )
            if "qualification_context.json" in hashes
            else None
        ),
    }


def _validate_full_record_task_manifest(
    task_manifest: Mapping[str, Any],
    *,
    run_manifest: Mapping[str, Any],
    record: Mapping[str, Any],
    record_id: str,
    target_items: tuple[str, ...],
    task_dir: pathlib.Path,
    context: str,
    fact_catalog: Any,
) -> dict[str, Any]:
    """Rebuild every semantic topology-B artifact from organizer inputs."""

    _require(
        set(task_manifest) == full_runner.TASK_MANIFEST_KEYS,
        f"{context}: full-record task-manifest keys differ",
    )
    _require(
        task_manifest.get("schema_version") == full_runner.TASK_SCHEMA_VERSION,
        f"{context}: wrong full-record task schema",
    )
    _require(
        task_manifest.get("manifest_sha256") == _manifest_hash(task_manifest),
        f"{context}: task manifest hash mismatch",
    )
    _require(
        task_manifest.get("run_key") == run_manifest["run_key"]
        and task_manifest.get("run_manifest_sha256")
        == run_manifest["manifest_sha256"],
        f"{context}: task/run lineage mismatch",
    )
    _require(
        task_manifest.get("record_id") == record_id
        and task_manifest.get("group") == FULL_RECORD_GROUP
        and task_manifest.get("target_items") == list(target_items),
        f"{context}: task identity mismatch",
    )
    _require(
        task_manifest.get("record_source_sha256")
        == codex_runner.sha256_object(record),
        f"{context}: source hash mismatch",
    )
    for field, expected in _lineage_projection(run_manifest).items():
        _require(task_manifest.get(field) == expected, f"{context}: {field} mismatch")

    artifacts = _task_artifacts(
        task_dir, task_manifest=task_manifest, context=context
    )
    full_context = artifacts["full_record_context"]
    context_errors = full_record_context.validate_full_record_context(
        record, full_context, catalog_index=fact_catalog
    )
    _require(
        not context_errors,
        f"{context}: full-record context errors {context_errors[:5]}",
    )
    qualification_catalog = (
        codex_runner.qualification_context.qualification_facts.CatalogReference.load()
    )
    expected_context = full_record_context.build_full_record_context(
        record,
        catalog_index=fact_catalog,
        qualification_catalog=qualification_catalog,
    )
    _require(
        full_context == expected_context,
        f"{context}: full-record context differs from reviewed builders",
    )
    rubric = full_runner.RUBRIC_PATH.read_text(encoding="utf-8")
    profile = full_runner.prompt_profiles.get_profile(
        run_manifest["prompt_lineage"]["profile"],
        annotator_role=run_manifest["annotator_role"],
    )
    expected_prompt = full_runner.build_prompt(
        profile=profile,
        rubric=rubric,
        context=expected_context,
        record_id=record_id,
    )
    expected_schema = full_record_output.output_schema()
    _require(
        artifacts["prompt"] == expected_prompt,
        f"{context}: full-record prompt mismatch",
    )
    _require(
        artifacts["output_schema"] == expected_schema,
        f"{context}: full-record output schema mismatch",
    )
    expected_hashes = {
        "full_record_context_sha256": expected_context["context_sha256"],
        "full_record_context_schema_version": expected_context["schema_version"],
        "full_record_context_bounds": expected_context["bounds"],
        "rubric_sha256": codex_runner.sha256_text(rubric),
        "system_prompt_sha256": codex_runner.sha256_text(rubric),
        "prompt_sha256": codex_runner.sha256_text(expected_prompt),
        "output_schema_sha256": codex_runner.sha256_object(expected_schema),
    }
    for field, expected in expected_hashes.items():
        _require(task_manifest.get(field) == expected, f"{context}: {field} mismatch")
    _require(
        task_manifest.get("semantic_inputs")
        == ["full_record_context.json", "prompt.txt"],
        f"{context}: semantic input boundary mismatch",
    )
    _require(
        task_manifest.get("cli_execution_input")
        == {
            "stdin": "prompt.txt bytes",
            "filesystem": ["output_schema.json"],
            "persistent_task_directory_is_not_cli_cwd": True,
        },
        f"{context}: CLI input boundary mismatch",
    )
    _require(
        task_manifest.get("threat_model_sha256")
        == codex_runner.sha256_text(codex_runner.THREAT_MODEL)
        and task_manifest.get("cli_may_read_outside_working_directory") is True,
        f"{context}: task threat-model mismatch",
    )
    return artifacts


def _validate_task_manifest(
    task_manifest: Mapping[str, Any],
    *,
    run_manifest: Mapping[str, Any],
    record: Mapping[str, Any],
    record_id: str,
    group_name: str,
    target_items: tuple[str, ...],
    task_dir: pathlib.Path,
    context: str,
    fact_catalog: Any = None,
) -> dict[str, Any]:
    if group_name == FULL_RECORD_GROUP:
        return _validate_full_record_task_manifest(
            task_manifest,
            run_manifest=run_manifest,
            record=record,
            record_id=record_id,
            target_items=target_items,
            task_dir=task_dir,
            context=context,
            fact_catalog=(
                fact_catalog or codex_runner.catalog_facts.CatalogIndex.load()
            ),
        )
    _require(
        set(task_manifest) == TASK_V2_KEYS,
        f"{context}: task-manifest keys differ",
    )
    _require(
        task_manifest.get("schema_version")
        == getattr(
            codex_runner,
            "LINEAGED_TASK_SCHEMA_VERSION",
            "dacon.independent.codex_cli_task.v2",
        ),
        f"{context}: wrong task schema",
    )
    _require(
        task_manifest.get("manifest_sha256") == _manifest_hash(task_manifest),
        f"{context}: task manifest hash mismatch",
    )
    _require(
        task_manifest.get("run_key") == run_manifest["run_key"]
        and task_manifest.get("run_manifest_sha256") == run_manifest["manifest_sha256"],
        f"{context}: task/run lineage mismatch",
    )
    _require(
        task_manifest.get("record_id") == record_id
        and task_manifest.get("group") == group_name
        and task_manifest.get("target_items") == list(target_items),
        f"{context}: task identity mismatch",
    )
    _require(
        task_manifest.get("record_source_sha256")
        == codex_runner.sha256_object(record),
        f"{context}: source hash mismatch",
    )
    for field, expected in _lineage_projection(run_manifest).items():
        _require(task_manifest.get(field) == expected, f"{context}: {field} mismatch")

    artifacts = _task_artifacts(task_dir, task_manifest=task_manifest, context=context)
    rubric = codex_runner.RUBRIC_PATH.read_text(encoding="utf-8")
    expected_hashes = {
        "packet_sha256": codex_runner.sha256_object(artifacts["packet"]),
        "fact_context_sha256": artifacts["fact"].get("context_sha256"),
        "qualification_context_sha256": (
            artifacts["qualification"].get("context_sha256")
            if artifacts["qualification"] is not None
            else None
        ),
        "law_context_sha256": artifacts["law"].get("context_sha256"),
        "rubric_sha256": codex_runner.sha256_text(rubric),
        "prompt_sha256": codex_runner.sha256_text(artifacts["prompt"]),
        "output_schema_sha256": codex_runner.sha256_object(artifacts["output_schema"]),
    }
    for field, expected in expected_hashes.items():
        _require(task_manifest.get(field) == expected, f"{context}: {field} mismatch")

    # The current lineaged runner implements grouped_8.  Rebuild every
    # semantic artifact for that topology; full_record_24 retains the same
    # receipt/evidence checks and has a separately versioned future runner.
    if group_name in annotate_groups.GROUP_BY_NAME:
        packet_budget = annotate_groups.GROUP_BY_NAME[group_name][1]
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
        _require(artifacts["packet"] == expected_packet, f"{context}: packet mismatch")
        fact_errors = codex_runner.fact_context.validate_fact_context(
            record, artifacts["fact"], catalog_index=fact_catalog
        )
        _require(not fact_errors, f"{context}: fact-context errors {fact_errors[:3]}")
        if artifacts["qualification"] is not None:
            qualification_errors = (
                codex_runner.qualification_context.validate_qualification_context(
                    record, artifacts["qualification"]
                )
            )
            _require(
                not qualification_errors,
                f"{context}: qualification-context errors {qualification_errors[:3]}",
            )
        law_errors = codex_runner.law_context.validate_law_context(artifacts["law"])
        _require(not law_errors, f"{context}: law-context errors {law_errors[:3]}")
        expected_law = codex_runner.law_context.build_law_context(
            group_name, max_chars=codex_runner.law_context.DEFAULT_MAX_CHARS
        )
        _require(artifacts["law"] == expected_law, f"{context}: law context mismatch")
        group_rubric = annotate_groups.extract_group_rubric(rubric, target_items)
        _require(
            task_manifest.get("group_rubric_sha256")
            == codex_runner.sha256_text(group_rubric),
            f"{context}: group rubric mismatch",
        )
        messages = annotate_groups.build_group_messages(
            record,
            artifacts["packet"],
            group_rubric,
            group_name,
            fact_context_payload=artifacts["fact"],
            qualification_context_payload=artifacts["qualification"],
            law_context_payload=artifacts["law"],
        )
        profile = codex_runner.prompt_profiles.get_profile(
            run_manifest["prompt_lineage"]["profile"],
            annotator_role=run_manifest["annotator_role"],
        )
        expected_prompt = codex_runner.build_profiled_prompt(
            messages,
            profile=profile,
            record_id=record_id,
            group_name=group_name,
            target_items=target_items,
        )
        _require(artifacts["prompt"] == expected_prompt, f"{context}: prompt mismatch")
        _require(
            artifacts["output_schema"] == codex_runner.group_output_schema(target_items),
            f"{context}: output schema mismatch",
        )
    expected_inputs = [
        "packet.json",
        "fact_context.json",
        *(
            ["qualification_context.json"]
            if artifacts["qualification"] is not None
            else []
        ),
        "law_context.json",
        "prompt.txt",
    ]
    _require(
        task_manifest.get("semantic_inputs") == expected_inputs,
        f"{context}: semantic input boundary mismatch",
    )
    _require(
        task_manifest.get("cli_execution_input")
        == {
            "stdin": "prompt.txt bytes",
            "filesystem": ["output_schema.json"],
            "persistent_task_directory_is_not_cli_cwd": True,
        },
        f"{context}: CLI input boundary mismatch",
    )
    _require(
        task_manifest.get("threat_model_sha256")
        == codex_runner.sha256_text(codex_runner.THREAT_MODEL)
        and task_manifest.get("cli_may_read_outside_working_directory") is True,
        f"{context}: task threat-model mismatch",
    )
    return artifacts


def _validate_full_attempt_stream(
    *,
    attempt_dir: pathlib.Path,
    expected_attempt: int,
    run_manifest: Mapping[str, Any],
    task_manifest: Mapping[str, Any],
    context: str,
) -> tuple[dict[str, Any], str, str, str, dict[str, Any]]:
    _require(attempt_dir.is_dir(), f"{context}: missing attempt directory")
    _require(
        {child.name for child in attempt_dir.iterdir() if child.is_file()}
        == bridge.ATTEMPT_FILES,
        f"{context}: attempt artifact set mismatch",
    )
    receipt = _load_json(attempt_dir / "receipt.json", context + " receipt")
    events = (attempt_dir / "events.jsonl").read_text(encoding="utf-8")
    stderr = (attempt_dir / "stderr.txt").read_text(encoding="utf-8")
    raw_final = (attempt_dir / "raw_final.json").read_text(encoding="utf-8")
    _require(
        receipt.get("attempt") == expected_attempt,
        f"{context}: attempt number mismatch",
    )
    _require(
        receipt.get("run_manifest_sha256") == run_manifest["manifest_sha256"]
        and receipt.get("task_manifest_sha256")
        == task_manifest["manifest_sha256"],
        f"{context}: attempt lineage mismatch",
    )
    audit = codex_runner._event_audit(events)
    expected_audit = {
        key: audit[key]
        for key in (
            "event_count",
            "parse_errors",
            "unsafe_items",
            "service_errors",
            "turn_completed",
        )
    }
    _require(
        receipt.get("event_audit") == expected_audit,
        f"{context}: event audit mismatch",
    )
    _require(not audit["parse_errors"], f"{context}: event parse error")
    _require(not audit["unsafe_items"], f"{context}: unsafe tool/nonmessage event")
    expected_hashes = {
        "raw_response_sha256": codex_runner.sha256_text(events),
        "event_stream_sha256": codex_runner.sha256_text(events),
        "stderr_sha256": codex_runner.sha256_text(stderr),
        "content_sha256": codex_runner.sha256_text(raw_final),
        "raw_final_sha256": codex_runner.sha256_text(raw_final),
    }
    for field, expected in expected_hashes.items():
        _require(receipt.get(field) == expected, f"{context}: {field} mismatch")
    _require(
        receipt.get("raw_content") == raw_final,
        f"{context}: raw final mismatch",
    )
    return receipt, events, stderr, raw_final, audit


def _full_ledger_decisions(
    ledger: Mapping[str, Any], record: Mapping[str, Any]
) -> dict[str, dict[str, Any]]:
    spans = ledger.get("source_spans")
    cells = ledger.get("cells")
    _require(isinstance(spans, list), "full ledger: source_spans is not a list")
    _require(isinstance(cells, list), "full ledger: cells is not a list")
    span_by_id = {
        str(span.get("span_id") or ""): span
        for span in spans
        if isinstance(span, Mapping)
    }
    documents = record.get("docs")
    _require(isinstance(documents, list), "full ledger: record documents missing")
    decisions: dict[str, dict[str, Any]] = {}
    for cell in cells:
        _require(isinstance(cell, Mapping), "full ledger: invalid cell")
        item = str(cell.get("item") or "")
        _require(item in annotate_groups.ITEMS, f"full ledger: unknown item {item}")
        _require(item not in decisions, f"full ledger: duplicate item {item}")
        label = cell.get("label")
        evidence = ""
        locations: list[dict[str, Any]] = []
        evidence_id = cell.get("positive_evidence_span_id")
        if label == 1 and item not in annotate_groups.ABSENCE_ITEMS:
            _require(
                isinstance(evidence_id, str) and evidence_id in span_by_id,
                f"full ledger: {item} lacks positive evidence span",
            )
            span = span_by_id[evidence_id]
            doc_index = span.get("doc_index")
            _require(
                isinstance(doc_index, int)
                and not isinstance(doc_index, bool)
                and 0 <= doc_index < len(documents),
                f"full ledger: {item} bad document index",
            )
            document = documents[doc_index]
            _require(isinstance(document, Mapping), "full ledger: invalid document")
            evidence = str(span.get("quote") or "")
            locations = [
                {
                    "origin": "full_record_source_registry",
                    "segment_id": evidence_id,
                    "doc_index": doc_index,
                    "doc_id": document.get("doc_id"),
                    "source_doc_sha256": span.get("source_doc_sha256"),
                    "start": span.get("start"),
                    "end": span.get("end"),
                    "segment_start": span.get("start"),
                    "segment_end": span.get("end"),
                    "evidence_sha256": codex_runner.sha256_text(evidence),
                }
            ]
        decisions[item] = {
            **dict(cell),
            "evidence": evidence,
            "evidence_locations": locations,
        }
    _require(
        tuple(decisions) == tuple(annotate_groups.ITEMS),
        "full ledger: cells are not exactly ordered v1 through v24",
    )
    return decisions


def _validate_full_record_receipt(
    row: Mapping[str, Any],
    *,
    checkpoint_line: int,
    task_dir: pathlib.Path,
    task_manifest: Mapping[str, Any],
    artifacts: Mapping[str, Any],
    run_manifest: Mapping[str, Any],
    record: Mapping[str, Any],
    record_id: str,
    target_items: tuple[str, ...],
) -> dict[str, Any]:
    context = f"checkpoint line {checkpoint_line} ({record_id}:{FULL_RECORD_GROUP})"
    _require(set(row) == full_runner.RECEIPT_KEYS, f"{context}: receipt keys differ")
    _require(
        row.get("schema_version") == full_runner.RESULT_SCHEMA_VERSION
        and row.get("runner_receipt_schema_version")
        == full_runner.RECEIPT_SCHEMA_VERSION,
        f"{context}: wrong full-record receipt schema",
    )
    _require(row.get("status") == "ok", f"{context}: status is not ok")
    _require(row.get("error") is None, f"{context}: successful receipt has error")
    _require(
        row.get("validation_errors") == [],
        f"{context}: validation errors present",
    )
    _require(
        row.get("id") == record_id
        and row.get("group") == FULL_RECORD_GROUP
        and row.get("target_items") == list(target_items),
        f"{context}: checkpoint identity mismatch",
    )
    _require(
        row.get("run_key") == run_manifest["run_key"]
        and row.get("run_manifest_sha256") == run_manifest["manifest_sha256"]
        and row.get("task_manifest_sha256")
        == task_manifest["manifest_sha256"],
        f"{context}: receipt lineage mismatch",
    )
    for field, expected in _lineage_projection(run_manifest).items():
        _require(row.get(field) == expected, f"{context}: receipt {field} mismatch")
    expected_direct = {
        "source_sha256": task_manifest["record_source_sha256"],
        "full_record_context_sha256": task_manifest[
            "full_record_context_sha256"
        ],
        "full_record_context_schema_version": task_manifest[
            "full_record_context_schema_version"
        ],
        "full_record_context_bounds": task_manifest["full_record_context_bounds"],
        "rubric_sha256": task_manifest["rubric_sha256"],
        "system_prompt_sha256": task_manifest["system_prompt_sha256"],
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
    _require(row.get("request_sha256") == expected_request, f"{context}: request mismatch")

    attempt = row.get("attempt")
    _require(
        isinstance(attempt, int) and not isinstance(attempt, bool) and attempt >= 1,
        f"{context}: invalid attempt number",
    )
    attempts_dir = task_dir / "attempts"
    _require(attempts_dir.is_dir(), f"{context}: missing attempts directory")
    expected_names = [f"attempt-{number:03d}" for number in range(1, attempt + 1)]
    actual_names = sorted(
        child.name for child in attempts_dir.iterdir() if child.is_dir()
    )
    _require(
        actual_names == expected_names,
        f"{context}: non-contiguous or post-checkpoint attempts {actual_names}",
    )
    final_raw = ""
    final_audit: dict[str, Any] = {}
    for number, name in enumerate(expected_names, 1):
        persisted, _, _, raw_final, audit = _validate_full_attempt_stream(
            attempt_dir=attempts_dir / name,
            expected_attempt=number,
            run_manifest=run_manifest,
            task_manifest=task_manifest,
            context=f"{context} {name}",
        )
        if number < attempt:
            _require(
                persisted.get("status") == "error",
                f"{context}: retry followed a successful attempt",
            )
        else:
            _require(persisted == row, f"{context}: checkpoint/receipt mismatch")
            final_raw = raw_final
            final_audit = audit
    _require(
        not final_audit["service_errors"] and final_audit["turn_completed"] is True,
        f"{context}: final turn failed or did not complete",
    )
    _require(
        len(final_audit["thread_ids"]) == 1 and bool(final_audit["thread_ids"][0]),
        f"{context}: thread lineage is not singular",
    )
    _require(
        final_audit["agent_messages"]
        and final_raw.strip() == final_audit["agent_messages"][-1].strip(),
        f"{context}: final output differs from last agent message",
    )
    _require(row.get("usage") == final_audit["usage"], f"{context}: usage mismatch")
    expected_provenance = {
        "endpoint_kind": "codex_cli_exec",
        "requested_model": run_manifest["semantic_config"]["model"],
        "declared_reasoning_effort": run_manifest["semantic_config"][
            "reasoning_effort"
        ],
        "resolved_model": None,
        "resolved_model_note": "CLI event protocol does not attest a resolved alias",
        "codex_cli": run_manifest["codex_cli"],
        "command_template": run_manifest["semantic_config"][
            "codex_command_template"
        ],
        "session_ephemeral": True,
        "sandbox": "read-only",
        "ignore_user_config": True,
        "ignore_rules": True,
        "thread_ids": final_audit["thread_ids"],
    }
    _require(
        row.get("model_provenance") == expected_provenance,
        f"{context}: model provenance mismatch",
    )
    try:
        parsed = full_record_output.parse_output(final_raw)
        normalized = full_record_output.normalize_output(parsed)
        registry = full_record_context.allowed_span_registry(
            artifacts["full_record_context"]
        )
        ledger = full_record_output.canonical_ledger_projection(
            normalized, record, allowed_span_registry=registry
        )
    except (TypeError, ValueError, full_record_output.FullRecordOutputError) as exc:
        raise FullDevGateError(f"{context}: invalid full-record output: {exc}") from exc
    _require(row.get("ledger") == ledger, f"{context}: ledger shadow mismatch")
    _require(
        isinstance(row.get("completed_utc"), str) and bool(row.get("completed_utc")),
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
    return _full_ledger_decisions(ledger, record)


def _validate_receipt(
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
    target_items: tuple[str, ...],
) -> dict[str, Any]:
    if group_name == FULL_RECORD_GROUP:
        return _validate_full_record_receipt(
            row,
            checkpoint_line=checkpoint_line,
            task_dir=task_dir,
            task_manifest=task_manifest,
            artifacts=artifacts,
            run_manifest=run_manifest,
            record=record,
            record_id=record_id,
            target_items=target_items,
        )
    context = f"checkpoint line {checkpoint_line} ({record_id}:{group_name})"
    _require(set(row) == RECEIPT_V2_KEYS, f"{context}: receipt keys differ")
    _require(
        row.get("schema_version") == annotate_groups.GROUP_SCHEMA_VERSION,
        f"{context}: wrong group-result schema",
    )
    _require(
        row.get("runner_receipt_schema_version")
        == getattr(
            codex_runner,
            "LINEAGED_RECEIPT_SCHEMA_VERSION",
            "dacon.independent.codex_cli_receipt.v2",
        ),
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
        f"{context}: receipt lineage mismatch",
    )
    for field, expected in _lineage_projection(run_manifest).items():
        _require(row.get(field) == expected, f"{context}: receipt {field} mismatch")

    expected_direct = {
        "source_sha256": task_manifest["record_source_sha256"],
        "packet_sha256": task_manifest["packet_sha256"],
        "fact_context_sha256": task_manifest["fact_context_sha256"],
        "fact_context_schema_version": artifacts["fact"].get("schema_version"),
        "fact_context_bounds": artifacts["fact"].get("bounds"),
        "qualification_context_sha256": task_manifest[
            "qualification_context_sha256"
        ],
        "qualification_context_schema_version": (
            artifacts["qualification"].get("schema_version")
            if artifacts["qualification"] is not None
            else None
        ),
        "qualification_context_bounds": (
            artifacts["qualification"].get("bounds")
            if artifacts["qualification"] is not None
            else None
        ),
        "qualification_catalog_sha256": run_manifest["semantic_config"][
            "qualification_context"
        ]["catalog_sha256"],
        "law_context_sha256": task_manifest["law_context_sha256"],
        "law_context_schema_version": artifacts["law"].get("schema_version"),
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
    _require(row.get("request_sha256") == expected_request, f"{context}: request mismatch")

    attempt = row.get("attempt")
    _require(
        isinstance(attempt, int) and not isinstance(attempt, bool) and attempt >= 1,
        f"{context}: invalid attempt number",
    )
    attempts_dir = task_dir / "attempts"
    _require(attempts_dir.is_dir(), f"{context}: missing attempts directory")
    attempt_name = f"attempt-{attempt:03d}"
    attempt_names = sorted(child.name for child in attempts_dir.iterdir() if child.is_dir())
    _require(
        attempt_names == [attempt_name],
        f"{context}: ambiguous retry lineage {attempt_names}",
    )
    attempt_dir = attempts_dir / attempt_name
    _require(
        {child.name for child in attempt_dir.iterdir() if child.is_file()}
        == bridge.ATTEMPT_FILES,
        f"{context}: attempt artifact set mismatch",
    )
    receipt = _load_json(attempt_dir / "receipt.json", context + " receipt")
    _require(receipt == row, f"{context}: checkpoint differs from immutable receipt")
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
        _require(row.get(field) == expected, f"{context}: {field} mismatch")
    _require(row.get("raw_content") == raw_final, f"{context}: raw final mismatch")

    audit = codex_runner._event_audit(events)
    expected_audit = {
        "event_count": audit["event_count"],
        "parse_errors": audit["parse_errors"],
        "unsafe_items": audit["unsafe_items"],
        "service_errors": audit["service_errors"],
        "turn_completed": audit["turn_completed"],
    }
    _require(row.get("event_audit") == expected_audit, f"{context}: event audit mismatch")
    _require(not audit["parse_errors"], f"{context}: event parse error")
    _require(not audit["unsafe_items"], f"{context}: unsafe tool/nonmessage event")
    _require(not audit["service_errors"], f"{context}: service error event")
    _require(audit["turn_completed"] is True, f"{context}: turn did not complete")
    _require(
        audit["agent_messages"]
        and raw_final.strip() == audit["agent_messages"][-1].strip(),
        f"{context}: final output differs from last agent message",
    )
    _require(row.get("usage") == audit["usage"], f"{context}: usage mismatch")
    _require(
        len(audit["thread_ids"]) == 1 and bool(audit["thread_ids"][0]),
        f"{context}: thread lineage is not singular",
    )
    expected_provenance = {
        "endpoint_kind": "codex_cli_exec",
        "requested_model": run_manifest["semantic_config"]["model"],
        "declared_reasoning_effort": run_manifest["semantic_config"][
            "reasoning_effort"
        ],
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
    _require(
        row.get("model_provenance") == expected_provenance,
        f"{context}: model provenance mismatch",
    )
    try:
        parsed = json.loads(raw_final)
        _require(isinstance(parsed, dict), f"{context}: final JSON is not an object")
        wire = codex_runner._normalize_codex_wire_output(parsed, target_items)
        decisions = annotate_groups.normalize_group_decision(wire, target_items)
        evidence_errors = annotate_groups.validate_evidence(
            decisions,
            artifacts["packet"],
            artifacts["fact"],
            artifacts["qualification"],
        )
    except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        raise FullDevGateError(f"{context}: invalid final decision: {exc}") from exc
    _require(not evidence_errors, f"{context}: invalid evidence {evidence_errors}")
    _require(row.get("decisions") == decisions, f"{context}: decision shadow mismatch")
    _require(
        isinstance(row.get("completed_utc"), str) and bool(row.get("completed_utc")),
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


def _validate_partition_receipts(
    *,
    indexed_rows: Mapping[tuple[str, str], tuple[int, dict[str, Any]]],
    staging_dir: pathlib.Path,
    run_manifest: Mapping[str, Any],
    records: Mapping[str, Mapping[str, Any]],
    item_map: Mapping[str, tuple[str, ...]],
) -> dict[tuple[str, str], dict[str, Any]]:
    verified: dict[tuple[str, str], dict[str, Any]] = {}
    fact_catalog = codex_runner.catalog_facts.CatalogIndex.load()
    for (record_id, group_name), (line_number, row) in sorted(indexed_rows.items()):
        task_dir = staging_dir / "tasks" / codex_runner._task_slug(record_id) / group_name
        task_manifest = _load_json(
            task_dir / "task_manifest.json", f"task {record_id}:{group_name}"
        )
        artifacts = _validate_task_manifest(
            task_manifest,
            run_manifest=run_manifest,
            record=records[record_id],
            record_id=record_id,
            group_name=group_name,
            target_items=item_map[group_name],
            task_dir=task_dir,
            context=f"task {record_id}:{group_name}",
            fact_catalog=fact_catalog,
        )
        verified[(record_id, group_name)] = _validate_receipt(
            row,
            checkpoint_line=line_number,
            task_dir=task_dir,
            task_manifest=task_manifest,
            artifacts=artifacts,
            run_manifest=run_manifest,
            record=records[record_id],
            record_id=record_id,
            group_name=group_name,
            target_items=item_map[group_name],
        )
    return verified


def _validate_exact_source_evidence(
    *,
    record: Mapping[str, Any],
    item: str,
    cell: Mapping[str, Any],
    context: str,
) -> None:
    label = cell.get("label")
    _require(
        isinstance(label, int) and not isinstance(label, bool) and label in (0, 1),
        f"{context}: U/non-binary label is forbidden before scoring",
    )
    evidence = cell.get("evidence")
    locations = cell.get("evidence_locations")
    _require(isinstance(evidence, str), f"{context}: evidence is not a string")
    _require(isinstance(locations, list), f"{context}: locations are not a list")
    needs_evidence = label == 1 and item not in annotate_groups.ABSENCE_ITEMS
    if not needs_evidence:
        _require(
            evidence == "" and locations == [],
            f"{context}: evidence is allowed only for non-absence positives",
        )
        return
    _require(0 < len(evidence) <= 500, f"{context}: evidence missing/too long")
    _require(bool(locations), f"{context}: positive has no evidence coordinates")
    documents = record.get("docs")
    _require(isinstance(documents, list), f"{context}: source documents unavailable")
    for index, location in enumerate(locations):
        _require(isinstance(location, Mapping), f"{context}: invalid location {index}")
        doc_index = location.get("doc_index")
        start = location.get("start")
        end = location.get("end")
        _require(
            isinstance(doc_index, int)
            and not isinstance(doc_index, bool)
            and isinstance(start, int)
            and not isinstance(start, bool)
            and isinstance(end, int)
            and not isinstance(end, bool),
            f"{context}: non-integer coordinate {index}",
        )
        _require(0 <= doc_index < len(documents), f"{context}: bad doc index {index}")
        document = documents[doc_index]
        _require(isinstance(document, Mapping), f"{context}: bad source document")
        source_text = str(document.get("text") or "")
        _require(0 <= start < end <= len(source_text), f"{context}: bad span {index}")
        _require(
            source_text[start:end] == evidence,
            f"{context}: coordinate {index} does not resolve to exact evidence",
        )
        _require(
            location.get("doc_id") == document.get("doc_id")
            and location.get("source_doc_sha256")
            == codex_runner.sha256_text(source_text)
            and location.get("evidence_sha256")
            == codex_runner.sha256_text(evidence),
            f"{context}: coordinate {index} provenance mismatch",
        )


def _project_cells(
    verified: Mapping[tuple[str, str], Mapping[str, Mapping[str, Any]]],
    *,
    records: Mapping[str, Mapping[str, Any]],
    item_map: Mapping[str, tuple[str, ...]],
) -> dict[tuple[str, str], dict[str, Any]]:
    cells: dict[tuple[str, str], dict[str, Any]] = {}
    for (record_id, group_name), decisions in sorted(verified.items()):
        target_items = item_map[group_name]
        _require(
            tuple(decisions) == target_items,
            f"{record_id}:{group_name}: decisions differ from topology",
        )
        for item in target_items:
            key = (record_id, item)
            _require(key not in cells, f"duplicate cell {record_id}:{item}")
            cell = decisions[item]
            _require(isinstance(cell, Mapping), f"{record_id}:{item}: invalid decision")
            _validate_exact_source_evidence(
                record=records[record_id],
                item=item,
                cell=cell,
                context=f"{record_id}:{item}",
            )
            cells[key] = dict(cell)
    _require(
        len(cells) == OFFICIAL_DEV_CELL_COUNT,
        f"candidate has {len(cells)} cells, expected 4800",
    )
    _require(
        {record_id for record_id, _ in cells} == set(records),
        "candidate IDs differ from complete official dev cohort",
    )
    _require(
        all(
            (record_id, item) in cells
            for record_id in records
            for item in annotate_groups.ITEMS
        ),
        "candidate is missing one or more official dev cells",
    )
    return cells


def _metric(counts: Mapping[str, int]) -> dict[str, Any]:
    tp = int(counts.get("tp", 0))
    fp = int(counts.get("fp", 0))
    fn = int(counts.get("fn", 0))
    denominator = 2 * tp + fp + fn
    return {
        **{name: int(counts.get(name, 0)) for name in ("tp", "fp", "fn", "tn")},
        "positive_f1": 0.0 if denominator == 0 else 2 * tp / denominator,
    }


def _score_cells(
    cells: Mapping[tuple[str, str], Mapping[str, Any]],
    labels: Mapping[str, Mapping[str, int]],
) -> dict[str, Any]:
    totals: Counter[str] = Counter()
    by_item_counts = {item: Counter() for item in annotate_groups.ITEMS}
    outcomes: list[dict[str, Any]] = []
    for record_id in labels:
        for item in annotate_groups.ITEMS:
            predicted = int(cells[(record_id, item)]["label"])
            gold = labels[record_id][item]
            if predicted == 1 and gold == 1:
                outcome = "tp"
            elif predicted == 1:
                outcome = "fp"
            elif gold == 1:
                outcome = "fn"
            else:
                outcome = "tn"
            totals[outcome] += 1
            by_item_counts[item][outcome] += 1
            outcomes.append(
                {
                    "id": record_id,
                    "item": item,
                    "gold_label": gold,
                    "predicted_label": predicted,
                    "outcome": outcome,
                }
            )
    by_item = {item: _metric(by_item_counts[item]) for item in annotate_groups.ITEMS}
    macro = mean(metric["positive_f1"] for metric in by_item.values())
    minimum = min(metric["positive_f1"] for metric in by_item.values())
    passed = macro >= MACRO_POSITIVE_F1_MIN and minimum >= ITEM_POSITIVE_F1_MIN
    return {
        "counts": _metric(totals),
        "macro_positive_f1": macro,
        "minimum_item_positive_f1": minimum,
        "by_item": by_item,
        "official_dev_role_gate_pass": passed,
        "cells": outcomes,
    }


def full_dev_score(
    *,
    records_path: pathlib.Path,
    labels_path: pathlib.Path,
    run_manifest_path: pathlib.Path | Sequence[pathlib.Path],
    checkpoint_path: pathlib.Path | Sequence[pathlib.Path],
    topology: str,
) -> dict[str, Any]:
    """Validate every candidate artifact and only then parse official labels."""

    _require(topology in TOPOLOGIES, f"unknown topology: {topology}")
    records = qualification_panel.read_records(records_path)
    _require(
        len(records) == OFFICIAL_DEV_ID_COUNT,
        f"official dev must have exactly 200 unique IDs, found {len(records)}",
    )
    organizer_ids = list(records)
    item_map = _group_items(topology)
    master_rows = _master_rows(organizer_ids, topology)
    expected_rows = 1_600 if topology == GROUPED_TOPOLOGY else 200
    _require(len(master_rows) == expected_rows, "internal topology row-count defect")
    _require(
        sum(len(item_map[group]) for _, group in master_rows)
        == OFFICIAL_DEV_CELL_COUNT,
        "internal topology does not project exactly 4800 cells",
    )

    manifest_paths = _batch_paths(run_manifest_path, "run manifests")
    checkpoint_paths = _batch_paths(checkpoint_path, "checkpoints")
    _require(
        len(manifest_paths) == len(checkpoint_paths),
        "batch inputs: run-manifest/checkpoint counts differ",
    )
    partitions: list[dict[str, Any]] = []
    reference_identity: dict[str, Any] | None = None
    run_instances: set[str] = set()
    claimed_rows: set[tuple[str, str]] = set()
    for index, (manifest_path, checkpoint_file) in enumerate(
        zip(manifest_paths, checkpoint_paths, strict=True), 1
    ):
        context = f"partition {index}"
        manifest = _load_json(manifest_path, context + " run manifest")
        staging_dir, planned_rows, identity = _validate_run_manifest(
            manifest,
            manifest_path=manifest_path,
            records_path=records_path,
            organizer_ids=organizer_ids,
            topology=topology,
            context=context,
        )
        if reference_identity is None:
            reference_identity = identity
        else:
            for field in (
                "tuple_sha256",
                "annotator_role",
                "prompt_profile",
                "prompt_lineage_sha256",
                "model_identity",
            ):
                _require(
                    identity[field] == reference_identity[field],
                    f"{context}: mixed {field} lineage",
                )
        instance = str(manifest["run_instance_sha256"])
        _require(instance not in run_instances, f"{context}: duplicate run instance")
        run_instances.add(instance)
        overlap = claimed_rows & planned_rows
        _require(not overlap, f"{context}: overlapping partitions {sorted(overlap)[:5]}")
        claimed_rows.update(planned_rows)
        _, indexed_rows = _read_checkpoint_identities(
            checkpoint_file,
            allowed_rows=planned_rows,
            item_map=item_map,
            context=context + " checkpoint",
        )
        partitions.append(
            {
                "index": index,
                "manifest_path": manifest_path,
                "checkpoint_path": checkpoint_file,
                "manifest": manifest,
                "staging_dir": staging_dir,
                "planned_rows": planned_rows,
                "indexed_rows": indexed_rows,
            }
        )
    missing = master_rows - claimed_rows
    extra = claimed_rows - master_rows
    _require(not missing, f"partition union: missing rows {sorted(missing)[:5]}")
    _require(not extra, f"partition union: extra rows {sorted(extra)[:5]}")
    _require(claimed_rows == master_rows, "partition union differs from fixed topology")

    # Everything below through _project_cells remains label-free.  In
    # particular, unsafe events, U values, and evidence defects fail here.
    verified: dict[tuple[str, str], dict[str, Any]] = {}
    for partition in partitions:
        batch = _validate_partition_receipts(
            indexed_rows=partition["indexed_rows"],
            staging_dir=partition["staging_dir"],
            run_manifest=partition["manifest"],
            records=records,
            item_map=item_map,
        )
        overlap = set(verified) & set(batch)
        _require(not overlap, f"partition {partition['index']}: duplicate verified rows")
        verified.update(batch)
    _require(set(verified) == master_rows, "verified receipts differ from master cohort")
    cells = _project_cells(verified, records=records, item_map=item_map)

    # This is intentionally the first access to the labels file.
    labels_sha256 = codex_runner.file_sha256(labels_path)
    labels = qualification_panel.read_labels(labels_path)
    _require(set(labels) == set(records), "official record/label ID sets differ")
    score = _score_cells(cells, labels)
    assert reference_identity is not None
    batches = [
        {
            "partition": partition["index"],
            "run_manifest_path": str(partition["manifest_path"].resolve()),
            "run_manifest_sha256": partition["manifest"]["manifest_sha256"],
            "run_instance_sha256": partition["manifest"]["run_instance_sha256"],
            "cohort_plan_sha256": partition["manifest"]["cohort_plan"][
                "manifest_sha256"
            ],
            "checkpoint_path": str(partition["checkpoint_path"].resolve()),
            "checkpoint_sha256": codex_runner.file_sha256(
                partition["checkpoint_path"]
            ),
            "group_rows": len(partition["planned_rows"]),
        }
        for partition in partitions
    ]
    gate_pass = bool(score["official_dev_role_gate_pass"])
    return {
        "schema_version": REPORT_SCHEMA,
        "purpose": "complete_official_dev_annotator_role_qualification",
        "is_full_official_dev_evaluation": True,
        "is_selected_panel": False,
        "qualified_for_declared_role": gate_pass,
        "qualified_annotator_role": (
            reference_identity["annotator_role"] if gate_pass else None
        ),
        "qualified_tuple_sha256": (
            reference_identity["tuple_sha256"] if gate_pass else None
        ),
        "qualified_for_gold_generation": False,
        "qualified_for_final_gold_generation": False,
        "final_gold_gate_note": (
            "This report qualifies only the declared role and frozen tuple at the "
            "official-dev competence gate. Peer qualification, adjudication, final "
            "build, and blind audit remain separate gates."
        ),
        "information_boundary": {
            "model_invocations": 0,
            "labels_read_after_all_candidate_artifact_validation": True,
            "competition_runtime_outputs_used": False,
        },
        "topology": topology,
        "records_sha256": codex_runner.file_sha256(records_path),
        "labels_sha256": labels_sha256,
        "identity": reference_identity,
        "coverage": {
            "unique_ids": len(records),
            "required_rows": len(master_rows),
            "verified_rows": len(verified),
            "unique_cells": len(cells),
            "partitions": len(partitions),
            "missing_rows": 0,
            "duplicate_rows": 0,
            "extra_rows": 0,
            "abstentions": 0,
            "invalid_evidence": 0,
            "unsafe_tool_events": 0,
        },
        "integrity": {
            "full_200_id_cohort": True,
            "single_fixed_topology": True,
            "partition_plans_disjoint_and_complete": True,
            "run_instances_unique": True,
            "semantic_tuple_role_profile_model_identical": True,
            "task_and_receipt_lineage_valid": True,
            "unsafe_events_absent": True,
            "all_cells_binary": True,
            "all_nonabsence_positive_evidence_exact": True,
            "labels_opened_post_validation": True,
        },
        "thresholds": {
            "macro_positive_f1_min": MACRO_POSITIVE_F1_MIN,
            "every_item_positive_f1_min": ITEM_POSITIVE_F1_MIN,
            "abstentions_allowed": 0,
        },
        "batch_lineages": batches,
        **score,
    }


def _write_json_immutable(path: pathlib.Path, value: Mapping[str, Any]) -> None:
    content = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if path.exists():
        _require(
            path.read_text(encoding="utf-8") == content,
            f"report already exists and differs: {path}",
        )
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--records", type=pathlib.Path, default=DEFAULT_RECORDS)
    parser.add_argument("--labels", type=pathlib.Path, default=DEFAULT_LABELS)
    parser.add_argument("--topology", choices=TOPOLOGIES, required=True)
    parser.add_argument("--run-manifest", type=pathlib.Path, action="append", required=True)
    parser.add_argument("--checkpoint", type=pathlib.Path, action="append", required=True)
    parser.add_argument("--report", type=pathlib.Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        report = full_dev_score(
            records_path=args.records,
            labels_path=args.labels,
            run_manifest_path=args.run_manifest,
            checkpoint_path=args.checkpoint,
            topology=args.topology,
        )
        _write_json_immutable(args.report, report)
        print(
            _canonical(
                {
                    "report": str(args.report),
                    "official_dev_role_gate_pass": report[
                        "official_dev_role_gate_pass"
                    ],
                    "macro_positive_f1": report["macro_positive_f1"],
                    "minimum_item_positive_f1": report[
                        "minimum_item_positive_f1"
                    ],
                    "qualified_annotator_role": report["qualified_annotator_role"],
                    "topology": report["topology"],
                }
            )
        )
        return 0 if report["official_dev_role_gate_pass"] else 1
    except (FullDevGateError, OSError, KeyError, TypeError, ValueError) as exc:
        print(f"Full official-dev gate failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
