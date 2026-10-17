#!/usr/bin/env python3
"""Delegate Worker deployment to a verified, unchanged shared Cloudflare profile."""
import argparse
import contextlib
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PINS = ROOT / "security/worker-profile"
PLATFORM = "linux_amd64"
BOOTSTRAP = (
    "import runpy,sys; from pathlib import Path; "
    "p=sys.argv.pop(1); sys.path.insert(0,str(Path(p).parent)); "
    "runpy.run_path(p,run_name='__main__')"
)


def checked_bytes(path, expected):
    """Hash the very bytes copied/extracted, rejecting nonregular input paths."""
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"Expected a regular file: {path}")
    content = path.read_bytes()
    if hashlib.sha256(content).hexdigest() != expected:
        raise ValueError(f"Unqualified content: {path}")
    return content


def profile():
    return json.loads((PINS / "profile.json").read_text())


@contextlib.contextmanager
def shared_snapshot(source, pins):
    """Only pinned files enter Python's import path; extra checkout files do not."""
    with tempfile.TemporaryDirectory(prefix="epg-shared-profile-") as name:
        target = Path(name)
        for relative, digest in pins["shared_files"].items():
            content = checked_bytes(source / relative, digest)
            destination = target / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(content)
        yield target


def install_receipts(destination, pins):
    """Preserve original producer context/logs; never manufacture a test result."""
    destination.mkdir()
    for receipt in pins["receipts"]:
        content = checked_bytes(PINS / receipt["file"], receipt["sha256"])
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            for suffix in ("json", "log"):
                name = f"shard-{receipt['index']}.{suffix}"
                (destination / name).write_bytes(archive.read(name))


def verify_provider(provider, pins):
    """A supplied JSON file is never the authority for executable bytes."""
    binary = checked_bytes(provider / pins["binary_name"], pins["binary_sha256"])
    original = json.loads(checked_bytes(
        PINS / "qualified-provenance.json", pins["qualified_provenance_sha256"]
    ))
    current = json.loads((provider / "provenance.json").read_text())
    if current != original:
        raise ValueError("Provider provenance differs from the qualified production build")
    return binary, original


@contextlib.contextmanager
def provider_snapshot(provider, pins):
    """Run the verified bytes, not a mutable second read of the supplied bundle."""
    binary, provenance = verify_provider(provider, pins)
    with tempfile.TemporaryDirectory(prefix="epg-qualified-provider-") as name:
        snapshot = Path(name)
        (snapshot / pins["binary_name"]).write_bytes(binary)
        (snapshot / pins["binary_name"]).chmod(0o700)
        (snapshot / "provenance.json").write_text(json.dumps(provenance))
        yield snapshot


def invoke(snapshot, script, arguments, env=None):
    # -I ignores PYTHONPATH/user site; only the copied pinned script set is added.
    subprocess.run(
        [sys.executable, "-I", "-c", BOOTSTRAP, str(snapshot / "scripts" / script),
         *map(str, arguments)], check=True, env=env, umask=0o077,
    )


def build(args, snapshot, pins):
    with tempfile.TemporaryDirectory(prefix="epg-unit-receipts-") as name:
        receipts = Path(name) / "receipts"
        install_receipts(receipts, pins)
        invoke(snapshot, "build_secure_provider.py", [
            "--go", args.go.resolve(strict=True), "--output", args.output.resolve(),
            "--platform", PLATFORM, "--test-receipts", receipts,
            "--test-shards", "16", "--qualification-id", pins["qualification_id"],
        ])
    verify_provider(args.output, pins)
    print("Production bytes match the independently qualified build exactly.")


def runtime_arguments(args, directory, command, extra=()):
    return [
        "--tofu", args.tofu.resolve(strict=True),
        "--provider-dir", args.provider_dir.resolve(strict=True),
        "--local-provider-archive", args.local_provider_archive.resolve(strict=True),
        "--directory", directory, "--platform", PLATFORM, command, *extra,
    ]


def qualify(args, snapshot):
    """Exercise actual schemas/mocked plans in public-source-only fresh copies."""
    with tempfile.TemporaryDirectory(prefix="epg-worker-plans-") as name:
        stage = Path(name)
        for module in ("google-home-worker-vpc", "alexa-worker-vpc"):
            source = ROOT / "deploy" / module
            destination = stage / module
            (destination / "terraform/tests").mkdir(parents=True)
            shutil.copyfile(source / "relay.mjs", destination / "relay.mjs")
            for path in (source / "terraform").glob("*.tf"):
                shutil.copyfile(path, destination / "terraform" / path.name)
            for path in (source / "terraform/tests").glob("*.tftest.hcl"):
                shutil.copyfile(path, destination / "terraform/tests" / path.name)
            invoke(snapshot, "run_secure_profile.py", runtime_arguments(
                args, destination / "terraform", "validate", ["-no-color"]
            ))
            invoke(snapshot, "check_epg_worker_plans.py", [
                args.tofu.resolve(strict=True), args.provider_dir.resolve(strict=True),
                args.local_provider_archive.resolve(strict=True), destination / "terraform",
            ])
    print("Both Worker modules passed actual provider schema and mock plan assertions.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--shared-source", required=True, type=Path)
    sub = parser.add_subparsers(dest="mode", required=True)
    builder = sub.add_parser("build")
    builder.add_argument("--go", type=Path, required=True)
    builder.add_argument("--output", type=Path, required=True)
    for mode in ("verify", "run", "check", "tls"):
        item = sub.add_parser(mode)
        item.add_argument("--provider-dir", type=Path, required=True)
        if mode != "verify":
            item.add_argument("--tofu", type=Path, required=True)
            item.add_argument("--local-provider-archive", type=Path, required=True)
        if mode == "run":
            item.add_argument("--directory", type=Path, required=True)
            item.add_argument("command", choices=("init", "validate", "plan", "apply"))
            item.add_argument("arguments", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    pins = profile()
    with shared_snapshot(args.shared_source, pins) as snapshot:
        if args.mode == "build":
            build(args, snapshot, pins)
            return
        with provider_snapshot(args.provider_dir, pins) as provider:
            args.provider_dir = provider
            if args.mode == "run":
                invoke(snapshot, "run_secure_profile.py", runtime_arguments(
                    args, args.directory.resolve(strict=True), args.command, args.arguments
                ))
            elif args.mode == "check":
                # Owned test helper, not arbitrary code from the supplied directory.
                shutil.copyfile(ROOT / "scripts/check_worker_plans.py",
                                snapshot / "scripts/check_epg_worker_plans.py")
                qualify(args, snapshot)
            elif args.mode == "tls":
                environment = {key: value for key, value in os.environ.items()
                               if key in ("PATH", "SYSTEMROOT")}
                environment.update(
                    TLS_TEST_PROFILE_ROOT=str(snapshot),
                    TLS_TEST_LOCAL_ARCHIVE=str(args.local_provider_archive.resolve(strict=True)),
                    TLS_TEST_TERRAFORM=str(args.tofu.resolve(strict=True)),
                    TLS_TEST_PROVIDER_DIR=str(args.provider_dir.resolve(strict=True)),
                    TLS_TEST_PLATFORM=PLATFORM,
                )
                invoke(snapshot, "test_tls_profile.py", [], env=environment)


if __name__ == "__main__":
    main()
