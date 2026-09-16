"""Small, read-only cold-start status and preserved-artifact check.

No model, network, label loading, prediction editing or automatic experiment.
--verify requires the private artifacts in the original local workspace.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def repo_path(root: Path, relative: str) -> Path:
    path = Path(relative)
    if path.is_absolute() or ":" in relative or "\\" in relative:
        raise ValueError(f"Expected a repository-relative POSIX path: {relative}")
    resolved = (root / path).resolve()
    if not resolved.is_relative_to(root.resolve()):
        raise ValueError(f"Path escapes the repository: {relative}")
    return resolved


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_state(root: Path, state: dict) -> dict:
    checks = []

    def file_check(relative, expected):
        try:
            path = repo_path(root, relative)
            if not path.is_file():
                checks.append({"path": relative, "status": "missing_local_artifact"})
                return False
            actual = sha256(path)
            valid = actual == expected
            checks.append({"path": relative, "status": "ok" if valid else "hash_mismatch"})
            return valid
        except (OSError, ValueError) as exc:
            checks.append({"path": relative, "status": "error", "error": str(exc)})
            return False

    for record in state["records"]:
        file_check(record["prediction"], record["sha256"])
        if "metrics" not in record:
            continue
        try:
            value = read_json(repo_path(root, record["metrics"]))
            for key in record["metric_key"]:
                value = value[key]
            valid = type(value) in (int, float) and math.isclose(
                value, record["macro_f1"], rel_tol=0, abs_tol=1e-12
            )
            checks.append({"path": record["metrics"], "status": "ok" if valid else "score_mismatch"})
        except (OSError, ValueError, KeyError, TypeError) as exc:
            checks.append({"path": record["metrics"], "status": "error", "error": str(exc)})

    for item in state.get("protected_files", []):
        file_check(item["path"], item["sha256"])
    for item in state.get("source_manifests", []):
        if not file_check(item["path"], item["sha256"]):
            continue
        try:
            mapping = read_json(repo_path(root, item["path"]))[item["map_key"]]
            for name, digest in mapping.items():
                file_check(f"{item['source_root']}/{name}", digest)
        except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
            checks.append({"path": item["path"], "status": "error", "error": str(exc)})

    return {"ok": bool(checks) and all(c["status"] == "ok" for c in checks),
            "checks": len(checks), "passed": sum(c["status"] == "ok" for c in checks),
            "failures": [c for c in checks if c["status"] != "ok"],
            "scope": "Read-only hashes and recorded metrics; not a CPU prediction replay, new inference or score measurement."}


def summarized_measurements(state):
    """Keep a fixed-input policy comparison distinct from a standalone run."""
    records = state['records']
    fresh = [r for r in records if r['kind'] == 'fresh_model_inference']
    cpu = [r for r in records if 'cpu_replay' in r['kind']]
    comparisons = [r for r in records
                   if r['kind'] == 'fresh_whole_cohort_policy_comparison']
    displayed = []
    if fresh:
        displayed.append(('Latest standalone whole fresh', fresh[-1]))
    # A completed comparison remains useful until a newer standalone run
    # supersedes that research round. Showing an older grid beside a newer
    # whole-fresh result makes a cold start look as if the grid is still active.
    latest_fresh_index = max(
        (i for i, r in enumerate(records) if r['kind'] == 'fresh_model_inference'),
        default=-1,
    )
    latest_comparison_index = max(
        (i for i, r in enumerate(records)
         if r['kind'] == 'fresh_whole_cohort_policy_comparison'),
        default=-1,
    )
    if comparisons and latest_comparison_index > latest_fresh_index:
        current_round = comparisons[-1]['comparison_round']
        current = [r for r in comparisons if r['comparison_round'] == current_round]
        # A standalone normal execution also participates in its declared grid.
        # Excluding it can report a lower diagnostic as that round's best result.
        current += [r for r in fresh if r.get('comparison_round') == current_round]
        controls = [r for r in current if r.get('policy') == 'current']
        if controls:
            displayed.append(('Latest whole comparison control', controls[-1]))
        displayed.append(('Best candidate in that whole comparison',
                          max(current, key=lambda r: r['macro_f1'])))
    if cpu:
        displayed.append(('Best recorded CPU', max(cpu, key=lambda r: r['macro_f1'])))
        displayed.append(('Latest CPU replay', cpu[-1]))
    return displayed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verify", action="store_true", help="Verify locally preserved predictions, raw responses and frozen source manifests")
    parser.add_argument("--json", action="store_true", help="Print machine-readable state")
    parser.add_argument("--history", action="store_true", help="Print every preserved score instead of the current summary")
    args = parser.parse_args()
    state = read_json(ROOT / "docs/STATE.json")
    result = {"state": state}
    if args.verify:
        result["verification"] = verify_state(ROOT, state)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"DACON 236754 | state updated {state['updated_utc']}")
        print(f"Official score: {state['official_score']} | target: {state['target_official_macro_f1']}")
        if args.history:
            displayed = [(record['id'], record) for record in state['records']]
        else:
            displayed = summarized_measurements(state)
        for label, record in displayed:
            print(f"{label}: {record['macro_f1']:.6f} | FP {record['fp']} / FN {record['fn']} | {record['id']} | {record['kind']}")
        print(f"Current task: {state['active_task']['status']}")
        print(f"Next: {state['active_task']['next_action']}")
        print(f"Single entry: {state['entrypoints']['current_input_b4']}")
        print(f"Validation: {state['entrypoints']['b4_validation']}")
        print("Canonical runtime: submission/. Historical experiments are evidence, not alternate active entries.")
        print("Read docs/LOCAL_HANDOFF.md if available before touching a live agent or GPU.")
        if args.verify:
            audit = result["verification"]
            print(f"Local preservation: {audit['passed']}/{audit['checks']} checks passed")
            for failure in audit["failures"]:
                print(f"  {failure['status']}: {failure['path']}")
            print(audit["scope"])
    return 0 if not args.verify or result["verification"]["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
