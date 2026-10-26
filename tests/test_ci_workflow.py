"""Exercise the actual CI gate so skipped or cancelled suites cannot pass."""

import ast
import json
import os
import re
import subprocess
from pathlib import Path

import pytest
import yaml

WORKFLOW = Path(__file__).resolve().parents[1] / ".github/workflows/checks.yml"
RESULTS = ("success", "failure", "cancelled", "skipped", "")


@pytest.mark.parametrize("python_result", RESULTS)
@pytest.mark.parametrize("node_result", RESULTS)
@pytest.mark.parametrize("container_result", RESULTS)
@pytest.mark.parametrize("worker_result", RESULTS)
@pytest.mark.parametrize("public", ["true", "false"])
def test_ci_gate_requires_all_applicable_suites(python_result, node_result, container_result, worker_result, public):
    gate = yaml.safe_load(WORKFLOW.read_text())["jobs"]["python"]
    assert set(gate["needs"]) == {"python_checks", "node_checks", "container", "worker_profile"}
    assert gate["if"].startswith("always() &&")
    gate_steps = [step for step in gate["steps"] if step.get("name") == "Verify required checks"]
    assert len(gate_steps) == 1
    step = gate_steps[0]
    assert step["env"] == {
        "PYTHON_RESULT": "${{ needs.python_checks.result }}",
        "NODE_RESULT": "${{ needs.node_checks.result }}",
        "CONTAINER_RESULT": "${{ needs.container.result }}",
        "WORKER_RESULT": "${{ needs.worker_profile.result }}",
        "PUBLIC_REPOSITORY": "${{ github.event.repository.private != true }}",
    }
    result = subprocess.run(
        ["bash", "--noprofile", "--norc", "-eo", "pipefail", "-c", step["run"]],
        env={**os.environ, "PYTHON_RESULT": python_result, "NODE_RESULT": node_result,
             "CONTAINER_RESULT": container_result, "WORKER_RESULT": worker_result, "PUBLIC_REPOSITORY": public},
        capture_output=True,
        text=True,
        check=False,
    )
    assert (result.returncode == 0) is (
        python_result == node_result == "success"
        and (public == "false" or container_result == worker_result == "success")
    )


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
            if node.func.id == "contains" and len(node.args) == 2:
                return evaluate(node.args[1]) in evaluate(node.args[0])
            if node.func.id == "format" and len(node.args) == 2:
                return evaluate(node.args[0]).format(evaluate(node.args[1]))
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
        "vars.CI_RUNNER_MODE": "github",
        "vars.CI_RUNNER_LABELS": '["reserved-public-ci"]',
        "github.ref": "refs/heads/main",
        "github.event.repository.default_branch": "main",
    }
    permitted = not private or event != "pull_request" or same_repository
    for name, job in workflow["jobs"].items():
        if name in ("container", "worker_profile"):
            assert _workflow_expression(job["if"], context) is (not private)
            assert _workflow_expression(job["runs-on"], context) == ["ubuntu-latest"]
            continue
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


@pytest.mark.parametrize("mode", ["", "github", "unknown", "self-hosted", "k3s"])
@pytest.mark.parametrize("labels", ["", '["reserved-public-ci"]'])
@pytest.mark.parametrize("event, same_repository, ref, trusted", [
    ("pull_request", True, "refs/pull/1/merge", True),
    ("pull_request", False, "refs/pull/1/merge", False),
    ("push", True, "refs/heads/trunk", True),
    ("push", True, "refs/heads/feature", False),
    ("schedule", True, "refs/heads/trunk", True),
    ("workflow_dispatch", True, "refs/heads/trunk", True),
    ("workflow_dispatch", True, "refs/heads/feature", False),
    ("pull_request_target", True, "refs/heads/trunk", False),
])
def test_public_reserve_requires_opt_in_and_trusted_event(mode, labels, event, same_repository, ref, trusted):
    jobs = yaml.safe_load(WORKFLOW.read_text())["jobs"]
    context = {
        "vars.CI_RUNNER_MODE": mode,
        "vars.CI_RUNNER_LABELS": labels,
        "github.event_name": event,
        "github.event.pull_request.head.repo.full_name": "example/project" if same_repository else "external/fork",
        "github.repository": "example/project",
        "github.ref": ref,
        "github.event.repository.default_branch": "trunk",
    }
    expected = json.loads(labels or "[]") if mode in ("self-hosted", "k3s") and trusted else ["ubuntu-latest"]
    for name in ("container", "worker_profile"):
        assert _workflow_expression(jobs[name]["runs-on"], context) == expected


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


def test_ci_and_container_use_the_same_uv_exporter():
    root = WORKFLOW.parents[2]
    pins = [
        line.removesuffix("\\").strip()
        for line in (root / "container/requirements-export.txt").read_text().splitlines()
        if line.startswith("uv==")
    ]
    assert len(pins) == 1
    expected = pins[0].removeprefix("uv==").strip()
    assert expected
    assert f"uv=={expected}" in (root / "container/requirements-export.in").read_text().splitlines()
    jobs = yaml.safe_load(WORKFLOW.read_text())["jobs"]
    exporters = [
        step["with"]["version"]
        for job in jobs.values()
        for step in job.get("steps", [])
        if str(step.get("uses", "")).startswith("astral-sh/setup-uv@")
    ]
    assert exporters
    assert all(version == expected for version in exporters)


def test_container_build_lock_preserves_declared_backends():
    root = WORKFLOW.parents[2]
    declared = {
        re.split(r"[<>=!~\[]", line, maxsplit=1)[0].strip().lower()
        for line in (root / "container/requirements-build.in").read_text().splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }
    locked = {
        line.split("==", maxsplit=1)[0].lower()
        for line in (root / "container/requirements-build.txt").read_text().splitlines()
        if "==" in line and not line.lstrip().startswith("#")
    }
    assert declared <= locked, f"Build lock omitted declared backends: {declared - locked}"
