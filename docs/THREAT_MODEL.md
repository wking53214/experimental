# Threat Model

Scope: the claim this project makes and who it holds against. Each row says what is **tested**, what is only **argued**, and what is **not covered**. If a claim is not backed by a test or a measurement, it is listed as not covered.

## The claim

1. **Asymmetry.** The system may tighten a constraint on its own. Only a human may loosen or disable one.
2. **Usability floor.** Automatic tightening never takes a boundary below 20% of its original limit.
3. **Detection.** Anomalous metric behaviour is flagged, with a measured false-positive rate and measured evasion rates.
4. **Auditability.** Every limit change, who authorized it, and why, can be reconstructed.

## Assets

| Asset | Why it matters |
|---|---|
| No-auto-loosen invariant | The core thesis. |
| Boundary availability | Tightening is the safe direction for security but costs availability. Legitimate users hit the limit. |
| Detector quality | Missed attacks, or alarms that cause tightening. |
| Decision log and version history | The evidence that 1 and 4 held. |
| Operator identity | Who may loosen. |

## Attacker classes

| Class | Capability | Considered |
|---|---|---|
| **A1** Observation-only | Controls the values and metrics fed to the system. Cannot call the API. | Yes, tested and measured |
| **A2** API caller without operator rights | Calls governor/authority/rollback methods, forges or mislabels proposals. Models bugs and API misuse. | Yes, tested |
| **A3** Holder of the adjudication-server token | Can call the HTTP endpoints. With the shared token alone, can claim any `operator_id`. With per-operator credentials, can act only as the operator whose credential they hold. | Yes (T9), when operator credentials are configured |
| **A4** Same-process code, or write access to stored state | Can edit memory, the version history and the decision log directly. | **Detection only** (T8): edits are evident, not prevented |
| **A5** Network observer or middlebox | Reads or alters HTTP traffic. | **No.** The server has no TLS. |
| **A6** Malicious or mistaken operator | Holds legitimate approval power. | **No.** Decisions are logged, not constrained. |

## Threats

| # | Threat | Attacker | Status | Evidence |
|---|---|---|---|---|
| T1 | **Evasion**: stay under the detectors | A1 | Partly mitigated. Evolved attacks still evade at weak strengths. A temporal layer roughly halves the weakest detectable shift, at the cost of absorbing slow attacks as drift (T5). | `PHASE9_EXPERIMENT_RESULTS.md`, `experiments/phase9_power_curves.py` |
| T2 | **Baseline poisoning**: attacker is present while the baseline is learned | A1 | **Real, measured, only partly mitigated.** 5% contamination of the baseline cuts detection of a 2 sd shift from 100% to 13% to 36%; 20% cuts a 3 sd shift to 3% to 12%; a slow ramp to 3% to 5%. A baseline check flags light and moderate contamination (94% to 100%) and ramps (100%) but is blind to heavy (40%) contamination and variance inflation, and flags about 8% of clean baselines. Robust fitting helps at 10% contamination and not at 40%, a ramp or inflation. The default detector learns from its first observations with no clean window; `Governor(baseline_check_at=N)` is opt-in. | `docs/BASELINE_POISONING.md`, `tests/test_baseline_poisoning.py` |
| T3 | **Tightening as denial of service**: feed over-limit values so the system tightens itself to the floor | A1 | **Real, now measured.** Opt-in mitigation reduces it. See below. | `experiments/dos_tightening.py`, `tests/test_tightening_limits.py` |
| T4 | **Spoofing "expected" load** so that real violations are excused | A2 | **Demonstrated; fixed only behind an option.** With default settings anyone who can call the classifier registers a pattern that excuses their own attack. `Governor(strict_patterns=True)` honors only patterns registered through an authenticated, audited operator call. Off by default. | `tests/test_phase13_integration.py`, `docs/PHASE_13_REPORT.md` |
| T5 | **Slow attack absorbed as drift** | A1 | **Open trade-off.** A change slower than the drift window is indistinguishable from legitimate drift. The temporal layer is off by default for this reason. | `PHASE_9F_REPORT.md` |
| T6 | **Mislabelled direction** (a proposal says TIGHTEN but raises the limit) | A2 | Fixed. The implied direction from the values is checked too. | `tests/test_invariant_attacks.py` |
| T7 | **Loosening through the side door** (rollback, direct store update, replayed grant) | A2 | Fixed. A loosening update needs a single-use grant that only the authority model can issue. | `tests/test_invariant_attacks.py`, `src/governance/grant.py` |
| T8 | **Tampering with history or the decision log** | A4 | **Detected, not prevented.** Every authority decision, boundary change, breaker hold and acknowledgement goes into a hash-chained audit log. Editing, deleting, inserting or reordering a past entry breaks the chain; editing the in-memory decision list without the log, or hiding a loosening from the log, fails the integrity check. **Limit:** someone who can rewrite the whole log can recompute every hash and it will verify. That is caught only against an anchor (`Governor.audit_anchor()`) stored somewhere the writer cannot modify, which is the operator's job. Tail truncation is likewise caught only against an anchor. | `src/governance/audit_log.py`, `tests/test_audit_and_identity.py` |
| T9 | **Operator impersonation** | A3 | **Mitigated when configured, not by default.** With an `OperatorRegistry` (`Governor(operators=...)`, or `ADJUDICATION_OPERATOR_TOKENS` on the server), every operator decision, rollback and breaker acknowledgement needs a credential that authenticates for that `operator_id`; failures are audited and change nothing. The server refuses the shared token for decisions once named operators exist. Without a registry, `operator_id` is still only a claim and the response says `identity_verified: false`. **Limit:** a shared secret held in one process proves the caller knew alice's secret; it is not non-repudiation (the process itself could forge any registered operator). | `src/governance/operators.py`, `tests/test_audit_and_identity.py`, `tests/test_adjudication_server_hardening.py` |
| T10 | **Traffic interception or tampering** | A5 | **Not covered.** No TLS; the token travels in clear. | none |
| T11 | **Unbounded memory growth** from event stores | A1 | **Partly addressed.** The breaker no longer logs one record per held step (7,997 per 8,000 real steps before); event stores themselves remain unbounded. Event lists are append-only and unbounded by design. | none |

Scope caveat on T6 and T7: the grant mechanism stops bugs and API misuse. Python offers no real isolation inside one process, so it does not stop A4.

| T12 | **False alarms exhaust the system's autonomy** (no attacker needed) | none | **Real, measured.** On 12 real machines (6% median false-alarm rate) the closed loop reached the breaker cap on every machine within 8,000 steps, all of it unjustified; with the breaker off every machine reached the floor. The evidence-window and fresh-evidence options (off by default) fix the clean-data case, not the real-data case. | `docs/PHASE_13_REPORT.md`, `experiments/phase13_soak.py` |

| T13 | **A restart resets the ratchet**: limits held in memory only, so after a restart the application re-creates each boundary at its configured value | none (operational) | **Fixed when `Governor(audit_path=...)` is set; still open otherwise.** The audit log is the source of truth: every boundary version is written to it before it takes effect (fail closed), and at startup the log is verified and replayed to rebuild limits, version history, the breaker count and the decision list. Startup is refused if the chain is broken, an anchor mismatches, or replay finds a limit raised without a grant. `create_boundary` on a restored boundary now raises; `ensure_boundary` keeps the lower restored limit. **Limits:** a forger who recomputes the chain AND invents a grant id gets a loosening past replay (the integrity check then flags the missing matching record); a full rewrite is caught only against an anchor; pending proposals, unused grants and expected-load patterns are not restored; limits must be JSON values. | `tests/test_durable_state.py`, `docs/DURABLE_STATE.md` |

| T14 | **A buggy or compromised signal source** drives limits down through `propose_tightening` | A1/A3 | **Bounded, not prevented.** A source can spend the whole automatic budget (3 steps by default, about 27% of the limit) immediately; after that every call is `held` and logged once. It cannot raise a limit, go below the floor, decide a proposal, or touch the audit log (a source credential is boxed in; verified over HTTP). Give each source its own credential to attribute and revoke it. | `tests/test_signal_interface.py`, `docs/INTEGRATION.md` |

| T15 | **Drift reports as a lever**: inflated `completed_median_ns`, or a baseline learned while the agent is already misbehaving, makes the drift rule hold tightening that a runaway deserves | A1/A3 | **Open, bounded.** It can only suppress automatic tightening (never loosen). No defence yet; the baseline is the same weakness as T2. | `docs/STACK_CLOSED_LOOP.md` |

## T3 in detail: the denial-of-service lever

Setup: one boundary with limit 100. Legitimate traffic is roughly N(50, 5). The attacker sends values 5% above the current limit and nothing else. Each value is a genuine violation, so the pattern detector tightens by 10% as designed. Results from `experiments/dos_tightening.py`:

| Defence | Fast attacker (1 value/min) | Patient attacker (1 value/hour) | Legit traffic blocked at end |
|---|---|---|---|
| None (default) | floor in 16 values, 0.01 days | floor in 16 values, 0.6 days | 100% |
| Fresh evidence only | floor in 42 values, 0.03 days | floor in 42 values, 1.7 days | 100% |
| Fresh evidence + 1 h cooldown | floor in 783 values, 0.5 days | floor in 42 values, 1.7 days | 100% |
| Fresh evidence + breaker (max 3) | limit stays 72.9 | limit stays 72.9 | 0% |

What this says:

- **Every governance invariant check passed throughout**, even as the system was driven to the floor. The thesis holds and the system is still being denied service. The invariant protects against loosening; it does not protect availability.
- **Before the fix**, the pattern detector re-fired on old violations, so every new violation after the third tightened again. "Fresh evidence" makes each tightening need new evidence. It makes the attack roughly 3x more expensive.
- **A cooldown only buys time.** A patient attacker reaches the floor either way.
- **The breaker bounds the damage.** After `max_auto_tightenings` automatic tightenings on a boundary, further ones are held and logged in `tightening_holds` until an operator calls `acknowledge_tightening`. This keeps the asymmetry: it removes autonomy only in the tightening direction, and loosening still needs a human.
- **The breaker's cost is real.** A genuine, sustained attack is also held after the cap. With a cap of 3, the most the system can lose without a human is about 27% of the limit. Whether that is harmless depends on headroom: here legitimate traffic sits at half the limit, so a 27% loss is harmless. A boundary with less than 27% headroom would still be hurt.
- The breaker (`max_auto_tightenings=3`) and the semantic layer (`use_semantic=True`) are **on by default**, per the theory document. The cooldown and freshness options stay off. Turning the breaker off (`max_auto_tightenings=None`) is a policy decision about how much autonomy to give up.

## Not covered, in priority order

1. Anchor storage (T8): the log only helps against a rewrite if its head hash is kept somewhere the governor cannot write. Nothing here does that for you.
2. Asymmetric operator signatures (T9) for real non-repudiation; needs a crypto library the project does not depend on yet.
3. T2 follow-up: procedural controls (no learning while exposed to an untrusted party, a person confirms the baseline window); the check and robust fit are partial. Not measured: poison smaller than the attack, poison placed at the end of the window, real telemetry.
4. T4: require operator approval to register an expected-load pattern.
5. T10 TLS, T11 memory bounds.
6. A6: a rule that high-impact loosenings need two operators.
