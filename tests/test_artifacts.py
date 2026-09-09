import ast
import json
import subprocess
import sys
import zipfile
from pathlib import Path

from tools.build_submission import FILES, build
from tools.create_colab_notebook import SOURCES

ROOT = Path(__file__).resolve().parents[1]


def test_submission_allowlist_and_isolated_entrypoint(tmp_path):
    archive = tmp_path / "candidate.zip"
    manifest = build(archive)
    with zipfile.ZipFile(archive) as z:
        assert set(z.namelist()) == set(FILES) | {"model/config.json"}
        assert z.testzip() is None
        z.extractall(tmp_path / "isolated")
    assert not manifest["l40s_runtime_verified"]
    result = subprocess.run([sys.executable, "script.py", "--help"], cwd=tmp_path / "isolated",
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert "--data-dir" in result.stdout


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
    scope = {"WORK": tmp_path}
    exec(compile(source_cell, "notebook_source", "exec"), scope)
    assert set(scope["SOURCE_FILES"]) == set(SOURCES)
    for name in SOURCES:
        assert (tmp_path / name).read_text(encoding="utf-8") == (ROOT / name).read_text(encoding="utf-8")
