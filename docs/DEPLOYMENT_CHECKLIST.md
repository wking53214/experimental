# Deployment checklist

What someone has to decide or set up before this runs anywhere but a research laptop. Each item names the threat or finding it answers. None of these is a code change; the code already has the hook, and the hook does nothing until the item is done. The order is the order of damage if skipped.

## 1. Put the audit anchor out of the governor's reach (T8)

`Governor(anchor_store=FileAnchorStore(path))` publishes the log's head after every write and refuses to start if the log was rewritten, truncated or deleted. It only means something if `path` is somewhere the governor's process can append to but cannot rewrite:

- a file owned by a different user, `chattr +a` set, the governor's user granted append through a group or a setgid directory; or
- an append-only object-store bucket or a second host behind the same two calls (`publish`, `latest`).

On the same host under the same user it catches nothing. `tests/test_audit_anchor.py` demonstrates both outcomes. Decide the location, write it down, and check it on a schedule (someone should run `verify()` against the anchor from a third place).

## 2. Put TLS in front of every HTTP path (T10)

The adjudication server and the kernel's limit reads are plain HTTP, and the shared token travels in clear. A reverse proxy with TLS (and client certificates for the kernel, if the proxy supports them) is enough for the prototype's surface. Do not expose either port without it.

## 3. Give every operator and every signal source its own credential (T9, T14)

- `Governor(operators=OperatorRegistry)` or `ADJUDICATION_OPERATOR_TOKENS` on the server: without it `operator_id` is a claim and every response says `identity_verified: false`.
- `Governor(signal_sources=OperatorRegistry)`: without it a monitor's source name is a claim. One credential per source lets a buggy source be attributed and revoked.
- These are shared secrets, not signatures. A person who can read the process memory can forge any operator. Asymmetric signatures are not built.

## 4. Register expected-load patterns through an operator, not the classifier (T4)

`Governor(strict_patterns=True)`. Off by default. Without it anyone who can call the classifier registers a pattern that excuses their own traffic.

## 5. Choose the trap policy thresholds for the workload, per class

The 1% rate threshold is a workload parameter: the traces hold labelled agents at 0.6%, 0.8%, 1.5% and 2% on both sides of it, and no trap-count rule separates them. `TrapPolicy(min_rate_by_class=...)` with `TrapBridge(agent_class=...)` lets an operator set an allowance per agent class above that class's natural timeout tail. Write down each class's tail and the threshold chosen above it. Agents slow from their first task will be tightened regardless (accepted limit; a person restores them).

The drift factor of 1.5 is flat down to 1.3 and breaks at 2.0 for 2x regressions. Leave it.

## 6. Choose the canary share

`TrapPolicy(validate_after=4, canary_share=...)`. The canary is the validation layer's only evidence. At 10% it confirms gross harm in about 7 slices and marginal harm in 20 or more; confirming benefit is almost impossible. A larger share resolves verdicts faster and gives up that share of the saving on hung tasks (round 6 of `docs/TRACE_TEST_PLAN.md` prices 10, 20 and 30%). Decide what a wrong tightening costs you against what a hung task costs you, and set the share from that. **Set `canary_key`** to a secret the agents cannot read; without it the canary is every tenth task by sequence number, and an agent that completes exactly those and hangs the rest turns the validation layer into a loosening lever (T16). The kernel must not reveal to a task which deadline it runs under.

## 7. Decide the breaker cap and who acknowledges it

`max_auto_tightenings=3` means automation can take at most about 27% off a limit before a named person must acknowledge. That is also the most it can save on a runaway. A boundary with less than 27% headroom will be hurt by a wrong tightening. Raise the cap only for limits with headroom; name the person who acknowledges holds and how they are paged.

## 8. Name the person who handles restore requests, and how often

Limits only go down on their own. The validation layer files a restore request when a tightening is confirmed harmful, and the drift rule files one when a regression is held after a limit was already cut. Everything else accumulates until someone reviews it. Decide the review cadence (the proposal's own number was quarterly) and the route (`GET /pending`, `POST /decide` on the adjudication server). About half the restore requests on inflating attackers are ones the person should refuse.

## 9. Enforce token and memory caps in the kernel, or do not govern them

The kernel example enforces the deadline only. The trace replays found that tightening token and memory caps added collateral and no benefit (a leak exceeds any cap), so the recommended policy governs the deadline only (`reasons=("deadline_exceeded",)`). Do not switch the other two on without a reason and a measurement.

## 10. Implement the canary in the kernel

The Python model runs the canary; the Rust kernel example does not. A kernel that implements the loop must run the share `TrapBridge.canary_plan()` asks for on the original deadline and report both groups' counts through `observe_transactions(..., canary_tasks=, canary_deadline_traps=)`. Until then the validation layer has no evidence in production.

## 11. Keep the claims to what was measured

The system is self-hardening in the safety direction and not self-judging. It has not run on real traffic. Statistical detection on real telemetry cries wolf and is not fit to act on unattended. Say "bounded autonomy with human-gated loosening and validated tightening", and point at `docs/STATUS.md` for the numbers.
