"""Adjudication server: input validation, authentication, identity, and error handling."""
import json
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import HTTPServer
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import scripts.adjudication_server as srv
from scripts.adjudication_server import GOV, Handler


@pytest.fixture(scope="module")
def port():
    httpd = HTTPServer(("127.0.0.1", 0), Handler)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    time.sleep(0.15)
    yield httpd.server_address[1]
    httpd.shutdown()


def call(port, method, path, data=None, headers=None, raw=None):
    body = raw if raw is not None else (None if data is None else json.dumps(data).encode())
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=body, method=method)
    if body is not None:
        req.add_header("Content-Type", "application/json")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode())


def _loosen_proposal(port, bid, limit=50, proposed=80):
    call(port, "POST", "/boundaries", {"boundary_id": bid, "initial_limit": limit})
    code, body = call(port, "POST", "/proposals/loosen", {"boundary_id": bid, "proposed_value": proposed})
    assert code == 200, body
    return body["proposal_id"]


class TestInputValidation:
    def test_malformed_json_is_a_400_not_a_dropped_connection(self, port):
        code, body = call(port, "POST", "/boundaries", raw=b"{not json")
        assert code == 400 and "JSON" in body["error"]

    def test_non_object_json_is_a_400(self, port):
        code, _ = call(port, "POST", "/boundaries", raw=b"[1, 2, 3]")
        assert code == 400

    def test_missing_required_field_is_a_400(self, port):
        code, body = call(port, "POST", "/proposals/loosen", {"proposed_value": 5})
        assert code == 400 and "boundary_id" in body["error"]

    @pytest.mark.parametrize("bad", ["nan", "inf", "-inf", 0, -5, True, None, [1], "abc", 1e999])
    def test_non_finite_or_non_positive_limits_are_rejected(self, port, bad):
        call(port, "POST", "/boundaries", {"boundary_id": "v_ok", "initial_limit": 10})
        code, _ = call(port, "POST", "/proposals/loosen", {"boundary_id": "v_ok", "proposed_value": bad})
        assert code == 400

    def test_bad_initial_limit_is_rejected(self, port):
        code, _ = call(port, "POST", "/boundaries", {"boundary_id": "v_bad", "initial_limit": "nan"})
        assert code == 400

    def test_unknown_boundary_is_a_404(self, port):
        code, _ = call(port, "POST", "/proposals/loosen", {"boundary_id": "nope", "proposed_value": 5})
        assert code == 404

    def test_duplicate_boundary_is_a_409(self, port):
        call(port, "POST", "/boundaries", {"boundary_id": "dup", "initial_limit": 10})
        code, _ = call(port, "POST", "/boundaries", {"boundary_id": "dup", "initial_limit": 99})
        assert code == 409

    def test_oversized_body_is_a_413(self, port):
        code, _ = call(port, "POST", "/boundaries", raw=b'{"x": "' + b"a" * 1_100_000 + b'"}')
        assert code == 413

    def test_unexpected_server_error_is_a_json_500_and_the_server_survives(self, port, monkeypatch):
        def boom():
            raise RuntimeError("boom")
        monkeypatch.setattr(GOV, "get_status", boom)
        code, body = call(port, "GET", "/health")
        assert code == 500 and body == {"error": "internal error"}
        monkeypatch.undo()
        assert call(port, "GET", "/health")[0] == 200


class TestAuthentication:
    def test_every_endpoint_except_minimal_health_needs_the_token(self, port, monkeypatch):
        monkeypatch.setattr(srv, "ADJUDICATION_TOKEN", "t0ken")
        assert call(port, "GET", "/proposals/pending")[0] == 401
        assert call(port, "GET", "/proposals/x/evidence")[0] == 401
        code, health = call(port, "GET", "/health")
        assert code == 200 and "proposals" not in health  # no counters without a token
        code, health = call(port, "GET", "/health", headers={"Authorization": "Bearer t0ken"})
        assert code == 200 and "proposals" in health

    def test_wrong_and_missing_tokens_are_rejected(self, port, monkeypatch):
        monkeypatch.setattr(srv, "ADJUDICATION_TOKEN", "t0ken")
        assert call(port, "GET", "/proposals/pending", headers={"Authorization": "Bearer nope"})[0] == 401
        assert call(port, "POST", "/boundaries", {"boundary_id": "a"}, headers={"X-Adjudication-Token": ""})[0] == 401

    def test_x_header_token_works(self, port, monkeypatch):
        monkeypatch.setattr(srv, "ADJUDICATION_TOKEN", "t0ken")
        assert call(port, "GET", "/proposals/pending", headers={"X-Adjudication-Token": "t0ken"})[0] == 200

    def test_token_comparison_is_constant_time_helper(self):
        assert srv._eq("abc", "abc") and not srv._eq("abc", "abd") and not srv._eq("abc", "abcd")


class TestOperatorIdentity:
    def test_decision_needs_a_real_operator_id_with_a_shared_token(self, port):
        pid = _loosen_proposal(port, "id_a")
        assert call(port, "POST", f"/proposals/{pid}/decide", {"decision": "approve_loosen"})[0] == 400
        assert call(port, "POST", f"/proposals/{pid}/decide",
                    {"decision": "approve_loosen", "operator_id": "anonymous"})[0] == 400
        code, body = call(port, "POST", f"/proposals/{pid}/decide",
                          {"decision": "approve_loosen", "operator_id": "alice"})
        assert code == 200 and body["status"] == "applied" and body["identity_verified"] is False

    def test_per_operator_tokens_bind_identity_to_the_credential(self, port, monkeypatch):
        monkeypatch.setattr(srv, "OPERATOR_TOKENS", {"tok-alice": "alice", "tok-bob": "bob"})
        pid = _loosen_proposal_authed(port, "id_b", "tok-alice")
        # alice's token but claiming to be bob
        code, _ = call(port, "POST", f"/proposals/{pid}/decide",
                       {"decision": "approve_loosen", "operator_id": "bob"},
                       headers={"Authorization": "Bearer tok-alice"})
        assert code == 403
        code, body = call(port, "POST", f"/proposals/{pid}/decide", {"decision": "approve_loosen"},
                          headers={"Authorization": "Bearer tok-alice"})
        assert code == 200 and body["decided_by"] == "alice" and body["identity_verified"] is True
        last = GOV.authority.latest_decision(pid)
        assert last.decided_by == "alice"

    def test_unknown_proposal_and_bad_decision(self, port):
        assert call(port, "POST", "/proposals/nope/decide",
                    {"decision": "approve", "operator_id": "alice"})[0] == 404
        pid = _loosen_proposal(port, "id_c")
        assert call(port, "POST", f"/proposals/{pid}/decide",
                    {"decision": "maybe", "operator_id": "alice"})[0] == 400

    def test_cannot_decide_twice(self, port):
        pid = _loosen_proposal(port, "id_d")
        d = {"decision": "approve_loosen", "operator_id": "alice"}
        assert call(port, "POST", f"/proposals/{pid}/decide", d)[0] == 200
        assert call(port, "POST", f"/proposals/{pid}/decide", d)[0] == 400


def _loosen_proposal_authed(port, bid, token):
    h = {"Authorization": f"Bearer {token}"}
    call(port, "POST", "/boundaries", {"boundary_id": bid, "initial_limit": 50}, headers=h)
    code, body = call(port, "POST", "/proposals/loosen", {"boundary_id": bid, "proposed_value": 80}, headers=h)
    assert code == 200, body
    return body["proposal_id"]


def test_http_approved_loosening_passes_the_integrity_audit(port):
    pid = _loosen_proposal(port, "audit_ok")
    assert call(port, "POST", f"/proposals/{pid}/decide",
                {"decision": "approve_loosen", "operator_id": "alice"})[0] == 200
    ok, _ = GOV.verify_governance_integrity()
    assert ok
