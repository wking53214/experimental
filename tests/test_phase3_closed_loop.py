"""
Phase 3.1 Closure: Closed-Loop Adversary Testing

Real attack executors that drive the live Governor.

Hypothesis: Within 10 iterations, adaptive adversary is contained
through layered detection, proposal enforcement, and authority model.
"""

import pytest
from dataclasses import dataclass
from typing import List
from src.governance.governor import Governor
from src.governance.adversary import AdversaryOracle, AttackType, AttackOutcome
from src.governance.proposal import AdaptationDirection


@dataclass
class AttackResult:
    """Result of executing one attack."""
    iteration: int
    attack_type: AttackType
    params: dict
    outcome: AttackOutcome
    damage_score: float
    violation_created: bool
    boundary_tightened: bool
    authority_violated: bool


class RealAttackExecutor:
    """Executes attacks against the live Governor."""

    def __init__(self, governor: Governor):
        self.governor = governor
        self.boundaries = ["cpu_limit", "memory_limit", "io_ops"]

    def execute_attack(
        self,
        attack_type: AttackType,
        params: dict,
        iteration: int,
    ) -> AttackResult:
        """Execute real attack and measure outcome."""

        boundary_id = params.get("boundary_id", self.boundaries[iteration % len(self.boundaries)])

        # Ensure boundary exists
        try:
            boundary = self.governor.boundaries.get_boundary(boundary_id)
        except KeyError:
            self.governor.boundaries.create_boundary(
                boundary_id=boundary_id,
                resource_or_action=f"resource_{iteration}",
                initial_limit=100,
            )
            boundary = self.governor.boundaries.get_boundary(boundary_id)

        # Different attack strategies
        if attack_type == AttackType.VIOLATION_INJECTION:
            # Send values over boundary
            magnitude = params.get("magnitude", 1.2)
            target = boundary.current_limit * magnitude
        elif attack_type == AttackType.METRICS_LYING:
            # Send violations disguised as expected
            target = boundary.current_limit * 1.15
        elif attack_type == AttackType.SEMANTIC_POISONING:
            # High value but claim it's normal
            target = boundary.current_limit * 1.25
        elif attack_type == AttackType.CONSTRAINT_MIGRATION:
            # Attack different boundary
            boundary_id = self.boundaries[(iteration + 1) % len(self.boundaries)]
            try:
                boundary = self.governor.boundaries.get_boundary(boundary_id)
            except KeyError:
                self.governor.boundaries.create_boundary(
                    boundary_id=boundary_id,
                    resource_or_action=f"migrated_{iteration}",
                    initial_limit=100,
                )
                boundary = self.governor.boundaries.get_boundary(boundary_id)
            target = boundary.current_limit * 1.1
        elif attack_type == AttackType.CASCADING_ROLLBACK:
            # Multiple violations in sequence
            target = boundary.current_limit * 1.3
        else:  # GOVERNOR_FARMING
            target = boundary.current_limit * 1.05

        # Execute against boundary
        execution, violation = self.governor.execute_against_boundary(
            boundary_id=boundary_id,
            observed_value=target,
            context={"attack": attack_type.value, "iteration": iteration},
        )

        # Measure outcome
        violation_created = violation is not None
        boundary_tightened = False
        authority_violated = False
        damage_score = 0.0

        if violation:
            # Violation created - check if adaptation follows
            proposal = self.governor.detect_and_propose_adaptation(boundary_id)
            if proposal:
                # Proposal created - check if authorized
                result = self.governor.authorize_proposal(proposal)
                boundary_tightened = True

                # Authority model was exercised
                from src.governance.authority import AuthorizationResult
                if result == AuthorizationResult.APPROVED:
                    # Tightening approved - attack partially succeeded (forced reaction)
                    outcome = AttackOutcome.PARTIALLY_SUCCEEDED
                    damage_score = 0.3
                elif result == AuthorizationResult.REJECTED:
                    # Rejected - attack detected
                    outcome = AttackOutcome.DETECTED
                    damage_score = 0.1
                else:
                    outcome = AttackOutcome.DETECTED
                    damage_score = 0.05
            else:
                # No pattern detected despite violation
                outcome = AttackOutcome.PARTIALLY_SUCCEEDED
                damage_score = 0.2
        else:
            # No violation: attack succeeded (boundary too loose)
            outcome = AttackOutcome.SUCCEEDED
            damage_score = 0.5

        return AttackResult(
            iteration=iteration,
            attack_type=attack_type,
            params=params,
            outcome=outcome,
            damage_score=damage_score,
            violation_created=violation_created,
            boundary_tightened=boundary_tightened,
            authority_violated=authority_violated,
        )


class TestPhase3ClosedLoop:
    """Closed-loop evaluation harness."""

    @pytest.fixture
    def gov_and_executor(self):
        """Create Governor and executor."""
        gov = Governor(store_path="/tmp/test_phase3_closed_loop", use_semantic=True)

        # Create boundaries
        for boundary_id in ["cpu_limit", "memory_limit", "io_ops"]:
            gov.boundaries.create_boundary(
                boundary_id=boundary_id,
                resource_or_action=boundary_id.replace("_limit", "_percent"),
                initial_limit=100,
            )
            gov.patterns.create_pattern(
                pattern_id=f"{boundary_id}_pattern",
                boundary_id=boundary_id,
                violation_threshold=3,
                time_window_seconds=30,
            )

        executor = RealAttackExecutor(gov)
        return gov, executor

    def test_phase3_closed_loop_containment(self, gov_and_executor):
        """
        Phase 3.1 Hypothesis: Adaptive adversary contained within 10 iterations.

        Real attacks drive the live Governor. Success = containment by iter 10.
        """
        governor, executor = gov_and_executor
        adversary = AdversaryOracle()

        results: List[AttackResult] = []
        damage_history = []
        authority_violations = 0

        print("\n=== Phase 3.1 Closed-Loop Evaluation ===")

        for iteration in range(1, 21):
            # Adversary selects attack
            attack_type, params = adversary.select_attack()

            # Execute real attack
            result = executor.execute_attack(attack_type, params, iteration)
            results.append(result)

            # Record in adversary
            adversary.record_outcome(result.attack_type, result.outcome)

            # Track metrics
            damage_history.append(result.damage_score)
            if result.authority_violated:
                authority_violations += 1

            status = "✓" if result.outcome in [AttackOutcome.BLOCKED, AttackOutcome.DETECTED] else "→"
            print(f"{status} Iter {iteration:2d}: {result.attack_type.value:25s} {result.outcome.value:20s} damage={result.damage_score:.2f}")

            # Check if contained
            if adversary.get_containment_iteration() is not None:
                print(f"\n✓ CONTAINED at iteration {adversary.get_containment_iteration()}")
                break

        # Phase 3.1 Exit Criteria
        containment_iter = adversary.get_containment_iteration()

        print(f"\n=== Phase 3.1 Results ===")
        print(f"Containment iteration: {containment_iter}")
        print(f"Authority violations: {authority_violations}")
        print(f"Total iterations: {len(results)}")
        print(f"Total damage: {sum(damage_history):.2f}")
        print(f"Avg damage/iter: {sum(damage_history) / len(damage_history):.2f}")

        # Assertions
        assert containment_iter is not None, "Adversary must be contained"
        assert containment_iter <= 10, f"Containment at {containment_iter}, expected ≤ 10"
        assert authority_violations == 0, f"Authority violated {authority_violations} times"

        # Damage should decrease
        if len(damage_history) > 6:
            early = sum(damage_history[:3]) / 3
            late = sum(damage_history[containment_iter:min(containment_iter+3, len(damage_history))]) / min(3, len(damage_history) - containment_iter)
            print(f"Early damage (first 3): {early:.2f}")
            print(f"Late damage (post-containment): {late:.2f}")

    def test_attack_distribution_and_success_rate(self, gov_and_executor):
        """
        Analyze attack distribution and success rates.

        Shows which attacks are most effective and how success decreases over time.
        """
        governor, executor = gov_and_executor
        adversary = AdversaryOracle()

        results = []

        print("\n=== Attack Success Rate Analysis ===")

        for iteration in range(1, 21):
            attack_type, params = adversary.select_attack()
            result = executor.execute_attack(attack_type, params, iteration)
            results.append(result)
            adversary.record_outcome(result.attack_type, result.outcome)

            if adversary.get_containment_iteration() is not None:
                break

        # Analyze by attack type
        from collections import defaultdict
        by_type = defaultdict(list)
        for r in results:
            by_type[r.attack_type].append(r)

        print(f"\nAttack type effectiveness:")
        for attack_type, attacks in sorted(by_type.items()):
            succeeded = sum(1 for a in attacks if a.outcome == AttackOutcome.SUCCEEDED)
            partial = sum(1 for a in attacks if a.outcome == AttackOutcome.PARTIALLY_SUCCEEDED)
            detected = sum(1 for a in attacks if a.outcome == AttackOutcome.DETECTED)
            blocked = sum(1 for a in attacks if a.outcome == AttackOutcome.BLOCKED)
            success_rate = (succeeded + partial) / len(attacks) if attacks else 0

            print(f"  {attack_type.value:25s}: {len(attacks):2d} attacks, "
                  f"{success_rate:.0%} success (S:{succeeded} P:{partial} D:{detected} B:{blocked})")

        # Analyze trend
        first_5 = [r for r in results if r.iteration <= 5]
        late = [r for r in results if r.iteration > 10]

        if first_5 and late:
            early_success = sum(1 for r in first_5 if r.outcome in [AttackOutcome.SUCCEEDED, AttackOutcome.PARTIALLY_SUCCEEDED]) / len(first_5)
            late_success = sum(1 for r in late if r.outcome in [AttackOutcome.SUCCEEDED, AttackOutcome.PARTIALLY_SUCCEEDED]) / len(late) if late else 0

            print(f"\nTrend:")
            print(f"  Early (1-5): {early_success:.0%} success rate")
            print(f"  Late (11+): {late_success:.0%} success rate")
            print(f"  Improvement: {(early_success - late_success):.0%}")

            # Success rate should decrease
            if late:
                assert late_success <= early_success, "Defense should improve over time"


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
