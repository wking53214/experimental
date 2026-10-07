#!/usr/bin/env python3
"""Phase 11D HTTP adjudication demo. M3: loopback default + optional token."""
from __future__ import annotations

import hmac
import json
import math
import os
import sys
import traceback
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.governance.governor import Governor
from src.governance.proposal import AdaptationDirection

GOV = Governor(store_path="/tmp/phase11_http_gov", use_semantic=True)
ADJUDICATION_TOKEN = os.environ.get("ADJUDICATION_TOKEN", "").strip()
MAX_BODY_BYTES = 1_000_000
DECISIONS = ("approve", "approve_loosen", "approve_disable", "reject", "rejected")


def _parse_operator_tokens(spec: str) -> dict:
    """ADJUDICATION_OPERATOR_TOKENS="alice=tokenA,bob=tokenB" -> {token: operator}."""
    out = {}
    for part in filter(None, (s.strip() for s in spec.split(","))):
        name, _, token = part.partition("=")
        if name.strip() and token.strip():
            out[token.strip()] = name.strip()
    return out


OPERATOR_TOKENS = _parse_operator_tokens(os.environ.get("ADJUDICATION_OPERATOR_TOKENS", ""))


class BadRequest(Exception):
    def __init__(self, message: str, code: int = 400):
        super().__init__(message)
        self.code = code


def _finite_positive(data: dict, field: str) -> float:
    if field not in data:
        raise BadRequest(f"missing required field: {field}")
    v = data[field]
    if isinstance(v, bool) or not isinstance(v, (int, float, str)):
        raise BadRequest(f"{field} must be a number")
    try:
        f = float(v)
    except ValueError:
        raise BadRequest(f"{field} must be a number")
    if not math.isfinite(f) or f <= 0:
        raise BadRequest(f"{field} must be a finite number greater than 0")
    return f


def _required_str(data: dict, field: str) -> str:
    v = data.get(field)
    if not isinstance(v, str) or not v.strip():
        raise BadRequest(f"missing required field: {field}")
    return v.strip()


def _eq(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode("utf-8"), b.encode("utf-8"))


class Handler(BaseHTTPRequestHandler):
    def _json(self, code: int, obj):
        body = json.dumps(obj, default=str).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self) -> dict:
        try:
            n = int(self.headers.get("Content-Length", 0))
        except ValueError:
            raise BadRequest("invalid Content-Length")
        if n > MAX_BODY_BYTES:
            raise BadRequest("request body too large", 413)
        if n <= 0:
            return {}
        try:
            data = json.loads(self.rfile.read(n).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise BadRequest("request body is not valid JSON")
        if not isinstance(data, dict):
            raise BadRequest("request body must be a JSON object")
        return data

    def _presented_token(self) -> str:
        auth = self.headers.get("Authorization", "")
        if auth.startswith("Bearer "):
            return auth[len("Bearer "):].strip()
        return self.headers.get("X-Adjudication-Token", "").strip()

    def _auth(self):
        """Returns (authenticated, operator_id). operator_id is set only when the token
        belongs to one named operator (ADJUDICATION_OPERATOR_TOKENS)."""
        if not ADJUDICATION_TOKEN and not OPERATOR_TOKENS:
            return True, None
        presented = self._presented_token()
        if not presented:
            return False, None
        for token, name in OPERATOR_TOKENS.items():
            if _eq(presented, token):
                return True, name
        if ADJUDICATION_TOKEN and _eq(presented, ADJUDICATION_TOKEN):
            return True, None
        return False, None

    def _unauthorized(self):
        return self._json(401, {
            "error": "unauthorized",
            "hint": "Authorization: Bearer <token> or X-Adjudication-Token",
        })

    def log_message(self, fmt, *args):
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    def _dispatch(self, fn):
        try:
            return fn()
        except BadRequest as e:
            return self._json(e.code, {"error": str(e)})
        except PermissionError as e:
            return self._json(403, {"error": str(e)})
        except Exception:
            traceback.print_exc(file=sys.stderr)
            return self._json(500, {"error": "internal error"})

    def do_GET(self):
        return self._dispatch(self._get)

    def do_POST(self):
        return self._dispatch(self._post)

    def _get(self):
        path = urlparse(self.path).path
        authed, _ = self._auth()
        auth_required = bool(ADJUDICATION_TOKEN or OPERATOR_TOKENS)
        if path == "/health":
            body = {"status": "ok", "auth_required": auth_required, "demo": True}
            if authed:
                body.update(GOV.get_status())
            return self._json(200, body)
        if not authed:
            return self._unauthorized()
        if path == "/proposals/pending":
            pending = GOV.list_pending_review()
            return self._json(200, {
                "proposals": [{
                    "proposal_id": p.proposal_id,
                    "boundary_id": p.boundary_id,
                    "direction": p.direction.value,
                    "current_value": p.current_value,
                    "proposed_value": p.proposed_value,
                    "reason": p.reason,
                    "status": p.status.value,
                } for p in pending]
            })
        if path.startswith("/proposals/") and path.endswith("/evidence"):
            pid = path[len("/proposals/"):-len("/evidence")].strip("/")
            try:
                return self._json(200, GOV.get_evidence_pack(pid))
            except KeyError:
                return self._json(404, {"error": f"proposal {pid} not found"})
        return self._json(404, {"error": "not found"})

    def _post(self):
        authed, token_operator = self._auth()
        if not authed:
            return self._unauthorized()
        path = urlparse(self.path).path
        data = self._read_json()
        if path == "/boundaries":
            bid = _required_str(data, "boundary_id") if "boundary_id" in data else "demo"
            limit = _finite_positive(data, "initial_limit") if "initial_limit" in data else 100.0
            try:
                GOV.boundaries.create_boundary(bid, data.get("resource", bid), limit)
            except ValueError:
                return self._json(409, {"error": f"boundary {bid} already exists"})
            return self._json(200, {"boundary_id": bid, "limit": limit})
        if path == "/proposals/loosen":
            bid = _required_str(data, "boundary_id")
            proposed = _finite_positive(data, "proposed_value")
            try:
                current = GOV.boundaries.get_boundary(bid).current_limit
            except KeyError:
                raise BadRequest("boundary not found", 404)
            prop = GOV.proposals.create_proposal(
                boundary_id=bid, source_evidence=[], current_value=current,
                proposed_value=proposed,
                reason=str(data.get("reason", "operator-requested loosen"))[:500],
                direction=AdaptationDirection.LOOSEN,
            )
            updated = GOV.submit_for_review(prop)
            return self._json(200, {"proposal_id": updated.proposal_id, "status": updated.status.value})
        if path.startswith("/proposals/") and path.endswith("/decide"):
            pid = path[len("/proposals/"):-len("/decide")].strip("/")
            decision = _required_str(data, "decision").lower()
            if decision not in DECISIONS:
                raise BadRequest(f"decision must be one of {', '.join(DECISIONS)}")
            claimed = data.get("operator_id")
            if token_operator:
                if claimed and str(claimed).strip() != token_operator:
                    raise BadRequest("operator_id does not match the credential used", 403)
                operator = token_operator
            else:
                operator = _required_str(data, "operator_id")
                if operator.lower() == "anonymous":
                    raise BadRequest("operator_id must identify the operator")
            try:
                GOV.proposals.get_proposal(pid)
            except KeyError:
                raise BadRequest(f"proposal {pid} not found", 404)
            try:
                result = GOV.apply_operator_decision(pid, decision, operator, str(data.get("rationale", ""))[:1000])
            except ValueError as e:
                raise BadRequest(str(e))
            final = GOV.proposals.get_proposal(pid)
            return self._json(200, {
                "proposal_id": final.proposal_id,
                "status": final.status.value,
                "decided_by": operator,
                "identity_verified": bool(token_operator),
                "detail": str(result[1]),
            })
        return self._json(404, {"error": "not found"})


def main():
    host = os.environ.get("ADJUDICATION_HOST", "127.0.0.1")
    port = int(os.environ.get("ADJUDICATION_PORT", sys.argv[1] if len(sys.argv) > 1 else "8765"))
    public = host in ("0.0.0.0", "::", "[::]")
    if public and os.environ.get("ALLOW_PUBLIC_BIND", "") != "1":
        print("REFUSING public bind to %s. Use 127.0.0.1 or ALLOW_PUBLIC_BIND=1." % host, file=sys.stderr)
        sys.exit(2)
    if public:
        print("WARNING: public bind %s — set ADJUDICATION_TOKEN." % host, file=sys.stderr)
    if not ADJUDICATION_TOKEN and not OPERATOR_TOKENS:
        print("WARNING: no token configured: every endpoint is unauthenticated (loopback only).", file=sys.stderr)
    elif ADJUDICATION_TOKEN and not OPERATOR_TOKENS:
        print("NOTE: one shared token: operator_id in the request body is self-asserted. "
              "Set ADJUDICATION_OPERATOR_TOKENS=alice=tok,bob=tok to bind identity to credentials.", file=sys.stderr)
    httpd = HTTPServer((host, port), Handler)
    print("Phase 11D on http://%s:%s (auth_required=%s)" % (host, port, bool(ADJUDICATION_TOKEN or OPERATOR_TOKENS)))
    httpd.serve_forever()


if __name__ == "__main__":
    main()
