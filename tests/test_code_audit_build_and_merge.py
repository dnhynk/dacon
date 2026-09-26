"""Build/evidence and probe-combination contracts, independent of labels."""
import importlib.util
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def calc():
    spec = importlib.util.spec_from_file_location('audit_final_calc', ROOT / 'runs/rebuild_c/transfer_20260925/final_calc.py')
    if not spec.origin or not Path(spec.origin).is_file():
        pytest.skip('Private probe plan not present in this checkout')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def score(calc, name, value):
    _, base, items = calc.SCORES[name]
    calc.SCORES[name] = (value, base, items)


def test_child_prediction_includes_its_measured_base_once(calc):
    score(calc, 'X4OBJ', 0.72)
    assert calc.predict(['X4OBJ']) == (0.72, [])
    assert calc.predict(['CG', 'X4OBJ', 'CG']) == (0.72, [])


def test_thinking_all_items_is_a_wildcard_for_interactions(calc):
    score(calc, 'THKA', 0.73)
    score(calc, 'V20OR', 0.701)
    total, conflicts = calc.predict(['THKA', 'V20OR'])
    assert ('v20', 'THKA', 'V20OR') in conflicts
    assert calc.predict(['THKA']) == (0.73, [])


def test_structural_dependencies_are_not_limited_to_observed_cells(calc):
    score(calc, 'PRX4X', 0.71)
    score(calc, 'X4OBJ', 0.72)
    _, conflicts = calc.predict(['PRX4X', 'X4OBJ'])
    assert any(it == 'v11' and {a, b} == {'PRX4X', 'X4OBJ'} for it, a, b in conflicts)


def test_unscored_probe_cannot_masquerade_as_zero_gain(calc):
    with pytest.raises(ValueError, match='unscored'):
        calc.predict(['V20OR'])


def test_conflicting_boolean_settings_are_not_silently_or_ed(calc, monkeypatch):
    monkeypatch.setattr(calc, 'overrides', lambda name: {'X': name == 'CG'})
    with pytest.raises(SystemExit, match='conflict on X'):
        calc.merge(['CG', 'V9B'])


def builder_fixture(tmp_path, successful=True, catalog=True):
    (tmp_path / 'tools').mkdir()
    shutil.copyfile(ROOT / 'tools/build_submission.py', tmp_path / 'tools/build_submission.py')
    src = tmp_path / 'submission'
    (src / 'pps_c/assets').mkdir(parents=True)
    if catalog:
        (src / 'pps_c/assets/catalog.csv').write_text('synthetic\n', encoding='utf-8')
    code = ('import os\nfrom pathlib import Path\np=Path(os.environ["PPS_OUTPUT_DIR"])\n'
            'p.mkdir(parents=True)\n(p/"submission.csv").write_text("id\\n")\n') if successful else 'raise SystemExit(3)\n'
    (src / 'script.py').write_text(code, encoding='utf-8')
    return tmp_path / 'tools/build_submission.py'


def build(script, name):
    return subprocess.run([sys.executable, '-X', 'utf8', '-B', str(script), name],
                          capture_output=True, text=True, encoding='utf-8')


def test_builder_preserves_existing_evidence(tmp_path):
    script = builder_fixture(tmp_path)
    assert build(script, 'first').returncode == 0
    archive = tmp_path / 'artifacts/rebuild_c/first/submit.zip'
    before = archive.read_bytes()
    retry = build(script, 'first')
    assert retry.returncode != 0 and 'overwrite' in retry.stderr
    assert archive.read_bytes() == before


def test_builder_fails_the_command_when_zip_mock_fails(tmp_path):
    done = build(builder_fixture(tmp_path, successful=False), 'bad')
    assert done.returncode != 0 and "'mock_run_from_zip': 'FAIL'" in done.stdout


def test_builder_rejects_path_escape(tmp_path):
    done = build(builder_fixture(tmp_path), '../../outside')
    assert done.returncode != 0 and 'must stay under' in done.stderr
    assert not (tmp_path / 'outside/submit.zip').exists()


def test_builder_refuses_without_the_local_catalog_copy(tmp_path):
    done = build(builder_fixture(tmp_path, catalog=False), 'no_catalog')
    assert done.returncode != 0 and 'catalog.csv' in done.stderr
    assert not (tmp_path / 'artifacts/rebuild_c/no_catalog/submit.zip').exists()
