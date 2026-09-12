import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
RETIRED = (
    "run_experiments.py", "audit_overlay.py", "audit_output_contract.py",
    "audit_retrieval.py", "evaluate_cache_pair.py", "replay_saved_outputs.py",
)


@pytest.mark.parametrize("name", RETIRED)
def test_retired_cli_help_and_execution_do_not_import_old_pipeline(name):
    # No research artifacts or model packages are needed even for the old CLIs.
    code = (
        "import runpy,sys\n"
        "sys.argv=[sys.argv[1]]+sys.argv[2:]\n"
        "try:\n runpy.run_path(sys.argv[0], run_name='__main__')\n"
        "finally:\n assert 'pps' not in sys.modules\n assert 'vllm' not in sys.modules\n"
    )
    command = [sys.executable, "-c", code, str(ROOT / "tools" / name)]
    help_result = subprocess.run([*command, "--help"], capture_output=True, text=True)
    assert help_result.returncode == 0, help_result.stderr
    assert "RETIRED" in help_result.stdout
    result = subprocess.run(command, capture_output=True, text=True)
    assert result.returncode == 2
    assert "no model, labels or output were opened" in result.stderr
