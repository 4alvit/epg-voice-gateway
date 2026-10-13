"""Exercise the actual CI gate so skipped or cancelled suites cannot pass."""

import os
import subprocess
from pathlib import Path

import pytest
import yaml

WORKFLOW = Path(__file__).resolve().parents[1] / ".github/workflows/checks.yml"
RESULTS = ("success", "failure", "cancelled", "skipped", "")


@pytest.mark.parametrize("python_result", RESULTS)
@pytest.mark.parametrize("node_result", RESULTS)
def test_ci_gate_requires_both_suites_to_succeed(python_result, node_result):
    gate = yaml.safe_load(WORKFLOW.read_text())["jobs"]["python"]
    assert set(gate["needs"]) == {"python_checks", "node_checks"}
    assert gate["if"].startswith("always() &&")
    step = gate["steps"][0]
    assert step["env"] == {
        "PYTHON_RESULT": "${{ needs.python_checks.result }}",
        "NODE_RESULT": "${{ needs.node_checks.result }}",
    }
    result = subprocess.run(
        ["bash", "--noprofile", "--norc", "-eo", "pipefail", "-c", step["run"]],
        env={**os.environ, "PYTHON_RESULT": python_result, "NODE_RESULT": node_result},
        capture_output=True,
        text=True,
        check=False,
    )
    assert (result.returncode == 0) is (python_result == node_result == "success")
