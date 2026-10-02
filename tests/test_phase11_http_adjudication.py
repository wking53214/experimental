"""Phase 11D: HTTP adjudication façade tests (+ M3 auth)."""
import json
import threading
import time
import urllib.error
import urllib.request
from http.server import HTTPServer

import pytest
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.adjudication_server import Handler, GOV
import scripts.adjudication_server as srv


@pytest.fixture(scope="module")
def server_port():
    httpd = HTTPServer(("127.0.0.1", 0), Handler)
    port = httpd.server_address[1]
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    time.sleep(0.15)
    yield port
    httpd.shutdown()


def _req(port, method, path, data=None, headers=None):
    url = f"http://127.0.0.1:{port}{path}"
    body = None if data is None else json.dumps(data).encode()
    req = urllib.request.Request(url, data=body, method=method)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    if headers:
        for k, v in headers.items():
            req.add_header(k, v)
    with urllib.request.urlopen(req, timeout=5) as resp:
        return json.loads(resp.read().decode())


def test_health(server_port):
    j = _req(server_port, "GET", "/health")
    assert j["status"] == "ok"


def test_loosen_flow(server_port):
    _req(server_port, "POST", "/boundaries", {"boundary_id": "http_cpu", "initial_limit": 50})
    created = _req(server_port, "POST", "/proposals/loosen", {
        "boundary_id": "http_cpu", "proposed_value": 80, "reason": "demo migration",
    })
    assert "proposal_id" in created
    pid = created["proposal_id"]
    pending = _req(server_port, "GET", "/proposals/pending")
    assert any(p["proposal_id"] == pid for p in pending["proposals"])
    pack = _req(server_port, "GET", f"/proposals/{pid}/evidence")
    assert pack["proposal"]["direction"] == "loosen"
    decided = _req(server_port, "POST", f"/proposals/{pid}/decide", {
        "decision": "approve_loosen", "operator_id": "tester", "rationale": "ok",
    })
    assert decided["status"] in ("applied", "approved")


def test_post_unauthorized_when_token_configured(monkeypatch, server_port):
    monkeypatch.setattr(srv, "ADJUDICATION_TOKEN", "secret-test-token")
    url = f"http://127.0.0.1:{server_port}/boundaries"
    req = urllib.request.Request(
        url, data=json.dumps({"boundary_id": "x", "initial_limit": 1}).encode(),
        method="POST", headers={"Content-Type": "application/json"},
    )
    try:
        urllib.request.urlopen(req, timeout=5)
        assert False, "expected 401"
    except urllib.error.HTTPError as e:
        assert e.code == 401


def test_post_authorized_with_bearer(monkeypatch, server_port):
    monkeypatch.setattr(srv, "ADJUDICATION_TOKEN", "secret-test-token")
    url = f"http://127.0.0.1:{server_port}/boundaries"
    req = urllib.request.Request(
        url, data=json.dumps({"boundary_id": "tok_cpu", "initial_limit": 10}).encode(),
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": "Bearer secret-test-token",
        },
    )
    with urllib.request.urlopen(req, timeout=5) as resp:
        body = json.loads(resp.read().decode())
    assert body["boundary_id"] == "tok_cpu"
