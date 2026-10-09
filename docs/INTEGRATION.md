# Integration: bring your own monitoring

The detectors in this repository are replaceable and, on real telemetry, not yet good enough to run unattended (`BASELINE_COMPARISON.md`, `PHASE_13_REPORT.md`). The part worth integrating is the **limit store**: it lets automation lower a limit and never raise one. Your monitoring decides *when*; the governor decides *whether*, and keeps the record.

## The three calls

| Call | What it does | Can it raise a limit? |
|---|---|---|
| `propose_tightening(boundary, source=, new_limit= or factor=, reason=, evidence=, idempotency_key=, credential=)` | Lowers the limit now, or says why not | **No.** A request that is not lower is `rejected`. |
| `request_loosening(boundary, requested_by=, new_limit=, reason=, ...)` | Queues a proposal for a named operator. Nothing changes. | Only through an operator's own decision, with their credential. |
| `boundary_status(boundary)` | Read only: limit, floor, breaker budget left, held or not, pending requests | n/a |

Same three over HTTP: `POST /signals/tighten`, `POST /signals/loosen-request`, `GET /boundaries/<id>`.

## What comes back (HTTP 200 for every policy outcome; read `status`)

| `status` | Meaning | What your monitor should do |
|---|---|---|
| `applied` | The limit is now lower. `limit_after`, `version`, `remaining_before_breaker` are included. | Nothing. |
| `applied_clamped` | You asked for less than the usability floor (20% of the original); it was set to the floor. | Nothing; do not retry for more. |
| `held` | The circuit breaker (default: 3 automatic tightenings) or the cooldown is holding. The limit did not change. | **Stop asking** and alert a person. Retrying cannot help; only an operator's acknowledgement reopens it. |
| `at_floor` | Already at the floor. | Alert a person if you think it should go lower; the system will not. |
| `rejected` | Not a tightening, or the change was refused; `reason` says why. | Fix the request. |
| `pending_review` | (loosening request) queued for an operator. | Wait; poll `GET /boundaries/<id>` or let a person tell you. |
| any, with `"duplicate": true` | The same `idempotency_key` was seen; this is the original result, not a new change. | Treat as done. |

Misuse is an error, not a status: 400 bad input (non-finite or non-numeric limits, a factor outside (0,1), oversized evidence, both or neither of limit and factor), 401 no or wrong token, 403 a source that cannot authenticate, wrong name for its credential, or a credential that is not allowed that action, 404 unknown boundary.

## Retries and idempotency

Monitors retry. Send an `idempotency_key` per *event* (for example `"<boundary>-episode-17"`). A retry returns the first result and does not tighten again. The key is stored in the same audit entry as the change, so it survives a crash and a restart (when `audit_path` is set).

## Credentials

- Machine sources: `ADJUDICATION_SOURCE_TOKENS="monitor=tok1,supervisor=tok2"` (server) or `Governor(signal_sources=OperatorRegistry)` (library). A source credential can send signals and read boundary status. It **cannot** decide a proposal, create a boundary, read the audit log or acknowledge the breaker; those need an operator credential.
- Humans: `ADJUDICATION_OPERATOR_TOKENS` / `Governor(operators=...)`, unchanged.
- Once source tokens exist, the shared token cannot masquerade as a source.
- Without a source registry, `source` is only a name you supply and results say `identity_verified: false`.

## Minimal Python

```python
from src.governance.governor import Governor
from src.governance.operators import OperatorRegistry

sources = OperatorRegistry(); sources.register("monitor", "a-long-secret-for-the-monitor")
gov = Governor(store_path="...", audit_path="/var/lib/gov/audit.jsonl", signal_sources=sources)
gov.ensure_boundary("api-key-rate", "rate", 100)

reply = gov.propose_tightening("api-key-rate", source="monitor", credential="a-long-secret-for-the-monitor",
                               factor=0.9, reason="error rate 6 sd above normal",
                               evidence={"error_rate": 0.31}, idempotency_key="evt-2026-10-09-001")
if reply["status"] == "held":
    page_a_human(reply["reason"])
```

Over HTTP see `examples/external_monitor.py` (standard library only, covered by a test that runs it against a live server).

## The guarantees, and what they do not cover

Whatever your monitor sends:

- a limit never goes up through these calls (tested with randomized hostile input: NaN, infinities, huge and negative numbers, wrong types);
- never below the floor, and never more automatic tightenings than the breaker allows without a person;
- every applied change, every hold, every failed authentication and every loosening request is in the hash-chained audit log, with the source and your evidence (capped at 4 KB).

Not covered:

- **A wrong monitor can still do harm within the breaker cap.** A buggy or compromised source can spend the whole automatic budget (by default 3 steps, about 27% of the limit) in seconds, which degrades service until a person acknowledges, reviews or loosens. That is the threat model's T14: bounded, not prevented. Give each source its own credential so you can see and revoke it.
- The interface trusts the *content* of a signal: it cannot tell a real incident from a false alarm.
- A pending loosening request is not an approval. Operators who rubber-stamp requests defeat the point.
