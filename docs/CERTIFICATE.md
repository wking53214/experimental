# The worst-case certificate

What this document claims, why it holds, and where it does not.

## The claim

Take an agent with a deadline D. The breaker allows at most three automatic tightenings of 10% each, so the agent's deadline can
never fall below 0.9 x 0.9 x 0.9 x D = 0.729 D, whatever the detector, the policy or an attacker does. Then:

1. **Honest cost.** Every task that finishes within 0.729 D is never killed by the limiter. The only legitimate tasks the limiter can
   kill that the static deadline would not are the ones that run longer than 0.729 D and no longer than D. So the extra failures,
   as a share of the agent's tasks, are at most B, the share of its tasks with duration in (0.729 D, D].
2. **Attacker gain.** A hung task is killed at the static deadline D anyway. The limiter can bring that kill forward to 0.729 D at
   the earliest, so the hung time it saves is at most 27.1% of the hung time.

Both bounds come from the same constant. The limiter's design fixes the exchange rate between what it can save and what it can
cost; detection quality changes which side of it an agent lands on, not the rate.

## Reading B before the limiter is switched on

B is a property of the agent's own history: the share of its past tasks that finished in the top 27.1% of its deadline. Hung tasks
sit far above D and do not enter B, so a runaway in the history does not hide the honest tail. An agent with B near zero can be
governed at almost no risk. An agent with B of 20 points (slow from the start) can lose up to 20 points of its tasks and should
be left alone, or governed with the cost budget below.

## The cost budget (round 17)

`TrapPolicy(cost_budget=b)` replaces the fixed 0.729 floor with an agent-specific one: a deadline tightening never goes below the
agent's own (1 - b) quantile of completed durations (b in 1%, 5%, 10%, 25%, 50%). The honest cost is then at most
about b, by the agent's own history, in addition to the cap. Nothing tightens until five slices of quantiles are known. The
trade is stated: a slow-completing agent keeps a deadline near D and gives up most of the saving, and an attacker can keep its
own deadline high by completing slowly, which leaves it exactly where the static deadline leaves it.

## Evidence

- Simulated rosters, six, 1,344 honest agent-runs: 0 violations; closest slack 0.012 points (round 16 scratch test).
- Real tool-call latencies, TraceLab release v0.0.1, 288 cells: 0 violations; honest cost equals the bound in several cells
  (`docs/TRACE_TEST_PLAN.md`, round 16). Spearman 0.92 between bound and cost.
- Cost budget, round 17: honest cost down 55% to 74% on real latencies (held-out stretches included), slow-from-start agents 18.5 to 1.2 points in simulation; the price is 4.3 points of saved hung time on sustained runaways and 7.3 on the inflate-first attacker (`docs/TRACE_TEST_PLAN.md`).
- Attacker side: across all 15 trace rounds the largest saved share of hung time is 27.1% (round 15, a runaway episode).

## Limits

- The certificate bounds tasks the limiter kills; it does not bound the work lost to a rerun (net time, `docs/RED_TEAM.md` T2).
- It assumes no operator has granted a loosening. A grant moves the starting point; the same arithmetic then applies from it.
- B comes from history. If the agent's durations shift upward after B is read, the bound moves with them; the budget re-reads the
  history every slice and takes the larger of its long-run and recent estimates, so a slowdown raises the floor within a few slices
  and a recovery lowers it slowly. A step change inside one slice is not covered.
- It covers the deadline limit. Token and memory limits have the same shape, but this round measured only the deadline.
- The certificate is per agent identity. An attacker that changes identity restarts at the default limits; an identity change every 6 slices cut the saving from about 24% to 11.4% (round 18). The honest-cost bound is unaffected.
- An attacker that completes slowly keeps its own deadline near the old one under the cost budget and gives up the saving (24.3% to 1.0%, round 18); it is then no worse off than the static deadline.
- Everything measured is simulation or replay. There is no live traffic and no runaway label in the real data.
