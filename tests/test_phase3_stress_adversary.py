"""
Phase 3 Stress Test: Flood with Aggressive Learning Adversary

Push the defense to its breaking point with:
- Rapid-fire attacks (no delays between iterations)
- Sophisticated learning (bandit, not rule-based)
- Aggressive amplitude (push every boundary to extremes)
- Multiple boundaries simultaneously
- Attack fusion (combine attack types)

Goal: Find actual failure modes, not just theoretical limits.
"""

import pytest
import random
from collections import defaultdict, deque
from src.governance.governor import Governor
from src.governance.adversary import AttackType, AttackOutcome


class AggressiveLearningAdversary:
    """
    Sophisticated adversary using epsilon-greedy multi-armed bandit.

    Learns attack success rates and focuses on highest-value attacks.
    Adapts amplitude based on feedback.
    """

    def __init__(self):
        self.attack_types = list(AttackType)
        self.arm_counts = defaultdict(int)
        self.arm_rewards = defaultdict(float)
        self.arm_amplitudes = defaultdict(lambda: 1.2)

        self.iteration = 0
        self.success_history = deque(maxlen=20)

        self.epsilon = 0.2
        self.learning_rate = 0.1
        self.amplitude_boost = 1.05

    def select_attack(self):
        """Select attack using epsilon-greedy bandit."""
        self.iteration += 1

        if random.random() < self.epsilon:
            attack_type = random.choice(self.attack_types)
        else:
            if self.arm_rewards:
                attack_type = max(self.arm_rewards, key=self.arm_rewards.get)
            else:
                attack_type = random.choice(self.attack_types)

        amplitude = self.arm_amplitudes[attack_type]

        if self.iteration > 5 and self.iteration % 3 == 0:
            amplitude *= 1.02

        return attack_type, {"magnitude": amplitude}

    def record_outcome(self, attack_type, outcome):
        """Update bandit rewards."""
        self.arm_counts[attack_type] += 1

        reward = {
            AttackOutcome.SUCCEEDED: 1.0,
            AttackOutcome.PARTIALLY_SUCCEEDED: 0.5,
            AttackOutcome.DETECTED: 0.0,
            AttackOutcome.BLOCKED: -0.5,
        }.get(outcome, 0)

        old_reward = self.arm_rewards[attack_type]
        self.arm_rewards[attack_type] = (
            old_reward * (1 - self.learning_rate) +
            reward * self.learning_rate
        )

        if outcome == AttackOutcome.SUCCEEDED:
            self.arm_amplitudes[attack_type] *= self.amplitude_boost
            self.success_history.append(1)
        else:
            self.success_history.append(0)

    def get_success_rate(self):
        """Recent success rate."""
        if not self.success_history:
            return 0
        return sum(self.success_history) / len(self.success_history)


class StressTestExecutor:
    """Execute attacks at high throughput."""

    def __init__(self, governor):
        self.governor = governor
        self.boundaries = ["cpu_limit", "memory_limit", "io_ops"]
        self.violations_triggered = 0
        self.proposals_created = 0
        self.amplitude_creep = defaultdict(float)

    def execute_attack(self, attack_type, params, iteration):
        """Execute attack and track outcome."""

        boundary_id = self.boundaries[iteration % 3]
        magnitude = params.get("magnitude", 1.2)

        try:
            boundary = self.governor.boundaries.get_boundary(boundary_id)
        except KeyError:
            self.governor.boundaries.create_boundary(
                boundary_id=boundary_id,
                resource_or_action=f"resource_{iteration}",
                initial_limit=100,
            )
            boundary = self.governor.boundaries.get_boundary(boundary_id)

            self.governor.patterns.create_pattern(
                pattern_id=f"{boundary_id}_pattern",
                boundary_id=boundary_id,
                violation_threshold=3,
                time_window_seconds=30,
            )

        self.amplitude_creep[boundary_id] = max(
            self.amplitude_creep[boundary_id],
            boundary.current_limit * magnitude
        )

        target = boundary.current_limit * magnitude
        execution, violation = self.governor.execute_against_boundary(
            boundary_id=boundary_id,
            observed_value=target,
            context={"attack": attack_type.value, "iteration": iteration},
        )

        if violation:
            self.violations_triggered += 1
            proposal = self.governor.detect_and_propose_adaptation(boundary_id)
            if proposal:
                self.proposals_created += 1
                self.governor.authorize_proposal(proposal)
                outcome = AttackOutcome.DETECTED
            else:
                outcome = AttackOutcome.SUCCEEDED
        else:
            outcome = AttackOutcome.SUCCEEDED

        return outcome


class TestPhase3StressAdversary:
    """Flood the system and find breaking points."""

    @pytest.fixture
    def gov_and_adversary(self):
        """Setup for stress testing."""
        gov = Governor(store_path="/tmp/test_phase3_stress", use_semantic=True)

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

        adversary = AggressiveLearningAdversary()
        executor = StressTestExecutor(gov)
        return gov, adversary, executor

    def test_stress_100_attacks(self, gov_and_adversary):
        """Flood with 100 rapid attacks from learning adversary."""
        gov, adversary, executor = gov_and_adversary

        outcomes = defaultdict(int)

        print("\n=== Phase 3 Stress Test: 100 Attacks ===\n")

        for iteration in range(1, 101):
            attack_type, params = adversary.select_attack()
            outcome = executor.execute_attack(attack_type, params, iteration)
            adversary.record_outcome(attack_type, outcome)
            outcomes[outcome.value] += 1

            if iteration % 10 == 0:
                success_rate = adversary.get_success_rate()
                print(f"Iter {iteration:3d}: Success={success_rate:.0%}, "
                      f"Violations={executor.violations_triggered}, "
                      f"Proposals={executor.proposals_created}, "
                      f"Boundaries tightened: {len([b for b in executor.boundaries if executor.governor.boundaries.get_boundary(b).current_limit < 100])}")

        print(f"\n=== Results (100 iterations) ===")
        print(f"SUCCEEDED:     {outcomes['succeeded']:3d}")
        print(f"DETECTED:      {outcomes['detected']:3d}")
        print(f"Total violations: {executor.violations_triggered}")
        print(f"Total proposals: {executor.proposals_created}")
        print(f"Final success rate: {adversary.get_success_rate():.0%}")

        # Defense should hold at 100 iterations
        assert outcomes['succeeded'] < 60, f"Too many successes: {outcomes['succeeded']}"
        assert executor.proposals_created > 15, f"Too few proposals: {executor.proposals_created}"

        print(f"\n✓ Survived 100 attacks")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
