"""Exercise the actual CI gate so skipped or cancelled suites cannot pass."""

import ast
import json
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


def _workflow_expression(expression, context):
    """Evaluate the small expression grammar used by the actual workflow."""
    for key, value in sorted(context.items(), key=lambda item: -len(item[0])):
        expression = expression.replace(key, repr(value))
    expression = expression.removeprefix("${{").removesuffix("}}")
    expression = expression.replace("&&", " and ").replace("||", " or ")
    expression = expression.replace("true", "True")

    def evaluate(node):
        if isinstance(node, ast.Constant):
            return node.value
        if isinstance(node, ast.BoolOp):
            result = evaluate(node.values[0])
            for value in node.values[1:]:
                if (isinstance(node.op, ast.And) and result) or (
                    isinstance(node.op, ast.Or) and not result
                ):
                    result = evaluate(value)
            return result
        if isinstance(node, ast.Compare) and len(node.ops) == 1:
            left, right = evaluate(node.left), evaluate(node.comparators[0])
            if isinstance(node.ops[0], ast.Eq):
                return left == right
            if isinstance(node.ops[0], ast.NotEq):
                return left != right
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id == "always" and not node.args:
                return True
            if node.func.id == "fromJSON" and len(node.args) == 1:
                return json.loads(evaluate(node.args[0]))
        raise AssertionError(f"Unrecognized workflow expression: {ast.dump(node)}")

    return evaluate(ast.parse(expression.strip(), mode="eval").body)


@pytest.mark.parametrize("private", [True, False])
@pytest.mark.parametrize("event", ["push", "pull_request"])
@pytest.mark.parametrize("same_repository", [True, False])
def test_public_forks_never_select_private_runner(private, event, same_repository):
    workflow = yaml.safe_load(WORKFLOW.read_text())
    context = {
        "github.event.repository.private": private,
        "github.event_name": event,
        "github.event.pull_request.head.repo.full_name": (
            "example/project" if same_repository else "external/fork"
        ),
        "github.repository": "example/project",
    }
    permitted = not private or event != "pull_request" or same_repository
    for job in workflow["jobs"].values():
        assert _workflow_expression(job["if"], context) is permitted
        labels = _workflow_expression(job["runs-on"], context)
        if private:
            assert labels == ["self-hosted", "Linux", "X64", "epg-voice-gateway"]
        else:
            assert labels == ["ubuntu-latest"]
        for step in job["steps"]:
            if str(step.get("uses", "")).startswith("actions/checkout@"):
                assert step["with"]["persist-credentials"] is False
    assert workflow["permissions"] == {"contents": "read"}
    assert "pull_request_target" not in workflow.get(True, workflow.get("on", {}))


@pytest.mark.parametrize("private, expected", [(True, 30), (False, 20), (None, 20)])
def test_python_timeout_reserves_private_runner_cleanup(private, expected):
    jobs = yaml.safe_load(WORKFLOW.read_text())["jobs"]
    actual = _workflow_expression(
        jobs["python_checks"]["timeout-minutes"],
        {"github.event.repository.private": private},
    )
    assert actual == expected
    assert jobs["node_checks"]["timeout-minutes"] == 20
    assert jobs["python"]["timeout-minutes"] == 5
