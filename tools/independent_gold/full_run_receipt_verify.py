"""Strict read-only verification for topology-B full-run receipts.

This module is the trust boundary between model-run directories and the review
ledger.  It does not repair, normalize, or rewrite a run.  Every selected row
is reconstructed from the organizer record and the currently reviewed runner
sources, every persisted attempt is checked, and the successful raw model
output is replayed into its canonical 24-cell ledger.  When a byte-verified
frozen source archive is explicitly supplied, only qualification-context
source drift is permitted; all reconstructed task bytes must still match.

The public :func:`verify_role_runs` API intentionally retains only checkpoint
byte offsets after verification.  A 20,000-row checkpoint embeds both raw
answers and canonical ledgers, so retaining parsed rows would otherwise make
memory usage proportional to the complete annotation payload.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import math
import pathlib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping, Sequence

try:
    from tools.independent_gold import codex_cli_annotator as base
    from tools.independent_gold import codex_full_record_annotator as runner
    from tools.independent_gold import full_record_context
    from tools.independent_gold import full_record_output
except ModuleNotFoundError:  # Direct execution from this directory.
    import codex_cli_annotator as base  # type: ignore[no-redef]
    import codex_full_record_annotator as runner  # type: ignore[no-redef]
    import full_record_context  # type: ignore[no-redef]
    import full_record_output  # type: ignore[no-redef]


EXPECTED_PHASE = "unlabeled_20000"
CHECKPOINT_NAME = "full_records.jsonl"
ATTEMPT_FILES = frozenset(
    {"events.jsonl", "stderr.txt", "raw_final.json", "receipt.json"}
)
TASK_FILES = frozenset(
    {
        "task_manifest.json",
        "prompt.txt",
        "output_schema.json",
        "full_record_context.json",
    }
)
DESCRIPTOR_SCHEMA_VERSION = "dacon.independent.full_run_descriptor.v1"
SOURCE_ARCHIVE_SCHEMA_VERSION = "dacon.independent.source_archive.v1"
_ARCHIVE_ALLOWED_CURRENT_DRIFT = frozenset(
    {"tools/independent_gold/qualification_context.py"}
)


class FullRunReceiptError(ValueError):
    """A full-run artifact violated a fail-closed verification invariant."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise FullRunReceiptError(message)


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise FullRunReceiptError(f"duplicate JSON object key: {key!r}")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise FullRunReceiptError(f"non-standard JSON constant is forbidden: {value}")


def _reject_nonfinite(value: Any, *, context: str) -> None:
    if isinstance(value, float) and not math.isfinite(value):
        raise FullRunReceiptError(f"{context}: non-finite JSON number")
    if isinstance(value, list):
        for child in value:
            _reject_nonfinite(child, context=context)
    elif isinstance(value, Mapping):
        for child in value.values():
            _reject_nonfinite(child, context=context)


def _strict_json(data: str | bytes, *, context: str) -> Any:
    if isinstance(data, bytes):
        try:
            data = data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise FullRunReceiptError(f"{context}: invalid UTF-8") from exc
    try:
        value = json.loads(
            data,
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_constant,
        )
    except FullRunReceiptError as exc:
        raise FullRunReceiptError(f"{context}: {exc}") from exc
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        raise FullRunReceiptError(f"{context}: invalid JSON: {exc}") from exc
    _reject_nonfinite(value, context=context)
    return value


def _strict_object(data: str | bytes, *, context: str) -> dict[str, Any]:
    value = _strict_json(data, context=context)
    _require(isinstance(value, dict), f"{context}: expected one JSON object")
    return value


def _load_canonical_object(path: pathlib.Path, *, context: str) -> dict[str, Any]:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise FullRunReceiptError(f"{context}: cannot read {path}: {exc}") from exc
    value = _strict_object(raw, context=context)
    expected = (base.canonical_json(value) + "\n").encode("utf-8")
    _require(raw == expected, f"{context}: file is not canonical immutable JSON")
    return value


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _manifest_hash(value: Mapping[str, Any]) -> str:
    return base.sha256_object(
        {key: child for key, child in value.items() if key != "manifest_sha256"}
    )


def _verify_frozen_source_archive(
    archive_dir: pathlib.Path,
) -> dict[str, Any]:
    """Verify every archived source byte before it can stand in for live bytes.

    An archive is never an alternative task answer.  Its exact files must still
    match each run's already-hashed imported-source bundle, while the current
    verifier independently rebuilds every source context, prompt and ledger.
    """

    _require(archive_dir.is_dir() and not archive_dir.is_symlink(), "source archive missing or symlinked")
    manifest_path = archive_dir / "manifest.json"
    try:
        raw = manifest_path.read_bytes()
    except OSError as exc:
        raise FullRunReceiptError(f"source archive manifest unreadable: {exc}") from exc
    archive = _strict_object(raw, context="source archive manifest")
    canonical = base.canonical_json(archive).encode("utf-8")
    _require(
        raw in (canonical + b"\n", canonical + b"\r\n"),
        "source archive manifest is not canonical JSON",
    )
    _require(
        set(archive)
        == {
            "schema_version",
            "archive_kind",
            "bundle_sha256",
            "files",
            "rubric_sha256",
            "run_manifest_sha256",
        },
        "source archive manifest keys differ",
    )
    _require(
        archive["schema_version"] == SOURCE_ARCHIVE_SCHEMA_VERSION
        and archive["archive_kind"] == "mechanical_source_bytes_only_no_model_call",
        "source archive kind/schema mismatch",
    )
    files = archive["files"]
    _require(isinstance(files, list) and files, "source archive file list missing")
    paths: list[str] = []
    for index, entry in enumerate(files):
        _require(
            isinstance(entry, dict) and set(entry) == {"path", "sha256"},
            f"source archive file {index}: invalid descriptor",
        )
        relative = entry["path"]
        _require(
            isinstance(relative, str)
            and relative.startswith("tools/independent_gold/")
            and "\\" not in relative
            and pathlib.PurePosixPath(relative).parts
            == tuple(relative.split("/"))
            and ".." not in pathlib.PurePosixPath(relative).parts,
            f"source archive file {index}: unsafe path",
        )
        _require(
            isinstance(entry["sha256"], str)
            and len(entry["sha256"]) == 64
            and all(char in "0123456789abcdef" for char in entry["sha256"]),
            f"source archive file {index}: invalid SHA-256",
        )
        path = archive_dir.joinpath(*relative.split("/"))
        cursor = archive_dir
        for component in relative.split("/"):
            cursor = cursor / component
            _require(
                not cursor.is_symlink(),
                f"source archive file {index}: symlinked path component",
            )
        _require(
            path.is_file(),
            f"source archive file {index}: missing or symlinked",
        )
        _require(
            path.resolve().is_relative_to(archive_dir.resolve()),
            f"source archive file {index}: path escapes archive",
        )
        _require(
            base.file_sha256(path) == entry["sha256"],
            f"source archive file {index}: byte SHA-256 mismatch",
        )
        paths.append(relative)
    _require(
        paths == sorted(set(paths)),
        "source archive files must be unique and sorted",
    )
    rubric_relative = "tools/independent_gold/rubric_v1.md"
    rubric_path = archive_dir.joinpath(*rubric_relative.split("/"))
    _require(rubric_path.is_file() and not rubric_path.is_symlink(), "source archive rubric missing")
    _require(
        base.file_sha256(rubric_path) == archive["rubric_sha256"],
        "source archive rubric byte SHA-256 mismatch",
    )
    archive_entries = list(archive_dir.rglob("*"))
    _require(
        not any(path.is_symlink() for path in archive_entries),
        "source archive contains a symlink",
    )
    actual_files = {
        path.relative_to(archive_dir).as_posix()
        for path in archive_entries
        if path.is_file()
    }
    _require(
        actual_files == set(paths) | {rubric_relative, "manifest.json"},
        "source archive contains missing or unexpected files",
    )
    bundle = {
        "schema_version": runner.SOURCE_BUNDLE_SCHEMA_VERSION,
        "files": files,
    }
    bundle["bundle_sha256"] = base.sha256_object(bundle)
    _require(
        archive["bundle_sha256"] == bundle["bundle_sha256"],
        "source archive bundle SHA-256 mismatch",
    )
    _require(
        isinstance(archive["run_manifest_sha256"], str)
        and len(archive["run_manifest_sha256"]) == 64
        and all(char in "0123456789abcdef" for char in archive["run_manifest_sha256"]),
        "source archive anchor run-manifest SHA-256 invalid",
    )
    return {
        "archive": archive,
        "bundle": bundle,
        "manifest_file_sha256": _sha256_bytes(raw),
    }


def _rebuild_with_frozen_source_bundle(
    rebuilt: Mapping[str, Any],
    frozen_bundle: Mapping[str, Any],
) -> dict[str, Any]:
    """Recompute the runner's exact tuple/run hashes with verified old bytes."""

    result = dict(rebuilt)
    result["imported_source_bundle"] = dict(frozen_bundle)
    tuple_payload = {
        "schema_version": result["schema_version"],
        "annotator_role": result["annotator_role"],
        "pass_kind": result["pass_kind"],
        "prompt_lineage": result["prompt_lineage"],
        "model_identity": result["model_identity"],
        "semantic_config": result["semantic_config"],
        "imported_source_bundle": result["imported_source_bundle"],
        "codex_cli": {
            "executable_sha256": result["codex_cli"]["executable_sha256"],
            "version_output": result["codex_cli"]["version_output"],
        },
        "execution_isolation": result["execution_isolation"],
        "peer_visibility": result["peer_visibility"],
    }
    result["tuple_sha256"] = base.sha256_object(tuple_payload)
    result["run_instance_sha256"] = base.sha256_object(
        {
            "tuple_sha256": result["tuple_sha256"],
            "record_input_sha256": result["record_input"]["sha256"],
            "phase": result["phase"],
            "cohort_plan_sha256": result["cohort_plan"]["manifest_sha256"],
        }
    )
    result["run_key"] = result["run_instance_sha256"]
    result["manifest_sha256"] = _manifest_hash(result)
    return result


def _parse_utc(value: Any, *, context: str) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        _require(
            isinstance(value, str) and bool(value.strip()),
            f"{context}: timestamp must be non-empty text",
        )
        normalized = value.strip()
        if normalized.endswith("Z"):
            normalized = normalized[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(normalized)
        except ValueError as exc:
            raise FullRunReceiptError(f"{context}: invalid ISO-8601 timestamp") from exc
    _require(parsed.tzinfo is not None, f"{context}: timezone is required")
    try:
        return parsed.astimezone(timezone.utc)
    except (OverflowError, ValueError) as exc:
        raise FullRunReceiptError(f"{context}: invalid timezone offset") from exc


def _record_id(record: Mapping[str, Any], *, context: str) -> str:
    value = record.get("id")
    _require(
        isinstance(value, str) and bool(value),
        f"{context}: record id must be non-empty text",
    )
    return value


def _open_record_input(path: pathlib.Path) -> Any:
    if path.name.endswith(".gz"):
        return gzip.open(path, "rb")
    return path.open("rb")


def _verify_organizer_records(
    organizer_input: pathlib.Path,
    records: Mapping[str, Mapping[str, Any]],
) -> tuple[list[str], dict[str, Mapping[str, Any]]]:
    _require(organizer_input.is_file(), f"organizer input is missing: {organizer_input}")
    normalized: dict[str, Mapping[str, Any]] = {}
    for supplied_key, record in records.items():
        _require(isinstance(record, Mapping), f"records[{supplied_key!r}] is not an object")
        record_id = _record_id(record, context=f"records[{supplied_key!r}]")
        _require(str(supplied_key) == record_id, f"records mapping key/id mismatch for {record_id}")
        _require(record_id not in normalized, f"duplicate supplied record id {record_id}")
        normalized[record_id] = record

    organizer_ids: list[str] = []
    seen: set[str] = set()
    try:
        handle = _open_record_input(organizer_input)
        with handle:
            for line_number, raw in enumerate(handle, 1):
                _require(raw.strip() != b"", f"organizer input line {line_number}: blank row")
                row = _strict_object(
                    raw,
                    context=f"organizer input line {line_number}",
                )
                record_id = _record_id(row, context=f"organizer input line {line_number}")
                _require(record_id not in seen, f"organizer input: duplicate id {record_id}")
                seen.add(record_id)
                organizer_ids.append(record_id)
                _require(record_id in normalized, f"organizer input: unexpected id {record_id}")
                _require(
                    row == normalized[record_id],
                    f"organizer input: supplied record differs for {record_id}",
                )
    except (OSError, EOFError) as exc:
        raise FullRunReceiptError(f"cannot read organizer input: {exc}") from exc
    _require(
        set(organizer_ids) == set(normalized),
        "organizer input IDs do not exactly equal the supplied records",
    )
    _require(organizer_ids, "organizer input is empty")
    return organizer_ids, normalized


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


def _verify_model_identity(identity: Any, *, model: str, context: str) -> None:
    _require(isinstance(identity, Mapping), f"{context}: invalid model identity")
    attestation = identity.get("attestation")
    attestation_path: pathlib.Path | None = None
    if attestation is not None:
        _require(isinstance(attestation, Mapping), f"{context}: invalid attestation")
        attestation_path = pathlib.Path(str(attestation.get("path") or ""))
    try:
        expected = base.build_model_identity(
            model=model,
            mode=str(identity.get("mode") or ""),
            model_revision=identity.get("requested_revision"),
            attestation_path=attestation_path,
        )
    except (OSError, TypeError, ValueError) as exc:
        raise FullRunReceiptError(f"{context}: invalid model identity: {exc}") from exc
    _require(identity == expected, f"{context}: model identity contract mismatch")


def _verify_partition(
    plan: Mapping[str, Any],
    *,
    organizer_ids: Sequence[str],
    organizer_input: pathlib.Path,
    context: str,
) -> list[str]:
    _require(
        plan.get("manifest_sha256") == _manifest_hash(plan),
        f"{context}: cohort-plan hash mismatch",
    )
    try:
        runner.validate_cohort_plan(
            plan,
            input_path=organizer_input,
            phase=EXPECTED_PHASE,
        )
    except (TypeError, ValueError) as exc:
        raise FullRunReceiptError(f"{context}: invalid cohort plan: {exc}") from exc

    _require(
        plan.get("input_record_count") == len(organizer_ids),
        f"{context}: input record count mismatch",
    )
    requested = plan.get("requested_ids")
    selected = plan.get("selected_ids")
    _require(isinstance(requested, list), f"{context}: requested_ids is not a list")
    _require(isinstance(selected, list), f"{context}: selected_ids is not a list")
    _require(
        all(isinstance(value, str) and value for value in requested),
        f"{context}: invalid requested id",
    )
    _require(len(requested) == len(set(requested)), f"{context}: duplicate requested id")
    organizer_set = set(organizer_ids)
    _require(set(requested) <= organizer_set, f"{context}: requested id is outside input")
    matched = [
        record_id
        for record_id in organizer_ids
        if not requested or record_id in set(requested)
    ]
    partitioning = plan.get("partitioning")
    _require(isinstance(partitioning, Mapping), f"{context}: invalid partitioning")
    shard_index = partitioning.get("shard_index")
    shard_count = partitioning.get("shard_count")
    limit_after = partitioning.get("limit_after_sharding")
    _require(
        partitioning.get("method") == "organizer_order_stride_v1"
        and type(shard_index) is int
        and type(shard_count) is int
        and shard_count >= 1
        and 0 <= shard_index < shard_count,
        f"{context}: invalid organizer-order shard",
    )
    expected = matched[shard_index::shard_count]
    if limit_after is not None:
        _require(type(limit_after) is int and limit_after > 0, f"{context}: invalid limit")
        expected = expected[:limit_after]
    _require(selected == expected, f"{context}: selected IDs differ from deterministic shard")
    _require(plan.get("matched_record_count") == len(matched), f"{context}: matched count mismatch")
    return list(selected)


def _verify_run_manifest(
    run_dir: pathlib.Path,
    *,
    role: str,
    organizer_input: pathlib.Path,
    organizer_ids: Sequence[str],
    fact_catalog: Any,
    qualification_catalog: Any,
    frozen_source: Mapping[str, Any] | None = None,
) -> tuple[dict[str, Any], list[str], str]:
    context = f"run {run_dir}"
    manifest_path = run_dir / "run_manifest.json"
    manifest = _load_canonical_object(manifest_path, context=f"{context} manifest")
    _require(
        manifest.get("schema_version") == runner.RUNNER_SCHEMA_VERSION,
        f"{context}: wrong runner schema",
    )
    _require(
        manifest.get("manifest_sha256") == _manifest_hash(manifest),
        f"{context}: run-manifest hash mismatch",
    )
    _require(manifest.get("annotator_role") == role, f"{context}: annotator role mismatch")
    _require(manifest.get("phase") == EXPECTED_PHASE, f"{context}: phase mismatch")
    _require(manifest.get("pass_kind") == runner.PASS_KIND, f"{context}: pass is not blind")
    _require(
        manifest.get("peer_visibility") == base.blind_peer_visibility(),
        f"{context}: peer visibility is not blind",
    )
    _require(
        pathlib.Path(str(manifest.get("staging_dir") or "")).resolve() == run_dir,
        f"{context}: staging directory binding mismatch",
    )
    record_input = manifest.get("record_input")
    _require(isinstance(record_input, Mapping), f"{context}: invalid record input")
    input_sha = base.file_sha256(organizer_input)
    _require(
        pathlib.Path(str(record_input.get("path") or "")).resolve() == organizer_input,
        f"{context}: organizer path mismatch",
    )
    _require(record_input.get("sha256") == input_sha, f"{context}: organizer hash mismatch")
    _require(
        record_input.get("allowed_kind") == organizer_input.name,
        f"{context}: organizer kind mismatch",
    )

    plan = manifest.get("cohort_plan")
    _require(isinstance(plan, Mapping), f"{context}: missing cohort plan")
    persisted_plan = _load_canonical_object(
        run_dir / "cohort_plan.json", context=f"{context} cohort plan"
    )
    _require(persisted_plan == plan, f"{context}: persisted/embedded cohort plan mismatch")
    selected = _verify_partition(
        plan,
        organizer_ids=organizer_ids,
        organizer_input=organizer_input,
        context=context,
    )

    profile_name = runner.prompt_profiles.DEFAULT_PROFILE_BY_ROLE[role]
    _require(
        (manifest.get("prompt_lineage") or {}).get("profile") == profile_name,
        f"{context}: non-canonical role prompt profile",
    )
    profile = runner.prompt_profiles.get_profile(profile_name, annotator_role=role)
    _require(
        manifest.get("prompt_lineage") == runner.build_prompt_lineage(profile),
        f"{context}: prompt lineage mismatch",
    )
    model = runner.ROLE_MODEL[role]
    config = manifest.get("semantic_config")
    _require(isinstance(config, Mapping), f"{context}: invalid semantic config")
    _require(config.get("model") == model, f"{context}: role model mismatch")
    _require(
        config.get("annotation_topology") == "one_call_per_record_all_24_items"
        and config.get("tool_event_policy")
        == "reject_every_item_type_except_agent_message_and_reasoning",
        f"{context}: topology or event policy mismatch",
    )
    _verify_model_identity(manifest.get("model_identity"), model=model, context=context)
    if frozen_source is None:
        _require(
            manifest.get("imported_source_bundle")
            == runner.build_imported_source_bundle(profile),
            f"{context}: imported source bundle mismatch",
        )
    else:
        frozen_bundle = frozen_source["bundle"]
        _require(
            manifest.get("imported_source_bundle") == frozen_bundle,
            f"{context}: run source bundle differs from byte-verified frozen archive",
        )
        _require(
            (manifest.get("semantic_config") or {}).get("rubric_sha256")
            == frozen_source["archive"]["rubric_sha256"],
            f"{context}: frozen archive rubric differs from run",
        )
        current_bundle = runner.build_imported_source_bundle(profile)
        current_files = {
            row["path"]: row["sha256"] for row in current_bundle["files"]
        }
        frozen_files = {
            row["path"]: row["sha256"] for row in frozen_bundle["files"]
        }
        _require(
            set(current_files) == set(frozen_files),
            f"{context}: frozen/current source path sets differ",
        )
        drift = {
            path
            for path in current_files
            if current_files[path] != frozen_files[path]
        }
        _require(
            drift <= _ARCHIVE_ALLOWED_CURRENT_DRIFT,
            f"{context}: unsupported frozen/current source drift: {sorted(drift)}",
        )

    cli = manifest.get("codex_cli")
    _require(isinstance(cli, Mapping), f"{context}: invalid CLI provenance")
    executable = pathlib.Path(str(cli.get("resolved_executable") or "")).resolve()
    _require(executable.is_file(), f"{context}: CLI executable is unavailable")
    _require(
        cli.get("executable_sha256") == base.file_sha256(executable),
        f"{context}: CLI executable hash mismatch",
    )
    _require(
        isinstance(cli.get("version_output"), str) and bool(cli.get("version_output")),
        f"{context}: missing CLI version",
    )
    threat_path = run_dir / "THREAT_MODEL.md"
    _require(threat_path.is_file(), f"{context}: missing threat model")
    _require(
        threat_path.read_text(encoding="utf-8") == base.THREAT_MODEL,
        f"{context}: threat-model content mismatch",
    )

    try:
        rebuilt = runner.build_run_manifest(
            input_path=organizer_input,
            staging_dir=run_dir,
            model=model,
            reasoning_effort=str(config.get("reasoning_effort") or ""),
            cli_provenance=cli,
            rubric=runner.RUBRIC_PATH.read_text(encoding="utf-8"),
            fact_catalog=fact_catalog,
            qualification_catalog=qualification_catalog,
            annotator_role=role,
            prompt_profile=profile,
            phase=EXPECTED_PHASE,
            cohort_plan=plan,
            model_identity=manifest["model_identity"],
        )
    except (OSError, TypeError, ValueError) as exc:
        raise FullRunReceiptError(f"{context}: cannot rebuild run manifest: {exc}") from exc
    if frozen_source is not None:
        rebuilt = _rebuild_with_frozen_source_bundle(
            rebuilt, frozen_source["bundle"]
        )
    _require(manifest == rebuilt, f"{context}: run manifest differs from reviewed runner output")
    return manifest, selected, base.file_sha256(manifest_path)


def _verify_task(
    *,
    run_dir: pathlib.Path,
    manifest: Mapping[str, Any],
    record: Mapping[str, Any],
    fact_catalog: Any,
    qualification_catalog: Any,
) -> tuple[pathlib.Path, dict[str, Any], dict[str, Any]]:
    record_id = _record_id(record, context="task record")
    task_dir = run_dir / "tasks" / base._task_slug(record_id) / runner.GROUP_NAME
    context = f"{run_dir} task {record_id}"
    _require(task_dir.is_dir(), f"{context}: missing task directory")
    actual_files = {child.name for child in task_dir.iterdir() if child.is_file()}
    actual_dirs = {child.name for child in task_dir.iterdir() if child.is_dir()}
    _require(actual_files == TASK_FILES, f"{context}: task artifact set mismatch")
    _require(actual_dirs == {"attempts"}, f"{context}: task directory set mismatch")

    task_manifest = _load_canonical_object(
        task_dir / "task_manifest.json", context=f"{context} manifest"
    )
    _require(set(task_manifest) == runner.TASK_MANIFEST_KEYS, f"{context}: manifest keys differ")
    _require(
        task_manifest.get("manifest_sha256") == _manifest_hash(task_manifest),
        f"{context}: manifest hash mismatch",
    )

    try:
        context_raw = (task_dir / "full_record_context.json").read_bytes()
        prompt_raw = (task_dir / "prompt.txt").read_bytes()
        schema_raw = (task_dir / "output_schema.json").read_bytes()
    except OSError as exc:
        raise FullRunReceiptError(f"{context}: cannot read task artifact: {exc}") from exc
    supplied_context = _strict_object(context_raw, context=f"{context} source context")
    supplied_schema = _strict_object(schema_raw, context=f"{context} output schema")
    try:
        prompt = prompt_raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise FullRunReceiptError(f"{context}: prompt is not UTF-8") from exc

    try:
        expected_context = full_record_context.build_full_record_context(
            record,
            catalog_index=fact_catalog,
            qualification_catalog=qualification_catalog,
        )
        context_errors = full_record_context.validate_full_record_context(
            record, supplied_context, catalog_index=fact_catalog
        )
    except (TypeError, ValueError) as exc:
        raise FullRunReceiptError(f"{context}: cannot rebuild source context: {exc}") from exc
    _require(not context_errors, f"{context}: invalid source context {context_errors[:5]}")
    _require(supplied_context == expected_context, f"{context}: source context mismatch")
    expected_context_text = full_record_context.render_full_record_context(expected_context)
    _require(
        context_raw == expected_context_text.encode("utf-8"),
        f"{context}: source context bytes are not canonical",
    )
    profile = runner.prompt_profiles.get_profile(
        manifest["prompt_lineage"]["profile"], annotator_role=manifest["annotator_role"]
    )
    rubric = runner.RUBRIC_PATH.read_text(encoding="utf-8")
    expected_prompt = runner.build_prompt(
        profile=profile,
        rubric=rubric,
        context=expected_context,
        record_id=record_id,
    )
    expected_schema = full_record_output.output_schema()
    _require(prompt == expected_prompt, f"{context}: prompt mismatch")
    _require(supplied_schema == expected_schema, f"{context}: output schema mismatch")
    _require(
        schema_raw == base.canonical_json(expected_schema).encode("utf-8"),
        f"{context}: output schema bytes are not canonical",
    )

    artifacts = {
        "full_record_context.json": expected_context_text,
        "output_schema.json": base.canonical_json(expected_schema),
        "prompt.txt": expected_prompt,
    }
    expected_task: dict[str, Any] = {
        "schema_version": runner.TASK_SCHEMA_VERSION,
        "run_key": manifest["run_key"],
        "run_manifest_sha256": manifest["manifest_sha256"],
        "record_id": record_id,
        "group": runner.GROUP_NAME,
        "target_items": list(runner.TARGET_ITEMS),
        "record_source_sha256": base.sha256_object(record),
        "full_record_context_sha256": expected_context["context_sha256"],
        "full_record_context_schema_version": expected_context["schema_version"],
        "full_record_context_bounds": expected_context["bounds"],
        "rubric_sha256": base.sha256_text(rubric),
        "system_prompt_sha256": base.sha256_text(rubric),
        "prompt_sha256": base.sha256_text(expected_prompt),
        "output_schema_sha256": base.sha256_object(expected_schema),
        "artifact_file_sha256": {
            name: base.sha256_text(content) for name, content in sorted(artifacts.items())
        },
        "semantic_inputs": ["full_record_context.json", "prompt.txt"],
        "cli_execution_input": {
            "stdin": "prompt.txt bytes",
            "filesystem": ["output_schema.json"],
            "persistent_task_directory_is_not_cli_cwd": True,
        },
        "threat_model_sha256": base.sha256_text(base.THREAT_MODEL),
        "cli_may_read_outside_working_directory": True,
        **_lineage_projection(manifest),
    }
    expected_task["manifest_sha256"] = _manifest_hash(expected_task)
    _require(task_manifest == expected_task, f"{context}: task manifest mismatch")
    return task_dir, task_manifest, expected_context


def _strict_event_audit(events: str, *, context: str) -> dict[str, Any]:
    encoded = events.encode("utf-8")
    if encoded:
        _require(encoded.endswith(b"\n"), f"{context}: event stream is not newline-terminated")
    for line_number, line in enumerate(encoded.splitlines(), 1):
        _require(line.strip() != b"", f"{context}: blank event row {line_number}")
        value = _strict_json(line, context=f"{context} event line {line_number}")
        _require(isinstance(value, dict), f"{context}: event line {line_number} is not an object")
    audit = base._event_audit(events)
    _require(not audit["parse_errors"], f"{context}: event parse error")
    _require(not audit["unsafe_items"], f"{context}: unsafe tool/nonmessage event")
    return audit


def _common_receipt_checks(
    receipt: Mapping[str, Any],
    *,
    task_manifest: Mapping[str, Any],
    manifest: Mapping[str, Any],
    events: str,
    stderr: str,
    raw_final: str,
    audit: Mapping[str, Any],
    expected_attempt: int,
    policy_frozen: datetime,
    context: str,
) -> datetime:
    _require(set(receipt) == runner.RECEIPT_KEYS, f"{context}: receipt keys differ")
    _require(
        receipt.get("schema_version") == runner.RESULT_SCHEMA_VERSION
        and receipt.get("runner_receipt_schema_version") == runner.RECEIPT_SCHEMA_VERSION,
        f"{context}: receipt schema mismatch",
    )
    _require(receipt.get("attempt") == expected_attempt, f"{context}: attempt mismatch")
    _require(
        receipt.get("id") == task_manifest["record_id"]
        and receipt.get("group") == runner.GROUP_NAME
        and receipt.get("target_items") == list(runner.TARGET_ITEMS),
        f"{context}: receipt identity mismatch",
    )
    _require(
        receipt.get("run_key") == manifest["run_key"]
        and receipt.get("run_manifest_sha256") == manifest["manifest_sha256"]
        and receipt.get("task_manifest_sha256") == task_manifest["manifest_sha256"],
        f"{context}: receipt lineage mismatch",
    )
    for field_name, expected in _lineage_projection(manifest).items():
        _require(receipt.get(field_name) == expected, f"{context}: {field_name} mismatch")
    direct = {
        "source_sha256": task_manifest["record_source_sha256"],
        "full_record_context_sha256": task_manifest["full_record_context_sha256"],
        "full_record_context_schema_version": task_manifest[
            "full_record_context_schema_version"
        ],
        "full_record_context_bounds": task_manifest["full_record_context_bounds"],
        "rubric_sha256": task_manifest["rubric_sha256"],
        "system_prompt_sha256": task_manifest["system_prompt_sha256"],
        "prompt_sha256": task_manifest["prompt_sha256"],
        "output_schema_sha256": task_manifest["output_schema_sha256"],
        "request_sha256": base.sha256_object(
            {
                "prompt_sha256": task_manifest["prompt_sha256"],
                "output_schema_sha256": task_manifest["output_schema_sha256"],
                "command": manifest["semantic_config"]["codex_command_template"],
            }
        ),
        "raw_response_sha256": base.sha256_text(events),
        "event_stream_sha256": base.sha256_text(events),
        "stderr_sha256": base.sha256_text(stderr),
        "content_sha256": base.sha256_text(raw_final),
        "raw_final_sha256": base.sha256_text(raw_final),
    }
    for field_name, expected in direct.items():
        _require(receipt.get(field_name) == expected, f"{context}: {field_name} mismatch")
    _require(receipt.get("raw_content") == raw_final, f"{context}: raw-content mismatch")
    expected_audit = {
        name: audit[name]
        for name in (
            "event_count",
            "parse_errors",
            "unsafe_items",
            "service_errors",
            "turn_completed",
        )
    }
    _require(receipt.get("event_audit") == expected_audit, f"{context}: event audit mismatch")
    _require(receipt.get("usage") == audit["usage"], f"{context}: usage mismatch")
    expected_provenance = {
        "endpoint_kind": "codex_cli_exec",
        "requested_model": manifest["semantic_config"]["model"],
        "declared_reasoning_effort": manifest["semantic_config"]["reasoning_effort"],
        "resolved_model": None,
        "resolved_model_note": "CLI event protocol does not attest a resolved alias",
        "codex_cli": manifest["codex_cli"],
        "command_template": manifest["semantic_config"]["codex_command_template"],
        "session_ephemeral": True,
        "sandbox": "read-only",
        "ignore_user_config": True,
        "ignore_rules": True,
        "thread_ids": audit["thread_ids"],
    }
    _require(
        receipt.get("model_provenance") == expected_provenance,
        f"{context}: model provenance mismatch",
    )
    elapsed = receipt.get("elapsed_seconds")
    _require(
        isinstance(elapsed, (int, float))
        and not isinstance(elapsed, bool)
        and math.isfinite(float(elapsed))
        and float(elapsed) >= 0,
        f"{context}: invalid elapsed time",
    )
    completed = _parse_utc(receipt.get("completed_utc"), context=f"{context} completed_utc")
    _require(completed >= policy_frozen, f"{context}: completed before audit-policy freeze")
    return completed


def _replay_ledger(
    raw_final: str,
    *,
    record: Mapping[str, Any],
    source_context: Mapping[str, Any],
    context: str,
) -> dict[str, Any]:
    try:
        parsed = full_record_output.parse_output(raw_final)
        normalized = full_record_output.normalize_output(parsed)
        registry = full_record_context.allowed_span_registry(source_context)
        full_record_output.validate_output(
            normalized, record, allowed_span_registry=registry
        )
        return full_record_output.canonical_ledger_projection(
            normalized, record, allowed_span_registry=registry
        )
    except (TypeError, ValueError, full_record_output.FullRecordOutputError) as exc:
        raise FullRunReceiptError(f"{context}: invalid full-record raw output: {exc}") from exc


def _verify_attempts(
    *,
    task_dir: pathlib.Path,
    checkpoint_row: Mapping[str, Any],
    task_manifest: Mapping[str, Any],
    manifest: Mapping[str, Any],
    record: Mapping[str, Any],
    source_context: Mapping[str, Any],
    policy_frozen: datetime,
    context: str,
) -> datetime:
    attempt = checkpoint_row.get("attempt")
    _require(type(attempt) is int and attempt >= 1, f"{context}: invalid attempt number")
    attempts_dir = task_dir / "attempts"
    expected_names = [f"attempt-{number:03d}" for number in range(1, attempt + 1)]
    actual_names = sorted(child.name for child in attempts_dir.iterdir() if child.is_dir())
    _require(actual_names == expected_names, f"{context}: non-contiguous attempt lineage")
    _require(
        not [child for child in attempts_dir.iterdir() if child.is_file()],
        f"{context}: unexpected file in attempts directory",
    )

    last_completed: datetime | None = None
    for number, name in enumerate(expected_names, 1):
        attempt_dir = attempts_dir / name
        attempt_context = f"{context} {name}"
        files = {child.name for child in attempt_dir.iterdir() if child.is_file()}
        directories = [child.name for child in attempt_dir.iterdir() if child.is_dir()]
        _require(files == ATTEMPT_FILES and not directories, f"{attempt_context}: artifact set mismatch")
        receipt = _load_canonical_object(
            attempt_dir / "receipt.json", context=f"{attempt_context} receipt"
        )
        try:
            event_bytes = (attempt_dir / "events.jsonl").read_bytes()
            stderr_bytes = (attempt_dir / "stderr.txt").read_bytes()
            raw_bytes = (attempt_dir / "raw_final.json").read_bytes()
            events = event_bytes.decode("utf-8")
            stderr = stderr_bytes.decode("utf-8")
            raw_final = raw_bytes.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise FullRunReceiptError(f"{attempt_context}: artifact is not UTF-8") from exc
        except OSError as exc:
            raise FullRunReceiptError(f"{attempt_context}: cannot read artifact: {exc}") from exc
        audit = _strict_event_audit(events, context=attempt_context)
        completed = _common_receipt_checks(
            receipt,
            task_manifest=task_manifest,
            manifest=manifest,
            events=events,
            stderr=stderr,
            raw_final=raw_final,
            audit=audit,
            expected_attempt=number,
            policy_frozen=policy_frozen,
            context=attempt_context,
        )
        if last_completed is not None:
            _require(completed >= last_completed, f"{attempt_context}: completion time regressed")
        last_completed = completed
        if number < attempt:
            _require(receipt.get("status") == "error", f"{attempt_context}: retry followed success")
            _require(
                isinstance(receipt.get("error"), str) and bool(receipt["error"]),
                f"{attempt_context}: failed receipt lacks error",
            )
            _require(
                isinstance(receipt.get("validation_errors"), list)
                and bool(receipt["validation_errors"]),
                f"{attempt_context}: failed receipt lacks validation errors",
            )
            if receipt.get("ledger") is not None:
                ledger = _replay_ledger(
                    raw_final,
                    record=record,
                    source_context=source_context,
                    context=attempt_context,
                )
                _require(
                    receipt.get("ledger") == ledger,
                    f"{attempt_context}: failed-attempt ledger replay mismatch",
                )
        else:
            _require(receipt == checkpoint_row, f"{attempt_context}: checkpoint/receipt mismatch")
            _require(receipt.get("status") == "ok", f"{attempt_context}: final status is not ok")
            _require(receipt.get("error") is None, f"{attempt_context}: successful receipt has error")
            _require(
                receipt.get("validation_errors") == [],
                f"{attempt_context}: successful receipt has validation errors",
            )
            _require(not audit["service_errors"], f"{attempt_context}: service error event")
            _require(audit["turn_completed"] is True, f"{attempt_context}: turn did not complete")
            _require(
                len(audit["thread_ids"]) == 1 and bool(audit["thread_ids"][0]),
                f"{attempt_context}: thread lineage is not singular",
            )
            _require(
                audit["agent_messages"]
                and raw_final.strip() == audit["agent_messages"][-1].strip(),
                f"{attempt_context}: raw final differs from last agent message",
            )
            ledger = _replay_ledger(
                raw_final,
                record=record,
                source_context=source_context,
                context=attempt_context,
            )
            _require(receipt.get("ledger") == ledger, f"{attempt_context}: ledger replay mismatch")
    assert last_completed is not None
    return last_completed


@dataclass(frozen=True)
class RoleRunIndex:
    """Verified byte-offset index over one role's full-run checkpoints."""

    role: str
    organizer_input: str
    organizer_input_sha256: str
    policy_frozen_utc: str
    locations: Mapping[str, tuple[pathlib.Path, int, int]]
    descriptors: tuple[dict[str, Any], ...]
    _row_sha256: Mapping[str, str] = field(repr=False)

    def load(self, record_id: str) -> dict[str, Any]:
        """Load one already-verified receipt and detect post-index mutation."""

        key = str(record_id)
        try:
            checkpoint_path, offset, line_number = self.locations[key]
            expected_sha = self._row_sha256[key]
        except KeyError as exc:
            raise KeyError(f"record is not in verified {self.role} index: {key}") from exc
        try:
            with checkpoint_path.open("rb") as handle:
                handle.seek(offset)
                raw = handle.readline()
        except OSError as exc:
            raise FullRunReceiptError(f"cannot reload checkpoint row {key}: {exc}") from exc
        _require(raw.endswith(b"\n"), f"checkpoint line {line_number}: row changed or truncated")
        _require(
            _sha256_bytes(raw) == expected_sha,
            f"checkpoint line {line_number}: row changed after verification",
        )
        value = _strict_object(raw, context=f"checkpoint line {line_number}")
        _require(value.get("id") == key, f"checkpoint line {line_number}: identity changed")
        return value


def _scan_checkpoint(
    *,
    checkpoint_path: pathlib.Path,
    run_dir: pathlib.Path,
    manifest: Mapping[str, Any],
    selected_ids: Sequence[str],
    records: Mapping[str, Mapping[str, Any]],
    fact_catalog: Any,
    qualification_catalog: Any,
    policy_frozen: datetime,
    global_locations: dict[str, tuple[pathlib.Path, int, int]],
    global_row_hashes: dict[str, str],
) -> tuple[str, int, int, str, str]:
    context = f"run {run_dir} checkpoint"
    _require(checkpoint_path.is_file(), f"{context}: missing {checkpoint_path.name}")
    selected = set(selected_ids)
    tasks_root = run_dir / "tasks"
    _require(tasks_root.is_dir(), f"{context}: tasks directory is missing")
    expected_task_slugs = {base._task_slug(record_id) for record_id in selected_ids}
    actual_task_slugs = {child.name for child in tasks_root.iterdir() if child.is_dir()}
    _require(
        actual_task_slugs == expected_task_slugs
        and not [child for child in tasks_root.iterdir() if child.is_file()],
        f"{context}: task-directory cohort mismatch",
    )
    seen: set[str] = set()
    digest = hashlib.sha256()
    row_count = 0
    byte_count = 0
    first_completed: datetime | None = None
    last_completed: datetime | None = None
    try:
        with checkpoint_path.open("rb") as handle:
            while True:
                offset = handle.tell()
                raw = handle.readline()
                if not raw:
                    break
                row_count += 1
                byte_count += len(raw)
                digest.update(raw)
                _require(raw.strip() != b"", f"{context} line {row_count}: blank row")
                _require(raw.endswith(b"\n"), f"{context} line {row_count}: partial row")
                row = _strict_object(raw, context=f"{context} line {row_count}")
                _require(
                    raw == (base.canonical_json(row) + "\n").encode("utf-8"),
                    f"{context} line {row_count}: checkpoint row is not canonical JSON",
                )
                record_id = row.get("id")
                _require(
                    isinstance(record_id, str) and record_id in selected,
                    f"{context} line {row_count}: out-of-cohort id {record_id!r}",
                )
                _require(record_id not in seen, f"{context} line {row_count}: duplicate id {record_id}")
                _require(
                    record_id not in global_locations,
                    f"{context} line {row_count}: id occurs in multiple runs: {record_id}",
                )
                record = records[record_id]
                task_dir, task_manifest, source_context = _verify_task(
                    run_dir=run_dir,
                    manifest=manifest,
                    record=record,
                    fact_catalog=fact_catalog,
                    qualification_catalog=qualification_catalog,
                )
                completed = _verify_attempts(
                    task_dir=task_dir,
                    checkpoint_row=row,
                    task_manifest=task_manifest,
                    manifest=manifest,
                    record=record,
                    source_context=source_context,
                    policy_frozen=policy_frozen,
                    context=f"{context} line {row_count} ({record_id})",
                )
                first_completed = completed if first_completed is None else min(first_completed, completed)
                last_completed = completed if last_completed is None else max(last_completed, completed)
                seen.add(record_id)
                global_locations[record_id] = (checkpoint_path, offset, row_count)
                global_row_hashes[record_id] = _sha256_bytes(raw)
    except OSError as exc:
        raise FullRunReceiptError(f"{context}: cannot scan checkpoint: {exc}") from exc
    missing = selected - seen
    _require(not missing, f"{context}: missing selected IDs {sorted(missing)[:5]}")
    _require(seen == selected, f"{context}: checkpoint/selection mismatch")
    _require(row_count > 0, f"{context}: empty checkpoint")
    _require(
        checkpoint_path.stat().st_size == byte_count,
        f"{context}: checkpoint changed while it was being verified",
    )
    assert first_completed is not None and last_completed is not None
    return (
        digest.hexdigest(),
        row_count,
        byte_count,
        first_completed.isoformat(),
        last_completed.isoformat(),
    )


def verify_role_runs(
    run_dirs: Iterable[pathlib.Path | str],
    *,
    role: str,
    organizer_input: pathlib.Path | str,
    records: Mapping[str, Mapping[str, Any]],
    policy_frozen_utc: str | datetime,
    frozen_source_archive: pathlib.Path | str | None = None,
) -> RoleRunIndex:
    """Verify complete full-record runs for one independent annotator role.

    The union of every run's deterministic selected cohort and checkpoint rows
    must equal ``records`` exactly.  No parsed checkpoint row is retained after
    validation; callers retrieve a receipt later with :meth:`RoleRunIndex.load`.
    """

    _require(role in runner.ANNOTATOR_ROLES, f"unsupported annotator role: {role}")
    organizer_path = pathlib.Path(organizer_input).resolve()
    policy_frozen = _parse_utc(policy_frozen_utc, context="audit-policy freeze")
    organizer_ids, normalized_records = _verify_organizer_records(
        organizer_path, records
    )
    resolved_dirs = [pathlib.Path(value).resolve() for value in run_dirs]
    _require(resolved_dirs, "at least one run directory is required")
    _require(
        len(resolved_dirs) == len(set(resolved_dirs)),
        "duplicate run directory",
    )
    _require(all(path.is_dir() for path in resolved_dirs), "run directory is missing")
    archive_path: pathlib.Path | None = None
    frozen_source: dict[str, Any] | None = None
    if frozen_source_archive is not None:
        requested_archive = pathlib.Path(frozen_source_archive)
        _require(
            not requested_archive.is_symlink(),
            "source archive root cannot be a symlink",
        )
        archive_path = requested_archive.resolve()
        frozen_source = _verify_frozen_source_archive(archive_path)

    fact_catalog = base.catalog_facts.CatalogIndex.load()
    qualification_catalog = (
        base.qualification_context.qualification_facts.CatalogReference.load()
    )
    locations: dict[str, tuple[pathlib.Path, int, int]] = {}
    row_hashes: dict[str, str] = {}
    descriptors: list[dict[str, Any]] = []
    tuple_sha256: str | None = None
    selected_union: set[str] = set()

    for run_dir in resolved_dirs:
        manifest, selected_ids, manifest_file_sha = _verify_run_manifest(
            run_dir,
            role=role,
            organizer_input=organizer_path,
            organizer_ids=organizer_ids,
            fact_catalog=fact_catalog,
            qualification_catalog=qualification_catalog,
            frozen_source=frozen_source,
        )
        overlap = selected_union.intersection(selected_ids)
        _require(not overlap, f"run cohorts overlap at {sorted(overlap)[:5]}")
        selected_union.update(selected_ids)
        if tuple_sha256 is None:
            tuple_sha256 = str(manifest["tuple_sha256"])
        else:
            _require(
                manifest["tuple_sha256"] == tuple_sha256,
                f"run {run_dir}: role tuple differs across shards",
            )
        checkpoint_path = run_dir / CHECKPOINT_NAME
        (
            checkpoint_sha,
            row_count,
            checkpoint_bytes,
            first_completed,
            last_completed,
        ) = _scan_checkpoint(
            checkpoint_path=checkpoint_path,
            run_dir=run_dir,
            manifest=manifest,
            selected_ids=selected_ids,
            records=normalized_records,
            fact_catalog=fact_catalog,
            qualification_catalog=qualification_catalog,
            policy_frozen=policy_frozen,
            global_locations=locations,
            global_row_hashes=row_hashes,
        )
        descriptors.append(
            {
                "schema_version": DESCRIPTOR_SCHEMA_VERSION,
                "run_dir": str(run_dir),
                "run_manifest_path": str(run_dir / "run_manifest.json"),
                "run_manifest_file_sha256": manifest_file_sha,
                "run_manifest_sha256": manifest["manifest_sha256"],
                "run_key": manifest["run_key"],
                "run_instance_sha256": manifest["run_instance_sha256"],
                "tuple_sha256": manifest["tuple_sha256"],
                "annotator_role": role,
                "phase": EXPECTED_PHASE,
                "pass_kind": runner.PASS_KIND,
                "prompt_profile": manifest["prompt_lineage"]["profile"],
                "prompt_lineage_sha256": manifest["prompt_lineage"]["lineage_sha256"],
                "model_identity": manifest["model_identity"],
                "cohort_plan_sha256": manifest["cohort_plan"]["manifest_sha256"],
                "selected_id_count": len(selected_ids),
                "checkpoint_path": str(checkpoint_path),
                "checkpoint_sha256": checkpoint_sha,
                "checkpoint_bytes": checkpoint_bytes,
                "checkpoint_rows": row_count,
                "first_completed_utc": first_completed,
                "last_completed_utc": last_completed,
                **(
                    {
                        "frozen_source_archive": {
                            "path": str(archive_path),
                            "manifest_file_sha256": frozen_source["manifest_file_sha256"],
                            "bundle_sha256": frozen_source["bundle"]["bundle_sha256"],
                            "anchor_run_manifest_sha256": frozen_source["archive"][
                                "run_manifest_sha256"
                            ],
                        }
                    }
                    if frozen_source is not None
                    else {}
                ),
            }
        )

    expected_ids = set(normalized_records)
    _require(
        selected_union == expected_ids,
        "union of selected run cohorts does not exactly equal supplied records",
    )
    _require(
        set(locations) == expected_ids,
        "union of verified checkpoint rows does not exactly equal supplied records",
    )
    if frozen_source is not None:
        _require(
            frozen_source["archive"]["run_manifest_sha256"]
            in {row["run_manifest_sha256"] for row in descriptors},
            "frozen source archive anchor manifest is outside verified run set",
        )
    return RoleRunIndex(
        role=role,
        organizer_input=str(organizer_path),
        organizer_input_sha256=base.file_sha256(organizer_path),
        policy_frozen_utc=policy_frozen.isoformat(),
        locations=dict(locations),
        descriptors=tuple(descriptors),
        _row_sha256=dict(row_hashes),
    )


__all__ = [
    "CHECKPOINT_NAME",
    "DESCRIPTOR_SCHEMA_VERSION",
    "EXPECTED_PHASE",
    "FullRunReceiptError",
    "RoleRunIndex",
    "verify_role_runs",
]
