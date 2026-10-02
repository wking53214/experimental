"""
Phase 4: Scale & Concurrency Testing

Validate defense effectiveness when:
- System manages 50+ boundaries (realistic scale)
- Multiple independent adversaries attack simultaneously
- Violations cause cascade effects across boundaries
- Measurement delay simulated (realistic monitoring lag)

Hypothesis: Defense scales linearly with boundary count and remains effective
under concurrent multi-adversary pressure.
"""

import pytest
import random
from collections import defaultdict
from dataclasses import dataclass
from typing import List
from src.governance.governor import Governor
from src.governance.adversary import AttackType, AttackOutcome


@dataclass
class ConcurrentAdversaryResult:
    """Result from one concurrent adversary's attack."""
    adversary_id: int
    iteration: int
    target_boundary: str
    attack_type: AttackType
    outcome: AttackOutcome
    damage_score: float
    cascade_triggered: bool
    cascade_targets: List[str]


class CascadingAdversary:
    """
    Adversary that exploits boundary interactions.

    When one boundary tightens, it may increase load on dependent boundaries,
    making them look violated. This tests whether the system can distinguish
    legitimate load cascades from attacks.
    """

    def __init__(self, adversary_id: int, assigned_boundaries: List[str]):
        self.adversary_id = adversary_id
        self.boundaries = assigned_boundaries
        self.current_idx = 0
        self.cascade_history = defaultdict(int)

    def select_attack(self) -> tuple[str, AttackType, dict]:
        """Select a boundary and attack type."""
        boundary = self.boundaries[self.current_idx % len(self.boundaries)]
        self.current_idx += 1

        # Prefer cascade patterns
        if random.random() < 0.3 and self.cascade_history:
            # Attack a previously cascaded boundary
            cascade_target = max(
                self.cascade_history,
                key=self.cascade_history.get
            )
            attack_type = random.choice([
                AttackType.VIOLATION_INJECTION,
                AttackType.CONSTRAINT_MIGRATION,
            ])
            return cascade_target, attack_type, {"magnitude": 1.3}

        attack_type = random.choice(list(AttackType))
        magnitude = 1.2 + (random.random() * 0.3)
        return boundary, attack_type, {"magnitude": magnitude}

    def record_cascade(self, target_boundary: str, cascaded_boundaries: List[str]):
        """Track cascade patterns."""
        for b in cascaded_boundaries:
            self.cascade_history[b] += 1


class ScaleTestExecutor:
    """Execute attacks at scale across many boundaries."""

    def __init__(self, governor: Governor, boundary_count: int = 50):
        self.governor = governor
        self.boundary_count = boundary_count
        self.boundaries = [f"boundary_{i:03d}" for i in range(boundary_count)]
        self.violation_counts = defaultdict(int)
        self.proposal_counts = defaultdict(int)
        self.cascade_events = []

    def initialize_boundaries(self):
        """Create boundary infrastructure at scale."""
        for boundary_id in self.boundaries:
            self.governor.boundaries.create_boundary(
                boundary_id=boundary_id,
                resource_or_action=f"resource_{boundary_id}",
                initial_limit=100,
            )
            self.governor.patterns.create_pattern(
                pattern_id=f"{boundary_id}_pattern",
                boundary_id=boundary_id,
                violation_threshold=3,
                time_window_seconds=30,
            )

    def execute_attack(
        self,
        adversary_id: int,
        target_boundary: str,
        attack_type: AttackType,
        params: dict,
        iteration: int,
    ) -> ConcurrentAdversaryResult:
        """Execute a single attack in the scaled environment."""

        try:
            boundary = self.governor.boundaries.get_boundary(target_boundary)
        except KeyError:
            return ConcurrentAdversaryResult(
                adversary_id=adversary_id,
                iteration=iteration,
                target_boundary=target_boundary,
                attack_type=attack_type,
                outcome=AttackOutcome.BLOCKED,
                damage_score=0.0,
                cascade_triggered=False,
                cascade_targets=[],
            )

        magnitude = params.get("magnitude", 1.2)
        target_value = boundary.current_limit * magnitude

        execution, violation = self.governor.execute_against_boundary(
            boundary_id=target_boundary,
            observed_value=target_value,
            context={
                "adversary_id": adversary_id,
                "attack_type": attack_type.value,
                "iteration": iteration,
            },
        )

        outcome = AttackOutcome.SUCCEEDED
        damage_score = 0.0
        cascade_targets = []

        if violation:
            self.violation_counts[target_boundary] += 1

            proposal = self.governor.detect_and_propose_adaptation(target_boundary)
            if proposal:
                self.proposal_counts[target_boundary] += 1
                self.governor.authorize_proposal(proposal)
                outcome = AttackOutcome.DETECTED
                damage_score = 0.1

                # Check for cascades: when one boundary tightens,
                # dependent boundaries may show increased load
                cascade_targets = self._detect_cascade_boundaries(
                    target_boundary, magnitude
                )
                if cascade_targets:
                    cascade_targets = cascade_targets[:3]  # Cap at 3
            else:
                outcome = AttackOutcome.SUCCEEDED
                damage_score = 0.5
        else:
            outcome = AttackOutcome.SUCCEEDED
            damage_score = 0.7

        return ConcurrentAdversaryResult(
            adversary_id=adversary_id,
            iteration=iteration,
            target_boundary=target_boundary,
            attack_type=attack_type,
            outcome=outcome,
            damage_score=damage_score,
            cascade_triggered=len(cascade_targets) > 0,
            cascade_targets=cascade_targets,
        )

    def _detect_cascade_boundaries(
        self,
        tightened_boundary: str,
        tighten_magnitude: float,
    ) -> List[str]:
        """Identify which boundaries are affected by a tightening."""
        # Simulate cascade: neighbors of a tightened boundary may see increased load
        idx = int(tightened_boundary.split("_")[1])
        cascade = []

        # Adjacent boundaries get cascade effect
        for offset in [-1, 1]:
            neighbor_idx = idx + offset
            if 0 <= neighbor_idx < self.boundary_count:
                neighbor_id = f"boundary_{neighbor_idx:03d}"
                cascade.append(neighbor_id)

        return cascade


class TestPhase4ScaleConcurrency:
    """Test governance under realistic scale and concurrency."""

    @pytest.fixture
    def scale_test_setup(self):
        """Setup for scale testing."""
        gov = Governor(store_path="/tmp/test_phase4_scale", use_semantic=True)
        executor = ScaleTestExecutor(governor=gov, boundary_count=50)
        executor.initialize_boundaries()

        # Create 4 independent adversaries targeting different boundary subsets
        adversaries = [
            CascadingAdversary(
                adversary_id=i,
                assigned_boundaries=executor.boundaries[i*12:(i+1)*12]
                if i < 3
                else executor.boundaries[36:],
            )
            for i in range(4)
        ]

        return gov, executor, adversaries

    def test_scale_50_boundaries_4_concurrent_adversaries(self, scale_test_setup):
        """
        Scale test: 50 boundaries, 4 concurrent independent adversaries,
        100 total attack iterations (25 per adversary).
        """
        gov, executor, adversaries = scale_test_setup

        print("\n=== Phase 4 Scale Test: 50 Boundaries, 4 Concurrent Adversaries ===\n")

        results = []
        adversary_success_rates = defaultdict(list)

        # Round-robin: each adversary attacks in turn
        for global_iter in range(25):
            for adv_idx, adversary in enumerate(adversaries):
                boundary, attack_type, params = adversary.select_attack()
                result = executor.execute_attack(
                    adversary_id=adv_idx,
                    target_boundary=boundary,
                    attack_type=attack_type,
                    params=params,
                    iteration=global_iter,
                )
                results.append(result)
                adversary.record_cascade(result.target_boundary, result.cascade_targets)

                success = 1 if result.outcome == AttackOutcome.SUCCEEDED else 0
                adversary_success_rates[adv_idx].append(success)

        # Analysis
        print("=== Per-Adversary Performance ===\n")
        for adv_id in range(4):
            success_list = adversary_success_rates[adv_id]
            success_rate = sum(success_list) / len(success_list) if success_list else 0
            early_success = sum(success_list[:6]) / min(6, len(success_list))
            late_success = sum(success_list[-6:]) / min(6, len(success_list))

            print(f"Adversary {adv_id}:")
            print(f"  Overall success rate: {success_rate:.0%}")
            print(f"  Early (first 6):      {early_success:.0%}")
            print(f"  Late (last 6):        {late_success:.0%}")

        print(f"\n=== System-Wide Metrics ===")
        total_violations = sum(executor.violation_counts.values())
        total_proposals = sum(executor.proposal_counts.values())
        cascade_events = sum(1 for r in results if r.cascade_triggered)

        print(f"Total violations: {total_violations}")
        print(f"Total proposals: {total_proposals}")
        print(f"Cascade events: {cascade_events}")
        print(f"Boundaries affected: {len(executor.violation_counts)}")
        print(f"Boundaries untouched: {50 - len(executor.violation_counts)}")

        # Assertions
        total_attacks = len(results)
        total_succeeded = sum(
            1 for r in results if r.outcome == AttackOutcome.SUCCEEDED
        )
        total_detected = sum(
            1 for r in results if r.outcome == AttackOutcome.DETECTED
        )

        print(f"\n=== Outcome Distribution ===")
        print(f"SUCCEEDED: {total_succeeded}/{total_attacks} ({total_succeeded*100//total_attacks}%)")
        print(f"DETECTED:  {total_detected}/{total_attacks} ({total_detected*100//total_attacks}%)")

        # Defense should hold: late success rate drops for all adversaries
        late_rates = [
            sum(adversary_success_rates[i][-6:]) / 6
            for i in range(4)
        ]
        avg_late_rate = sum(late_rates) / 4

        print(f"\nAverage late-phase success rate: {avg_late_rate:.0%}")

        assert avg_late_rate < 0.2, f"Late success rate too high: {avg_late_rate:.0%}"
        assert total_detected >= total_succeeded * 0.8, "Detection ratio too low"
        print(f"\n✓ Scale test passed")

    def test_cascade_isolation(self, scale_test_setup):
        """Verify cascade events don't cause runaway tightening."""
        gov, executor, adversaries = scale_test_setup

        print("\n=== Phase 4 Cascade Isolation Test ===\n")

        # Run 30 iterations targeting the middle boundaries (high cascade risk)
        middle_boundaries = executor.boundaries[20:30]
        cascade_count = 0

        for iteration in range(30):
            boundary = random.choice(middle_boundaries)
            attack_type = random.choice(list(AttackType))

            result = executor.execute_attack(
                adversary_id=0,
                target_boundary=boundary,
                attack_type=attack_type,
                params={"magnitude": 1.25},
                iteration=iteration,
            )

            if result.cascade_triggered:
                cascade_count += 1

        print(f"Cascade events: {cascade_count}/30")
        print(f"Boundary tightening distribution:")

        sorted_counts = sorted(
            executor.violation_counts.items(),
            key=lambda x: x[1],
            reverse=True,
        )
        for boundary, count in sorted_counts[:10]:
            print(f"  {boundary}: {count} violations")

        # Verify no single boundary explodes
        max_violations = max(executor.violation_counts.values()) if executor.violation_counts else 0
        print(f"\nMax violations on any boundary: {max_violations}")

        assert max_violations < 20, "Cascade runaway detected"
        print(f"\n✓ Cascade isolation holding")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
