"""Runtime delivery must reject vendored vulnerabilities, not just direct packages."""

import importlib.util
from pathlib import Path

import pytest
import yaml

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/verify_container_security.py"
SPEC = importlib.util.spec_from_file_location("container_security", SCRIPT)
security = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(security)


def report():
    return {
        "SchemaVersion": 2, "ArtifactType": "container_image",
        "Results": [
            {"Class": "os-pkgs", "Type": "debian", "Packages": [
                {"Name": "liblzma5", "Version": "5.8.1", "Release": "1+deb13u2"},
            ]},
            {"Class": "lang-pkgs", "Type": "python-pkg", "Packages": [
                {"Name": "epg-voice-gateway", "Version": "0.1.0b3"},
            ]},
        ],
    }


def add_finding(data, index, severity="HIGH", fixed=""):
    data["Results"][index]["Vulnerabilities"] = [{
        "VulnerabilityID": "CVE-EXAMPLE", "PkgName": "example",
        "Severity": severity, "FixedVersion": fixed,
    }]


def test_complete_clean_inventory_passes():
    assert security.verify(report())["passed"]


@pytest.mark.parametrize("severity", ["HIGH", "CRITICAL"])
@pytest.mark.parametrize("fixed", ["", "2.0"])
@pytest.mark.parametrize("analyzer", ["python-pkg", "sbom"])
def test_language_findings_block_even_when_vendored_or_unfixed(severity, fixed, analyzer):
    data = report()
    data["Results"][1]["Packages"].append({
        "Name": "example", "Version": "1.0", "AnalyzedBy": analyzer,
    })
    add_finding(data, 1, severity, fixed)
    result = security.verify(data)
    assert not result["passed"]
    assert result["blocking_findings"] == [f"CVE-EXAMPLE: example ({severity})"]


@pytest.mark.parametrize("severity", ["HIGH", "CRITICAL"])
def test_fixed_os_findings_block(severity):
    data = report()
    add_finding(data, 0, severity, "2.0")
    assert not security.verify(data)["passed"]


def test_unfixed_os_is_disclosed_not_misreported_as_fixed():
    data = report()
    add_finding(data, 0)
    result = security.verify(data)
    assert result["passed"]
    assert result["unfixed_os_high_critical"] == ["CVE-EXAMPLE: example (HIGH)"]
    assert result["all_severity_counts"] == {"HIGH": 1}


@pytest.mark.parametrize("severity", ["MEDIUM", "LOW", "UNKNOWN"])
def test_lower_findings_remain_visible(severity):
    data = report()
    add_finding(data, 1, severity)
    result = security.verify(data)
    assert result["passed"]
    assert result["all_severity_counts"] == {severity: 1}


def test_runtime_installer_is_rejected_even_without_advisory():
    data = report()
    data["Results"][1]["Packages"].append({"Name": "pip", "Version": "26.2.2"})
    assert not security.verify(data)["passed"]


@pytest.mark.parametrize("mutation", [
    lambda data: data.pop("Results"),
    lambda data: data.update(Results=[]),
    lambda data: data.update(ArtifactType="filesystem"),
    lambda data: data["Results"].pop(),
    lambda data: data["Results"][1].pop("Packages"),
    lambda data: data["Results"][1].update(Packages=[]),
    lambda data: data["Results"][1].update(Class="unknown"),
    lambda data: data["Results"][1].update(Vulnerabilities=None),
    lambda data: data["Results"][1].update(Vulnerabilities=[{"Severity": "HIGH"}]),
])
def test_incomplete_evidence_fails_closed(mutation):
    data = report()
    mutation(data)
    with pytest.raises(ValueError):
        security.verify(data)


@pytest.mark.parametrize("advisory", sorted(security.XZ_ADVISORIES))
def test_original_xz_protection_is_retained(advisory):
    data = report()
    add_finding(data, 0, "LOW")
    data["Results"][0]["Vulnerabilities"][0]["VulnerabilityID"] = advisory
    assert not security.verify(data)["passed"]


def test_unpatched_or_duplicate_native_library_is_rejected():
    data = report()
    data["Results"][0]["Packages"][0]["Release"] = "1+deb13u1"
    assert not security.verify(data)["passed"]

    data = report()
    data["Results"][0]["Packages"] *= 2
    assert not security.verify(data)["passed"]


def test_required_container_job_uses_unfiltered_scan_and_blocking_gate():
    workflow = yaml.safe_load((SCRIPT.parents[1] / ".github/workflows/checks.yml").read_text())
    steps = workflow["jobs"]["container"]["steps"]
    scan = next(step for step in steps if step.get("name", "").startswith("Scan the actual"))
    assert scan["with"]["ignore-unfixed"] is False
    assert scan["with"]["list-all-pkgs"] is True
    assert "severity" not in scan["with"]
    gate = next(step for step in steps if step.get("name") == "Reject actionable runtime vulnerabilities")
    assert gate["run"] == "python3 scripts/verify_container_security.py container-trivy.json"
    assert not gate.get("continue-on-error") and "if" not in gate
    evidence = next(step for step in steps if step.get("name", "").startswith("Upload image inventory"))
    assert evidence["if"] == "${{ !cancelled() }}"
