"""Limit records as things a CNS verdict can be bound to.

These skip where ``cns`` is not installed. CI installs requirements-cns.txt and sets EXPERIMENTAL_REQUIRE_CNS=1, so
there they cannot skip. Binding only: binding records what a limit was and grants nothing, and one test here shows a
passing verdict bound to a loosening does not move a limit.
"""
import copy
import dataclasses
import hashlib
import math
import os

import pytest

if os.environ.get("EXPERIMENTAL_REQUIRE_CNS") == "1":  # CI: a missing cns is a failure, not a skip
    import cns.gate as gate
else:
    gate = pytest.importorskip("cns.gate")

from src.governance.audit_log import AuditLog  # noqa: E402
from src.governance.authority import AuthorizationDecision, AuthorizationResult  # noqa: E402
from src.governance.boundary import BoundaryStatus, BoundaryVersion  # noqa: E402
from src.governance.cns_connector import (  # noqa: E402
    AUTHORIZATION_SUBJECT, LIMIT_CHANGE_SUBJECT, LIMIT_STATE_SUBJECT, authorization_content, authorization_digest,
    binds, cns_available, limit_change_content, limit_change_digest, limit_state_content, limit_state_digest)
from src.governance.governor import Governor  # noqa: E402
from src.governance.operators import OperatorRegistry  # noqa: E402
from src.governance.proposal import AdaptationDirection  # noqa: E402

CANARY = "CANARY-9c4d77e1-private-monitor-text"
MON, OPS = "monitor-secret-1", "alice-secret-1"


def sha(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def state():
    return BoundaryVersion(boundary_id="api", version=2, resource_or_action="requests", current_limit=80.0,
                           status=BoundaryStatus.ACTIVE, created_at=1000.0, parent_version=1)


def change():
    """The shape propose_tightening returns, plus the optional fields."""
    return {"boundary_id": "api", "limit_before": 100.0, "limit_after": 80.0, "source": "monitor", "status": "applied",
            "identity_verified": True, "version": 2, "proposal_id": "prop-1", "reason": f"cpu spike {CANARY}"}


def decision():
    return AuthorizationDecision(
        proposal_id="prop-1", direction=AdaptationDirection.TIGHTEN, result=AuthorizationResult.AUTO_APPROVED,
        reason=f"TIGHTEN auto-approved by authority model {CANARY}", timestamp=2000.0, decided_by="system",
        effective_direction=AdaptationDirection.TIGHTEN, grant_id=None, identity_verified=False)


def issued_against(subject, digest):
    return gate.GateResult(gate="test.gate", position=gate.GatePosition.OMEGA, outcome=gate.GateOutcome.PASS,
                           reason="issued for the test", subject=subject, subject_digest=digest)


def conforms(value):
    """Only what cns.gate accepts: str, int, float, bool, None, list, and dicts with str keys."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return True
    if isinstance(value, list):
        return all(conforms(v) for v in value)
    if isinstance(value, dict):
        return all(isinstance(k, str) and conforms(v) for k, v in value.items())
    return False


def make_governor(tmp_path, durable=False, name="g"):
    sources = OperatorRegistry(iterations=1000)
    sources.register("monitor", MON)
    ops = OperatorRegistry(iterations=1000)
    ops.register("alice", OPS)
    extra = {"audit_path": str(tmp_path / "audit.jsonl")} if durable else {}
    return Governor(store_path=str(tmp_path / f"ev-{name}"), signal_sources=sources, operators=ops, **extra)


def test_cns_is_reported_available():
    assert cns_available() is True


# ------------------------------------------------------------ determinism and shape

def test_the_same_record_gives_the_same_content_and_digest():
    assert limit_state_content(state()) == limit_state_content(state())
    assert limit_change_content(change()) == limit_change_content(copy.deepcopy(change()))
    assert authorization_content(decision()) == authorization_content(decision())
    assert limit_state_digest(state()) == limit_state_digest(state())
    assert limit_change_digest(change()) == limit_change_digest(change())
    assert authorization_digest(decision()) == authorization_digest(decision())


def test_the_digest_is_cns_subject_digest_over_the_content():
    assert limit_state_digest(state()) == gate.subject_digest(limit_state_content(state()))
    assert limit_change_digest(change()) == gate.subject_digest(limit_change_content(change()))
    assert authorization_digest(decision()) == gate.subject_digest(authorization_content(decision()))


def test_content_holds_only_types_cns_accepts_and_cns_can_digest_it():
    bare = {k: change()[k] for k in ("boundary_id", "limit_before", "limit_after", "source", "status")}
    for content in (limit_state_content(state()), limit_state_content(dataclasses.replace(state(), parent_version=None)),
                    limit_change_content(change()), limit_change_content(bare), authorization_content(decision()),
                    authorization_content(dataclasses.replace(decision(), effective_direction=None))):
        assert conforms(content), content
        assert len(gate.subject_digest(content)) == 64


def test_the_three_kinds_of_record_do_not_share_a_digest():
    assert len({limit_state_digest(state()), limit_change_digest(change()), authorization_digest(decision())}) == 3


def test_a_limit_is_bound_as_it_was_set():
    assert limit_state_content(state())["limit"] == 80.0
    assert limit_state_content(dataclasses.replace(state(), current_limit=80))["limit"] == 80
    assert limit_state_content(dataclasses.replace(state(), current_limit=None))["limit"] is None


# ------------------------------------------------------------ every bound field is bound

STATE_CHANGES = {
    "boundary_id": {"boundary_id": "db"},
    "version": {"version": 3},
    "parent_version": {"parent_version": 0},
    "parent_version removed": {"parent_version": None},
    "resource": {"resource_or_action": "connections"},
    "limit": {"current_limit": 79.0},
    "limit kind": {"current_limit": 80},
    "limit as text": {"current_limit": "80.0"},
    "status superseded": {"status": BoundaryStatus.SUPERSEDED},
    "status disabled": {"status": BoundaryStatus.DISABLED},
}


@pytest.mark.parametrize("name", list(STATE_CHANGES))
def test_changing_any_bound_limit_state_field_changes_the_digest(name):
    assert limit_state_digest(dataclasses.replace(state(), **STATE_CHANGES[name])) != limit_state_digest(state())


CHANGE_CHANGES = {
    "boundary_id": {"boundary_id": "db"},
    "limit_before": {"limit_before": 120.0},
    "limit_after": {"limit_after": 70.0},
    "source": {"source": "supervisor"},
    "status": {"status": "applied_clamped"},
    "identity_verified": {"identity_verified": False},
    "version": {"version": 3},
    "proposal_id": {"proposal_id": "prop-2"},
    "reason": {"reason": "a different reason"},
}


@pytest.mark.parametrize("name", list(CHANGE_CHANGES))
def test_changing_any_bound_limit_change_field_changes_the_digest(name):
    assert limit_change_digest({**change(), **CHANGE_CHANGES[name]}) != limit_change_digest(change())


@pytest.mark.parametrize("key", ["identity_verified", "version", "proposal_id", "reason"])
def test_an_optional_field_that_is_absent_differs_from_one_that_is_present(key):
    without = {k: v for k, v in change().items() if k != key}
    assert limit_change_digest(without) != limit_change_digest(change())
    assert limit_change_content(without)[key if key != "reason" else "reason_sha256"] is None


DECISION_CHANGES = {
    "proposal_id": {"proposal_id": "prop-2"},
    "direction": {"direction": AdaptationDirection.LOOSEN},
    "effective_direction": {"effective_direction": AdaptationDirection.LOOSEN},
    "effective_direction removed": {"effective_direction": None},
    "result": {"result": AuthorizationResult.REQUIRES_HUMAN_REVIEW},
    "decided_by": {"decided_by": "alice"},
    "identity_verified": {"identity_verified": True},
    "grant_id": {"grant_id": "grant-1"},
    "reason": {"reason": "a different reason"},
}


@pytest.mark.parametrize("name", list(DECISION_CHANGES))
def test_changing_any_bound_authorization_field_changes_the_digest(name):
    assert authorization_digest(dataclasses.replace(decision(), **DECISION_CHANGES[name])) != authorization_digest(decision())


def test_who_decided_is_what_the_record_says_it_was():
    system = authorization_content(decision())
    operator = authorization_content(dataclasses.replace(
        decision(), direction=AdaptationDirection.LOOSEN, effective_direction=AdaptationDirection.LOOSEN,
        result=AuthorizationResult.OPERATOR_APPROVED, decided_by="alice", identity_verified=True, grant_id="grant-1"))
    assert (system["decided_by"], system["result"], system["grant_id"]) == ("system", "auto_approved", None)
    assert (operator["decided_by"], operator["result"], operator["grant_id"]) == ("alice", "operator_approved", "grant-1")


# ------------------------------------------------------------ direction

@pytest.mark.parametrize("before, after, direction", [
    (100.0, 80.0, "tighten"), (100, 99, "tighten"), (80.0, 100.0, "loosen"), (80, 81, "loosen"),
    (80.0, 80.0, "unchanged"), (80, 80, "unchanged"), (80, 80.0, "unchanged"), (None, None, "unchanged"),
    ("a", "a", "unchanged"), ("a", "b", "loosen"), (80.0, None, "loosen"), (True, False, "loosen"),
    (10 ** 18, 10 ** 18 + 50, "loosen"), (10 ** 18 + 50, 10 ** 18, "tighten")])
def test_the_direction_is_the_one_the_two_values_imply(before, after, direction):
    record = {**change(), "limit_before": before, "limit_after": after}
    assert limit_change_content(record)["direction"] == direction


def test_a_request_to_loosen_that_changed_nothing_is_recorded_as_unchanged():
    record = {"boundary_id": "api", "limit_before": 80.0, "limit_after": 80.0, "source": "monitor",
              "status": "pending_review", "proposal_id": "prop-9"}
    assert limit_change_content(record)["direction"] == "unchanged"


# ------------------------------------------------------------ what is claimed not to matter

def test_created_at_is_not_part_of_a_limit_state_and_the_timestamp_is_not_part_of_an_authorization():
    assert limit_state_digest(dataclasses.replace(state(), created_at=5.0)) == limit_state_digest(state())
    assert authorization_digest(dataclasses.replace(decision(), timestamp=9.0)) == authorization_digest(decision())


def test_retry_markers_counters_extra_keys_and_key_order_are_not_part_of_a_limit_change():
    base = limit_change_digest(change())
    noisy = {**change(), "duplicate": True, "restored": True, "automatic_tightenings_since_acknowledgement": 2,
             "remaining_before_breaker": 1, "something_else": {"a": [1]}}
    assert limit_change_digest(noisy) == base
    assert limit_change_digest(dict(reversed(list(change().items())))) == base


def test_a_number_of_two_kinds_is_the_same_direction_but_not_the_same_record():
    as_int = {**change(), "limit_before": 100, "limit_after": 80}
    assert limit_change_content(as_int)["direction"] == limit_change_content(change())["direction"] == "tighten"
    assert limit_change_digest(as_int) != limit_change_digest(change())  # 100 and 100.0 are different limits to CNS


def test_a_lone_surrogate_in_a_reason_hashes_instead_of_raising():
    assert len(limit_change_digest({**change(), "reason": "broken \ud83d reason"})) == 64
    assert len(authorization_digest(dataclasses.replace(decision(), reason="broken \ud83d reason"))) == 64


# ------------------------------------------------------------ wrong types and missing keys

@pytest.mark.parametrize("bad", [None, "api", 7, {"boundary_id": "api"}, change(), decision(), [state()]])
def test_a_limit_state_that_is_not_a_boundary_version_is_a_type_error(bad):
    with pytest.raises(TypeError, match="BoundaryVersion"):
        limit_state_content(bad)


@pytest.mark.parametrize("bad", [None, "api", 7, [("boundary_id", "api")], state(), decision(), ("a", "b")])
def test_a_limit_change_that_is_not_a_mapping_is_a_type_error(bad):
    with pytest.raises(TypeError, match="mapping"):
        limit_change_content(bad)


@pytest.mark.parametrize("bad", [None, "system", 7, {"proposal_id": "p"}, state(), change()])
def test_an_authorization_that_is_not_an_authorization_decision_is_a_type_error(bad):
    with pytest.raises(TypeError, match="AuthorizationDecision"):
        authorization_content(bad)


@pytest.mark.parametrize("changes", [
    {"boundary_id": 7}, {"version": "2"}, {"version": True}, {"version": 2.0}, {"parent_version": "1"},
    {"resource_or_action": None}, {"current_limit": [80]}, {"current_limit": {"a": 1}}, {"current_limit": (80,)},
    {"current_limit": object()}, {"current_limit": 80 + 1j}, {"current_limit": math.nan}, {"current_limit": math.inf},
    {"current_limit": -math.inf}, {"status": "active"}, {"status": None}, {"status": AdaptationDirection.TIGHTEN}])
def test_a_limit_state_field_of_the_wrong_type_is_a_type_error(changes):
    with pytest.raises(TypeError):
        limit_state_content(dataclasses.replace(state(), **changes))


@pytest.mark.parametrize("changes", [
    {"boundary_id": 7}, {"limit_before": [1]}, {"limit_after": object()}, {"limit_before": math.nan},
    {"limit_after": math.inf}, {"source": None}, {"source": 3}, {"status": 200}, {"identity_verified": "yes"},
    {"identity_verified": 1}, {"version": "2"}, {"version": True}, {"proposal_id": 5}, {"reason": 5}, {"reason": b"x"}])
def test_a_limit_change_field_of_the_wrong_type_is_a_type_error(changes):
    with pytest.raises(TypeError):
        limit_change_content({**change(), **changes})


@pytest.mark.parametrize("changes", [
    {"proposal_id": 1}, {"direction": "tighten"}, {"direction": None}, {"effective_direction": "tighten"},
    {"result": "auto_approved"}, {"result": AdaptationDirection.TIGHTEN}, {"decided_by": None},
    {"identity_verified": "no"}, {"identity_verified": 0}, {"grant_id": 5}, {"reason": None}, {"reason": 7}])
def test_an_authorization_field_of_the_wrong_type_is_a_type_error(changes):
    with pytest.raises(TypeError):
        authorization_content(dataclasses.replace(decision(), **changes))


@pytest.mark.parametrize("key", ["boundary_id", "limit_before", "limit_after", "source", "status"])
def test_a_limit_change_missing_a_required_key_is_a_value_error_naming_it(key):
    record = change()
    del record[key]
    with pytest.raises(ValueError, match=key):
        limit_change_content(record)


def test_every_missing_key_is_named():
    with pytest.raises(ValueError) as caught:
        limit_change_content({"boundary_id": "api"})
    for key in ("limit_before", "limit_after", "source", "status"):
        assert key in str(caught.value)


def test_a_restored_signal_result_has_no_limit_before_and_is_refused_by_name():
    restored = {"status": "applied", "boundary_id": "api", "limit_after": 80.0, "version": 2, "source": "monitor",
                "restored": True}
    with pytest.raises(ValueError, match="limit_before"):
        limit_change_content(restored)


# ------------------------------------------------------------ binds

RECORDS = [
    pytest.param(state, LIMIT_STATE_SUBJECT, lambda: dataclasses.replace(state(), current_limit=79.0), id="limit state"),
    pytest.param(change, LIMIT_CHANGE_SUBJECT, lambda: {**change(), "limit_after": 70.0}, id="limit change"),
    pytest.param(decision, AUTHORIZATION_SUBJECT, lambda: dataclasses.replace(decision(), decided_by="alice"),
                 id="authorization"),
]
DIGESTS = {LIMIT_STATE_SUBJECT: lambda: limit_state_digest(state()), LIMIT_CHANGE_SUBJECT: lambda: limit_change_digest(change()),
           AUTHORIZATION_SUBJECT: lambda: authorization_digest(decision())}


@pytest.mark.parametrize("record, subject, other", RECORDS)
def test_a_verdict_issued_against_exactly_this_record_binds_to_it(record, subject, other):
    result = issued_against(subject, DIGESTS[subject]())
    assert binds(result, record()) is True
    assert result.binds(subject, DIGESTS[subject]())


@pytest.mark.parametrize("record, subject, other", RECORDS)
def test_a_verdict_does_not_bind_to_a_different_record_or_under_another_subject(record, subject, other):
    result = issued_against(subject, DIGESTS[subject]())
    assert binds(result, other()) is False
    assert binds(result, record(), subject="some.other.subject") is False
    assert binds(issued_against("some.other.subject", DIGESTS[subject]()), record()) is False
    assert binds(issued_against("some.other.subject", DIGESTS[subject]()), record(), subject="some.other.subject") is True


def test_a_verdict_does_not_bind_across_kinds():
    verdicts = {subject: issued_against(subject, DIGESTS[subject]()) for subject in DIGESTS}
    kinds = {LIMIT_STATE_SUBJECT: state, LIMIT_CHANGE_SUBJECT: change, AUTHORIZATION_SUBJECT: decision}
    for subject, verdict in verdicts.items():
        for other_subject, build in kinds.items():
            assert binds(verdict, build()) is (subject == other_subject)


def test_the_default_subject_follows_the_kind_of_record():
    wrong_label = issued_against(LIMIT_CHANGE_SUBJECT, limit_state_digest(state()))
    assert binds(wrong_label, state()) is False
    assert binds(wrong_label, state(), subject=LIMIT_CHANGE_SUBJECT) is True


@pytest.mark.parametrize("record, subject, other", RECORDS)
def test_an_unbound_hand_built_verdict_binds_to_nothing(record, subject, other):
    unbound = gate.GateResult(gate="test.gate", position=gate.GatePosition.OMEGA, outcome=gate.GateOutcome.PASS)
    assert not unbound.bound() and binds(unbound, record()) is False
    no_digest = gate.GateResult(gate="g", position=gate.GatePosition.OMEGA, outcome=gate.GateOutcome.PASS, subject=subject)
    assert binds(no_digest, record()) is False


@pytest.mark.parametrize("bad", [None, "pass", {"subject": "x"}, True])
def test_binds_takes_a_gate_result(bad):
    with pytest.raises(TypeError, match="GateResult"):
        binds(bad, state())


@pytest.mark.parametrize("bad", [None, "api", 3, [state()]])
def test_binds_takes_one_of_the_three_records(bad):
    result = issued_against(LIMIT_STATE_SUBJECT, limit_state_digest(state()))
    with pytest.raises(TypeError, match="BoundaryVersion, an AuthorizationDecision or a limit-change mapping"):
        binds(result, bad)


# ------------------------------------------------------------ free text is never copied

def test_no_free_text_appears_in_any_content():
    for content in (limit_change_content(change()), authorization_content(decision())):
        assert CANARY not in repr(content)
        assert "cpu spike" not in repr(content) and "auto-approved" not in repr(content)
    assert limit_change_content(change())["reason_sha256"] == sha(change()["reason"])
    assert authorization_content(decision())["reason_sha256"] == sha(decision().reason)


def test_the_identity_of_the_limit_and_of_who_asked_is_kept_as_written():
    content = limit_change_content(change())
    assert (content["boundary_id"], content["source"], content["status"], content["proposal_id"]) == (
        "api", "monitor", "applied", "prop-1")
    assert (content["limit_before"], content["limit_after"], content["identity_verified"]) == (100.0, 80.0, True)
    assert limit_state_content(state()) == {"boundary_id": "api", "version": 2, "parent_version": 1,
                                            "resource": "requests", "limit": 80.0, "status": "active"}


# ------------------------------------------------------------ against the repository's own code

def test_a_real_tightening_binds_and_a_verdict_on_it_is_about_that_tightening(tmp_path):
    g = make_governor(tmp_path)
    g.ensure_boundary("api", "rate", 100.0)
    result = g.propose_tightening("api", source="monitor", credential=MON, new_limit=80.0, reason=f"spike {CANARY}")
    assert result["status"] == "applied"
    content = limit_change_content(result)
    assert content["direction"] == "tighten" and content["identity_verified"] is True and CANARY not in repr(content)
    assert (content["limit_before"], content["limit_after"], content["version"]) == (100.0, 80.0, 2)

    verdict = issued_against(LIMIT_CHANGE_SUBJECT, limit_change_digest(result))
    assert binds(verdict, result)
    assert not binds(verdict, {**result, "limit_after": 70.0}) and not binds(verdict, {**result, "source": "someone else"})
    assert not binds(verdict, {**result, "identity_verified": False})


def test_a_real_loosening_request_is_recorded_as_a_change_that_did_not_happen(tmp_path):
    g = make_governor(tmp_path)
    g.ensure_boundary("api", "rate", 100.0)
    result = g.request_loosening("api", requested_by="monitor", credential=MON, new_limit=150.0, reason="please")
    assert result["status"] == "pending_review"
    content = limit_change_content(result)
    assert content["direction"] == "unchanged" and content["limit_before"] == content["limit_after"] == 100.0
    assert limit_state_content(g.boundaries.get_boundary("api"))["limit"] == 100.0


def test_a_rejected_attempt_to_raise_a_limit_through_the_tightening_call_binds_as_unchanged(tmp_path):
    g = make_governor(tmp_path)
    g.ensure_boundary("api", "rate", 100.0)
    result = g.propose_tightening("api", source="monitor", credential=MON, new_limit=150.0, reason="try")
    assert result["status"] == "rejected"
    assert limit_change_content(result)["direction"] == "unchanged"
    assert g.boundaries.get_boundary("api").current_limit == 100.0


def test_a_passing_verdict_bound_to_a_loosening_does_not_move_any_limit(tmp_path):
    """Binding records what a limit was. A PASS bound to a record of a raise grants nothing."""
    g = make_governor(tmp_path)
    g.ensure_boundary("api", "rate", 100.0)
    g.propose_tightening("api", source="monitor", credential=MON, new_limit=80.0)
    current = g.boundaries.get_boundary("api")
    raise_to_150 = {"boundary_id": "api", "limit_before": 80.0, "limit_after": 150.0, "source": "monitor",
                    "status": "applied", "identity_verified": True, "version": 3}
    assert limit_change_content(raise_to_150)["direction"] == "loosen"
    verdicts = [issued_against(LIMIT_CHANGE_SUBJECT, limit_change_digest(raise_to_150)),
                issued_against(LIMIT_STATE_SUBJECT, limit_state_digest(dataclasses.replace(current, current_limit=150.0)))]
    assert binds(verdicts[0], raise_to_150) and all(v.outcome is gate.GateOutcome.PASS for v in verdicts)

    entries_before = len(g.audit.entries)
    with pytest.raises(PermissionError, match="without an authorization grant"):
        g.boundaries.update_boundary("api", 150.0, reason="a passing verdict says so")
    assert g.boundaries.get_boundary("api").current_limit == 80.0
    assert len(g.audit.entries) == entries_before
    assert g.boundaries.unauthorized_loosenings() == [] and g.authority.verify_no_auto_loosen()


def test_a_real_system_tightening_and_an_operator_loosening_are_told_apart_by_who_decided(tmp_path):
    g = make_governor(tmp_path)
    g.ensure_boundary("api", "rate", 100.0)
    g.propose_tightening("api", source="monitor", credential=MON, new_limit=80.0)
    system = g.authority.decisions[-1]

    proposal = g.proposals.create_proposal(
        boundary_id="api", source_evidence=[], current_value=80.0, proposed_value=90.0, reason="load eased",
        direction=AdaptationDirection.LOOSEN)
    g.submit_for_review(proposal)
    _approved, version = g.apply_operator_decision(proposal.proposal_id, "approve_loosen", "alice", credential=OPS)
    operator = g.authority.decisions[-1]

    a, b = authorization_content(system), authorization_content(operator)
    assert (a["decided_by"], a["result"], a["effective_direction"], a["grant_id"]) == ("system", "auto_approved", "tighten", None)
    assert (b["decided_by"], b["result"], b["effective_direction"], b["identity_verified"]) == (
        "alice", "operator_approved", "loosen", True)
    assert b["grant_id"] and authorization_digest(system) != authorization_digest(operator)
    assert limit_state_content(version)["limit"] == 90.0 and limit_state_content(version)["version"] == 3

    verdict = issued_against(AUTHORIZATION_SUBJECT, authorization_digest(operator))
    assert binds(verdict, operator) and not binds(verdict, system)


def test_limit_states_and_decisions_have_the_same_digests_after_a_restart(tmp_path):
    g = make_governor(tmp_path, durable=True)
    g.ensure_boundary("api", "rate", 100.0)
    g.propose_tightening("api", source="monitor", credential=MON, new_limit=80.0)
    g.propose_tightening("api", source="monitor", credential=MON, new_limit=60.0)
    versions = g.boundaries.get_boundary_history("api").get_version_history()
    decisions = list(g.authority.decisions)
    assert len(versions) == 3 and len(decisions) == 2
    digests = [limit_state_digest(v) for v in versions]
    verdict = issued_against(LIMIT_STATE_SUBJECT, digests[2])

    restarted = make_governor(tmp_path, durable=True, name="restarted")
    assert restarted.restored["versions"] == 3
    revived = restarted.boundaries.get_boundary_history("api").get_version_history()
    assert [v.created_at for v in revived] != [v.created_at for v in versions]  # restored from the log's clock
    assert [limit_state_digest(v) for v in revived] == digests
    assert [authorization_digest(d) for d in restarted.authority.decisions] == [authorization_digest(d) for d in decisions]
    assert [d.timestamp for d in restarted.authority.decisions] != [d.timestamp for d in decisions]
    assert binds(verdict, revived[2])


def test_a_tampered_stored_limit_no_longer_binds(tmp_path):
    g = make_governor(tmp_path, durable=True)
    g.ensure_boundary("api", "rate", 100.0)
    g.propose_tightening("api", source="monitor", credential=MON, new_limit=80.0)
    active = g.boundaries.get_boundary("api")
    verdict = issued_against(LIMIT_STATE_SUBJECT, limit_state_digest(active))
    assert binds(verdict, active)
    assert not binds(verdict, dataclasses.replace(active, current_limit=100.0))
    assert not binds(verdict, dataclasses.replace(active, status=BoundaryStatus.DISABLED))
    assert AuditLog(str(tmp_path / "audit.jsonl")).verify()[0] is True
