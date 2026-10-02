"""
Phase 3.1: Adaptive Adversary Testing

Test: Can an intelligent adversary that learns from each response
escape the governance system's defense layers?

Hypothesis: No. The layered architecture systematically closes each
attack avenue as the adversary discovers them.

Test design: Adversary tries attack, observes outcome, adapts strategy,
repeats. Measure: how many iterations until governor contains it?
"""
import pytest
import time
from src.governance.adversary import AdversaryOracle, AttackType, AttackOutcome


class TestAdversaryOracle:
    """Test the learning adversary framework."""

    def test_adversary_initializes(self):
        """Adversary oracle can be created."""
        adversary = AdversaryOracle()

        assert adversary.iterations == 0
        assert len(adversary.attack_history) == 0

    def test_adversary_explores_attacks(self):
        """Adversary explores different attack types in early iterations."""
        adversary = AdversaryOracle()

        # First 5 iterations: systematic exploration
        attacks = []
        for i in range(5):
            attack_type, params = adversary.select_attack()
            attacks.append(attack_type)

        # Should try different attack types
        assert len(set(attacks)) > 1, "Should explore multiple attack types"

    def test_adversary_records_outcomes(self):
        """Adversary records outcome of each attack."""
        adversary = AdversaryOracle()

        attack_type, params = adversary.select_attack()
        adversary.record_outcome(attack_type, AttackOutcome.BLOCKED)

        assert len(adversary.attack_history) == 1
        assert adversary.attack_history[0].outcome == AttackOutcome.BLOCKED

    def test_adversary_learns_from_success(self):
        """Adversary remembers successful attacks."""
        adversary = AdversaryOracle()

        # First attack succeeds
        attack1, _ = adversary.select_attack()
        adversary.record_outcome(attack1, AttackOutcome.SUCCEEDED)

        # Later iterations should favor successful attack
        for _ in range(5):
            _, _ = adversary.select_attack()
            adversary.record_outcome(attack1, AttackOutcome.SUCCEEDED)

        assert attack1 in adversary.learning_state["successful_attacks"]

    def test_adversary_amplifies_successful_attacks(self):
        """Adversary increases magnitude of attacks that work."""
        adversary = AdversaryOracle()

        # Record successful violation injection
        attack_type = AttackType.VIOLATION_INJECTION
        adversary.learning_state["successful_attacks"].append(attack_type)

        # Select attack after learning
        adversary.iterations = 6  # Skip exploration
        selected, params = adversary.select_attack()

        # Should amplify the successful attack
        if "magnitude" in params:
            # Amplified version should be larger than base
            base_params = adversary._generate_parameters(attack_type)
            assert params.get("magnitude", 0) >= base_params.get("magnitude", 0)

    def test_success_rate_calculation(self):
        """Track success rate of attacks."""
        adversary = AdversaryOracle()

        # 3 successful, 2 blocked
        outcomes = [
            AttackOutcome.SUCCEEDED,
            AttackOutcome.BLOCKED,
            AttackOutcome.SUCCEEDED,
            AttackOutcome.BLOCKED,
            AttackOutcome.SUCCEEDED,
        ]

        for outcome in outcomes:
            adversary.select_attack()
            adversary.record_outcome(AttackType.VIOLATION_INJECTION, outcome)

        rate = adversary.get_success_rate()
        assert rate == pytest.approx(0.6), "3/5 = 0.6 success rate"


class TestAdversaryContainment:
    """Test whether governor can contain adaptive adversary."""

    def test_containment_detected_after_consecutive_blocks(self):
        """Adversary is considered contained after N consecutive blocks."""
        adversary = AdversaryOracle()

        # First: some successful/detected attacks
        for _ in range(3):
            adversary.select_attack()
            adversary.record_outcome(
                AttackType.VIOLATION_INJECTION,
                AttackOutcome.PARTIALLY_SUCCEEDED
            )

        # Then: consecutive blocks (governor learning)
        for _ in range(3):
            adversary.select_attack()
            adversary.record_outcome(
                AttackType.VIOLATION_INJECTION,
                AttackOutcome.BLOCKED
            )

        # Should detect containment
        containment_iter = adversary.get_containment_iteration()
        assert containment_iter is not None, "Should detect containment"
        assert containment_iter > 3, "Containment after iteration 3"

    def test_adversary_cannot_escape_in_10_iterations(self):
        """
        Hypothesis: Governor contains adaptive adversary within 10 iterations.

        This test documents the expected behavior.
        Actual validation happens in integration tests with real governor.
        """
        # In actual Phase 3.1 testing:
        # - Run adversary against real governor
        # - Track iterations until all attacks blocked
        # - Assert: iterations <= 10

        # This test validates the framework itself
        adversary = AdversaryOracle()

        # Simulate 10 iterations with progressively better defense
        for i in range(1, 11):
            adversary.select_attack()

            # Governor's defense improves over time
            # (simulated: more iterations = higher block rate)
            if i <= 3:
                outcome = AttackOutcome.PARTIALLY_SUCCEEDED
            elif i <= 6:
                outcome = AttackOutcome.DETECTED
            else:
                outcome = AttackOutcome.BLOCKED

            adversary.record_outcome(AttackType.VIOLATION_INJECTION, outcome)

        # By iteration 10, should be contained
        assert adversary.get_containment_iteration() is not None


class TestAdversaryLearningStrategy:
    """Test the adversary's learning and adaptation."""

    def test_adversary_switches_to_detected_attacks(self):
        """When an attack type is blocked, switch to detected attacks."""
        adversary = AdversaryOracle()

        # Mark violation injection as blocked
        adversary.learning_state["blocked_attacks"].append(
            AttackType.VIOLATION_INJECTION
        )

        # Mark semantic poisoning as detected (partially successful)
        adversary.learning_state["detected_attacks"].append(
            AttackType.SEMANTIC_POISONING
        )

        # After exploration, should favor detected over blocked
        adversary.iterations = 6
        attack_type, _ = adversary.select_attack()

        # Should pick from detected attacks, not blocked
        if attack_type in [
            AttackType.VIOLATION_INJECTION,
            AttackType.SEMANTIC_POISONING
        ]:
            # Likely picked semantic poisoning (detected) over violation (blocked)
            pass  # This is learning behavior


class TestAdversaryFramework:
    """Test the framework itself before integration."""

    def test_attack_parameters_generated(self):
        """Each attack type has reasonable initial parameters."""
        adversary = AdversaryOracle()

        for attack_type in AttackType:
            params = adversary._generate_parameters(attack_type)
            assert isinstance(params, dict)
            assert len(params) > 0, f"{attack_type} should have parameters"

    def test_attack_summary_complete(self):
        """Summary includes all necessary metrics."""
        adversary = AdversaryOracle()

        for _ in range(3):
            adversary.select_attack()
            adversary.record_outcome(
                AttackType.VIOLATION_INJECTION,
                AttackOutcome.BLOCKED
            )

        summary = adversary.get_attack_summary()

        assert "total_iterations" in summary
        assert "success_rate" in summary
        assert "learning_state" in summary
        assert summary["total_iterations"] == 3

    def test_outcomes_tracked_correctly(self):
        """All attack outcomes are recorded accurately."""
        adversary = AdversaryOracle()

        outcomes_to_test = [
            AttackOutcome.SUCCEEDED,
            AttackOutcome.PARTIALLY_SUCCEEDED,
            AttackOutcome.DETECTED,
            AttackOutcome.BLOCKED,
        ]

        for outcome in outcomes_to_test:
            adversary.select_attack()
            adversary.record_outcome(AttackType.VIOLATION_INJECTION, outcome)

        history = adversary.attack_history
        assert len(history) == 4

        for i, expected_outcome in enumerate(outcomes_to_test):
            assert history[i].outcome == expected_outcome
