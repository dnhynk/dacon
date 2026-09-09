"""Finish only H3 arm8, pinning its allocated KV blocks to the preserved arm32.

This is an explicit recovery, not a newly executed two-arm experiment. Original
artifacts remain immutable. The validated arm32 and frozen CPU inputs are reused;
arm8 model outputs are generated once in a fresh process with a cold KV cache.
"""
from __future__ import annotations

import argparse
import copy
import gc
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import traceback
import uuid

sys.dont_write_bytecode = True


def file_hash(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def require(condition, message):
    if not condition:
        raise ValueError(message)


def verify_reuse(source, manifest):
    """No failed output or changed dependency is accepted by the recovery."""
    for name, expected in manifest["source_files"].items():
        path = (source / name).resolve()
        require(path.is_relative_to(source.resolve()), "Unsafe source path")
        require(path.is_file() and file_hash(path) == expected, "Reuse parent changed: " + name)
    result = read(source / "arm32/result.json")
    calls = read(source / "arm32/calls.json")["calls"]
    require(result["status"] == "GPU_ARM_COMPLETED" and result["gpu_executed"] is True
            and result["requests"] == 96 and result["failed_outputs"] == 0,
            "Only the complete successful arm32 can be reused")
    require(len(calls) == 96 and all(c["status"] == "MODEL_OUTPUT_VALIDATED" for c in calls),
            "Missing or failed preserved model output")
    require(len({(c["record_rank"], c["group"]) for c in calls}) == 96,
            "Duplicate preserved output")
    engine = read(source / "arm32/engine.json")
    require(engine["effective"]["cache_config"]["num_gpu_blocks"] == manifest["target_blocks"],
            "Pinned KV allocation differs from the preserved engine")
    return result


def equivalent_engine(previous, actual, blocks):
    """Allow only the declared allocation controller; compare all actual capacity."""
    # AutoConfig.to_dict retains integer id2label keys; JSON persistence uses
    # strings. Compare the same serialization boundary as the frozen harness.
    actual = json.loads(json.dumps(actual, allow_nan=False))
    previous = json.loads(json.dumps(previous, allow_nan=False))
    require(previous["cache_config"]["num_gpu_blocks_override"] is None,
            "Unexpected preserved override")
    require(actual["cache_config"]["num_gpu_blocks_override"] == blocks,
            "The requested block pin was not applied")
    require(actual["cache_config"]["num_gpu_blocks"] == previous["cache_config"]["num_gpu_blocks"] == blocks,
            "Actual KV block allocation differs")
    comparable = copy.deepcopy(actual)
    comparable["cache_config"]["num_gpu_blocks_override"] = None
    require(comparable == previous, "Effective engine differs beyond the explicit KV allocation controller")
    return comparable


def load_harness(path, expected):
    require(file_hash(path) == expected, "Frozen harness changed")
    spec = importlib.util.spec_from_file_location("frozen_h3_recovery_parent", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run_arm8(args, h, manifest):
    root, started, runner = args.output_dir, time.perf_counter(), None
    dest = root / "arm8"
    dest.mkdir(exist_ok=False)
    result = {"status": "RUNNING_NOT_REUSABLE", "arm": 8, "pid": os.getpid(),
              "sample_id": uuid.uuid4().hex, "gpu_executed": False, "output_reusable": False,
              "expected_calls": 96, "phase": "verify_reused_boundary"}
    h.atomic(dest / "result.json", result)
    exit_code = 1
    try:
        verify_reuse(args.source_pair, manifest)
        packets = read(root / "frozen/frozen_requests.json")["requests"]
        spec = read(root / "frozen/frozen_spec.json")
        original_paths = spec["source_paths"]
        args.source_trace = Path(original_paths["trace"])
        args.source_run_report = Path(original_paths["report"])
        args.source_environment = Path(original_paths["environment"])
        args.source_snapshot = Path(original_paths["snapshot"])
        args.scheduler_test_manifest = args.harness.parent / "scheduler_test_manifest.json"
        result["phase"] = "pre_engine_validation"
        h.atomic(dest / "result.json", result)
        config = h.validate_frozen(packets, spec, args, gpu=True)
        result["pre_engine_validation_seconds"] = time.perf_counter() - started
        result["phase"] = "engine_load"
        h.atomic(dest / "result.json", result)

        class PinnedRunner(h.ReplayRunner):
            # generate_raw, parsing, audit, and request ordering are inherited unchanged.
            def __init__(self):
                os.environ["VLLM_USE_V2_MODEL_RUNNER"] = "0"
                import vllm
                from vllm.config import ReasoningConfig
                require(vllm.__version__ == "0.26.0", "Expected vLLM 0.26.0")
                h.installed_native_parser()
                self.config, self.version = config, vllm.__version__
                kwargs = h.base.expected_engine_kwargs(args.model_dir, config)
                kwargs["structured_outputs_config"] = {"reasoning_parser": "gemma4", "enable_in_reasoning": False}
                kwargs["reasoning_config"] = ReasoningConfig(reasoning_start_str=h.START, reasoning_end_str=h.END)
                kwargs["num_gpu_blocks_override"] = manifest["target_blocks"]
                load_started = time.perf_counter()
                self.raw_llm = self.llm = vllm.LLM(**kwargs)
                self.tokenizer = self.llm.get_tokenizer()
                self.load_seconds = time.perf_counter() - load_started
                self.effective = h.effective_engine(self.llm)

        runner = PinnedRunner()
        result["engine_load_seconds"] = runner.load_seconds
        result["phase"] = "post_engine_validation"
        h.atomic(dest / "result.json", result)
        checked = time.perf_counter()
        require(h.fingerprint(runner.tokenizer.chat_template) == spec["template_sha256"], "Actual template drift")
        for packet in packets:
            require(h.token_hash(h.base.token_ids(runner.tokenizer, packet["messages"], True)) == packet["token_ids_sha256"],
                    "Actual tokenizer drift")
        h.validate_frozen(packets, spec, args, gpu=True, check_weights=False)
        previous = read(root / "arm32/engine.json")["effective"]
        h.atomic(dest / "engine.json", {"status": "RUNTIME_CAPTURED_NOT_VALIDATED", "pid": os.getpid(),
                 "effective": runner.effective, "effective_sha256": h.fingerprint(runner.effective)})
        comparable = equivalent_engine(previous, runner.effective, manifest["target_blocks"])
        result["post_engine_validation_seconds"] = time.perf_counter() - checked
        h.atomic(dest / "engine.json", {"status": "RUNTIME_VALIDATED", "pid": os.getpid(),
                 "effective": runner.effective, "effective_sha256": h.fingerprint(runner.effective),
                 "comparable_effective_sha256": h.fingerprint(comparable),
                 "declared_control_difference": {"cache_config.num_gpu_blocks_override": [None, manifest["target_blocks"]]},
                 "runtime_weight_manifest_sha256": h.fingerprint(spec["runtime_weight_manifest"]),
                 "spec_hash": spec["spec_hash"], "token_hashes_verified": 96,
                 "engine_load_seconds": runner.load_seconds})
        result.update({"phase": "new_gpu_samples", "gpu_executed": True})
        h.atomic(dest / "result.json", result)
        inference = time.perf_counter()
        rows, batches, fatal = h.replay_batches(packets, spec, config, 8, runner, dest, result["sample_id"])
        result["inference_and_audit_seconds"] = time.perf_counter() - inference
        result.update(h.summarize(rows, batches))
        result["measurement_failures"] = sum(type(r.get("cached_input_tokens")) is not int or
            not 0 <= r["cached_input_tokens"] < r["input_tokens"] for r in rows)
        result["status"] = ("FAILED_ENGINE" if fatal else "FAILED_OUTPUTS" if result["failed_outputs"] else
                            "FAILED_MEASUREMENT" if result["measurement_failures"] else "GPU_ARM_COMPLETED")
        h.atomic(dest / "calls.json", {"status": result["status"], "calls": rows})
        h.atomic(dest / "batches.json", {"status": result["status"], "batches": batches})
        exit_code = 0 if result["status"] == "GPU_ARM_COMPLETED" else 2
    except Exception as exc:
        result.update({"status": "FAILED_ENGINE_OR_CONTRACT", "error_type": type(exc).__name__,
                       "error_frames": [{"file": Path(f.filename).name, "line": f.lineno, "function": f.name}
                                        for f in traceback.extract_tb(exc.__traceback__)]})
    finally:
        shut = time.perf_counter()
        try:
            if runner is not None:
                runner.raw_llm.llm_engine.engine_core.shutdown()
                del runner
                gc.collect()
            result["explicit_shutdown_seconds"] = time.perf_counter() - shut
        except Exception as exc:
            result.update({"status": "FAILED_SHUTDOWN", "shutdown_error_type": type(exc).__name__})
            exit_code = 1
        result["child_body_seconds"] = time.perf_counter() - started
        h.atomic(dest / "result.json", result)
    return exit_code


def compare(root, h, manifest):
    a, b = (read(root / f"arm{s}/result.json") for s in (32, 8))
    require(a["status"] == b["status"] == "GPU_ARM_COMPLETED", "Both arms must be complete")
    require(a["sample_id"] != b["sample_id"] and a["pid"] != b["pid"], "Samples must be distinct")
    engines = [read(root / f"arm{s}/engine.json") for s in (32, 8)]
    equivalent_engine(engines[0]["effective"], engines[1]["effective"], manifest["target_blocks"])
    require(engines[0]["runtime_weight_manifest_sha256"] == engines[1]["runtime_weight_manifest_sha256"],
            "Model weight identity differs")
    rows = [read(root / f"arm{s}/calls.json")["calls"] for s in (32, 8)]
    index = [{(r["record_rank"], r["group"]): r for r in arm} for arm in rows]
    require(all(len(x) == 96 for x in index), "Missing paired output")
    differences = []
    for key in map(tuple, h.base.orders(32)):
        left, right = index[0][key], index[1][key]
        for name in ("token_ids_sha256", "messages_sha256", "schema_sha256", "sampling_sha256", "effective_sampling_sha256"):
            require(left[name] == right[name], "Paired request configuration drift")
        changed = [name for name in ("status", "labels", "evidence_refs", "final_answer_sha256", "raw_output_sha256",
                   "generated_tokens", "answer_tokens", "thought_tokens") if left.get(name) != right.get(name)]
        if changed:
            differences.append({"record_rank": key[0], "group": key[1], "changed_fields": changed})
    result = {"status": "PAIR_COMPLETED_WITH_DRIFT" if differences else "PAIR_COMPLETED_EQUIVALENT",
              "production_adoption": False, "root_scoring_required": True,
              "reused_successful_model_outputs": 96, "new_model_outputs_this_recovery": 96,
              "kv_capacity_matched": True, "target_blocks": manifest["target_blocks"],
              "capacity_note": "Exact actual blocks/tokens/concurrency matched; raw profiled free GiB may differ. Only arm8 uses the explicit controller.",
              "arms": {"arm32": a, "arm8": b}, "different_calls": len(differences), "differences": differences,
              "later_group_cache_near_shared_boundary": {f"arm{s}": sum(
                  r["cached_input_tokens"] >= r["shared_floor_32"] - 32 for r in rows[i] if r["group"] > 1)
                  for i, s in enumerate((32, 8))},
              "criterion": "At least58/64 later-group hits near shared boundary in arm8, below58 in arm32"}
    h.atomic(root / "comparison.json", result)
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for name in ("source-pair", "output-dir", "manifest", "harness", "model-dir"):
        p.add_argument("--" + name, required=True, type=Path)
    p.add_argument("--child", action="store_true")
    args = p.parse_args()
    manifest = read(args.manifest)
    h = load_harness(args.harness, manifest["harness_sha256"])
    if args.child:
        return run_arm8(args, h, manifest)
    require(not args.output_dir.exists(), "Preserve prior results; use a new recovery directory")
    started = time.perf_counter()
    verify_reuse(args.source_pair, manifest)
    args.output_dir.mkdir(parents=True)
    for name in ("arm32", "frozen"):
        shutil.copytree(args.source_pair / name, args.output_dir / name)
    h.atomic(args.output_dir / "recovery_manifest.json", {**manifest, "recovery_source_sha256": file_hash(__file__),
             "source_pair": args.source_pair.as_posix(), "reused_outputs": 96, "planned_new_outputs": 96})
    h.atomic(args.output_dir / "status.json", {"status": "RUNNING_NOT_REUSABLE", "reused_outputs": 96, "new_outputs": 0})
    launch = time.perf_counter()
    child = subprocess.run([sys.executable, "-B", str(Path(__file__).resolve()), *sys.argv[1:], "--child"],
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False)
    child_wall = time.perf_counter() - launch
    h.atomic(args.output_dir / "arm8/startup.json", h.startup_summary(child.stdout.decode("utf-8", errors="replace")))
    del child.stdout
    result = read(args.output_dir / "arm8/result.json")
    h.atomic(args.output_dir / "recovery_timing.json", {
        "new_child_process_wall_seconds": child_wall,
        "recovery_parent_wall_seconds": time.perf_counter() - started,
        "original_failed_pair_wall_seconds": read(args.source_pair / "status.json")["parent_wall_seconds"],
        "child_returncode": child.returncode, "child_status": result["status"]})
    if child.returncode != 0 or result["status"] != "GPU_ARM_COMPLETED":
        h.atomic(args.output_dir / "status.json", {"status": "FAILED_RECOVERY_NOT_REUSABLE",
                 "reused_successful_model_outputs": 96,
                 "new_outputs_saved": len(list((args.output_dir / "arm8/calls").glob("*.json"))),
                 "new_generation_entered": result.get("gpu_executed", False),
                 "production_adoption": False})
    require(child.returncode == 0 and result["status"] == "GPU_ARM_COMPLETED", "Recovery did not complete; preserve failure")
    verify_reuse(args.source_pair, manifest)
    comparison = compare(args.output_dir, h, manifest)
    status = {"status": comparison["status"], "production_adoption": False,
              "reused_successful_model_outputs": 96, "new_model_outputs_this_recovery": 96,
              "recovery_parent_wall_seconds": time.perf_counter() - started,
              "new_child_process_wall_seconds": child_wall,
              "original_failed_pair_wall_seconds": read(args.source_pair / "status.json")["parent_wall_seconds"]}
    h.atomic(args.output_dir / "status.json", status)
    print(json.dumps(status), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
