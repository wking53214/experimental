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
