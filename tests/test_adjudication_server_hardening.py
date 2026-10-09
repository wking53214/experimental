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

    def test_shared_token_cannot_decide_once_named_operators_exist(self, port, monkeypatch):
        monkeypatch.setattr(srv, "ADJUDICATION_TOKEN", "t0ken-shared")
        monkeypatch.setattr(srv, "OPERATOR_TOKENS", {"tok-alice": "alice"})
        pid = _loosen_proposal_authed(port, "id_shared", "tok-alice")
        code, body = call(port, "POST", f"/proposals/{pid}/decide",
                          {"decision": "approve_loosen", "operator_id": "alice"},
                          headers={"Authorization": "Bearer t0ken-shared"})
        assert code == 403 and "own credential" in body["error"]
        assert GOV.boundaries.get_boundary("id_shared").current_limit == 50
        code, _ = call(port, "POST", f"/proposals/{pid}/decide", {"decision": "approve_loosen"},
                       headers={"Authorization": "Bearer tok-alice"})
        assert code == 200

    def test_core_registry_follows_the_configured_tokens(self, port, monkeypatch):
        monkeypatch.setattr(srv, "OPERATOR_TOKENS", {"tok-carol": "carol"})
        pid = _loosen_proposal_authed(port, "id_core", "tok-carol")
        call(port, "POST", f"/proposals/{pid}/decide", {"decision": "approve_loosen"},
             headers={"Authorization": "Bearer tok-carol"})
        assert GOV.authority.operators is not None
        assert GOV.authority.latest_decision(pid).identity_verified is True
        monkeypatch.setattr(srv, "OPERATOR_TOKENS", {})
        pid2 = _loosen_proposal(port, "id_core2")
        call(port, "POST", f"/proposals/{pid2}/decide", {"decision": "approve_loosen", "operator_id": "dan"})
        assert GOV.authority.operators is None

    def test_audit_endpoint_reports_an_intact_chain_and_needs_auth(self, port, monkeypatch):
        code, body = call(port, "GET", "/audit")
        assert code == 200 and body["chain_intact"] is True and body["length"] == len(GOV.audit.entries)
        monkeypatch.setattr(srv, "ADJUDICATION_TOKEN", "t0ken")
        assert call(port, "GET", "/audit")[0] == 401
        assert call(port, "GET", "/audit", headers={"Authorization": "Bearer t0ken"})[0] == 200

    def test_audit_endpoint_flags_tampering(self, port):
        GOV.audit.append("test_marker", {"note": "ensures the log is not empty"})
        saved = GOV.audit.entries[0]["payload"]
        GOV.audit.entries[0]["payload"] = {"forged": True}
        try:
            code, body = call(port, "GET", "/audit")
            assert code == 200 and body["chain_intact"] is False and "entry 0" in body["problem"]
        finally:
            GOV.audit.entries[0]["payload"] = saved

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


class TestSignalEndpoints:
    """Bring-your-own-monitoring over HTTP (docs/INTEGRATION.md)."""

    SRC = {"Authorization": "Bearer src-monitor-1"}
    OP = {"Authorization": "Bearer tok-alice"}

    def _mk(self, port, bid, limit=100):
        """Creating a boundary needs an operator credential once any token is configured."""
        code, _ = call(port, "POST", "/boundaries", {"boundary_id": bid, "initial_limit": limit}, headers=self.OP)
        assert code in (200, 201), code

    def test_a_source_can_tighten_and_read_status(self, port, monkeypatch):
        monkeypatch.setattr(srv, "SOURCE_TOKENS", {"src-monitor-1": "monitor"})
        monkeypatch.setattr(srv, "OPERATOR_TOKENS", {"tok-alice": "alice"})
        self._mk(port, "sig_a")
        code, body = call(port, "POST", "/signals/tighten",
                          {"boundary_id": "sig_a", "factor": 0.9, "reason": "errors up", "evidence": {"e": 0.3},
                           "idempotency_key": "k1"}, headers=self.SRC)
        assert code == 200 and body["status"] == "applied" and body["source"] == "monitor"
        assert body["identity_verified"] is True and body["limit_after"] == pytest.approx(90.0)
        code, st = call(port, "GET", "/boundaries/sig_a", headers=self.SRC)
        assert code == 200 and st["limit"] == pytest.approx(90.0) and st["remaining_before_breaker"] == 2

    def test_a_retry_with_the_same_key_is_a_duplicate(self, port, monkeypatch):
        monkeypatch.setattr(srv, "SOURCE_TOKENS", {"src-monitor-1": "monitor"})
        monkeypatch.setattr(srv, "OPERATOR_TOKENS", {"tok-alice": "alice"})
        self._mk(port, "sig_b")
        a = call(port, "POST", "/signals/tighten", {"boundary_id": "sig_b", "factor": 0.9, "idempotency_key": "r1"}, headers=self.SRC)[1]
        b = call(port, "POST", "/signals/tighten", {"boundary_id": "sig_b", "factor": 0.9, "idempotency_key": "r1"}, headers=self.SRC)[1]
        assert a["status"] == "applied" and b["duplicate"] is True
        assert GOV.boundaries.get_boundary("sig_b").current_limit == pytest.approx(90.0)

    def test_a_source_credential_is_boxed_in(self, port, monkeypatch):
        monkeypatch.setattr(srv, "SOURCE_TOKENS", {"src-monitor-1": "monitor"})
        monkeypatch.setattr(srv, "OPERATOR_TOKENS", {"tok-alice": "alice"})
        self._mk(port, "sig_c")
        loosen = call(port, "POST", "/signals/loosen-request", {"boundary_id": "sig_c", "new_limit": 150}, headers=self.SRC)[1]
        pid = loosen["proposal_id"]
        for method, path, data in (
                ("POST", f"/proposals/{pid}/decide", {"decision": "approve_loosen"}),
                ("POST", "/boundaries", {"boundary_id": "evil", "initial_limit": 5}),
                ("POST", "/proposals/loosen", {"boundary_id": "sig_c", "proposed_value": 500}),
                ("GET", "/audit", None), ("GET", "/proposals/pending", None)):
            code, body = call(port, method, path, data, headers=self.SRC)
            assert code == 403, (path, body)
        assert GOV.boundaries.get_boundary("sig_c").current_limit == 100

    def test_a_source_cannot_claim_another_name(self, port, monkeypatch):
        monkeypatch.setattr(srv, "SOURCE_TOKENS", {"src-monitor-1": "monitor"})
        monkeypatch.setattr(srv, "OPERATOR_TOKENS", {"tok-alice": "alice"})
        self._mk(port, "sig_d")
        code, _ = call(port, "POST", "/signals/tighten", {"boundary_id": "sig_d", "factor": 0.9, "source": "someone-else"}, headers=self.SRC)
        assert code == 403

    def test_the_shared_token_cannot_masquerade_as_a_source_once_sources_exist(self, port, monkeypatch):
        monkeypatch.setattr(srv, "SOURCE_TOKENS", {"src-monitor-1": "monitor"})
        monkeypatch.setattr(srv, "OPERATOR_TOKENS", {"tok-alice": "alice"})
        monkeypatch.setattr(srv, "ADJUDICATION_TOKEN", "t0ken")
        self._mk_auth = {"Authorization": "Bearer t0ken"}
        call(port, "POST", "/boundaries", {"boundary_id": "sig_e", "initial_limit": 100}, headers=self._mk_auth)
        code, body = call(port, "POST", "/signals/tighten", {"boundary_id": "sig_e", "factor": 0.9, "source": "monitor"},
                          headers=self._mk_auth)
        assert code == 403, body
        assert GOV.boundaries.get_boundary("sig_e").current_limit == 100

    def test_bad_input_is_400_and_unknown_boundary_404_and_no_token_401(self, port, monkeypatch):
        monkeypatch.setattr(srv, "SOURCE_TOKENS", {"src-monitor-1": "monitor"})
        monkeypatch.setattr(srv, "OPERATOR_TOKENS", {"tok-alice": "alice"})
        self._mk(port, "sig_f")
        for bad in ({"factor": 2}, {"factor": -1}, {"new_limit": "x"}, {}, {"factor": 0.9, "new_limit": 50}):
            code, _ = call(port, "POST", "/signals/tighten", {"boundary_id": "sig_f", **bad}, headers=self.SRC)
            assert code == 400, bad
        assert call(port, "POST", "/signals/tighten", {"boundary_id": "nope", "factor": 0.9}, headers=self.SRC)[0] == 404
        assert call(port, "POST", "/signals/tighten", {"boundary_id": "sig_f", "factor": 0.9})[0] == 401
        assert call(port, "POST", "/signals/tighten", {"boundary_id": "sig_f", "factor": 0.9},
                    headers={"Authorization": "Bearer wrong"})[0] == 401
        assert GOV.boundaries.get_boundary("sig_f").current_limit == 100

    def test_a_loosening_request_waits_for_an_operator_who_then_decides(self, port, monkeypatch):
        monkeypatch.setattr(srv, "SOURCE_TOKENS", {"src-monitor-1": "monitor"})
        monkeypatch.setattr(srv, "OPERATOR_TOKENS", {"tok-alice": "alice"})
        self._mk(port, "sig_g")
        call(port, "POST", "/signals/tighten", {"boundary_id": "sig_g", "factor": 0.8}, headers=self.SRC)
        r = call(port, "POST", "/signals/loosen-request", {"boundary_id": "sig_g", "new_limit": 100, "reason": "recovered"}, headers=self.SRC)[1]
        assert r["status"] == "pending_review" and GOV.boundaries.get_boundary("sig_g").current_limit == pytest.approx(80.0)
        code, body = call(port, "POST", f"/proposals/{r['proposal_id']}/decide", {"decision": "approve_loosen"}, headers=self.OP)
        assert code == 200 and body["decided_by"] == "alice"
        assert GOV.boundaries.get_boundary("sig_g").current_limit == 100


def test_the_example_monitor_works_end_to_end_against_a_live_server(port, monkeypatch):
    import random
    from examples.external_monitor import call as ex_call, run as ex_run
    monkeypatch.setattr(srv, "SOURCE_TOKENS", {"src-example-1": "example-monitor"})
    monkeypatch.setattr(srv, "OPERATOR_TOKENS", {"tok-alice": "alice"})
    call(port, "POST", "/boundaries", {"boundary_id": "ex_api", "initial_limit": 100}, headers={"Authorization": "Bearer tok-alice"})
    rng = random.Random(1)
    series = [rng.gauss(50, 5) for _ in range(300)] + [rng.gauss(95, 5) for _ in range(100)]
    base = f"http://127.0.0.1:{port}"
    replies = ex_run(base, "src-example-1", "ex_api", series)
    assert replies and all(code == 200 for code, _ in replies)
    assert replies[0][1]["status"] == "applied"
    status = ex_call(base, "src-example-1", "GET", "/boundaries/ex_api")[1]
    assert 20.0 <= status["limit"] < 100.0 and status["automatic_tightenings_since_acknowledgement"] <= 3
    # the monitor can ask as often as it likes: the breaker, not the monitor, decides how far this goes
    assert GOV.boundaries.get_boundary("ex_api").current_limit >= 72.9 - 1e-9
    assert GOV.verify_governance_integrity()[0]
