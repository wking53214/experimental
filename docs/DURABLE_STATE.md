# Durable State (threat T13)

Without this, limits live in memory. After a restart the application re-creates each boundary at its configured value, which silently undoes every automatic tightening, with no grant and no audit record. That is a loosening, which the design forbids.

## How it works

The hash-chained audit log is the source of truth.

1. Every boundary version, including creation, is written to the log **before** it takes effect. If the write fails the change does not happen.
2. At startup the governor verifies the chain (and an anchor, if you give one), then replays the log to rebuild:
   - boundaries and their full version history,
   - which versions were authorized loosenings, with their grant ids,
   - the circuit-breaker count since the last operator acknowledgement,
   - the authority's decision list (so the integrity cross-check keeps working).
3. Startup is **refused** (`AuditIntegrityError`) if the chain is broken, the anchor does not match, a line in the middle is corrupt, or replay finds a step that raises a limit with no grant on record.
4. A final line cut off by a crash is dropped and the file is repaired; it never became durable.
5. Each restart is recorded in the log (`restored`).

## Using it

```python
g = Governor(store_path="...", audit_path="/var/lib/gov/audit.jsonl",
             operators=registry, audit_anchor=load_anchor_from_somewhere_safe())
g.ensure_boundary("api-key-rate", "rate", configured_limit=100)   # NOT create_boundary
...
save_anchor_somewhere_safe(g.audit_anchor())   # periodically, off the governor's host
```

- `ensure_boundary` creates a boundary the first time and afterwards keeps the **restored** limit. A configured limit higher than the restored one is ignored and noted in the log (`configured_limit_ignored`), because applying it would loosen without a grant.
- `create_boundary` on a restored boundary raises, so a restart can no longer reset a limit by accident.
- To raise a limit on purpose, go through the operator path as before.

## Limits (what this does not do)

- **A full rewrite of the log is invisible** without an anchor stored where the governor's process cannot write. The anchor is your job.
- A forger who recomputes the chain **and** invents a grant id gets a loosening past replay, because the log cannot prove a grant id is real. The integrity check then flags it (no matching authority decision and `boundary_update` record), but the limit is already loaded. Treat a failed integrity check as an incident.
- **Not restored:** pending proposals (an operator must resubmit), unused grants (void, which is safe), registered expected-load patterns (register them again through `register_expected_pattern`), detector baselines (the detector relearns from the first observations, with the poisoning exposure that implies; see `BASELINE_POISONING.md`).
- Limits must be JSON values (numbers).
- The log is one file, appended and fsynced per entry, for one process. There is no locking for several writers.
- Size grows without bound; no compaction yet.
