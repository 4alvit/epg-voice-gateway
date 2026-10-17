"""Test-only entry point staged beside the pinned common provider helpers."""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path


def main():
    # Imported only after worker_profile stages the exact common source closure.
    from check_secure_provider import install_local_provider, verified_derivative

    root = Path(__file__).resolve().parents[1]
    tofu, provider, local_archive, workspace = map(Path, sys.argv[1:])
    manifest, executable = verified_derivative(root, provider, "linux_amd64")
    with tempfile.TemporaryDirectory(prefix="worker-mock-plan-") as name:
        temporary = Path(name)
        mirror = temporary / "mirror"
        target = mirror / manifest["provider_address"] / "5.24.0/linux_amd64"
        target.mkdir(parents=True)
        (target / executable.name).write_bytes(executable.read_bytes())
        (target / executable.name).chmod(0o700)
        install_local_provider(root, local_archive, mirror, "linux_amd64")
        configuration = temporary / "mirror.tfrc"
        configuration.write_text(
            "provider_installation {\n filesystem_mirror {\n path = "
            + json.dumps(str(mirror)) + "\n }\n}\n"
        )
        environment = {
            "PATH": os.environ.get("PATH", ""), "HOME": str(temporary),
            "TF_CLI_CONFIG_FILE": str(configuration), "TF_INPUT": "0",
            "CHECKPOINT_DISABLE": "1", "GODEBUG": "fips140=on",
        }
        for command in (["init", "-backend=false", "-input=false", "-no-color"],
                        ["test", "-no-color"]):
            subprocess.run([str(tofu), *command], cwd=workspace, env=environment,
                           check=True, umask=0o077)


if __name__ == "__main__":
    main()
