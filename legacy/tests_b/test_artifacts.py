import ast
import hashlib
import json
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from tools.build_submission import CONFIG_FILES, INDEX_FILE, build, source_files, source_payload
from tools.create_colab_notebook import SOURCES

ROOT = Path(__file__).resolve().parents[1]


def test_submission_allowlist_and_isolated_entrypoint(tmp_path):
    archive = tmp_path / "candidate.zip"
    manifest = build(archive)
    with zipfile.ZipFile(archive) as z:
        # The text sources plus exactly one binary file: the corpus line index, shipped even while its switch is off.
        assert set(z.namelist()) == {*source_files(), INDEX_FILE}
        assert z.testzip() is None
        assert all(name.startswith("submission/") or name in {"script.py", "requirements.txt"}
                   for name in z.namelist())
        for name, info in manifest["files"].items():
            assert hashlib.sha256(z.read(name)).hexdigest() == info["sha256"]
        z.extractall(tmp_path / "isolated")
    assert not manifest["l40s_runtime_verified"]
    result = subprocess.run([sys.executable, "script.py", "--help"], cwd=tmp_path / "isolated",
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert "--data-dir" in result.stdout
    assert "--model-dir" in result.stdout


def test_single_runtime_archive_is_deterministic_and_nonoverwriting(tmp_path):
    first, second = tmp_path / "one.zip", tmp_path / "two.zip"
    a, b = build(first), build(second)
    assert a == b and first.read_bytes() == second.read_bytes()
    before = first.read_bytes()
    with pytest.raises(FileExistsError):
        build(first)
    assert first.read_bytes() == before


def test_packaging_ignores_research_and_nonallowlisted_files(tmp_path):
    for name, content in source_payload(with_index=True).items():
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
    for name in ("submission/secrets.env", "submission/answers.csv", "submission/model/labels.json",
                 "submission/model/other_hashes.u64",
                 "submission/results/hidden.py", "pps/pipeline.py", "experiments/private.py"):
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("must not be packaged", encoding="utf-8")
    assert source_payload(tmp_path, with_index=True) == source_payload(with_index=True)
    assert source_payload(tmp_path) == source_payload() and INDEX_FILE not in source_payload()


def test_canonical_imports_cannot_fall_back_to_legacy_packages():
    for name, content in source_payload().items():
        if not name.endswith(".py"):
            continue
        for node in ast.walk(ast.parse(content.decode("utf-8"), filename=name)):
            if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                assert node.module.split(".")[0] not in {"pps", "original_a", "v20_legacy", "experiments"}, name
            elif isinstance(node, ast.Import):
                assert not {a.name.split(".")[0] for a in node.names} & {"pps", "original_a", "v20_legacy", "experiments"}, name


def test_retired_experiment_selector_is_not_a_submission_interface(tmp_path):
    result = subprocess.run([sys.executable, str(ROOT / "tools/build_submission.py"),
                             "--experiment-dir", str(tmp_path)], capture_output=True, text=True)
    assert result.returncode != 0
    assert "unrecognized arguments" in result.stderr


def test_notebook_code_is_valid_and_embedded_source_matches(tmp_path):
    notebook = json.loads((ROOT / "notebooks/dacon_colab.ipynb").read_text(encoding="utf-8"))
    assert notebook["nbformat"] == 4
    source_cell = None
    for cell in notebook["cells"]:
        if cell["cell_type"] == "code":
            code = "".join(cell["source"])
            ast.parse(code)
            assert cell["outputs"] == []
            if "SOURCE_FILES = " in code:
                source_cell = code
    assert source_cell
    scope = {"WORK": tmp_path, "hashlib": hashlib}
    exec(compile(source_cell, "notebook_source", "exec"), scope)
    assert set(scope["SOURCE_FILES"]) == set(SOURCES)
    for name in SOURCES:
        assert (tmp_path / name).read_text(encoding="utf-8") == (ROOT / name).read_text(encoding="utf-8")
    # The notebook embeds text sources only; the binary corpus line index travels in the archive.
    assert set(SOURCES) == set(source_files()) and INDEX_FILE not in SOURCES
    assert "run_experiments.py" not in json.dumps(notebook)
    assert set(CONFIG_FILES).issubset(SOURCES)
