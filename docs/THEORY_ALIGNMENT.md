# Theory Alignment

Status of each claim in the theory documents (`p3-2-governance-research/docs/white-paper-v1.md`
and `ARCHITECTURE.md`) against this prototype. Tests named here live in
`tests/test_theory_alignment.py` unless noted.

| Theory claim | Status | Where |
|---|---|---|
| Tightening is automatic; loosening needs an operator grant | Implemented | `authority.py`, `boundary.py` (grant check) |
| Disabling needs two humans | **Not implemented.** No disable path exists in the code, so a two-person rule would have nothing to guard. | — |
| Operator decisions are logged to the audit trail | Implemented | `governor.apply_operator_decision` writes to `ImmutableFileStore` |
| Dampening and circuit breaker against cascading tightening | Implemented. After `Governor.AUTO_TIGHTEN_LIMIT` (3) automatic tightenings of one boundary, further tightenings wait for a human. Any human decision on that boundary resets the count. | `governor.py`, `authority.py` |
| Interpretation layer: violations are classified before tightening | Implemented, on by default. Violations inside a registered expected-load range do not drive tightening. With nothing registered, behavior is unchanged. | `workload.py`, `governor.py` |
| Validation layer: adaptations judged against an independent signal | Partial. `Governor.review_effectiveness` runs the registered validator. Without a validator the result is UNKNOWN, so the layer does nothing until someone supplies an independent signal. | `governor.py`, `validation.py` |
| Rollback on degradation | Implemented, with the loosening gate. A DEGRADED outcome triggers a rollback. A rollback that loosens is queued for a human, never applied automatically. | `review_effectiveness`, `rollback.py` |
| Human review queue includes held tightenings | Implemented | `Governor.list_pending_review` |
| Access isolation (code cannot read learning summaries) | **Not implemented.** This is a library running in one process, so nothing stops in-process code from reading state. | — |
| Real-time stream, PostgreSQL/TimescaleDB, Kafka, Flink | **Not implemented, deliberately.** The prototype is in-process with a file-backed audit store. The theory's own sec. 8 lists these as technology choices, not the core claim. | — |
| Effectiveness score is defined | Not defined. Validation stays categorical (IMPROVED/UNCHANGED/DEGRADED/UNKNOWN) rather than inventing a metric. | `validation.py` docstring |

## Known remaining gaps

- The validation layer is only as good as the validator a boundary is given.
- The circuit breaker counts tightenings, not time. A slow, steady attack below the count can still shrink a boundary over a long period, and the human queue is the backstop.
- Operator identity is self-asserted in the HTTP demo (see `LIMITATIONS.md`).
