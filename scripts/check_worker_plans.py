"""Exercise real provider plans offline; never create resources or contact Cloudflare."""
import base64
import contextlib
import http.server
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

VARIABLES = {
    "account_id": "0" * 32,
    "tunnel_id": "00000000-0000-0000-0000-000000000001",
    "workers_namespace": "fixture",
    "worker_name": "voice",
}


@contextlib.contextmanager
def reject_api_requests():
    """Trap accidental API calls on loopback, including mutations, and fail closed."""
    requests = []

    class Reject(http.server.BaseHTTPRequestHandler):
        def handle(self):
            requests.append(True)
            super().handle()

        def log_message(self, *_args):
            pass

    with http.server.ThreadingHTTPServer(("127.0.0.1", 0), Reject) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield f"http://127.0.0.1:{server.server_port}/"
        finally:
            server.shutdown()
            thread.join()
    if requests:
        raise AssertionError("An offline plan attempted a Cloudflare API connection")


def run(tofu, workspace, environment, arguments, *, input_text=None, expected=0):
    result = subprocess.run(
        [str(tofu), *arguments], cwd=workspace, env=environment, text=True,
        input=input_text, capture_output=True, timeout=120, umask=0o077,
    )
    if result.returncode != expected:
        raise AssertionError(f"OpenTofu {arguments[0]} failed: {result.stdout}\n{result.stderr}")
    return result


def assert_plan(plan, workspace, *, published, channels):
    changes = plan["resource_changes"]
    assert len(changes) == 4 and all(item["change"]["actions"] == ["create"] for item in changes)
    assert all(item["change"]["after"]["account_id"] == VARIABLES["account_id"] for item in changes)
    resources = {item["type"]: item for item in changes}
    worker = resources["cloudflare_worker"]["change"]["after"]
    assert worker["name"] == VARIABLES["worker_name"]
    assert worker["subdomain"]["enabled"] is published
    assert worker["subdomain"]["previews_enabled"] is False
    assert worker["logpush"] is False and worker["tail_consumers"] == []
    assert worker["observability"]["enabled"] is False
    assert worker["observability"]["logs"]["enabled"] is False
    assert worker["observability"]["traces"]["enabled"] is False
    service = resources["cloudflare_connectivity_directory_service"]["change"]["after"]
    google = resources["cloudflare_worker"]["name"] == "google"
    assert service["host"] == {"ipv4": "127.0.0.1", "network": {"tunnel_id": VARIABLES["tunnel_id"]},
                               "hostname": None, "ipv6": None, "resolver_network": None}
    assert service["http_port"] == (8123 if google else 8080) and service["type"] == "http"
    version = resources["cloudflare_worker_version"]["change"]["after"]
    assert version["main_module"] == "relay.mjs" and len(version["modules"]) == 1
    module = version["modules"][0]
    assert module["name"] == "relay.mjs" and module["content_type"] == "application/javascript+module"
    assert base64.b64decode(module["content_base64"], validate=True) == (workspace.parent / "relay.mjs").read_bytes()
    bindings = version["bindings"]
    assert len(bindings) == (3 if google else 2)
    assert bindings[0]["name"] == "BACKEND" and bindings[0]["type"] == "vpc_service"
    assert bindings[1]["name"] == "PUBLIC_HOST" and bindings[1]["text"] == "voice.fixture.workers.dev"
    if google:
        assert bindings[2]["name"] == "CHANNELS_ENABLED" and bindings[2]["text"] == str(channels).lower()
    deployment = resources["cloudflare_workers_deployment"]["change"]["after"]
    assert deployment["strategy"] == "percentage" and len(deployment["versions"]) == 1
    assert deployment["versions"][0]["percentage"] == 100
    # IDs remain unknown until apply. Check the engine's actual dependency graph,
    # rather than replacing the provider's computed IDs with made-up mock values.
    config = {item["type"]: item for item in plan["configuration"]["root_module"]["resources"]}
    for source, attribute, target, field in (
        ("cloudflare_worker_version", "worker_id", "cloudflare_worker", "id"),
        ("cloudflare_worker_version", "bindings", "cloudflare_connectivity_directory_service", "service_id"),
        ("cloudflare_workers_deployment", "versions", "cloudflare_worker_version", "id"),
        ("cloudflare_workers_deployment", "script_name", "cloudflare_worker", "name"),
    ):
        assert config[target]["address"] + "." + field in config[source]["expressions"][attribute]["references"]
    worker_check = [check for check in plan["checks"] if check["address"].get("type") == "cloudflare_worker"]
    assert len(worker_check) == 1 and worker_check[0]["status"] == "unknown"


def namespace_expression(workspace):
    source = (workspace / "main.tf").read_text()
    matches = re.findall(r"postcondition\s*\{\s*condition\s*=\s*(.*?)\n\s*error_message\s*=", source, re.S)
    if len(matches) != 1 or matches[0].count("self.subdomain.url") != 2:
        raise AssertionError("Expected exactly one null-checked namespace postcondition")
    return " ".join(matches[0].replace("self.subdomain.url", "fixture_url").split())


def qualify_plans(tofu, workspace, environment):
    with reject_api_requests() as endpoint:
        environment = {**environment, "CLOUDFLARE_BASE_URL": endpoint, "CLOUDFLARE_API_TOKEN": "0" * 40}
        run(tofu, workspace, environment, ["init", "-backend=false", "-input=false", "-no-color"])
        varfile = workspace / "synthetic.tfvars.json"
        google = "google-home" in str(workspace)
        cases = [(False, False), (True, False)] + ([(False, True)] if google else [])
        for published, channels in cases:
            values = {**VARIABLES, "publish": published}
            if google:
                values["channels_enabled"] = channels
            varfile.write_text(json.dumps(values))
            run(tofu, workspace, environment, ["plan", "-refresh=false", "-input=false", "-no-color",
                                               "-var-file=" + str(varfile), "-out=offline.plan"])
            result = run(tofu, workspace, environment, ["show", "-json", "offline.plan"])
            assert_plan(json.loads(result.stdout), workspace, published=published, channels=channels)
        varfile.write_text(json.dumps(VARIABLES))
        urls = ["voice.fixture.workers.dev", "https://voice.fixture.workers.dev",
                "voice.wrong.workers.dev", "http://voice.fixture.workers.dev", None, ""]
        condition = namespace_expression(workspace).replace("local.worker_hostname", json.dumps("voice.fixture.workers.dev"))
        expression = "jsonencode([for fixture_url in " + json.dumps(urls) + " : " + condition + "])"
        with tempfile.TemporaryDirectory(prefix="worker-condition-") as name:
            result = run(tofu, Path(name), environment, ["console"], input_text=expression + "\n")
        assert json.loads(json.loads(result.stdout)) == [True, True, False, False, False, False]
        varfile.write_text(json.dumps({**VARIABLES, "account_id": "invalid"}))
        result = run(tofu, workspace, environment, ["plan", "-refresh=false", "-input=false", "-no-color",
                                                   "-var-file=" + str(varfile)], expected=1)
        assert "Invalid value for variable" in result.stderr and "account_id" in result.stderr
    print(f"PASS {workspace.parent.name}: {len(cases)} real offline plans, 6 namespace cases, invalid account rejected, zero API connections")


def main():
    from check_secure_provider import install_local_provider, verified_derivative

    root = Path(__file__).resolve().parents[1]
    tofu, provider, local_archive, workspace = map(Path, sys.argv[1:])
    manifest, executable = verified_derivative(root, provider, "linux_amd64")
    with tempfile.TemporaryDirectory(prefix="worker-offline-plan-") as name:
        temporary = Path(name)
        mirror = temporary / "mirror"
        target = mirror / manifest["provider_address"] / "5.24.0/linux_amd64"
        target.mkdir(parents=True)
        (target / executable.name).write_bytes(executable.read_bytes())
        (target / executable.name).chmod(0o700)
        install_local_provider(root, local_archive, mirror, "linux_amd64")
        configuration = temporary / "mirror.tfrc"
        configuration.write_text("provider_installation {\n filesystem_mirror {\n path = " + json.dumps(str(mirror)) + "\n }\n}\n")
        environment = {"PATH": os.environ.get("PATH", ""), "HOME": str(temporary),
                       "TF_CLI_CONFIG_FILE": str(configuration), "TF_INPUT": "0",
                       "CHECKPOINT_DISABLE": "1", "GODEBUG": "fips140=on"}
        qualify_plans(tofu, workspace, environment)


if __name__ == "__main__":
    main()
