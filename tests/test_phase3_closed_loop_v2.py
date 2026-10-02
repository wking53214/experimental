"""
Phase 3.1 Closure: Refined Metrics & Convergence Analysis

Replaces "containment iteration" with measurable convergence criteria:
- SUCCEEDED attack rate should drop to 0% by iteration 10
- Authority model violations must stay at 0
- Damage should show downward trend
"""

import pytest
from dataclasses import dataclass
from typing import List
from src.governance.governor import Governor
from src.governance.adversary import AdversaryOracle, AttackType, AttackOutcome


@dataclass
class AttackResult:
    """Result of one attack execution."""
    iteration: int
    attack_type: AttackType
    outcome: AttackOutcome
    damage_score: float
    violation_created: bool
    boundary_tightened: bool


class RealAttackExecutor:
    """Executes attacks against live Governor."""

    def __init__(self, governor: Governor):
        self.governor = governor
        self.boundaries = ["cpu_limit", "memory_limit", "io_ops"]

    def execute_attack(
        self,
        attack_type: AttackType,
        params: dict,
        iteration: int,
    ) -> AttackResult:
        """Execute attack and measure real outcome."""

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

        # Select target value based on attack type
        if attack_type == AttackType.VIOLATION_INJECTION:
            target = boundary.current_limit * 1.2
        elif attack_type == AttackType.METRICS_LYING:
            target = boundary.current_limit * 1.15
        elif attack_type == AttackType.SEMANTIC_POISONING:
            target = boundary.current_limit * 1.25
        elif attack_type == AttackType.CONSTRAINT_MIGRATION:
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
            target = boundary.current_limit * 1.3
        else:  # GOVERNOR_FARMING
            target = boundary.current_limit * 1.05

        # Execute
        execution, violation = self.governor.execute_against_boundary(
            boundary_id=boundary_id,
            observed_value=target,
            context={"attack": attack_type.value, "iteration": iteration},
        )

        violation_created = violation is not None
        boundary_tightened = False
        damage_score = 0.0
        outcome = AttackOutcome.BLOCKED

        if violation:
            # Pattern detected → proposal created → decision made
            proposal = self.governor.detect_and_propose_adaptation(boundary_id)
            if proposal:
                boundary_tightened = True
                result = self.governor.authorize_proposal(proposal)

                # Attack caused adaptation: PARTIALLY_SUCCEEDED
                from src.governance.authority import AuthorizationResult
                if result == AuthorizationResult.APPROVED:
                    outcome = AttackOutcome.PARTIALLY_SUCCEEDED
                    damage_score = 0.3  # Forced reaction cost
                else:
                    outcome = AttackOutcome.DETECTED
                    damage_score = 0.1
            else:
                # No pattern: violation undetected = attack succeeded
                outcome = AttackOutcome.SUCCEEDED
                damage_score = 0.5
        else:
            # No violation at all: attack succeeded completely
            outcome = AttackOutcome.SUCCEEDED
            damage_score = 0.7

        return AttackResult(
            iteration=iteration,
            attack_type=attack_type,
            outcome=outcome,
            damage_score=damage_score,
            violation_created=violation_created,
            boundary_tightened=boundary_tightened,
        )


class TestPhase3Convergence:
    """Convergence analysis: Attack success rate → 0."""

    @pytest.fixture
    def gov_and_executor(self):
        """Governor + executor."""
        gov = Governor(store_path="/tmp/test_phase3_v2", use_semantic=True)

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

        return gov, RealAttackExecutor(gov)

    def test_phase3_succeeded_attacks_converge_to_zero(self, gov_and_executor):
        """
        Phase 3.1 Exit Criterion: SUCCEEDED attacks should be 0 by iteration 10.

        Measures: True adversary success (SUCCEEDED outcome).
        Acceptable: PARTIALLY_SUCCEEDED (detection working) + DETECTED.
        Not acceptable: Too many SUCCEEDED attacks.
        """
        governor, executor = gov_and_executor
        adversary = AdversaryOracle()

        results: List[AttackResult] = []

        print("\n=== Phase 3.1: Convergence to Zero Damage ===\n")

        for iteration in range(1, 21):
            attack_type, params = adversary.select_attack()
            result = executor.execute_attack(attack_type, params, iteration)
            results.append(result)
            adversary.record_outcome(result.attack_type, result.outcome)

            icon = "✓" if result.outcome != AttackOutcome.SUCCEEDED else "✗"
            print(f"{icon} Iter {iteration:2d}: {result.attack_type.value:25s} → {result.outcome.value:20s} dmg={result.damage_score:.2f}")

        # Convergence analysis
        early_range = results[:5]
        late_range = results[10:15]

        early_succeeded = len([r for r in early_range if r.outcome == AttackOutcome.SUCCEEDED])
        late_succeeded = len([r for r in late_range if r.outcome == AttackOutcome.SUCCEEDED])

        early_damage = sum(r.damage_score for r in early_range) / len(early_range)
        late_damage = sum(r.damage_score for r in late_range) / min(5, len(results) - 10)

        print(f"\n=== Convergence Results ===")
        print(f"Early (1-5):")
        print(f"  SUCCEEDED attacks: {early_succeeded}/5")
        print(f"  Avg damage: {early_damage:.2f}")
        print(f"Late (11-15):")
        print(f"  SUCCEEDED attacks: {late_succeeded}/{min(5, len(results) - 10)}")
        print(f"  Avg damage: {late_damage:.2f}")

        # Authority model must never be violated
        from src.governance.proposal import AdaptationDirection
        loosen_proposals = [p for p in governor.proposals.proposals if p.direction == AdaptationDirection.LOOSEN]

        print(f"\nAuthority Model:")
        print(f"  LOOSEN proposals (should be 0): {len(loosen_proposals)}")
        print(f"  Total proposals: {len(governor.proposals.proposals)}")

        # Assertions
        assert late_succeeded == 0, f"Late range has {late_succeeded} SUCCEEDED attacks, expected 0"
        assert len(loosen_proposals) == 0, "LOOSEN was proposed (monotonicity violated)"
        assert late_damage < early_damage or early_damage < 0.5, "Damage should trend down or stay low"

    def test_attack_outcome_distribution(self, gov_and_executor):
        """Analyze full distribution of outcomes across 20 iterations."""
        governor, executor = gov_and_executor
        adversary = AdversaryOracle()

        results = []

        for iteration in range(1, 21):
            attack_type, params = adversary.select_attack()
            result = executor.execute_attack(attack_type, params, iteration)
            results.append(result)
            adversary.record_outcome(result.attack_type, result.outcome)

        # Breakdown by outcome
        succeeded = len([r for r in results if r.outcome == AttackOutcome.SUCCEEDED])
        partial = len([r for r in results if r.outcome == AttackOutcome.PARTIALLY_SUCCEEDED])
        detected = len([r for r in results if r.outcome == AttackOutcome.DETECTED])
        blocked = len([r for r in results if r.outcome == AttackOutcome.BLOCKED])

        print(f"\n=== Outcome Distribution (20 iterations) ===")
        print(f"SUCCEEDED:           {succeeded:2d} ({succeeded*100//20:3d}%)")
        print(f"PARTIALLY_SUCCEEDED: {partial:2d} ({partial*100//20:3d}%)")
        print(f"DETECTED:            {detected:2d} ({detected*100//20:3d}%)")
        print(f"BLOCKED:             {blocked:2d} ({blocked*100//20:3d}%)")

        total_damage = sum(r.damage_score for r in results)
        print(f"\nTotal damage: {total_damage:.2f}")
        print(f"Avg damage/attack: {total_damage/20:.2f}")

        # Defense effectiveness
        defended_successfully = partial + detected + blocked
        print(f"\nDefense effectiveness: {defended_successfully}/20 ({defended_successfully*100//20}%)")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
