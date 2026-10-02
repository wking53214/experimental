#!/usr/bin/env python3
"""Phase 11D: Thin HTTP façade over Governor adjudication API.

Demo-only. No authentication. Not for production.
"""
from __future__ import annotations

import json
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.governance.governor import Governor
from src.governance.proposal import AdaptationDirection

GOV = Governor(store_path="/tmp/phase11_http_gov", use_semantic=True)


class Handler(BaseHTTPRequestHandler):
    def _json(self, code: int, obj):
        body = json.dumps(obj, default=str).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self):
        n = int(self.headers.get("Content-Length", 0))
        if n <= 0:
            return {}
        return json.loads(self.rfile.read(n).decode("utf-8"))

    def log_message(self, fmt, *args):
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/health":
            return self._json(200, {"status": "ok", **GOV.get_status()})
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
            except Exception as e:
                return self._json(404, {"error": str(e)})
        return self._json(404, {"error": "not found"})

    def do_POST(self):
        path = urlparse(self.path).path
        data = self._read_json()
        if path == "/boundaries":
            bid = data.get("boundary_id", "demo")
            limit = float(data.get("initial_limit", 100))
            try:
                GOV.boundaries.create_boundary(bid, data.get("resource", bid), limit)
            except Exception:
                pass
            return self._json(200, {"boundary_id": bid, "limit": limit})
        if path == "/proposals/loosen":
            bid = data["boundary_id"]
            proposed = float(data["proposed_value"])
            try:
                b = GOV.boundaries.get_boundary(bid)
                current = b.current_limit
            except Exception:
                return self._json(400, {"error": "boundary not found"})
            prop = GOV.proposals.create_proposal(
                boundary_id=bid,
                source_evidence=[],
                current_value=current,
                proposed_value=proposed,
                reason=data.get("reason", "operator-requested loosen"),
                direction=AdaptationDirection.LOOSEN,
            )
            updated = GOV.submit_for_review(prop)
            return self._json(200, {
                "proposal_id": updated.proposal_id,
                "status": updated.status.value,
            })
        if path.startswith("/proposals/") and path.endswith("/decide"):
            pid = path[len("/proposals/"):-len("/decide")].strip("/")
            try:
                result = GOV.apply_operator_decision(
                    pid,
                    data.get("decision", "reject"),
                    data.get("operator_id", "anonymous"),
                    data.get("rationale", ""),
                )
                prop = result[0]
                return self._json(200, {
                    "proposal_id": prop.proposal_id,
                    "status": prop.status.value,
                    "detail": str(result[1]),
                })
            except Exception as e:
                return self._json(400, {"error": str(e)})
        return self._json(404, {"error": "not found"})


def main():
    host, port = "127.0.0.1", 8765
    if len(sys.argv) > 1:
        port = int(sys.argv[1])
    httpd = HTTPServer((host, port), Handler)
    print(f"Phase 11D adjudication server on http://{host}:{port}")
    httpd.serve_forever()


if __name__ == "__main__":
    main()
