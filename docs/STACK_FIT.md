# Where this core fits in the repository library

Written 2026-10-09 after reading the library, not from memory.

## What I read, and what I did not

- **Read in full or in depth (README plus code where noted):** STACK-Kernel (README, C++ README, the P3.2 schema design), p3-2-governance-research (README), DGK (README and code), Governance_Gateway, Warden, Judge, ZTGKT, fortress-kernel (READMEs).
- **Skimmed (opening of each README, plus a search of the code for limits, quotas, thresholds, rate limits):** the other ~40 public repositories, all cloned shallowly.
- **Not read at all:** the ~25 private repositories (including the archived ones). Anything below about them is silence, not evidence.

## The one-sentence answer

`p3-2-governance-research` proposes "asymmetric adaptive authority" for the STACK kernel's P3.2 layer: enforcement events are telemetry, automatic tightening is allowed, autonomous loosening is not. STACK-Kernel implements the telemetry half (trap events, a documented "flywheel") and describes the reorganization of boundaries as an offline step with no enforcement of its direction that I could find in the code. **This governance core is the missing adaptation half.** The thesis was written for it.

## Integrations built

| Where | What was built | Status |
|---|---|---|
| **DGK** | `integrations/dgk_governed.py`: DGK's three hardcoded health limits (latency 500, abort rate 0.25, re-entry 2.0) become governed boundaries read on every transaction; a conservative sustained-stress policy asks for tighter limits; DGK's own regime recovery is left alone. Tested against the real DGK (46 tests, run in CI against a pinned commit). | Working. The DGK defects it works around are fixed on a DGK branch (commit `4a0ecd2`, not merged); CI runs against both. |
| **STACK-Kernel (P3.2 trap events)** | `integrations/stack_traps.py`: trap events in the P3.2 schema become per-agent tightening signals for the deadline, token-bucket and memory limits, with a repeated-pattern policy, replay protection per trap id, a cap on agents, and an "expected load" hook. Tested on synthetic events that follow the schema (16 tests). | Working at the Python level, and now run closed-loop against the real Rust kernel's own trap events with simulated task durations (`docs/STACK_CLOSED_LOOP.md`; the default policy tightens healthy and slow agents too). **Not connected to production trap data** (there is none in the repository) and the Rust kernel can now read the governed deadline (STACK-Kernel branch `ccr-e916ef52-wh7751`, `crates/stack-p3-2/src/governed_limits.rs`; unmerged); token and memory limits are readable but not yet enforced, and the C++ headers do not read anything yet. |

## Defects found in the library while doing this

DGK, run with hostile telemetry: a NaN reading is committed and bypasses the health limits; that NaN permanently corrupts the classifier (`lyapunov_energy` stays NaN); infinities and large negatives raise out of `process_transaction` before anything is recorded; small negatives are committed as valid. Full table, reproduction and a suggested fix: `integrations/DGK_FINDINGS.md`. Fixed on DGK branch `ccr-e916ef52-wh7751` (`4a0ecd2`): telemetry is validated at the door and refused with cause `TELEMETRY_INVALID`; 41 regression tests, 37 fail on the old code. Not merged to DGK's `main`.

## Candidates, not built

| Repo | Why it might fit | Why I stopped |
|---|---|---|
| **fortress-kernel** | Its README lists hardcoded tunables (`activation_threshold=0.45`, `max_contraction_ratio=0.98`, `nominal_slew=0.20`, ...) and says it "does not approve policy or issue grants", i.e. it needs an authority. | They are controller tunables, and for several "tighter" is not "smaller" (an enter threshold and an exit threshold move in opposite directions). This core only orders single numbers. Needs a decision about which direction means tighter for each. |
| **STACK KineticGovernor / deadlines / arena** (C++) | Token-bucket rate, deadline and arena capacity are exactly the limits the trap bridge adjusts. | Rust and C++ would read the governed value over the HTTP interface (`GET /boundaries/<id>` returns `limit`) or from a file. I did not write or test that client. |
| **observe-perceive / PERCEIVE / ZTS / DIT / URE** | Gate thresholds exist in all of them. | I did not read their threshold code in depth. Unknown. |
| **Capability masks** (STACK Layer 4) | A permission set is the natural thing to tighten. | A mask is a set, not an ordered number; the core cannot say which of two masks is stricter. Needs a set-valued boundary type that does not exist yet. |

## Same principle, already in the library

The human-only-promotion idea recurs, built separately: **CCC** (human-only promotion to fact), **Warden** (a named human grant; "Unknown is not Approved"), **Judge** (reads, never writes), **fortress-kernel** ("does not issue grants"). This core's grants and operator registry are a fifth statement of it. I did not integrate them: Warden's authorization is about code changes, not limits.

## Duplication worth a decision

At least seven separate hash-chained audit implementations exist in the library: DGK (`ledger.py` and `audit.py`), `sentinel_os` (Postgres-backed), `interconnected_delta`, the STACK sentinel verifier, DIT, PERCEIVE, `fortress-kernel`, and this core's `audit_log.py`. DGK's README lists, as an open gap, that truncating the end of the ledger "is not detectable" without "a published head hash (an external anchor)" and that "where that anchor lives is not yet decided." The same gap exists in this core's log. One concrete option, **not verified**: `sentinel_os` is described as an independent custody runtime with a Postgres ledger; it could hold the head hash for the others. I did not read its API. A shared contract in CNS ("the contracts every repo joins on") would stop a seventh and eighth implementation, but CNS is one of the repositories I only skimmed.

## What would make this real in the stack

1. DONE for Rust and the deadline only (see above). Remaining: C++, and enforcing the token and memory limits. Original item: a small client in the kernel (C++ or Rust) that reads `GET /boundaries/stack.agent.<id>.<limit>` before an execution, with a short cache. Without this, the bridge adjusts numbers nobody reads.
2. Real trap data, to set the policy (`min_traps`, `window_events`, `factor`) from evidence. Everything above uses the defaults of an educated guess, and the Phase 13 result warns what noise does to a tightening budget.
3. DONE on a DGK branch; merging it into DGK's `main` is the owner's call.
4. Deciding where the audit anchor lives (see above).

## What this does not show

That the adaptation improves anything on a real workload. The value comparison in `SUCCESS_CRITERIA.md` is synthetic, a perfect human beat the system on sustained attacks, and no experiment here uses STACK data. These integrations make the idea usable in the stack; they do not demonstrate that the stack is better with it.
