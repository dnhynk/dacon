"""Small, read-only cold-start status and preserved-artifact check.

No model, network, label loading, prediction editing or automatic experiment.
--verify checks the local evidence hashes in runs/evidence_manifest.json, which only the
original workspace has (runs/ is git-ignored).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = "runs/evidence_manifest.json"


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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verify", action="store_true", help=f"Verify the local evidence hashes in {EVIDENCE}")
    parser.add_argument("--json", action="store_true", help="Print machine-readable state")
    parser.add_argument("--ledger", action="store_true", help="Print every official submission")
    args = parser.parse_args()
    # STATE.json text can fall outside the console code page (cp949 on this Windows machine); print '?' there.
    sys.stdout.reconfigure(errors="replace")
    state = read_json(ROOT / "docs/STATE.json")
    result = {"state": state}
    if args.verify:
        manifest = ROOT / EVIDENCE
        result["verification"] = verify_state(ROOT, read_json(manifest)) if manifest.is_file() else {
            "ok": False, "checks": 1, "passed": 0,
            "failures": [{"path": EVIDENCE, "status": "missing_local_artifact"}],
            "scope": "No local evidence manifest; a public clone has no private artifacts to check."}
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        official, task, runtime = state["official"], state["task"], state["runtime"]
        best = official["best"]
        print(f"{state['competition']} | state updated {state['updated_utc']}")
        print(f"Official best: {best['name']} {best['score']} | goal {state['goal_official_macro_f1']}")
        if args.ledger:
            for entry in official["ledger"]:
                score = "pending" if entry["score"] is None else f"{entry['score']:.10f}"
                print(f"  {entry['row']:>3} {entry.get('kst', ''):10} {entry['name']:<10} {score:<12} {entry.get('outcome', '')}")
        pending = [entry["name"] for entry in official["ledger"] if entry["score"] is None]
        if pending:
            print(f"Awaiting scores: {', '.join(pending)}")
        print(f"Task: {task['status']}")
        print(f"Next: {task['next_action']}")
        print(f"Entry: {runtime['entry']} -> {runtime['package']} (probe switches: {runtime['switches']})")
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
