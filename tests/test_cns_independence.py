"""This repository is independent of CNS, and these tests hold whether or not CNS is installed.

Each runs in a fresh interpreter in which ``cns`` is blocked outright (``sys.modules['cns'] = None`` makes any import
of it fail), so the result does not depend on what the test environment happens to contain. They never skip.

They also hold the connector to the thesis: it only reads records. It names nothing that issues, consumes or decides a
grant, a proposal or a limit.
"""
import ast
import os
import subprocess
import sys
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONNECTOR = ROOT / "src" / "governance" / "cns_connector.py"
BLOCK = "import sys; sys.modules['cns'] = None; sys.modules['cns.gate'] = None\n"
ENV = {"PYTHONPATH": os.pathsep.join([str(ROOT)] + [p for p in os.environ.get("PYTHONPATH", "").split(os.pathsep) if p]),
       "PATH": os.environ.get("PATH", "/usr/bin:/bin"), "PYTHONDONTWRITEBYTECODE": "1"}
PIN = "cns @ git+https://github.com/wking53214/CNS.git@v1.8.0"
# Everything that can issue or consume a grant, decide a proposal, or move a limit.
AUTHORITY_NAMES = {
    "AuthorizationGrant", "_ISSUER_KEY", "AuthorityModel", "BoundaryStore", "ProposalStore", "Governor", "consume",
    "update_boundary", "restore_version", "create_boundary", "record_operator_decision", "authorize_proposal",
    "evaluate_proposal", "apply_approved_proposal", "apply_operator_decision", "create_proposal", "mark_approved",
    "mark_applied", "propose_tightening", "request_loosening", "grants"}


def run(code: str, *, block: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-c", (BLOCK if block else "") + textwrap.dedent(code)],
                          capture_output=True, text=True, env=ENV, timeout=120)


def ok(done: subprocess.CompletedProcess) -> None:
    assert done.returncode == 0, done.stderr
    assert done.stdout.strip().endswith("ok"), done.stdout


def imported_names(tree):
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            yield from (a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            yield node.module


def test_the_connector_imports_with_cns_blocked():
    ok(run("import src.governance.cns_connector; print('ok')"))


def test_the_governor_still_does_its_job_with_cns_blocked(tmp_path):
    ok(run(f"""
        import tempfile
        from src.governance.governor import Governor
        from src.governance import cns_connector as c
        g = Governor(store_path={str(tmp_path / "ev")!r}, audit_path={str(tmp_path / "audit.jsonl")!r}, unverified_operators=True)
        g.ensure_boundary("api", "rate", 100.0)
        done = g.propose_tightening("api", source="monitor", new_limit=80.0, reason="spike")
        assert done["status"] == "applied" and g.boundaries.get_boundary("api").current_limit == 80.0, done
        try:
            g.boundaries.update_boundary("api", 150.0)
        except PermissionError:
            pass
        else:
            raise SystemExit("a limit was raised without a grant")
        asked = g.request_loosening("api", requested_by="monitor", new_limit=150.0)
        assert asked["status"] == "pending_review" and g.boundaries.get_boundary("api").current_limit == 80.0, asked
        # the pure half of the connector works without cns
        assert c.limit_change_content(done)["direction"] == "tighten"
        assert c.limit_change_content(asked)["direction"] == "unchanged"
        assert c.limit_state_content(g.boundaries.get_boundary("api"))["limit"] == 80.0
        assert c.authorization_content(g.authority.decisions[0])["decided_by"] == "system"
        print('ok')
    """))


def test_the_connector_says_what_is_missing_when_cns_is_blocked():
    ok(run("""
        from src.governance.boundary import BoundaryStore
        from src.governance.cns_connector import (CnsNotInstalled, authorization_digest, binds, cns_available,
                                                  limit_change_digest, limit_state_digest)
        store = BoundaryStore()
        version = store.create_boundary("api", "rate", 100.0)
        change = {"boundary_id": "api", "limit_before": 100.0, "limit_after": 80.0, "source": "m", "status": "applied"}
        assert cns_available() is False
        calls = (lambda: limit_state_digest(version), lambda: limit_change_digest(change),
                 lambda: authorization_digest(None),  # cns is asked for before the record is read
                 lambda: binds(object(), version))
        for call in calls:
            try:
                call()
            except CnsNotInstalled as e:
                assert isinstance(e, ImportError)
                assert "pip install -r requirements-cns.txt" in str(e)
            else:
                raise SystemExit("computed a digest without cns")
        print('ok')
    """))


def test_importing_the_governance_modules_does_not_load_cns_even_when_it_is_installed():
    ok(run("""
        import sys
        import src.governance.authority, src.governance.boundary, src.governance.cns_connector, src.governance.governor
        import src.governance.grant, src.governance.signals
        assert not [m for m in sys.modules if m == "cns" or m.startswith("cns.")], "cns was loaded"
        print('ok')
    """, block=False))


def test_no_live_module_imports_cns_by_statement():
    # Live modules: everything outside tests/. The test files import cns.gate on purpose.
    paths = [p for p in ROOT.rglob("*.py") if ".git" not in p.parts and "tests" not in p.relative_to(ROOT).parts[:1]]
    assert CONNECTOR in paths and ROOT / "src" / "governance" / "governor.py" in paths and len(paths) > 30
    offenders = {}
    for path in paths:
        bad = [n for n in imported_names(ast.parse(path.read_text(encoding="utf-8"))) if n == "cns" or n.startswith("cns.")]
        if bad:
            offenders[str(path.relative_to(ROOT))] = bad
    assert offenders == {}


def test_the_connector_only_reads_records_and_names_nothing_that_grants_or_moves_a_limit():
    tree = ast.parse(CONNECTOR.read_text(encoding="utf-8"))
    names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    names |= {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    names |= {a.name for n in ast.walk(tree) if isinstance(n, (ast.Import, ast.ImportFrom)) for a in n.names}
    names |= {n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str)}  # getattr(x, "grants")
    assert names & AUTHORITY_NAMES == set()
    assert {"AuthorizationDecision", "AuthorizationResult", "BoundaryVersion", "is_loosening"} <= names  # the reads it does make


def test_the_repository_declares_no_runtime_dependency_and_pins_the_cns_extra():
    assert "cns" not in (ROOT / "requirements.txt").read_text(encoding="utf-8").lower()
    lines = (ROOT / "requirements-cns.txt").read_text(encoding="utf-8").splitlines()
    assert [line for line in lines if line.strip() and not line.startswith("#")] == [PIN]
    assert len([line for line in lines if line.startswith("#")]) == 1
