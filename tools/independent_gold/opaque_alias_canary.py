"""Fail-closed continuity canaries for opaque hosted model aliases.

The Codex CLI currently exposes a requested hosted alias, not a cryptographic
attestation of the service-side model revision.  This module therefore does
not attempt to invent an exact revision.  It freezes source-only organizer-dev
canaries and compares a deliberately narrow, auditable projection of repeated
topology-B runs:

* the complete semantic tuple, prompt profile, requested model identity, and
  output schemas;
* all 24 binary labels;
* each positive-evidence span ID, quote, document hash, and exact coordinates;
* source/context/request hashes; and
* clean event/safety status, including every persisted attempt.

The first ``before_qualification`` observation starts a role-specific epoch.
Every later checkpoint must arrive in the frozen order.  A difference or an
untrustworthy runner artifact closes the epoch and invalidates the exact
organizer-order task range since the previous passing canary.  Candidate and
verifier epochs are intentionally separate.

Canary agreement is only evidence of continuity.  It is not model-revision
attestation and must never be described as one.
"""

from __future__ import annotations

import argparse
import copy
import json
import pathlib
import sys
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

try:
    from tools.independent_gold import codex_cli_annotator as base
    from tools.independent_gold import codex_full_record_annotator as runner
    from tools.independent_gold import full_record_context
    from tools.independent_gold import full_record_output
    from tools.independent_gold import full_record_prompt_profiles
except ModuleNotFoundError:  # Direct execution from this directory.
    import codex_cli_annotator as base  # type: ignore[no-redef]
    import codex_full_record_annotator as runner  # type: ignore[no-redef]
    import full_record_context  # type: ignore[no-redef]
    import full_record_output  # type: ignore[no-redef]
    import full_record_prompt_profiles  # type: ignore[no-redef]


ROOT = pathlib.Path(__file__).resolve().parents[2]
DEFAULT_DEV_INPUT = ROOT / "data_open" / "dev.jsonl.gz"
DEFAULT_FULL_INPUT = ROOT / "data_open" / "train_unlabeled.jsonl.gz"

PLAN_SCHEMA_VERSION = "dacon.independent.opaque_alias_canary_plan.v1"
RESULT_SCHEMA_VERSION = "dacon.independent.opaque_alias_canary_result.v1"
EPOCH_SCHEMA_VERSION = "dacon.independent.opaque_alias_continuity_epoch.v1"
POLICY_VERSION = "opaque-hosted-alias-continuity-v1"
SELECTION_METHOD = "role_salted_sha256_rank_without_replacement_v1"
CANARY_PHASE = "continuity_canary"
ROLES = ("candidate", "verifier")
CHECKPOINT_KINDS = (
    "before_qualification",
    "pre_full_run",
    "interval",
    "post_run",
)

ROLE_DEFAULTS = {
    "candidate": {
        "model": "gpt-5.6-sol",
        "prompt_profile": "candidate-full-record-source-direct-v2",
        "reasoning_effort": "xhigh",
    },
    "verifier": {
        "model": "gpt-6-astra",
        "prompt_profile": "verifier-full-record-falsification-v2",
        "reasoning_effort": "max",
    },
}

_PLAN_KEYS = frozenset(
    {
        "schema_version",
        "policy_version",
        "identity_statement",
        "selection",
        "organizer_inputs",
        "workload_order",
        "runner_contract",
        "schedule",
        "roles",
        "information_boundary",
        "manifest_sha256",
    }
)
_RESULT_KEYS = frozenset(
    {
        "schema_version",
        "policy_version",
        "plan_manifest_sha256",
        "annotator_role",
        "checkpoint",
        "runner_artifact",
        "status",
        "failures",
        "comparison_projection",
        "comparison_sha256",
        "observed_utc",
        "identity_statement",
        "manifest_sha256",
    }
)
_EPOCH_KEYS = frozenset(
    {
        "schema_version",
        "policy_version",
        "plan_manifest_sha256",
        "annotator_role",
        "epoch_id",
        "sequence",
        "previous_epoch_manifest_sha256",
        "status",
        "baseline_result_manifest_sha256",
        "baseline_comparison_sha256",
        "baseline_comparison_projection",
        "checkpoint_history",
        "last_passing_checkpoint",
        "next_required_checkpoint_id",
        "invalidation",
        "continuity_qualified",
        "new_epoch_and_requalification_required",
        "identity_statement",
        "manifest_sha256",
    }
)


class CanaryError(ValueError):
    """Raised when a frozen canary or epoch manifest is itself invalid."""


def _canonical(value: Any) -> str:
    return base.canonical_json(value)


def _sha(value: Any) -> str:
    return base.sha256_object(value)


def _without_manifest_hash(value: Mapping[str, Any]) -> dict[str, Any]:
    return {key: child for key, child in value.items() if key != "manifest_sha256"}


def _manifest_hash(value: Mapping[str, Any]) -> str:
    return _sha(_without_manifest_hash(value))


def _is_sha256(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise CanaryError(message)


def _write_json_immutable(path: pathlib.Path, value: Mapping[str, Any]) -> None:
    base._write_json_immutable(path, value)


def _load_json(path: pathlib.Path, context: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise CanaryError(f"{context}: cannot read {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise CanaryError(f"{context}: invalid JSON in {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise CanaryError(f"{context}: expected a JSON object")
    return value


def _read_jsonl_strict(path: pathlib.Path, context: str) -> list[dict[str, Any]]:
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise CanaryError(f"{context}: cannot read {path}: {exc}") from exc
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(raw.splitlines(), 1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise CanaryError(
                f"{context}: invalid JSON at line {line_number}: {exc.msg}"
            ) from exc
        if not isinstance(value, dict):
            raise CanaryError(f"{context}: line {line_number} is not an object")
        rows.append(value)
    return rows


def _ordered_ids(records: Sequence[Mapping[str, Any]], context: str) -> list[str]:
    ids: list[str] = []
    for index, record in enumerate(records):
        record_id = record.get("id")
        if not isinstance(record_id, (str, int)) or isinstance(record_id, bool):
            raise CanaryError(f"{context}[{index}]: missing organizer ID")
        record_id = str(record_id)
        if not record_id:
            raise CanaryError(f"{context}[{index}]: empty organizer ID")
        ids.append(record_id)
    if len(ids) != len(set(ids)):
        raise CanaryError(f"{context}: duplicate organizer IDs")
    return ids


def _select_role_ids(
    dev_ids: Sequence[str], *, canary_count: int, seed: str
) -> dict[str, list[str]]:
    if canary_count <= 0:
        raise CanaryError("canary_count must be positive")
    if len(dev_ids) < canary_count * len(ROLES):
        raise CanaryError("organizer dev cohort is too small for disjoint role canaries")
    index = {record_id: position for position, record_id in enumerate(dev_ids)}
    used: set[str] = set()
    selected: dict[str, list[str]] = {}
    for role in ROLES:
        ranked = sorted(
            (record_id for record_id in dev_ids if record_id not in used),
            key=lambda record_id: (
                base.sha256_text(f"{POLICY_VERSION}\0{seed}\0{role}\0{record_id}"),
                index[record_id],
            ),
        )
        role_set = set(ranked[:canary_count])
        role_ids = [record_id for record_id in dev_ids if record_id in role_set]
        selected[role] = role_ids
        used.update(role_ids)
    return selected


def build_schedule(
    *, qualification_task_count: int, full_run_task_count: int, interval_every: int
) -> dict[str, Any]:
    """Return an exact, non-skippable canary boundary schedule."""

    for name, value in (
        ("qualification_task_count", qualification_task_count),
        ("full_run_task_count", full_run_task_count),
        ("interval_every", interval_every),
    ):
        if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
            raise CanaryError(f"{name} must be a positive integer")
    if interval_every >= full_run_task_count:
        raise CanaryError("interval_every must be smaller than full_run_task_count")

    raw: list[tuple[str, int, int, str]] = [
        (
            "before-qualification",
            0,
            0,
            "must pass before the first official-dev qualification task",
        ),
        (
            "pre-full-run",
            qualification_task_count,
            0,
            "must pass after qualification and immediately before full-run task 0",
        ),
    ]
    for completed in range(interval_every, full_run_task_count, interval_every):
        raw.append(
            (
                f"full-{completed:05d}",
                qualification_task_count,
                completed,
                f"must pass after exactly {completed} full-run tasks",
            )
        )
    raw.append(
        (
            "post-full-run",
            qualification_task_count,
            full_run_task_count,
            "must pass immediately after the final full-run task",
        )
    )
    checkpoints: list[dict[str, Any]] = []
    for checkpoint_index, (suffix, qualified, completed, timing) in enumerate(raw):
        if checkpoint_index == 0:
            kind = "before_qualification"
        elif checkpoint_index == 1:
            kind = "pre_full_run"
        elif checkpoint_index == len(raw) - 1:
            kind = "post_run"
        else:
            kind = "interval"
        checkpoints.append(
            {
                "checkpoint_index": checkpoint_index,
                "checkpoint_id": f"c{checkpoint_index:03d}-{suffix}",
                "kind": kind,
                "qualification_tasks_completed": qualified,
                "full_run_tasks_completed": completed,
                "global_tasks_completed": qualified + completed,
                "required_timing": timing,
            }
        )
    return {
        "qualification_task_count": qualification_task_count,
        "full_run_task_count": full_run_task_count,
        "interval_every": interval_every,
        "checkpoint_count": len(checkpoints),
        "checkpoints": checkpoints,
        "skipping_checkpoints_allowed": False,
        "post_drift_resume_allowed": False,
    }


def _role_contract(role: str, reasoning_effort: str) -> dict[str, Any]:
    defaults = ROLE_DEFAULTS[role]
    profile = full_record_prompt_profiles.get_profile(
        defaults["prompt_profile"], annotator_role=role
    )
    model_identity = base.build_model_identity(model=defaults["model"])
    return {
        "annotator_role": role,
        "requested_model": defaults["model"],
        "required_model_family": profile.required_model_family,
        "reasoning_effort": reasoning_effort,
        "prompt_profile": profile.name,
        "prompt_protocol_version": profile.protocol_version,
        "model_identity": model_identity,
        "runner_phase": CANARY_PHASE,
        "pass_kind": runner.PASS_KIND,
    }


def build_plan_from_records(
    *,
    dev_records: Sequence[Mapping[str, Any]],
    full_records: Sequence[Mapping[str, Any]],
    dev_input_path: pathlib.Path,
    dev_input_sha256: str,
    full_input_path: pathlib.Path,
    full_input_sha256: str,
    context_fingerprints: Mapping[str, Mapping[str, Any]],
    canary_count: int = 8,
    selection_seed: str = "dacon-236754-independent-gold-canary-v1",
    interval_every: int = 500,
    candidate_reasoning_effort: str = "xhigh",
    verifier_reasoning_effort: str = "max",
) -> dict[str, Any]:
    """Build a label-free plan from already validated organizer records.

    ``context_fingerprints`` is supplied separately so tests and offline plan
    review do not need to rebuild catalogs.  :func:`build_plan` constructs it
    from the production-independent full-record context builder.
    """

    dev_ids = _ordered_ids(dev_records, "dev_records")
    full_ids = _ordered_ids(full_records, "full_records")
    if not _is_sha256(dev_input_sha256) or not _is_sha256(full_input_sha256):
        raise CanaryError("organizer input hashes must be SHA-256 values")
    selected = _select_role_ids(
        dev_ids, canary_count=canary_count, seed=selection_seed
    )
    dev_by_id = {str(record["id"]): record for record in dev_records}
    reasoning = {
        "candidate": candidate_reasoning_effort,
        "verifier": verifier_reasoning_effort,
    }
    roles: dict[str, Any] = {}
    for role in ROLES:
        if not reasoning[role].strip():
            raise CanaryError(f"{role} reasoning effort must be explicit")
        records: list[dict[str, Any]] = []
        for record_id in selected[role]:
            context = context_fingerprints.get(record_id)
            if not isinstance(context, Mapping):
                raise CanaryError(f"missing context fingerprint for {record_id}")
            context_sha = context.get("full_record_context_sha256")
            if not _is_sha256(context_sha):
                raise CanaryError(f"bad context hash for {record_id}")
            records.append(
                {
                    "record_id": record_id,
                    "organizer_position": dev_ids.index(record_id),
                    "record_source_sha256": _sha(dev_by_id[record_id]),
                    "full_record_context_sha256": context_sha,
                    "full_record_context_schema_version": context.get(
                        "full_record_context_schema_version"
                    ),
                    "full_record_context_bounds": copy.deepcopy(
                        context.get("full_record_context_bounds")
                    ),
                }
            )
        role_contract = _role_contract(role, reasoning[role])
        roles[role] = {
            **role_contract,
            "canary_ids": list(selected[role]),
            "canary_ids_sha256": _sha(selected[role]),
            "records": records,
        }

    schedule = build_schedule(
        qualification_task_count=len(dev_ids),
        full_run_task_count=len(full_ids),
        interval_every=interval_every,
    )
    plan: dict[str, Any] = {
        "schema_version": PLAN_SCHEMA_VERSION,
        "policy_version": POLICY_VERSION,
        "identity_statement": (
            "Opaque hosted aliases have no attested resolved revision; this plan "
            "measures continuity only and does not identify an exact model revision."
        ),
        "selection": {
            "method": SELECTION_METHOD,
            "seed": selection_seed,
            "canary_count_per_role": canary_count,
            "roles_are_disjoint": True,
            "organizer_order_preserved_after_selection": True,
            "labels_consulted": False,
        },
        "organizer_inputs": {
            "dev": {
                "path": str(dev_input_path.resolve()),
                "sha256": dev_input_sha256,
                "record_count": len(dev_ids),
                "ordered_ids_sha256": _sha(dev_ids),
            },
            "full_unlabeled": {
                "path": str(full_input_path.resolve()),
                "sha256": full_input_sha256,
                "record_count": len(full_ids),
                "ordered_ids_sha256": _sha(full_ids),
            },
        },
        "workload_order": {
            "official_dev_qualification": list(dev_ids),
            "unlabeled_full_run": list(full_ids),
        },
        "runner_contract": {
            "runner_schema_version": runner.RUNNER_SCHEMA_VERSION,
            "task_schema_version": runner.TASK_SCHEMA_VERSION,
            "receipt_schema_version": runner.RECEIPT_SCHEMA_VERSION,
            "result_schema_version": runner.RESULT_SCHEMA_VERSION,
            "output_schema_version": full_record_output.SCHEMA_VERSION,
            "ledger_schema_version": full_record_output.LEDGER_SCHEMA_VERSION,
            "output_schema_sha256": _sha(full_record_output.output_schema()),
            "full_record_context_schema_version": full_record_context.SCHEMA_VERSION,
            "annotation_topology": "one_call_per_record_all_24_items",
            "target_items": list(runner.TARGET_ITEMS),
            "canary_phase": CANARY_PHASE,
            "one_attempt_required": True,
        },
        "schedule": schedule,
        "roles": roles,
        "information_boundary": {
            "model_payload_contains_organizer_labels": False,
            "plan_contains_organizer_labels": False,
            "allowed_semantic_inputs": [
                "competition-supplied organizer dev notices",
                "competition-supplied item/law/catalog snapshots",
            ],
            "forbidden_inputs": [
                "organizer labels in model requests",
                "competition submission runtime",
                "production predictions",
                "saved production model responses",
                "external notice or law data",
                "peer annotator outputs",
            ],
        },
    }
    plan["manifest_sha256"] = _manifest_hash(plan)
    validate_plan(plan)
    return plan


def build_plan(
    *,
    dev_input_path: pathlib.Path = DEFAULT_DEV_INPUT,
    full_input_path: pathlib.Path = DEFAULT_FULL_INPUT,
    canary_count: int = 8,
    selection_seed: str = "dacon-236754-independent-gold-canary-v1",
    interval_every: int = 500,
    candidate_reasoning_effort: str = "xhigh",
    verifier_reasoning_effort: str = "max",
) -> dict[str, Any]:
    """Build a plan from the two exact competition-supplied organizer inputs."""

    dev_path = dev_input_path.resolve()
    full_path = full_input_path.resolve()
    if dev_path != DEFAULT_DEV_INPUT.resolve():
        raise CanaryError("canary source must be the competition organizer dev input")
    if full_path != DEFAULT_FULL_INPUT.resolve():
        raise CanaryError("full workload must be the competition unlabeled input")
    dev_records = list(base.read_records(dev_path))
    full_records = list(base.read_records(full_path))
    dev_ids = _ordered_ids(dev_records, "organizer dev")
    selected = _select_role_ids(
        dev_ids, canary_count=canary_count, seed=selection_seed
    )
    selected_ids = set(selected["candidate"]) | set(selected["verifier"])
    fact_catalog = base.catalog_facts.CatalogIndex.load()
    qualification_catalog = (
        base.qualification_context.qualification_facts.CatalogReference.load()
    )
    contexts: dict[str, dict[str, Any]] = {}
    for record in dev_records:
        record_id = str(record["id"])
        if record_id not in selected_ids:
            continue
        context = full_record_context.build_full_record_context(
            record,
            catalog_index=fact_catalog,
            qualification_catalog=qualification_catalog,
        )
        errors = full_record_context.validate_full_record_context(
            record, context, catalog_index=fact_catalog
        )
        if errors:
            raise CanaryError(f"{record_id}: invalid full-record context: {errors[:5]}")
        contexts[record_id] = {
            "full_record_context_sha256": context["context_sha256"],
            "full_record_context_schema_version": context["schema_version"],
            "full_record_context_bounds": copy.deepcopy(context["bounds"]),
        }
    return build_plan_from_records(
        dev_records=dev_records,
        full_records=full_records,
        dev_input_path=dev_path,
        dev_input_sha256=base.file_sha256(dev_path),
        full_input_path=full_path,
        full_input_sha256=base.file_sha256(full_path),
        context_fingerprints=contexts,
        canary_count=canary_count,
        selection_seed=selection_seed,
        interval_every=interval_every,
        candidate_reasoning_effort=candidate_reasoning_effort,
        verifier_reasoning_effort=verifier_reasoning_effort,
    )


def validate_plan(plan: Mapping[str, Any]) -> None:
    _require(set(plan) == _PLAN_KEYS, "canary plan keys differ from v1 contract")
    _require(plan.get("schema_version") == PLAN_SCHEMA_VERSION, "wrong plan schema")
    _require(plan.get("policy_version") == POLICY_VERSION, "wrong canary policy")
    _require(plan.get("manifest_sha256") == _manifest_hash(plan), "plan hash mismatch")
    selection = plan.get("selection")
    _require(isinstance(selection, Mapping), "plan selection missing")
    _require(selection.get("method") == SELECTION_METHOD, "selection method differs")
    _require(selection.get("labels_consulted") is False, "label-free selection required")
    count = selection.get("canary_count_per_role")
    _require(type(count) is int and count > 0, "invalid canary count")

    inputs = plan.get("organizer_inputs")
    _require(isinstance(inputs, Mapping), "organizer inputs missing")
    _require(set(inputs) == {"dev", "full_unlabeled"}, "organizer input keys differ")
    workload = plan.get("workload_order")
    _require(isinstance(workload, Mapping), "workload order missing")
    dev_ids = workload.get("official_dev_qualification")
    full_ids = workload.get("unlabeled_full_run")
    for name, ids, input_key in (
        ("official_dev_qualification", dev_ids, "dev"),
        ("unlabeled_full_run", full_ids, "full_unlabeled"),
    ):
        _require(
            isinstance(ids, list)
            and all(isinstance(value, str) and value for value in ids)
            and len(ids) == len(set(ids)),
            f"{name}: invalid ordered IDs",
        )
        metadata = inputs[input_key]
        _require(isinstance(metadata, Mapping), f"{input_key}: metadata missing")
        _require(_is_sha256(metadata.get("sha256")), f"{input_key}: bad input hash")
        _require(metadata.get("record_count") == len(ids), f"{input_key}: count mismatch")
        _require(
            metadata.get("ordered_ids_sha256") == _sha(ids),
            f"{input_key}: ordered ID hash mismatch",
        )

    schedule = plan.get("schedule")
    _require(isinstance(schedule, Mapping), "schedule missing")
    expected_schedule = build_schedule(
        qualification_task_count=len(dev_ids),
        full_run_task_count=len(full_ids),
        interval_every=schedule.get("interval_every"),
    )
    _require(schedule == expected_schedule, "schedule differs from frozen contract")

    contract = plan.get("runner_contract")
    _require(isinstance(contract, Mapping), "runner contract missing")
    expected_contract = {
        "runner_schema_version": runner.RUNNER_SCHEMA_VERSION,
        "task_schema_version": runner.TASK_SCHEMA_VERSION,
        "receipt_schema_version": runner.RECEIPT_SCHEMA_VERSION,
        "result_schema_version": runner.RESULT_SCHEMA_VERSION,
        "output_schema_version": full_record_output.SCHEMA_VERSION,
        "ledger_schema_version": full_record_output.LEDGER_SCHEMA_VERSION,
        "output_schema_sha256": _sha(full_record_output.output_schema()),
        "full_record_context_schema_version": full_record_context.SCHEMA_VERSION,
        "annotation_topology": "one_call_per_record_all_24_items",
        "target_items": list(runner.TARGET_ITEMS),
        "canary_phase": CANARY_PHASE,
        "one_attempt_required": True,
    }
    _require(contract == expected_contract, "runner contract has changed; freeze a new plan")

    roles = plan.get("roles")
    _require(isinstance(roles, Mapping) and set(roles) == set(ROLES), "role plans differ")
    expected_selection = _select_role_ids(
        dev_ids,
        canary_count=count,
        seed=str(selection.get("seed") or ""),
    )
    all_ids: list[str] = []
    for role in ROLES:
        role_plan = roles[role]
        _require(isinstance(role_plan, Mapping), f"{role}: role plan missing")
        ids = role_plan.get("canary_ids")
        records = role_plan.get("records")
        _require(
            isinstance(ids, list)
            and len(ids) == count
            and len(ids) == len(set(ids))
            and all(record_id in dev_ids for record_id in ids),
            f"{role}: invalid canary IDs",
        )
        _require(role_plan.get("canary_ids_sha256") == _sha(ids), f"{role}: ID hash mismatch")
        _require(ids == expected_selection[role], f"{role}: canary IDs are not the label-free deterministic sample")
        _require(isinstance(records, list) and len(records) == count, f"{role}: records differ")
        _require([record.get("record_id") for record in records] == ids, f"{role}: record order differs")
        for record in records:
            _require(
                _is_sha256(record.get("record_source_sha256"))
                and _is_sha256(record.get("full_record_context_sha256")),
                f"{role}: bad record/context hash",
            )
            _require(
                record.get("full_record_context_schema_version")
                == contract["full_record_context_schema_version"],
                f"{role}: context schema differs",
            )
            _require(
                record.get("organizer_position")
                == dev_ids.index(record["record_id"]),
                f"{role}: organizer position differs",
            )
        defaults = ROLE_DEFAULTS[role]
        profile = full_record_prompt_profiles.get_profile(
            defaults["prompt_profile"], annotator_role=role
        )
        expected_identity = base.build_model_identity(model=defaults["model"])
        _require(role_plan.get("annotator_role") == role, f"{role}: role identity differs")
        _require(role_plan.get("requested_model") == defaults["model"], f"{role}: model differs")
        _require(role_plan.get("prompt_profile") == profile.name, f"{role}: profile differs")
        _require(
            role_plan.get("required_model_family") == profile.required_model_family,
            f"{role}: model family differs",
        )
        _require(role_plan.get("model_identity") == expected_identity, f"{role}: model identity differs")
        _require(role_plan.get("runner_phase") == CANARY_PHASE, f"{role}: wrong phase")
        _require(role_plan.get("pass_kind") == runner.PASS_KIND, f"{role}: pass kind differs")
        _require(
            isinstance(role_plan.get("reasoning_effort"), str)
            and role_plan["reasoning_effort"].strip(),
            f"{role}: reasoning effort missing",
        )
        identity = role_plan["model_identity"]
        _require(identity.get("mode") == "opaque_hosted_alias", f"{role}: identity not opaque")
        _require(identity.get("resolved_revision") is None, f"{role}: invented resolved revision")
        _require(identity.get("attestation_verified") is False, f"{role}: false attestation")
        all_ids.extend(ids)
    _require(len(all_ids) == len(set(all_ids)), "candidate/verifier canary IDs overlap")
    boundary = plan.get("information_boundary")
    _require(isinstance(boundary, Mapping), "information boundary missing")
    _require(
        boundary.get("model_payload_contains_organizer_labels") is False
        and boundary.get("plan_contains_organizer_labels") is False,
        "organizer labels must not enter the canary plan or prompt",
    )


def load_plan(path: pathlib.Path) -> dict[str, Any]:
    plan = _load_json(path, "canary plan")
    validate_plan(plan)
    return plan


def _checkpoint(plan: Mapping[str, Any], checkpoint_id: str) -> dict[str, Any]:
    for checkpoint in plan["schedule"]["checkpoints"]:
        if checkpoint["checkpoint_id"] == checkpoint_id:
            return copy.deepcopy(checkpoint)
    raise CanaryError(f"unknown checkpoint ID: {checkpoint_id}")


def build_runner_command(
    plan: Mapping[str, Any],
    *,
    annotator_role: str,
    checkpoint_id: str,
    staging_dir: pathlib.Path,
    codex_bin: str = "codex",
    python_executable: str = sys.executable,
) -> list[str]:
    """Build the exact label-free topology-B command for one canary checkpoint."""

    validate_plan(plan)
    _checkpoint(plan, checkpoint_id)
    if annotator_role not in ROLES:
        raise CanaryError(f"unknown annotator role: {annotator_role}")
    role = plan["roles"][annotator_role]
    command = [
        python_executable,
        "-B",
        str(pathlib.Path(runner.__file__).resolve()),
        "--input",
        plan["organizer_inputs"]["dev"]["path"],
        "--staging-dir",
        str(staging_dir.resolve()),
        "--model",
        role["requested_model"],
        "--reasoning-effort",
        role["reasoning_effort"],
        "--codex-bin",
        codex_bin,
        "--annotator-role",
        annotator_role,
        "--prompt-profile",
        role["prompt_profile"],
        "--phase",
        CANARY_PHASE,
        "--retries",
        "1",
        "--execute",
    ]
    for record_id in role["canary_ids"]:
        command.extend(("--record-id", record_id))
    return command


def _lineage_projection(manifest: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "annotator_role": manifest["annotator_role"],
        "pass_kind": manifest["pass_kind"],
        "phase": manifest["phase"],
        "tuple_sha256": manifest["tuple_sha256"],
        "run_instance_sha256": manifest["run_instance_sha256"],
        "cohort_plan_sha256": manifest["cohort_plan"]["manifest_sha256"],
        "prompt_profile": manifest["prompt_lineage"]["profile"],
        "prompt_lineage_sha256": manifest["prompt_lineage"]["lineage_sha256"],
        "model_identity": manifest["model_identity"],
        "peer_visibility": manifest["peer_visibility"],
        "imported_source_bundle_sha256": manifest["imported_source_bundle"][
            "bundle_sha256"
        ],
    }


def _tuple_hash(manifest: Mapping[str, Any]) -> str:
    payload = {
        "schema_version": manifest["schema_version"],
        "annotator_role": manifest["annotator_role"],
        "pass_kind": manifest["pass_kind"],
        "prompt_lineage": manifest["prompt_lineage"],
        "model_identity": manifest["model_identity"],
        "semantic_config": manifest["semantic_config"],
        "imported_source_bundle": manifest["imported_source_bundle"],
        "codex_cli": {
            "executable_sha256": manifest["codex_cli"]["executable_sha256"],
            "version_output": manifest["codex_cli"]["version_output"],
        },
        "execution_isolation": manifest["execution_isolation"],
        "peer_visibility": manifest["peer_visibility"],
    }
    return _sha(payload)


def _validate_run_manifest(
    manifest: Mapping[str, Any],
    *,
    plan: Mapping[str, Any],
    annotator_role: str,
    staging_dir: pathlib.Path,
) -> None:
    role = plan["roles"][annotator_role]
    _require(manifest.get("manifest_sha256") == _manifest_hash(manifest), "runner manifest hash mismatch")
    _require(manifest.get("schema_version") == runner.RUNNER_SCHEMA_VERSION, "wrong runner schema")
    _require(manifest.get("annotator_role") == annotator_role, "wrong runner annotator role")
    _require(manifest.get("phase") == CANARY_PHASE, "runner phase is not continuity_canary")
    _require(manifest.get("pass_kind") == runner.PASS_KIND, "runner pass kind differs")
    _require(manifest.get("model_identity") == role["model_identity"], "runner model identity differs")
    _require(manifest.get("model_identity", {}).get("resolved_revision") is None, "runner claims an unattested resolved revision")
    prompt = manifest.get("prompt_lineage")
    _require(isinstance(prompt, Mapping), "runner prompt lineage missing")
    _require(prompt.get("profile") == role["prompt_profile"], "runner prompt profile differs")
    _require(prompt.get("annotator_role") == annotator_role, "prompt lineage role differs")
    _require(prompt.get("lineage_sha256") == _sha({key: value for key, value in prompt.items() if key != "lineage_sha256"}), "prompt lineage hash mismatch")
    source_bundle = manifest.get("imported_source_bundle")
    _require(isinstance(source_bundle, Mapping), "source bundle missing")
    _require(source_bundle.get("bundle_sha256") == _sha({key: value for key, value in source_bundle.items() if key != "bundle_sha256"}), "source bundle hash mismatch")
    source_files = source_bundle.get("files")
    _require(isinstance(source_files, list) and source_files, "source bundle files missing")
    for source_file in source_files:
        _require(isinstance(source_file, Mapping), "invalid source bundle entry")
        source_path = pathlib.Path(str(source_file.get("path") or ""))
        if not source_path.is_absolute():
            source_path = ROOT / source_path
        _require(source_path.is_file(), f"source bundle file unavailable: {source_path}")
        _require(
            base.file_sha256(source_path) == source_file.get("sha256"),
            f"source bundle file hash differs: {source_path}",
        )
    _require(manifest.get("tuple_sha256") == _tuple_hash(manifest), "semantic tuple hash mismatch")
    cohort = manifest.get("cohort_plan")
    _require(isinstance(cohort, Mapping), "cohort plan missing")
    _require(cohort.get("manifest_sha256") == _manifest_hash(cohort), "cohort plan hash mismatch")
    _require(cohort.get("phase") == CANARY_PHASE, "cohort phase differs")
    _require(cohort.get("selected_ids") == role["canary_ids"], "cohort IDs differ from frozen canaries")
    _require(cohort.get("selected_groups") == [runner.GROUP_NAME], "cohort topology differs")
    _require(cohort.get("partitioning") == {"method": "organizer_order_stride_v1", "shard_index": 0, "shard_count": 1, "limit_after_sharding": None}, "canary run must be an unsharded complete cohort")
    record_input = manifest.get("record_input")
    _require(isinstance(record_input, Mapping), "runner record input missing")
    _require(record_input.get("sha256") == plan["organizer_inputs"]["dev"]["sha256"], "runner dev input hash differs")
    _require(
        pathlib.Path(str(record_input.get("path") or "")).resolve()
        == pathlib.Path(plan["organizer_inputs"]["dev"]["path"]).resolve(),
        "runner dev input path differs",
    )
    runner_source = manifest.get("runner_source")
    _require(isinstance(runner_source, Mapping), "runner source identity missing")
    runner_source_path = pathlib.Path(str(runner_source.get("path") or ""))
    _require(runner_source_path.is_file(), "runner source is unavailable")
    _require(
        base.file_sha256(runner_source_path) == runner_source.get("sha256"),
        "runner source hash differs",
    )
    cli = manifest.get("codex_cli")
    _require(isinstance(cli, Mapping), "Codex CLI provenance missing")
    executable = pathlib.Path(str(cli.get("resolved_executable") or ""))
    _require(executable.is_file(), "recorded Codex CLI executable is unavailable")
    _require(
        base.file_sha256(executable) == cli.get("executable_sha256"),
        "Codex CLI executable hash differs",
    )
    config = manifest.get("semantic_config")
    _require(isinstance(config, Mapping), "semantic config missing")
    _require(config.get("model") == role["requested_model"], "requested model differs")
    _require(config.get("reasoning_effort") == role["reasoning_effort"], "reasoning effort differs")
    _require(config.get("prompt_profile") == role["prompt_profile"], "semantic prompt profile differs")
    _require(config.get("annotation_topology") == "one_call_per_record_all_24_items", "annotation topology differs")
    _require(config.get("drift_canary_policy", {}).get("required") is True, "runner disabled drift canaries")
    expected_instance = _sha(
        {
            "tuple_sha256": manifest["tuple_sha256"],
            "record_input_sha256": record_input["sha256"],
            "phase": CANARY_PHASE,
            "cohort_plan_sha256": cohort["manifest_sha256"],
        }
    )
    _require(manifest.get("run_instance_sha256") == expected_instance, "run instance hash mismatch")
    _require(manifest.get("run_key") == expected_instance, "run key differs from run instance")
    _require(
        pathlib.Path(str(manifest.get("staging_dir") or "")).resolve()
        == staging_dir.resolve(),
        "runner staging directory differs from inspected artifact",
    )


def _project_event_audit(audit: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: copy.deepcopy(audit.get(key))
        for key in (
            "event_count",
            "parse_errors",
            "unsafe_items",
            "service_errors",
            "turn_completed",
        )
    }


def _artifact_inventory(staging_dir: pathlib.Path) -> dict[str, Any]:
    entries: list[dict[str, Any]] = []
    for relative in ("run_manifest.json", "cohort_plan.json", "full_records.jsonl"):
        path = staging_dir / relative
        if path.is_file():
            entries.append(
                {
                    "path": relative,
                    "sha256": base.file_sha256(path),
                    "bytes": path.stat().st_size,
                }
            )
    return {
        "staging_dir": str(staging_dir.resolve()),
        "top_level_files": entries,
        "top_level_files_sha256": _sha(entries),
    }


def _validate_task_and_receipt(
    *,
    staging_dir: pathlib.Path,
    record: Mapping[str, Any],
    expected_record: Mapping[str, Any],
    row: Mapping[str, Any],
    run_manifest: Mapping[str, Any],
) -> dict[str, Any]:
    record_id = expected_record["record_id"]
    task_dir = staging_dir / "tasks" / base._task_slug(record_id) / runner.GROUP_NAME
    task_manifest = _load_json(task_dir / "task_manifest.json", f"{record_id} task manifest")
    _require(set(task_manifest) == runner.TASK_MANIFEST_KEYS, f"{record_id}: task keys differ")
    _require(task_manifest.get("manifest_sha256") == _manifest_hash(task_manifest), f"{record_id}: task manifest hash mismatch")
    _require(task_manifest.get("record_id") == record_id, f"{record_id}: task record differs")
    _require(task_manifest.get("group") == runner.GROUP_NAME, f"{record_id}: task group differs")
    _require(task_manifest.get("target_items") == list(runner.TARGET_ITEMS), f"{record_id}: target items differ")
    _require(task_manifest.get("record_source_sha256") == expected_record["record_source_sha256"], f"{record_id}: source hash differs")
    _require(task_manifest.get("record_source_sha256") == _sha(record), f"{record_id}: organizer record hash mismatch")
    _require(task_manifest.get("full_record_context_sha256") == expected_record["full_record_context_sha256"], f"{record_id}: context hash differs")
    _require(task_manifest.get("full_record_context_schema_version") == expected_record["full_record_context_schema_version"], f"{record_id}: context schema differs")
    _require(task_manifest.get("full_record_context_bounds") == expected_record["full_record_context_bounds"], f"{record_id}: context bounds differ")
    _require(task_manifest.get("run_manifest_sha256") == run_manifest["manifest_sha256"], f"{record_id}: run manifest lineage differs")
    for key, value in _lineage_projection(run_manifest).items():
        _require(task_manifest.get(key) == value, f"{record_id}: task lineage {key} differs")

    artifacts: dict[str, str] = {}
    for name, expected_hash in task_manifest["artifact_file_sha256"].items():
        path = task_dir / name
        _require(path.is_file(), f"{record_id}: missing task artifact {name}")
        actual = base.file_sha256(path)
        _require(actual == expected_hash, f"{record_id}: task artifact {name} hash mismatch")
        artifacts[name] = actual
    context = _load_json(task_dir / "full_record_context.json", f"{record_id} context")
    _require(context.get("context_sha256") == expected_record["full_record_context_sha256"], f"{record_id}: rendered context hash differs")
    context_errors = full_record_context.validate_full_record_context(record, context)
    _require(not context_errors, f"{record_id}: rendered context invalid: {context_errors[:3]}")

    _require(set(row) == runner.RECEIPT_KEYS, f"{record_id}: receipt keys differ")
    _require(row.get("schema_version") == runner.RESULT_SCHEMA_VERSION, f"{record_id}: result schema differs")
    _require(row.get("runner_receipt_schema_version") == runner.RECEIPT_SCHEMA_VERSION, f"{record_id}: receipt schema differs")
    event_shadow = row.get("event_audit")
    _require(isinstance(event_shadow, Mapping), f"{record_id}: event audit missing")
    _require(not event_shadow.get("parse_errors"), f"{record_id}: event parse errors observed")
    _require(not event_shadow.get("unsafe_items"), f"{record_id}: unsafe event observed")
    _require(not event_shadow.get("service_errors"), f"{record_id}: service error observed")
    _require(event_shadow.get("turn_completed") is True, f"{record_id}: turn did not complete")
    _require(row.get("status") == "ok" and row.get("error") is None and row.get("validation_errors") == [], f"{record_id}: final receipt is not clean")
    _require(row.get("id") == record_id and row.get("group") == runner.GROUP_NAME, f"{record_id}: receipt identity differs")
    _require(row.get("run_manifest_sha256") == run_manifest["manifest_sha256"], f"{record_id}: receipt run lineage differs")
    _require(row.get("task_manifest_sha256") == task_manifest["manifest_sha256"], f"{record_id}: receipt task lineage differs")
    for key, value in _lineage_projection(run_manifest).items():
        _require(row.get(key) == value, f"{record_id}: receipt lineage {key} differs")
    for key in (
        "source_sha256",
        "full_record_context_sha256",
        "full_record_context_schema_version",
        "full_record_context_bounds",
        "rubric_sha256",
        "system_prompt_sha256",
        "prompt_sha256",
        "output_schema_sha256",
    ):
        task_key = "record_source_sha256" if key == "source_sha256" else key
        _require(row.get(key) == task_manifest.get(task_key), f"{record_id}: receipt {key} differs")
    raw = row.get("raw_content")
    _require(isinstance(raw, str), f"{record_id}: raw content missing")
    _require(row.get("content_sha256") == base.sha256_text(raw), f"{record_id}: content hash mismatch")
    _require(row.get("raw_final_sha256") == base.sha256_text(raw), f"{record_id}: final hash mismatch")
    expected_request_sha = _sha(
        {
            "prompt_sha256": task_manifest["prompt_sha256"],
            "output_schema_sha256": task_manifest["output_schema_sha256"],
            "command": run_manifest["semantic_config"]["codex_command_template"],
        }
    )
    _require(
        row.get("request_sha256") == expected_request_sha,
        f"{record_id}: request hash mismatch",
    )
    attempt = row.get("attempt")
    _require(type(attempt) is int and attempt == 1, f"{record_id}: canaries require exactly one attempt")

    attempt_root = task_dir / "attempts"
    attempt_dirs = sorted(path for path in attempt_root.glob("attempt-*") if path.is_dir())
    _require([path.name for path in attempt_dirs] == ["attempt-001"], f"{record_id}: canary attempt history differs")
    attempt_dir = attempt_dirs[0]
    receipt = _load_json(attempt_dir / "receipt.json", f"{record_id} attempt receipt")
    _require(receipt == row, f"{record_id}: checkpoint/attempt receipt mismatch")
    try:
        events = (attempt_dir / "events.jsonl").read_text(encoding="utf-8")
        stderr = (attempt_dir / "stderr.txt").read_text(encoding="utf-8")
        raw_final = (attempt_dir / "raw_final.json").read_text(encoding="utf-8")
    except OSError as exc:
        raise CanaryError(f"{record_id}: incomplete attempt artifacts: {exc}") from exc
    audit = base._event_audit(events)
    _require(not audit["parse_errors"], f"{record_id}: event parse errors observed")
    _require(not audit["unsafe_items"], f"{record_id}: unsafe event observed")
    _require(not audit["service_errors"], f"{record_id}: service error observed")
    _require(audit["turn_completed"] is True, f"{record_id}: turn did not complete")
    _require(len(audit["thread_ids"]) == 1 and audit["thread_ids"][0], f"{record_id}: thread lineage is not singular")
    _require(_project_event_audit(audit) == row["event_audit"], f"{record_id}: event audit shadow differs")
    _require(row.get("event_stream_sha256") == base.sha256_text(events), f"{record_id}: event hash mismatch")
    _require(row.get("raw_response_sha256") == base.sha256_text(events), f"{record_id}: raw response hash mismatch")
    _require(row.get("stderr_sha256") == base.sha256_text(stderr), f"{record_id}: stderr hash mismatch")
    _require(raw_final == raw, f"{record_id}: raw final differs from receipt")
    _require(audit["agent_messages"] and audit["agent_messages"][-1].strip() == raw.strip(), f"{record_id}: final agent message differs")
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
        "thread_ids": audit["thread_ids"],
    }
    _require(
        row.get("model_provenance") == expected_provenance,
        f"{record_id}: model provenance differs",
    )

    parsed = full_record_output.parse_output(raw)
    normalized = full_record_output.normalize_output(parsed)
    registry = full_record_context.allowed_span_registry(context)
    ledger = full_record_output.canonical_ledger_projection(
        normalized, record, allowed_span_registry=registry
    )
    _require(ledger == row.get("ledger"), f"{record_id}: canonical ledger shadow differs")
    cells = ledger["cells"]
    _require([cell["item"] for cell in cells] == list(runner.TARGET_ITEMS), f"{record_id}: cell order differs")
    span_by_id = {span["span_id"]: span for span in ledger["source_spans"]}
    labels: list[list[Any]] = []
    evidence: list[dict[str, Any]] = []
    for cell in cells:
        label = cell["label"]
        _require(type(label) is int and label in (0, 1), f"{record_id}:{cell['item']}: canary label is not binary")
        labels.append([cell["item"], label])
        evidence_id = cell["positive_evidence_span_id"]
        projected = None
        if evidence_id is not None:
            span = span_by_id[evidence_id]
            projected = {
                "span_id": evidence_id,
                "doc_index": span["doc_index"],
                "start": span["start"],
                "end": span["end"],
                "quote": span["quote"],
                "source_doc_sha256": span["source_doc_sha256"],
            }
        evidence.append({"item": cell["item"], "positive_evidence": projected})
    return {
        "comparison": {
            "record_id": record_id,
            "record_source_sha256": row["source_sha256"],
            "full_record_context_sha256": row["full_record_context_sha256"],
            "full_record_context_schema_version": row[
                "full_record_context_schema_version"
            ],
            "output_schema_sha256": row["output_schema_sha256"],
            "prompt_sha256": row["prompt_sha256"],
            "request_sha256": row["request_sha256"],
            "labels": labels,
            "positive_evidence": evidence,
            "safety_events": {
                "parse_errors": [],
                "unsafe_items": [],
                "service_errors": [],
                "turn_completed": True,
                "attempt_count": 1,
            },
        },
        "integrity": {
            "record_id": record_id,
            "task_manifest_sha256": task_manifest["manifest_sha256"],
            "receipt_sha256": _sha(row),
            "attempt_artifact_sha256": {
                "events.jsonl": base.file_sha256(attempt_dir / "events.jsonl"),
                "raw_final.json": base.file_sha256(attempt_dir / "raw_final.json"),
                "receipt.json": base.file_sha256(attempt_dir / "receipt.json"),
                "stderr.txt": base.file_sha256(attempt_dir / "stderr.txt"),
            },
            "task_artifact_sha256": artifacts,
        },
    }


def inspect_runner_checkpoint(
    plan: Mapping[str, Any],
    *,
    annotator_role: str,
    checkpoint_id: str,
    staging_dir: pathlib.Path,
) -> dict[str, Any]:
    """Inspect one runner directory and return a hashed pass/fail result manifest.

    Runner defects are represented as ``fail_closed`` results so they can close
    an already-open epoch.  A malformed plan is never converted into a result:
    the caller must first supply a valid frozen policy artifact.
    """

    validate_plan(plan)
    if annotator_role not in ROLES:
        raise CanaryError(f"unknown annotator role: {annotator_role}")
    checkpoint = _checkpoint(plan, checkpoint_id)
    staging = staging_dir.resolve()
    failures: list[dict[str, str]] = []
    comparison: dict[str, Any] | None = None
    run_manifest: dict[str, Any] | None = None
    validated_record_artifacts: list[dict[str, Any]] = []
    try:
        run_manifest = _load_json(staging / "run_manifest.json", "runner manifest")
        _validate_run_manifest(
            run_manifest,
            plan=plan,
            annotator_role=annotator_role,
            staging_dir=staging,
        )
        role = plan["roles"][annotator_role]
        source_path = pathlib.Path(plan["organizer_inputs"]["dev"]["path"])
        _require(source_path.is_file(), "frozen organizer dev input is unavailable")
        _require(
            base.file_sha256(source_path)
            == plan["organizer_inputs"]["dev"]["sha256"],
            "frozen organizer dev input hash differs",
        )
        records = list(base.read_records(source_path))
        records_by_id = {str(record["id"]): record for record in records}
        checkpoint_path = staging / "full_records.jsonl"
        rows = _read_jsonl_strict(checkpoint_path, "full-record checkpoint")
        ids = [str(row.get("id") or "") for row in rows]
        _require(len(ids) == len(set(ids)), "checkpoint contains duplicate record IDs")
        _require(ids == role["canary_ids"], "checkpoint rows differ from frozen canary order")
        expected_by_id = {record["record_id"]: record for record in role["records"]}
        projections = []
        for row in rows:
            record_id = str(row["id"])
            _require(record_id in records_by_id, f"unknown organizer record {record_id}")
            validated = _validate_task_and_receipt(
                staging_dir=staging,
                record=records_by_id[record_id],
                expected_record=expected_by_id[record_id],
                row=row,
                run_manifest=run_manifest,
            )
            projections.append(validated["comparison"])
            validated_record_artifacts.append(validated["integrity"])
        comparison = {
            "schema_contract": {
                "runner_schema_version": run_manifest["schema_version"],
                "task_schema_version": runner.TASK_SCHEMA_VERSION,
                "receipt_schema_version": runner.RECEIPT_SCHEMA_VERSION,
                "result_schema_version": runner.RESULT_SCHEMA_VERSION,
                "output_schema_version": full_record_output.SCHEMA_VERSION,
                "ledger_schema_version": full_record_output.LEDGER_SCHEMA_VERSION,
                "output_schema_sha256": plan["runner_contract"][
                    "output_schema_sha256"
                ],
                "target_items": list(runner.TARGET_ITEMS),
            },
            "semantic_identity": {
                "annotator_role": annotator_role,
                "pass_kind": run_manifest["pass_kind"],
                "tuple_sha256": run_manifest["tuple_sha256"],
                "prompt_profile": run_manifest["prompt_lineage"]["profile"],
                "prompt_lineage_sha256": run_manifest["prompt_lineage"][
                    "lineage_sha256"
                ],
                "model_identity": copy.deepcopy(run_manifest["model_identity"]),
                "requested_model": run_manifest["semantic_config"]["model"],
                "reasoning_effort": run_manifest["semantic_config"][
                    "reasoning_effort"
                ],
                "imported_source_bundle_sha256": run_manifest[
                    "imported_source_bundle"
                ]["bundle_sha256"],
            },
            "records": projections,
        }
    except (CanaryError, OSError, ValueError, TypeError, KeyError) as exc:
        failures.append(
            {
                "code": "runner_artifact_untrusted",
                "detail": f"{type(exc).__name__}: {exc}",
            }
        )

    result: dict[str, Any] = {
        "schema_version": RESULT_SCHEMA_VERSION,
        "policy_version": POLICY_VERSION,
        "plan_manifest_sha256": plan["manifest_sha256"],
        "annotator_role": annotator_role,
        "checkpoint": checkpoint,
        "runner_artifact": {
            **_artifact_inventory(staging),
            "validated_record_artifacts": validated_record_artifacts,
            "validated_record_artifacts_sha256": _sha(validated_record_artifacts),
            "run_manifest_sha256": (
                run_manifest.get("manifest_sha256")
                if isinstance(run_manifest, Mapping)
                else None
            ),
            "tuple_sha256": (
                run_manifest.get("tuple_sha256")
                if isinstance(run_manifest, Mapping)
                else None
            ),
        },
        "status": "valid" if not failures else "fail_closed",
        "failures": failures,
        "comparison_projection": comparison if not failures else None,
        "comparison_sha256": _sha(comparison) if not failures else None,
        "observed_utc": base.utc_now(),
        "identity_statement": (
            "Continuity observation only; no exact model revision is attested."
        ),
    }
    result["manifest_sha256"] = _manifest_hash(result)
    validate_result(result, plan=plan)
    return result


def validate_result(result: Mapping[str, Any], *, plan: Mapping[str, Any]) -> None:
    validate_plan(plan)
    _require(set(result) == _RESULT_KEYS, "canary result keys differ from v1 contract")
    _require(result.get("schema_version") == RESULT_SCHEMA_VERSION, "wrong result schema")
    _require(result.get("policy_version") == POLICY_VERSION, "wrong result policy")
    _require(result.get("manifest_sha256") == _manifest_hash(result), "result hash mismatch")
    _require(result.get("plan_manifest_sha256") == plan["manifest_sha256"], "result uses another plan")
    role = result.get("annotator_role")
    _require(role in ROLES, "result role is invalid")
    checkpoint = result.get("checkpoint")
    _require(isinstance(checkpoint, Mapping), "result checkpoint missing")
    _require(checkpoint == _checkpoint(plan, checkpoint.get("checkpoint_id")), "result checkpoint differs from plan")
    status = result.get("status")
    _require(status in {"valid", "fail_closed"}, "result status is invalid")
    failures = result.get("failures")
    _require(isinstance(failures, list), "result failures missing")
    if status == "valid":
        projection = result.get("comparison_projection")
        _require(isinstance(projection, Mapping), "valid result lacks comparison projection")
        _require(result.get("comparison_sha256") == _sha(projection), "comparison hash mismatch")
        _require(not failures, "valid result contains failures")
        identity = projection.get("semantic_identity")
        _require(isinstance(identity, Mapping), "semantic identity missing")
        _require(identity.get("annotator_role") == role, "comparison role differs")
        _require(identity.get("model_identity") == plan["roles"][role]["model_identity"], "comparison model identity differs")
        _require(identity.get("model_identity", {}).get("resolved_revision") is None, "comparison invents a model revision")
        _require(_is_sha256(identity.get("tuple_sha256")), "comparison tuple hash missing")
        _require(
            identity.get("prompt_profile") == plan["roles"][role]["prompt_profile"]
            and identity.get("requested_model")
            == plan["roles"][role]["requested_model"]
            and identity.get("reasoning_effort")
            == plan["roles"][role]["reasoning_effort"],
            "comparison semantic role contract differs",
        )
        schema = projection.get("schema_contract")
        _require(isinstance(schema, Mapping), "comparison schema contract missing")
        expected_schema = {
            "runner_schema_version": plan["runner_contract"][
                "runner_schema_version"
            ],
            "task_schema_version": plan["runner_contract"]["task_schema_version"],
            "receipt_schema_version": plan["runner_contract"][
                "receipt_schema_version"
            ],
            "result_schema_version": plan["runner_contract"][
                "result_schema_version"
            ],
            "output_schema_version": plan["runner_contract"][
                "output_schema_version"
            ],
            "ledger_schema_version": plan["runner_contract"][
                "ledger_schema_version"
            ],
            "output_schema_sha256": plan["runner_contract"][
                "output_schema_sha256"
            ],
            "target_items": plan["runner_contract"]["target_items"],
        }
        _require(schema == expected_schema, "comparison schema contract differs")
        records = projection.get("records")
        _require(isinstance(records, list), "comparison records missing")
        _require([record.get("record_id") for record in records] == plan["roles"][role]["canary_ids"], "comparison record order differs")
        expected_records = {
            record["record_id"]: record for record in plan["roles"][role]["records"]
        }
        dev_source = pathlib.Path(plan["organizer_inputs"]["dev"]["path"])
        _require(dev_source.is_file(), "organizer dev source is unavailable")
        _require(
            base.file_sha256(dev_source)
            == plan["organizer_inputs"]["dev"]["sha256"],
            "organizer dev source hash differs",
        )
        source_records = {
            str(source_record["id"]): source_record
            for source_record in base.read_records(dev_source)
        }
        for record in records:
            expected = expected_records[record["record_id"]]
            _require(
                record.get("record_source_sha256")
                == expected["record_source_sha256"]
                and record.get("full_record_context_sha256")
                == expected["full_record_context_sha256"]
                and record.get("full_record_context_schema_version")
                == expected["full_record_context_schema_version"],
                f"{record['record_id']}: comparison source/context differs",
            )
            labels = record.get("labels")
            _require(
                isinstance(labels, list)
                and [value[0] for value in labels]
                == plan["runner_contract"]["target_items"]
                and all(
                    isinstance(value, list)
                    and len(value) == 2
                    and type(value[1]) is int
                    and value[1] in (0, 1)
                    for value in labels
                ),
                f"{record['record_id']}: comparison labels are not 24 binary cells",
            )
            evidence = record.get("positive_evidence")
            _require(
                isinstance(evidence, list)
                and [value.get("item") for value in evidence]
                == plan["runner_contract"]["target_items"],
                f"{record['record_id']}: evidence projection differs",
            )
            labels_by_item = dict(labels)
            source_record = source_records[record["record_id"]]
            documents = source_record["docs"]
            for evidence_row in evidence:
                item = evidence_row["item"]
                span = evidence_row.get("positive_evidence")
                evidence_required = (
                    labels_by_item[item] == 1
                    and item not in base.annotate_groups.ABSENCE_ITEMS
                )
                _require(
                    (span is not None) == evidence_required,
                    f"{record['record_id']}:{item}: positive evidence presence differs",
                )
                if span is None:
                    continue
                _require(
                    isinstance(span, Mapping)
                    and _is_sha256(span.get("source_doc_sha256"))
                    and type(span.get("doc_index")) is int
                    and type(span.get("start")) is int
                    and type(span.get("end")) is int
                    and span["end"] > span["start"]
                    and isinstance(span.get("quote"), str)
                    and bool(span["quote"]),
                    f"{record['record_id']}: invalid positive evidence projection",
                )
                doc_index = span["doc_index"]
                _require(
                    0 <= doc_index < len(documents),
                    f"{record['record_id']}:{item}: evidence document is invalid",
                )
                text = documents[doc_index]["text"]
                _require(
                    base.sha256_text(text) == span["source_doc_sha256"]
                    and text[span["start"] : span["end"]] == span["quote"],
                    f"{record['record_id']}:{item}: evidence quote/coordinates differ",
                )
            _require(
                record.get("safety_events")
                == {
                    "parse_errors": [],
                    "unsafe_items": [],
                    "service_errors": [],
                    "turn_completed": True,
                    "attempt_count": 1,
                },
                f"{record['record_id']}: safety projection is not clean",
            )
        artifact = result.get("runner_artifact")
        _require(isinstance(artifact, Mapping), "runner artifact identity missing")
        _require(
            artifact.get("validated_record_artifacts_sha256")
            == _sha(artifact.get("validated_record_artifacts")),
            "validated runner-artifact hash mismatch",
        )
    else:
        _require(bool(failures), "fail-closed result lacks a reason")
        _require(result.get("comparison_projection") is None, "failed result must not expose a trusted comparison")
        _require(result.get("comparison_sha256") is None, "failed result must not expose a comparison hash")


def load_result(path: pathlib.Path, *, plan: Mapping[str, Any]) -> dict[str, Any]:
    result = _load_json(path, "canary result")
    validate_result(result, plan=plan)
    return result


def _diff_values(baseline: Any, observed: Any, path: str = "$") -> list[dict[str, Any]]:
    if type(baseline) is not type(observed):
        return [{"path": path, "baseline": baseline, "observed": observed}]
    if isinstance(baseline, Mapping):
        differences: list[dict[str, Any]] = []
        keys = sorted(set(baseline) | set(observed))
        for key in keys:
            child_path = f"{path}.{key}"
            if key not in baseline:
                differences.append({"path": child_path, "baseline": None, "observed": observed[key]})
            elif key not in observed:
                differences.append({"path": child_path, "baseline": baseline[key], "observed": None})
            else:
                differences.extend(_diff_values(baseline[key], observed[key], child_path))
        return differences
    if isinstance(baseline, list):
        differences = []
        for index in range(max(len(baseline), len(observed))):
            child_path = f"{path}[{index}]"
            if index >= len(baseline):
                differences.append({"path": child_path, "baseline": None, "observed": observed[index]})
            elif index >= len(observed):
                differences.append({"path": child_path, "baseline": baseline[index], "observed": None})
            else:
                differences.extend(_diff_values(baseline[index], observed[index], child_path))
        return differences
    return [] if baseline == observed else [{"path": path, "baseline": baseline, "observed": observed}]


def _invalidation(
    plan: Mapping[str, Any],
    *,
    previous_checkpoint: Mapping[str, Any] | None,
    current_checkpoint: Mapping[str, Any],
    cause: str,
    differences: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    if previous_checkpoint is None:
        previous = {
            "qualification_tasks_completed": 0,
            "full_run_tasks_completed": 0,
            "global_tasks_completed": 0,
            "checkpoint_id": None,
        }
    else:
        previous = previous_checkpoint
    segments: list[dict[str, Any]] = []
    for workload_key, plan_key, start_key, end_key in (
        (
            "official_dev_qualification",
            "official_dev_qualification",
            "qualification_tasks_completed",
            "qualification_tasks_completed",
        ),
        (
            "unlabeled_full_run",
            "unlabeled_full_run",
            "full_run_tasks_completed",
            "full_run_tasks_completed",
        ),
    ):
        start = int(previous[start_key])
        end = int(current_checkpoint[end_key])
        if end <= start:
            continue
        ids = plan["workload_order"][plan_key][start:end]
        segments.append(
            {
                "workload": workload_key,
                "ordinal_start_inclusive": start,
                "ordinal_end_exclusive": end,
                "task_count": end - start,
                "record_ids": ids,
                "record_ids_sha256": _sha(ids),
            }
        )
    return {
        "required": True,
        "cause": cause,
        "since_checkpoint_id": previous.get("checkpoint_id"),
        "detected_at_checkpoint_id": current_checkpoint["checkpoint_id"],
        "global_ordinal_start_inclusive": previous["global_tasks_completed"],
        "global_ordinal_end_exclusive": current_checkpoint["global_tasks_completed"],
        "task_count": current_checkpoint["global_tasks_completed"]
        - previous["global_tasks_completed"],
        "segments": segments,
        "differences": [copy.deepcopy(dict(value)) for value in differences],
    }


def _history_entry(result: Mapping[str, Any], outcome: str) -> dict[str, Any]:
    return {
        "checkpoint_index": result["checkpoint"]["checkpoint_index"],
        "checkpoint_id": result["checkpoint"]["checkpoint_id"],
        "kind": result["checkpoint"]["kind"],
        "result_manifest_sha256": result["manifest_sha256"],
        "result_status": result["status"],
        "comparison_sha256": result["comparison_sha256"],
        "runner_artifact_sha256": _sha(result["runner_artifact"]),
        "outcome": outcome,
        "observed_utc": result["observed_utc"],
    }


def start_epoch(plan: Mapping[str, Any], result: Mapping[str, Any]) -> dict[str, Any]:
    validate_plan(plan)
    validate_result(result, plan=plan)
    _require(result["checkpoint"]["checkpoint_index"] == 0, "an epoch must start before qualification")
    role = result["annotator_role"]
    if result["status"] == "valid":
        status = "open"
        baseline_result_sha = result["manifest_sha256"]
        baseline_sha = result["comparison_sha256"]
        baseline_projection = copy.deepcopy(result["comparison_projection"])
        history = [_history_entry(result, "baseline_pass")]
        last_pass = copy.deepcopy(result["checkpoint"])
        next_id = plan["schedule"]["checkpoints"][1]["checkpoint_id"]
        invalidation = None
        requalification = False
    else:
        status = "closed_failure"
        baseline_result_sha = None
        baseline_sha = None
        baseline_projection = None
        history = [_history_entry(result, "fail_closed")]
        last_pass = None
        next_id = None
        invalidation = _invalidation(
            plan,
            previous_checkpoint=None,
            current_checkpoint=result["checkpoint"],
            cause="untrusted_before_qualification_canary",
            differences=result["failures"],
        )
        requalification = True
    epoch_id = _sha(
        {
            "plan_manifest_sha256": plan["manifest_sha256"],
            "annotator_role": role,
            "baseline_result_manifest_sha256": result["manifest_sha256"],
        }
    )
    epoch: dict[str, Any] = {
        "schema_version": EPOCH_SCHEMA_VERSION,
        "policy_version": POLICY_VERSION,
        "plan_manifest_sha256": plan["manifest_sha256"],
        "annotator_role": role,
        "epoch_id": epoch_id,
        "sequence": 0,
        "previous_epoch_manifest_sha256": None,
        "status": status,
        "baseline_result_manifest_sha256": baseline_result_sha,
        "baseline_comparison_sha256": baseline_sha,
        "baseline_comparison_projection": baseline_projection,
        "checkpoint_history": history,
        "last_passing_checkpoint": last_pass,
        "next_required_checkpoint_id": next_id,
        "invalidation": invalidation,
        "continuity_qualified": False,
        "new_epoch_and_requalification_required": requalification,
        "identity_statement": (
            "Continuity epoch only; no exact model revision is attested."
        ),
    }
    epoch["manifest_sha256"] = _manifest_hash(epoch)
    validate_epoch(epoch, plan=plan)
    return epoch


def advance_epoch(
    plan: Mapping[str, Any],
    previous_epoch: Mapping[str, Any],
    result: Mapping[str, Any],
) -> dict[str, Any]:
    validate_plan(plan)
    validate_epoch(previous_epoch, plan=plan)
    validate_result(result, plan=plan)
    _require(previous_epoch["status"] == "open", "only an open epoch may advance")
    _require(result["annotator_role"] == previous_epoch["annotator_role"], "result belongs to another role")
    expected_id = previous_epoch["next_required_checkpoint_id"]
    _require(result["checkpoint"]["checkpoint_id"] == expected_id, f"checkpoint gap: expected {expected_id}, got {result['checkpoint']['checkpoint_id']}")
    observed_artifact_sha = _sha(result["runner_artifact"])
    previous_artifact_hashes = {
        entry.get("runner_artifact_sha256")
        for entry in previous_epoch["checkpoint_history"]
    }
    _require(
        observed_artifact_sha not in previous_artifact_hashes,
        "canary runner artifact was replayed from an earlier checkpoint",
    )
    baseline = previous_epoch["baseline_comparison_projection"]
    differences: list[dict[str, Any]] = []
    if result["status"] == "fail_closed":
        outcome = "fail_closed"
        status = "closed_failure"
        cause = "untrusted_canary_artifact"
        differences = [copy.deepcopy(value) for value in result["failures"]]
    else:
        differences = _diff_values(baseline, result["comparison_projection"])
        if differences:
            outcome = "drift"
            status = "closed_drift"
            cause = "continuity_projection_changed"
        else:
            outcome = "pass"
            status = (
                "complete"
                if result["checkpoint"]["kind"] == "post_run"
                else "open"
            )
            cause = ""

    history = copy.deepcopy(previous_epoch["checkpoint_history"])
    history.append(_history_entry(result, outcome))
    if status in {"closed_failure", "closed_drift"}:
        invalidation = _invalidation(
            plan,
            previous_checkpoint=previous_epoch["last_passing_checkpoint"],
            current_checkpoint=result["checkpoint"],
            cause=cause,
            differences=differences,
        )
        last_pass = copy.deepcopy(previous_epoch["last_passing_checkpoint"])
        next_id = None
        continuity_qualified = False
        requalification = True
    else:
        invalidation = None
        last_pass = copy.deepcopy(result["checkpoint"])
        next_index = result["checkpoint"]["checkpoint_index"] + 1
        next_id = (
            plan["schedule"]["checkpoints"][next_index]["checkpoint_id"]
            if next_index < plan["schedule"]["checkpoint_count"]
            else None
        )
        continuity_qualified = status == "complete"
        requalification = False

    epoch: dict[str, Any] = {
        "schema_version": EPOCH_SCHEMA_VERSION,
        "policy_version": POLICY_VERSION,
        "plan_manifest_sha256": plan["manifest_sha256"],
        "annotator_role": previous_epoch["annotator_role"],
        "epoch_id": previous_epoch["epoch_id"],
        "sequence": previous_epoch["sequence"] + 1,
        "previous_epoch_manifest_sha256": previous_epoch["manifest_sha256"],
        "status": status,
        "baseline_result_manifest_sha256": previous_epoch[
            "baseline_result_manifest_sha256"
        ],
        "baseline_comparison_sha256": previous_epoch[
            "baseline_comparison_sha256"
        ],
        "baseline_comparison_projection": copy.deepcopy(baseline),
        "checkpoint_history": history,
        "last_passing_checkpoint": last_pass,
        "next_required_checkpoint_id": next_id,
        "invalidation": invalidation,
        "continuity_qualified": continuity_qualified,
        "new_epoch_and_requalification_required": requalification,
        "identity_statement": previous_epoch["identity_statement"],
    }
    epoch["manifest_sha256"] = _manifest_hash(epoch)
    validate_epoch(epoch, plan=plan)
    return epoch


def validate_epoch(epoch: Mapping[str, Any], *, plan: Mapping[str, Any]) -> None:
    validate_plan(plan)
    _require(set(epoch) == _EPOCH_KEYS, "epoch keys differ from v1 contract")
    _require(epoch.get("schema_version") == EPOCH_SCHEMA_VERSION, "wrong epoch schema")
    _require(epoch.get("policy_version") == POLICY_VERSION, "wrong epoch policy")
    _require(epoch.get("manifest_sha256") == _manifest_hash(epoch), "epoch hash mismatch")
    _require(epoch.get("plan_manifest_sha256") == plan["manifest_sha256"], "epoch uses another plan")
    role = epoch.get("annotator_role")
    _require(role in ROLES, "epoch role is invalid")
    history = epoch.get("checkpoint_history")
    _require(isinstance(history, list) and history, "epoch history is empty")
    _require(epoch.get("sequence") == len(history) - 1, "epoch sequence/history mismatch")
    indices = [entry.get("checkpoint_index") for entry in history]
    _require(indices == list(range(len(history))), "epoch contains a checkpoint gap")
    schedule = plan["schedule"]["checkpoints"]
    for index, entry in enumerate(history):
        _require(entry.get("checkpoint_id") == schedule[index]["checkpoint_id"], "epoch checkpoint history differs from plan")
        _require(_is_sha256(entry.get("runner_artifact_sha256")), "epoch runner artifact hash missing")
    artifact_hashes = [entry["runner_artifact_sha256"] for entry in history]
    _require(
        len(artifact_hashes) == len(set(artifact_hashes)),
        "epoch reuses a canary runner artifact",
    )
    status = epoch.get("status")
    _require(status in {"open", "complete", "closed_failure", "closed_drift"}, "epoch status invalid")
    _require(
        epoch.get("epoch_id")
        == _sha(
            {
                "plan_manifest_sha256": plan["manifest_sha256"],
                "annotator_role": role,
                "baseline_result_manifest_sha256": history[0][
                    "result_manifest_sha256"
                ],
            }
        ),
        "epoch ID differs from its baseline observation",
    )
    passing_outcomes = {"baseline_pass", "pass"}
    passing_entries = [entry for entry in history if entry.get("outcome") in passing_outcomes]
    expected_last_pass = (
        copy.deepcopy(schedule[passing_entries[-1]["checkpoint_index"]])
        if passing_entries
        else None
    )
    _require(
        epoch.get("last_passing_checkpoint") == expected_last_pass,
        "epoch last-passing checkpoint differs from history",
    )
    if status == "open":
        _require(history[-1].get("outcome") in passing_outcomes, "open epoch did not pass its last canary")
        _require(epoch.get("next_required_checkpoint_id") == schedule[len(history)]["checkpoint_id"], "open epoch next checkpoint differs")
        _require(epoch.get("invalidation") is None, "open epoch cannot invalidate tasks")
        _require(epoch.get("continuity_qualified") is False, "open epoch is not complete")
        _require(epoch.get("new_epoch_and_requalification_required") is False, "open epoch cannot require a reset")
    elif status == "complete":
        _require(history[-1].get("outcome") == "pass", "complete epoch did not pass post-run canary")
        _require(len(history) == len(schedule), "complete epoch lacks checkpoints")
        _require(epoch.get("next_required_checkpoint_id") is None, "complete epoch has another checkpoint")
        _require(epoch.get("invalidation") is None, "complete epoch cannot invalidate tasks")
        _require(epoch.get("continuity_qualified") is True, "complete epoch must be continuity-qualified")
        _require(epoch.get("new_epoch_and_requalification_required") is False, "complete epoch cannot require a reset")
    else:
        expected_outcome = "drift" if status == "closed_drift" else "fail_closed"
        _require(history[-1].get("outcome") == expected_outcome, "closed epoch outcome differs")
        _require(epoch.get("next_required_checkpoint_id") is None, "closed epoch cannot advance")
        invalidation = epoch.get("invalidation")
        _require(isinstance(invalidation, Mapping) and invalidation.get("required") is True, "closed epoch lacks invalidation")
        _require(epoch.get("continuity_qualified") is False, "closed epoch cannot qualify")
        _require(epoch.get("new_epoch_and_requalification_required") is True, "closed epoch must require requalification")
        expected_cause = (
            "continuity_projection_changed"
            if status == "closed_drift"
            else (
                "untrusted_before_qualification_canary"
                if history[-1]["checkpoint_index"] == 0
                else "untrusted_canary_artifact"
            )
        )
        expected_invalidation = _invalidation(
            plan,
            previous_checkpoint=expected_last_pass,
            current_checkpoint=schedule[history[-1]["checkpoint_index"]],
            cause=expected_cause,
            differences=invalidation.get("differences") or [],
        )
        _require(
            invalidation == expected_invalidation,
            "closed epoch invalidation range differs from frozen workload order",
        )
    identity = epoch.get("baseline_comparison_projection")
    if epoch.get("baseline_comparison_sha256") is not None:
        _require(isinstance(identity, Mapping), "epoch baseline projection missing")
        _require(epoch["baseline_comparison_sha256"] == _sha(identity), "epoch baseline hash mismatch")


def load_epoch(path: pathlib.Path, *, plan: Mapping[str, Any]) -> dict[str, Any]:
    epoch = _load_json(path, "canary epoch")
    validate_epoch(epoch, plan=plan)
    return epoch


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    plan_parser = subparsers.add_parser("plan", help="freeze a label-free canary plan")
    plan_parser.add_argument("--output", type=pathlib.Path, required=True)
    plan_parser.add_argument("--dev-input", type=pathlib.Path, default=DEFAULT_DEV_INPUT)
    plan_parser.add_argument("--full-input", type=pathlib.Path, default=DEFAULT_FULL_INPUT)
    plan_parser.add_argument("--canary-count", type=int, default=8)
    plan_parser.add_argument("--selection-seed", default="dacon-236754-independent-gold-canary-v1")
    plan_parser.add_argument("--interval-every", type=int, default=500)
    plan_parser.add_argument("--candidate-reasoning-effort", default="xhigh")
    plan_parser.add_argument("--verifier-reasoning-effort", default="max")

    command_parser = subparsers.add_parser("runner-command", help="print a frozen runner argv as JSON")
    command_parser.add_argument("--plan", type=pathlib.Path, required=True)
    command_parser.add_argument("--role", choices=ROLES, required=True)
    command_parser.add_argument("--checkpoint", required=True)
    command_parser.add_argument("--staging-dir", type=pathlib.Path, required=True)
    command_parser.add_argument("--codex-bin", default="codex")

    observe_parser = subparsers.add_parser("observe", help="freeze one canary result")
    observe_parser.add_argument("--plan", type=pathlib.Path, required=True)
    observe_parser.add_argument("--role", choices=ROLES, required=True)
    observe_parser.add_argument("--checkpoint", required=True)
    observe_parser.add_argument("--staging-dir", type=pathlib.Path, required=True)
    observe_parser.add_argument("--output", type=pathlib.Path, required=True)

    advance_parser = subparsers.add_parser("advance", help="start or advance a role-specific epoch")
    advance_parser.add_argument("--plan", type=pathlib.Path, required=True)
    advance_parser.add_argument("--result", type=pathlib.Path, required=True)
    advance_parser.add_argument("--previous-epoch", type=pathlib.Path)
    advance_parser.add_argument("--output", type=pathlib.Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "plan":
        plan = build_plan(
            dev_input_path=args.dev_input,
            full_input_path=args.full_input,
            canary_count=args.canary_count,
            selection_seed=args.selection_seed,
            interval_every=args.interval_every,
            candidate_reasoning_effort=args.candidate_reasoning_effort,
            verifier_reasoning_effort=args.verifier_reasoning_effort,
        )
        _write_json_immutable(args.output.resolve(), plan)
        print(_canonical({"status": "ok", "manifest_sha256": plan["manifest_sha256"], "output": str(args.output.resolve())}))
        return 0
    plan = load_plan(args.plan.resolve())
    if args.command == "runner-command":
        command = build_runner_command(
            plan,
            annotator_role=args.role,
            checkpoint_id=args.checkpoint,
            staging_dir=args.staging_dir,
            codex_bin=args.codex_bin,
        )
        print(json.dumps(command, ensure_ascii=False, indent=2))
        return 0
    if args.command == "observe":
        result = inspect_runner_checkpoint(
            plan,
            annotator_role=args.role,
            checkpoint_id=args.checkpoint,
            staging_dir=args.staging_dir,
        )
        _write_json_immutable(args.output.resolve(), result)
        print(_canonical({"status": result["status"], "manifest_sha256": result["manifest_sha256"], "output": str(args.output.resolve())}))
        return 0 if result["status"] == "valid" else 2
    result = load_result(args.result.resolve(), plan=plan)
    if args.previous_epoch:
        previous = load_epoch(args.previous_epoch.resolve(), plan=plan)
        epoch = advance_epoch(plan, previous, result)
    else:
        epoch = start_epoch(plan, result)
    _write_json_immutable(args.output.resolve(), epoch)
    print(_canonical({"status": epoch["status"], "manifest_sha256": epoch["manifest_sha256"], "output": str(args.output.resolve())}))
    return 0 if epoch["status"] in {"open", "complete"} else 2


__all__ = [
    "CANARY_PHASE",
    "CanaryError",
    "EPOCH_SCHEMA_VERSION",
    "PLAN_SCHEMA_VERSION",
    "POLICY_VERSION",
    "RESULT_SCHEMA_VERSION",
    "advance_epoch",
    "build_plan",
    "build_plan_from_records",
    "build_runner_command",
    "build_schedule",
    "inspect_runner_checkpoint",
    "load_epoch",
    "load_plan",
    "load_result",
    "start_epoch",
    "validate_epoch",
    "validate_plan",
    "validate_result",
]


if __name__ == "__main__":
    raise SystemExit(main())
