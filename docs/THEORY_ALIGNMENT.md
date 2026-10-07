# Theory Alignment

Status of each claim in the theory documents (`p3-2-governance-research/docs/white-paper-v1.md`
and `ARCHITECTURE.md`) against this prototype. Tests named here live in
`tests/test_theory_alignment.py` unless noted.

| Theory claim | Status | Where |
|---|---|---|
| Tightening is automatic; loosening needs an operator grant | Implemented | `authority.py`, `boundary.py` (grant check) |
| Disabling needs two humans | **Not implemented.** No disable path exists in the code, so a two-person rule would have nothing to guard. | — |
| Operator decisions are logged to the audit trail | Implemented | `governor.apply_operator_decision` writes to `ImmutableFileStore` |
| Dampening and circuit breaker against cascading tightening | Implemented, **opt-in**. `Governor(max_auto_tightenings=N)` holds automatic tightenings after N of them until an operator calls `acknowledge_tightening`. Also available: a cooldown and a freshness check (`tighten_cooldown_s`, `require_fresh_evidence`). Off by default, as documented in `THREAT_MODEL.md`. | `governor.py`, `tests/test_tightening_limits.py` |
| Interpretation layer: violations are classified before tightening | Implemented, **opt-in**. `Governor(use_semantic=True)` lets violations inside a registered expected-load range skip tightening. With nothing registered, behavior matches the default path. | `workload.py`, `governor.py` |
| Validation layer: adaptations judged against an independent signal | Partial. `Governor.review_effectiveness` runs the registered validator. Without a validator the result is UNKNOWN, so the layer does nothing until someone supplies an independent signal. | `governor.py`, `validation.py` |
| Rollback on degradation | Implemented, with the loosening gate. A DEGRADED outcome triggers a rollback. A rollback that loosens is queued for a human, never applied automatically. | `review_effectiveness`, `rollback.py` |
| Held tightenings are visible to a human | Partial. Held tightenings are recorded in `Governor.tightening_holds` with a reason. They are not in `list_pending_review`, which covers loosening and disabling proposals only. | `governor.py` |
| Access isolation (code cannot read learning summaries) | **Not implemented.** This is a library running in one process, so nothing stops in-process code from reading state. | — |
| Real-time stream, PostgreSQL/TimescaleDB, Kafka, Flink | **Not implemented, deliberately.** The prototype is in-process with a file-backed audit store. The theory's own sec. 8 lists these as technology choices, not the core claim. | — |
| Effectiveness score is defined | Not defined. Validation stays categorical (IMPROVED/UNCHANGED/DEGRADED/UNKNOWN) rather than inventing a metric. | `validation.py` docstring |

## Known remaining gaps

- The validation layer is only as good as the validator a boundary is given.
- The circuit breaker is opt-in. Turning it on by default is a policy decision still open (see the PR).
- The interpretation layer is opt-in for the same reason.
- The breaker counts tightenings. Cooldown and freshness are separate, optional limits; a slow steady attack can still shrink a boundary over time.
- Operator identity is self-asserted in the HTTP demo (see `LIMITATIONS.md`).
