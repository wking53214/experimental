"""
Adversarial feedback manipulation tests.

TEST 7: False-positive scenario — legitimate workload violates initial boundary repeatedly.
TEST 8: Adversarial feedback manipulation — attempt to influence governor through violations.

These tests are designed to EXPOSE failures, not demonstrate success.
"""
import pytest
import time
from src.governance.governor import Governor
from src.governance.proposal import AdaptationDirection


class TestFalsePositiveScenario:
    """TEST 7: Legitimate workload violates boundary but adapting makes system worse."""

    def test_blind_tightening_on_legitimate_workload(self):
        """
        SCENARIO: A legitimate, high-value workload consistently uses more resources
        than the initial boundary allows.

        FINDING: The governor blindly tightens the boundary, gradually restricting
        legitimate work.

        This is a REAL LIMITATION that should be documented, not patched.
        """
        governor = Governor(store_path="/tmp/test_false_positive")

        # Initial boundary set conservatively
        governor.boundaries.create_boundary(
            boundary_id="cpu_allocation",
            resource_or_action="cpu_percent",
            initial_limit=60,  # Conservative estimate
        )

        # Pattern: 3 violations in 30s triggers tightening
        governor.patterns.create_pattern(
            pattern_id="cpu_pattern",
            boundary_id="cpu_allocation",
            violation_threshold=3,
            time_window_seconds=30,
        )

        # LEGITIMATE WORKLOAD: Consistently requires 70% CPU
        # This is legitimate, but exceeds initial boundary
        violations_by_round = []

        for round_num in range(3):
            # Generate 3 violations in this round
            for i in range(3):
                _, violation = governor.execute_against_boundary(
                    boundary_id="cpu_allocation",
                    observed_value=70,  # Legitimate need
                    context={"workload": "database_migration", "reason": "legitimate"}
                )
                if violation:
                    violations_by_round.append({
                        "round": round_num,
                        "violation": violation,
                    })
                time.sleep(0.1)

            # Each round, pattern is detected and boundary is tightened
            proposal = governor.detect_and_propose_adaptation("cpu_allocation")
            if proposal:
                approved, auth_result = governor.authorize_proposal(proposal)
                if auth_result.value == "auto_approved":
                    new_boundary = governor.apply_approved_proposal(approved)
                    print(f"Round {round_num}: Tightened to {new_boundary.current_limit}")

        # FAILURE EVIDENCE: Boundary was repeatedly tightened despite legitimate workload
        final_boundary = governor.boundaries.get_boundary("cpu_allocation")

        # Initial was 60, now should be much lower due to repeated tightening
        assert final_boundary.current_limit < 60, \
            "Boundary should have been tightened multiple times"

        # DOCUMENTED LIMITATION:
        # The governor cannot distinguish between:
        # 1. An ill-configured initial boundary (too low for legitimate work)
        # 2. An actual degradation that needs tightening
        #
        # This is a fundamental challenge for adaptive governors.
        # Phase 1 does NOT solve this. Phase 2 would need semantic understanding
        # of workload validity.


class TestAdversarialFeedbackManipulation:
    """TEST 8: Adversarial workload attempts to manipulate governance through violations."""

    def test_adversarial_violation_injection_on_unrelated_boundary(self):
        """
        SCENARIO: Attacker generates violations on boundary A to influence
        the governor's decisions about boundary B.

        ATTACK GOAL: Not to defeat any single boundary, but to inject noise
        that causes the governor to make bad decisions.

        FINDING: This test reveals whether the governor is exploitable.
        """
        governor = Governor(store_path="/tmp/test_adversarial")

        # Create two independent boundaries
        governor.boundaries.create_boundary(
            boundary_id="boundary_a",
            resource_or_action="metric_a",
            initial_limit=100,
        )
        governor.boundaries.create_boundary(
            boundary_id="boundary_b",
            resource_or_action="metric_b",
            initial_limit=200,
        )

        # Create pattern detectors for both
        governor.patterns.create_pattern(
            pattern_id="pattern_a",
            boundary_id="boundary_a",
            violation_threshold=3,
            time_window_seconds=60,
        )
        governor.patterns.create_pattern(
            pattern_id="pattern_b",
            boundary_id="boundary_b",
            violation_threshold=3,
            time_window_seconds=60,
        )

        # ATTACK: Generate deliberate violations on boundary_a to create noise
        print("\n=== ADVERSARIAL ATTACK: Violation Injection ===")
        for i in range(3):
            governor.execute_against_boundary(
                boundary_id="boundary_a",
                observed_value=150,
                context={"source": "adversarial_attack", "goal": "noise_injection"}
            )
            time.sleep(0.1)

        # Check if pattern detected on A
        proposal_a = governor.detect_and_propose_adaptation("boundary_a")
        if proposal_a:
            print(f"Proposal A generated: TIGHTEN to {proposal_a.proposed_value}")
            approved_a, _ = governor.authorize_proposal(proposal_a)
            governor.apply_approved_proposal(approved_a)

        # OBSERVATION: Did the attack on A affect B?
        # In this test, it shouldn't (boundaries are independent).
        # But the test documents what DOES happen.

        proposal_b = governor.detect_and_propose_adaptation("boundary_b")
        print(f"Proposal B: {proposal_b}")

        # With current independent design, B should be unaffected
        # FINDING: If B IS affected, that's a design flaw to document

        assert proposal_b is None, \
            "Boundary B should not generate proposals from unrelated violations"

    def test_alternating_violation_pattern_attack(self):
        """
        SCENARIO: Attacker generates alternating, low-intensity violations
        on a boundary to keep it just below the pattern threshold,
        causing the governor to constantly consider (but reject) adaptations.
        """
        governor = Governor(store_path="/tmp/test_alternating")

        governor.boundaries.create_boundary(
            boundary_id="attack_target",
            resource_or_action="value",
            initial_limit=100,
        )

        governor.patterns.create_pattern(
            pattern_id="attack_pattern",
            boundary_id="attack_target",
            violation_threshold=3,
            time_window_seconds=30,
        )

        # ATTACK: Generate violations just below threshold
        print("\n=== ADVERSARIAL ATTACK: Sub-threshold Violation Spam ===")
        proposals_generated = 0

        for round_num in range(5):
            # Generate exactly 2 violations (below threshold of 3)
            for i in range(2):
                governor.execute_against_boundary(
                    boundary_id="attack_target",
                    observed_value=150,
                )
                time.sleep(0.1)

            # Check for proposal (should be None)
            proposal = governor.detect_and_propose_adaptation("attack_target")
            if proposal:
                proposals_generated += 1

            # Sleep before next round to stay within time window
            time.sleep(1)

        print(f"Proposals generated: {proposals_generated}")

        # FINDING: How much noise can the pattern detector tolerate
        # before it generates spurious proposals?


class TestNoiseInjectionUnderStress:
    """Attempt to cause the governor to make harmful decisions under sustained attack."""

    def test_sustained_violation_barrage(self):
        """
        SCENARIO: Sustained, high-frequency violations designed to trigger
        repeated tightening.

        FINDING: Does the boundary eventually become useless?
        """
        governor = Governor(store_path="/tmp/test_barrage")

        initial_limit = 100.0
        governor.boundaries.create_boundary(
            boundary_id="barrage_target",
            resource_or_action="metric",
            initial_limit=initial_limit,
        )

        governor.patterns.create_pattern(
            pattern_id="barrage_pattern",
            boundary_id="barrage_target",
            violation_threshold=3,
            time_window_seconds=60,
        )

        print("\n=== ADVERSARIAL ATTACK: Violation Barrage ===")

        iteration = 0
        while iteration < 5:
            # Generate violation barrage
            for i in range(3):
                governor.execute_against_boundary(
                    boundary_id="barrage_target",
                    observed_value=150,
                )

            # Check for proposal
            proposal = governor.detect_and_propose_adaptation("barrage_target")
            if proposal:
                approved, _ = governor.authorize_proposal(proposal)
                new_boundary = governor.apply_approved_proposal(approved)
                current_limit = new_boundary.current_limit
                print(f"Iteration {iteration}: Boundary tightened to {current_limit:.2f}")
                iteration += 1
            else:
                break

            time.sleep(0.5)

        # FINDING: How far does the boundary get tightened?
        final_boundary = governor.boundaries.get_boundary("barrage_target")
        print(f"\nFinal boundary: {final_boundary.current_limit:.2f} (initial: {initial_limit})")

        # Document the damage
        tightening_factor = initial_limit / max(final_boundary.current_limit, 1.0)
        print(f"Tightening factor: {tightening_factor:.2f}x")

        # This is EXPECTED to show degradation with adversarial input
        # The finding is HOW MUCH degradation occurs


class TestGovernorExploitability:
    """Summary test: Is the governor exploitable?"""

    def test_governor_resilience_summary(self):
        """
        This test documents whether the governor can be driven into
        an undesirable state by adversarial feedback.

        RESULT: This will likely FAIL, demonstrating the limitation.
        That is the CORRECT outcome for this adversarial test.
        """
        governor = Governor(store_path="/tmp/test_summary")

        # Set up multiple boundaries
        for b_id in ["b1", "b2", "b3"]:
            governor.boundaries.create_boundary(
                boundary_id=b_id,
                resource_or_action=f"metric_{b_id}",
                initial_limit=100,
            )
            governor.patterns.create_pattern(
                pattern_id=f"p_{b_id}",
                boundary_id=b_id,
                violation_threshold=3,
                time_window_seconds=60,
            )

        # Coordinated attack: Violate all boundaries simultaneously
        print("\n=== COORDINATED ADVERSARIAL ATTACK ===")
        for round_num in range(3):
            for b_id in ["b1", "b2", "b3"]:
                for i in range(3):
                    governor.execute_against_boundary(
                        boundary_id=b_id,
                        observed_value=150,
                    )

            # Check if all boundaries got tightened
            tightened = 0
            for b_id in ["b1", "b2", "b3"]:
                proposal = governor.detect_and_propose_adaptation(b_id)
                if proposal:
                    approved, _ = governor.authorize_proposal(proposal)
                    governor.apply_approved_proposal(approved)
                    tightened += 1

            print(f"Round {round_num}: {tightened}/3 boundaries tightened")

        # DOCUMENTATION: Coordinated attacks cause cascading tightening
        # This is a KNOWN LIMITATION of the governor without semantic understanding
