# Positioning: what this is, what exists already, what is left that is distinctive

Written 2026-10-08. The survey below was four shallow web searches, not a market study: absence of a hit is weak evidence. "STACK" in your question is not defined anywhere in this repository or conversation, so fit to your stack is described in general terms.

## What it is, reduced to its core

A small Python library (numpy, standard library only) that keeps **numeric limits on resources or actions** (rate caps, budgets, quotas, permission levels) with one rule enforced in the data structure rather than in configuration:

- Automation may **lower** a limit. It may never raise one.
- Raising a limit needs a single-use grant, issued only when a named, authenticated operator approves that exact change.
- Lowering is bounded: a usability floor (20% of the original), and a circuit breaker (3 automatic steps, then a human).
- Every decision and change goes into a hash-chained audit log, and an integrity check cross-checks it.

Everything else in the repository (the anomaly detectors, the evolutionary adversary, the baseline checks) is experiment scaffolding around that core.

## What already exists (so is not a differentiator)

| Piece | Existing tools |
|---|---|
| Policy enforcement, approvals, budgets, signed audit for AI agents | [Microsoft Agent Governance Toolkit](https://opensource.microsoft.com/blog/2026/04/02/introducing-the-agent-governance-toolkit/), [Runeward](https://pkg.go.dev/github.com/Runewardd/runeward@v0.2.1) |
| Hash-chained tamper-evident agent audit logs | [waxseal](https://pypi.org/project/waxseal/), [halo-record](https://pypi.org/project/halo-record/0.2.28/), [CSAE](https://pypi.org/project/csae/), [AuditWeave](https://arxiv.org/abs/2607.09682) (halo-record also describes an external witness, which this audit log lacks) |
| Automatic restriction with manual override | fail2ban-style banning (automatic ban, manual unban; bans usually expire on a timer) |
| Anomaly detection | Many mature tools; on real data a plain Mahalanobis baseline matched this project's detector |

## What I could not find, and so may be distinctive

An **adaptive limit whose monotonicity is a property of the store**: limits that move on their own in response to observed behavior, with "automation can only tighten" guaranteed by construction (grant-gated loosening, direction checked from the values themselves, version history audited) instead of by policy text. Policy engines decide allow or deny per action; they do not change their own limits from experience under a one-way constraint. The agent-governance sources I found describe the one-way rule as "a reasonable design" without naming it as an existing pattern.

Distinctive is not the same as valuable. Measured value so far: against a sustained runaway the system cuts admitted excess usage by 43%, bounded by the breaker cap; a human with perfect discrimination does better on a sustained attack; the system is better only in the window before a human can respond. Against an attacker who stays inside every limit it does nothing.

## What is not ready for a stack

1. ~~Limits are not persisted (T13)~~ **Fixed 2026-10-08 when `audit_path` is set** (`docs/DURABLE_STATE.md`): limits, the breaker count and the decision list are rebuilt from the verified audit log at startup. It is opt-in, and it needs `ensure_boundary` instead of `create_boundary` at startup.
2. **The built-in detector is not usable unattended on real telemetry** (Phase 13). Use your own signal: the supported way is the signal interface (`docs/INTEGRATION.md`: `propose_tightening`, `request_loosening`, `boundary_status`, over Python or HTTP), which applies the same floor, breaker, cooldown and audit as the built-in loop and cannot raise a limit.
3. Single process, in-memory state; the HTTP server is a demo with no TLS; Python only.
4. The audit log needs an external anchor to mean anything against a determined rewrite.

## Where it would fit

As a **sidecar or library that owns the limit state for something else**, with detection supplied from outside: an AI agent's spend or tool-call budget, an API key's rate cap, a permission tier. You send it "this principal looks wrong, propose a tighter limit"; it applies the tightening instantly and refuses any increase without a human grant. The part worth building a stack around is that refusal, plus the audit trail. The adaptive detection is replaceable and, today, the weak half.

## Update 2026-10-09: what the library showed

After reading the repository library, the picture changed: this core is the adaptation half of the STACK kernel's P3.2 proposal, not a free-standing product. Integrations exist for DGK and for STACK trap events; see `docs/STACK_FIT.md`. The "not ready for a stack" list above still applies to the kernel side: nothing in Rust or C++ reads the governed limits yet.

## Novelty check, 2026-10-14 (eight web searches; shallow, vendor-heavy, US-only; absence of a hit is weak evidence)

Taken apart into its pieces, and each piece held against what already exists.

| Piece | Closest prior art | What is different here | Novelty |
|---|---|---|---|
| Automation may only reduce a limit; a human raises it | OpenBSD `pledge`/`unveil` and Linux `seccomp` with `no_new_privs` (a process can only drop capabilities, never regain them); HSTS (a browser's policy can only strengthen); fail2ban (automatic ban, manual unban, timed expiry); "ratchet" as a safety-engineering term | Applied to **adaptive numeric limits** that move from observed behaviour, with the one-way rule enforced in the store (grant-gated loosening, direction derived from the values, audited versions, durable across restarts) rather than in policy text. No search hit describes that combination; agent-governance writing (for example Malhotra's "asymmetric verbs", budgets and tripwires talk, 2026) argues for asymmetry without a store-enforced ratchet on learned limits | Moderate: a new combination of known primitives, not a new primitive |
| Enforcement events as the signal that moves a limit | TCP congestion control (AIMD); Netflix `concurrency-limits`; GitLab Gitaly's adaptive concurrency limit; adaptive load shedding; adaptive rate-limiting products | Those learn in both directions from latency and errors. Here the signal is the enforcement event itself, the movement is one-way, per agent, and the loosening is human-gated | Low to moderate: the reframing is useful, the control loop is standard |
| Interpretation rules (rate, drift, excess release with a slowdown fit under deadline truncation, recovery clearing) | Per-signal heuristics in every adaptive limiter; drift handling in anomaly detection | The excess-release rule, fitting a slowdown factor from truncated completion quantiles to decide whether a trap share is explained, is specific and was tested adversarially (T15) | Low: engineering, though the truncation correction is a concrete small contribution |
| Validating a tightening by a held-back canary, in one time currency, with confidence-bounded verdicts | Netflix Kayenta and Flagger (canary-against-baseline analysis for releases); Rehak, Pechoucek et al., "Dynamic Information Source Selection for Intrusion Detection Systems" (AAMAS 2009): **challenge insertion**, slipping inputs of known class into live traffic to score an adaptive detector, chosen so an attacker shaping the inputs cannot steer the adaptation | Here the canary validates a **governance action** (a tightening) against the same agent's own traffic, judged by hung time saved against completing-task time burned, with the finding that a 10% canary confirms harm but almost never benefit. The gaming attack and the keyed selection (T16) are the same idea as Rehak's hidden challenges, rediscovered on the other side of the loop | Moderate: the application and the power analysis are new; the mechanism has a direct 2009 precedent that the paper draft must cite |
| Hash-chained audit log with an external anchor | Certificate Transparency, Trillian, blockchain anchoring, `halo-record`'s witness | Nothing | None |
| Pre-registered pass bars and predictions, scored row by row, with post-hoc changes recorded and rechecked on fresh seeds | Standard in experimental science; rare in systems and security prototypes | The method, not the mechanism, and the negative results it produced: trap counts cannot separate a wide-tailed agent from a mild runaway; the self-judging claim failed; validation confirms harm, not benefit, at a 10% canary | The most distinctive thing in the repository is the record, not the code |

**Verdict.** No component is new. The combination (adaptive numeric limits, a store-enforced one-way ratchet, human-gated grants, canary-validated tightening, and measured negative results) did not turn up anywhere in these searches, and the agent-governance material of 2026 argues for the asymmetry without building it into learned limits. The honest claim is "a novel combination with a direct precedent for its validation mechanism and a set of negative results worth publishing", not "a novel mechanism".

**What a paper must do before claiming anything.** A systematic search (ACM DL, IEEE, arXiv, patents) on monotone policy adaptation, human-in-the-loop admission control, and adaptive timeout validation; and a related-work section that cites `pledge`/`seccomp`, AIMD and the concurrency-limits line, Kayenta, and Rehak 2009 by name. `docs/references.bib` has none of them yet.

**Addendum after round 9 (same day).** The head-to-head against a two-way limiter decomposes the ratchet's value: the cap and the drift hold do the protecting, the direction's unique contribution is that an attacker cannot wait out a recovery. That narrows the novelty claim further and makes it more defensible: the contribution is the measured decomposition, not the direction.
