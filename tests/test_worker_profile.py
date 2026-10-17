"""Adversarial tests for the independently pinned provider trust boundary."""
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("worker_profile", ROOT / "scripts/worker_profile.py")
profile = importlib.util.module_from_spec(spec)
spec.loader.exec_module(profile)


def test_caller_cannot_self_attest_a_different_binary(tmp_path, monkeypatch):
    pins = {"binary_name": "provider", "binary_sha256": hashlib.sha256(b"qualified").hexdigest()}
    (tmp_path / "provider").write_bytes(b"attacker")
    (tmp_path / "provenance.json").write_text(json.dumps({
        "binary_sha256": hashlib.sha256(b"attacker").hexdigest()
    }))
    with pytest.raises(ValueError, match="Unqualified content"):
        profile.verify_provider(tmp_path, pins)


def test_provenance_must_match_original(tmp_path, monkeypatch):
    binary = b"qualified"
    (tmp_path / "provider").write_bytes(binary)
    (tmp_path / "qualified-provenance.json").write_bytes(b'{"source": "original"}')
    (tmp_path / "provenance.json").write_text('{"source": "changed"}')
    monkeypatch.setattr(profile, "PINS", tmp_path)
    pins = {"binary_name": "provider", "binary_sha256": hashlib.sha256(binary).hexdigest(),
            "qualified_provenance_sha256": hashlib.sha256(b'{"source": "original"}').hexdigest()}
    with pytest.raises(ValueError, match="provenance differs"):
        profile.verify_provider(tmp_path, pins)
    (tmp_path / "provenance.json").write_text('{"source": "original"}')
    profile.verify_provider(tmp_path, pins)


def test_receipt_archive_tampering_is_rejected(tmp_path, monkeypatch):
    pins = profile.profile()
    monkeypatch.setattr(profile, "PINS", tmp_path)
    (tmp_path / "receipts").mkdir()
    (tmp_path / pins["receipts"][0]["file"]).write_bytes(b"changed")
    with pytest.raises(ValueError, match="Unqualified content"):
        profile.install_receipts(tmp_path / "output", pins)


def test_original_receipts_preserve_context_and_all_397_packages(tmp_path):
    pins = profile.profile()
    profile.install_receipts(tmp_path / "receipts", pins)
    selected = []
    for path in (tmp_path / "receipts").glob("*.json"):
        receipt = json.loads(path.read_text())
        assert receipt["context"]["qualification_id"] == pins["qualification_id"]
        selected.extend(receipt["packages"])
    assert len(selected) == len(set(selected)) == 397


def test_shared_source_tampering_and_unpinned_import_are_rejected(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "main.py").write_bytes(b"qualified")
    (source / "json.py").write_bytes(b"malicious shadow module")
    pins = {"shared_files": {"main.py": hashlib.sha256(b"qualified").hexdigest()}}
    with profile.shared_snapshot(source, pins) as snapshot:
        assert sorted(path.name for path in snapshot.iterdir()) == ["main.py"]
    (source / "main.py").write_bytes(b"changed")
    with pytest.raises(ValueError, match="Unqualified content"):
        with profile.shared_snapshot(source, pins):
            pytest.fail("must reject modified shared code")


def test_isolated_invocation_ignores_pythonpath(tmp_path, monkeypatch):
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    (scripts / "probe.py").write_text("import json; print(json.dumps({'ok': True}))")
    hostile = tmp_path / "hostile"
    hostile.mkdir()
    (hostile / "json.py").write_text("raise RuntimeError('injected')")
    monkeypatch.setenv("PYTHONPATH", str(hostile))
    profile.invoke(tmp_path, "probe.py", [])


def test_symlink_is_not_accepted_as_pinned_source(tmp_path):
    target = tmp_path / "target"
    target.write_bytes(b"same")
    link = tmp_path / "link"
    link.symlink_to(target)
    with pytest.raises(ValueError, match="regular file"):
        profile.checked_bytes(link, hashlib.sha256(b"same").hexdigest())


def test_executed_snapshot_survives_external_bundle_replacement(tmp_path, monkeypatch):
    binary = b"qualified"
    original = b'{"source": "original"}'
    (tmp_path / "provider").write_bytes(binary)
    (tmp_path / "qualified-provenance.json").write_bytes(original)
    (tmp_path / "provenance.json").write_bytes(original)
    monkeypatch.setattr(profile, "PINS", tmp_path)
    pins = {"binary_name": "provider", "binary_sha256": hashlib.sha256(binary).hexdigest(),
            "qualified_provenance_sha256": hashlib.sha256(original).hexdigest()}
    with profile.provider_snapshot(tmp_path, pins) as private:
        (tmp_path / "provider").write_bytes(b"replacement")
        assert (private / "provider").read_bytes() == binary
        assert (private / "provider").stat().st_mode & 0o777 == 0o700
