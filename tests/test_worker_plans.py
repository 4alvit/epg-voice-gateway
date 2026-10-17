"""Fail-closed boundaries of the real offline Worker-plan qualification."""
import importlib.util
import socket
from pathlib import Path
from urllib.parse import urlsplit

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("worker_plans", ROOT / "scripts/check_worker_plans.py")
plans = importlib.util.module_from_spec(spec)
spec.loader.exec_module(plans)


def test_offline_api_trap_rejects_actual_connection():
    with pytest.raises(AssertionError, match="API connection"):
        with plans.reject_api_requests() as endpoint:
            url = urlsplit(endpoint)
            with socket.create_connection((url.hostname, url.port), timeout=2) as client:
                client.sendall(b"GET / HTTP/1.0\r\nHost: localhost\r\n\r\n")
                assert b"501" in client.recv(4096)


def test_offline_api_trap_accepts_no_connection():
    with plans.reject_api_requests():
        pass


@pytest.mark.parametrize("source", ["", "postcondition { condition = true\nerror_message = \"none\" }"])
def test_missing_or_replaced_namespace_guard_fails_closed(tmp_path, source):
    (tmp_path / "main.tf").write_text(source)
    with pytest.raises(AssertionError, match="namespace postcondition"):
        plans.namespace_expression(tmp_path)
