"""
Phase 1B: Deception Attacks

Adversary knows the governor's rules and semantics.

Test whether the architecture's fundamental rule holds:
"The system may autonomously respond to evidence by increasing restriction,
but no observation generated inside the adaptive loop can grant the loop
authority to reduce or redefine its governing constraints."

Four nasty attacks:
1. Semantic poisoning (legitimate behavior → malicious signature)
2. Governor farming (attack the evidence chain, not the resource)
3. Constraint migration (move attack across boundaries)
4. Goodhart attack (violate less, but degrade more)
"""
import pytest
import time
from src.governance.governor import Governor
from src.governance.proposal import AdaptationDirection
from src.governance.metrics import EffectivenessOutcome


class AttackSemanticPoisoning:
    """
    Adversary question: Can I create LEGITIMATE behavior that has the
    SEMANTIC SIGNATURE of malicious behavior?

    Example: Database migration (known legitimate high-load activity)
    But adversary times it to coincide with attack window.

    Governor sees: "legitimate database migration"
    But system experiences: "concurrent attacks during maintenance window"
    """

    def attack(self, governor: Governor, boundary_id: str):
        """
        Create violations with context that SHOULD be filtered as expected,
        but the system is actually degrading.

        Returns: whether governor correctly identified this as benign or
                 incorrectly treated as anomaly requiring tightening.
        """
        # Adversary marks violations as "expected maintenance"
        # This is technically truthful—there IS maintenance
        # But it's a cover for concurrent attack

        violations_during_maintenance = 0

        for i in range(5):
            governor.execute_against_boundary(
                boundary_id=boundary_id,
                observed_value=150,  # Way over limit
                context={
                    "workload_type": "database_migration",  # Legitimate signal
                    "expected": True,  # This SHOULD make semantic layer filter it
                    "actual_cause": "attacker"  # Hidden from governor
                }
            )
            time.sleep(0.05)
            violations_during_maintenance += 1

        return violations_during_maintenance


class AttackGovernorFarming:
    """
    Adversary question: Can I attack the evidence chain instead of the
    protected resource?

    Instead of: metric → high load
    Attack: violation → pattern → proposal → boundary tightening

    Goal: Induce governor to tighten itself into dysfunction.

    The boundary doesn't protect against this attack—it actually ENABLES it.
    Because the governor's own responsiveness becomes the weapon.
    """

    def attack(self, governor: Governor, boundary_id: str):
        """
        Generate violations specifically calibrated to:
        1. Be detected by pattern detector
        2. Trigger tightening proposal
        3. Have tightening reduce legitimate throughput
        4. Not trigger rollback (metrics stay in "acceptable" range)

        Returns: final boundary limit after farming
        """
        pre_metrics = {
            "violation_rate": 0.15,
            "slo_attainment": 0.92,
            "throughput": 1000.0,
        }

        tighten_count = 0

        # Farm: repeatedly trigger tightening
        for cycle in range(5):
            # Generate pattern-triggering violations
            governor.patterns.create_pattern(
                f"farmed_pat_{cycle}",
                boundary_id,
                violation_threshold=2,
                time_window_seconds=30,
            )

            for i in range(2):
                governor.execute_against_boundary(
                    boundary_id=boundary_id,
                    observed_value=150,
                    context={"cycle": cycle}
                )
                time.sleep(0.05)

            # Trigger proposal
            proposal = governor.detect_and_propose_adaptation(boundary_id)

            if proposal and proposal.direction == AdaptationDirection.TIGHTEN:
                # Authorize and apply
                approved, _ = governor.authorize_proposal(proposal)
                if approved.status.value != "approved":
                    break  # circuit breaker held it for a human
                governor.apply_approved_proposal(approved)
                tighten_count += 1

                # Post-adaptation metrics: carefully chosen to NOT trigger rollback
                # (just enough improvement to seem beneficial)
                time.sleep(0.1)
                post_metrics = {
                    "violation_rate": pre_metrics["violation_rate"] * 0.9,  # Slight improvement
                    "slo_attainment": pre_metrics["slo_attainment"] * 0.95,  # Still acceptable
                    "throughput": pre_metrics["throughput"] * 0.85,  # Degraded, but hidden
                }

                outcome, conf = governor.metrics_tracker.evaluate_post_adaptation(
                    proposal.proposal_id,
                    boundary_id,
                    post_metrics,
                )

                # If metrics DON'T trigger rollback, the tightening persists
                # Legitimate throughput degrades, but violation_rate appears better

        boundary = governor.boundaries.get_boundary(boundary_id)
        return tighten_count, boundary.current_limit


class AttackConstraintMigration:
    """
    Adversary question: Can I move the attack across boundaries without
    ever violating the monotonicity rule?

    System tightens A → Attacker switches to B
    System tightens B → Attacker switches to C
    ...

    Result: System is perfectly monotonic (version numbers increase)
    but progressively weaponized.
    """

    def attack(self, governor: Governor, boundary_ids: list[str]):
        """
        Attack boundary 0 → governor tightens 0
        Attack boundary 1 → governor tightens 1
        ...

        Each boundary becomes more restrictive (monotonic ✓)
        But the attacker's goal (degrade system) succeeds anyway
        """
        results = {}

        for boundary_id in boundary_ids:
            # Create boundary if needed
            try:
                governor.boundaries.get_boundary(boundary_id)
            except KeyError:
                governor.boundaries.create_boundary(boundary_id, "metric", 100)

            # Create pattern detector
            governor.patterns.create_pattern(
                f"pat_{boundary_id}",
                boundary_id,
                violation_threshold=2,
                time_window_seconds=30,
            )

            # Attack: generate violations
            for i in range(2):
                governor.execute_against_boundary(
                    boundary_id=boundary_id,
                    observed_value=150,
                )
                time.sleep(0.05)

            # Governor responds: tightens
            proposal = governor.detect_and_propose_adaptation(boundary_id)
            if proposal:
                approved, _ = governor.authorize_proposal(proposal)
                governor.apply_approved_proposal(approved)

            # Record: boundary got tighter
            boundary = governor.boundaries.get_boundary(boundary_id)
            results[boundary_id] = {
                "limit": boundary.current_limit,
                "version": boundary.version,
            }

        # All boundaries are now tighter (MONOTONIC ✓)
        # But the system is progressively degraded
        return results


class AttackGoodhart:
    """
    Adversary question: Can I make "fewer violations" mean "worse system"?

    Goodhart's Law: When a measure becomes a target, it ceases to be a good measure.

    Governor optimizes: violation_rate down
    Attacker makes: legitimate work impossible
    Result: violation_rate down + legitimate throughput down = system broken

    But "effectiveness oracle" says: improved!
    """

    def attack(self, governor: Governor, boundary_id: str):
        """
        Pre-adaptation: violation_rate = 0.2, throughput = 1000

        Tighten boundary so much that:
        - Legitimate work can't execute
        - Violations disappear (because nothing runs)
        - But throughput also disappears

        Metrics show "improvement" but system is dead.
        """
        # Record pre
        pre_metrics = {
            "violation_rate": 0.20,
            "slo_attainment": 0.90,
            "throughput": 1000.0,
            "latency_p99": 150.0,
        }
        governor.metrics_tracker.evaluate_proposal("goodhart_prop", boundary_id, pre_metrics)

        # Tighten AGGRESSIVELY
        current_boundary = governor.boundaries.get_boundary(boundary_id)
        aggressive_limit = current_boundary.current_limit * 0.3  # Tighten by 70%

        prop = governor.proposals.create_proposal(
            boundary_id, [], current_boundary.current_limit, aggressive_limit,
            "Aggressive tightening",
            AdaptationDirection.TIGHTEN,
        )
        approved, _ = governor.authorize_proposal(prop)
        governor.apply_approved_proposal(approved)

        time.sleep(0.1)

        # Post: violations gone, but so is everything else
        post_metrics = {
            "violation_rate": 0.02,  # Way down! (because nothing runs)
            "slo_attainment": 0.95,  # Appears improved!
            "throughput": 10.0,      # COLLAPSED (hidden in other metrics)
            "latency_p99": 50.0,     # Appears better (but no requests)
        }

        outcome, confidence = governor.metrics_tracker.evaluate_post_adaptation(
            "goodhart_prop",
            boundary_id,
            post_metrics,
        )

        # Question: does oracle say "improved" while system is dead?
        return outcome, confidence, pre_metrics, post_metrics


class TestSemanticPoisoning:
    """
    Can adversary hide attack inside legitimate-looking behavior?
    """

    def test_semantic_layer_distinguishes_poisoned_maintenance(self):
        """
        Adversary uses maintenance window to hide attack.
        Question: Can semantic layer tell apart:
        - Legitimate maintenance (high load, expected, handled)
        - Poisoned maintenance (high load, marked expected, actually attack)
        """
        governor = Governor(
            store_path=f"/tmp/poison_{int(time.time()*1000)}",
            use_semantic=True
        )

        governor.boundaries.create_boundary("target", "metric", 80)

        attacker = AttackSemanticPoisoning()
        violations = attacker.attack(governor, "target")

        # QUESTION: Did semantic layer let this through because it matched
        # the "expected maintenance" pattern?
        #
        # If yes: semantic layer has a vulnerability
        #         (can be poisoned by legitimate-looking context)
        # If no: the layer is more sophisticated than just context matching

        # For now: report what happened
        violations_by_type = [
            v for v in governor.events.get_violations_for_boundary("target")
        ]

        # Document: semantic layer's decision
        assert governor.classifier is not None
        for v in violations_by_type[:3]:
            is_expected = governor.classifier.is_expected_violation(v)
            # Attacker marked it "expected" (maintenance context)
            # But it's actually attack
            # Did semantic layer believe it?


class TestGovernorFarming:
    """
    Can adversary weaponize the governor's own responsiveness?
    """

    def test_farming_induces_progressive_tightening(self):
        """
        Adversary farms the evidence chain.

        Question: Can adversary cause multiple tightenings without
                 triggering rollback?

        If yes: Governor's own mechanism enables progressive degradation
        If no: Rollback mechanism prevents farming
        """
        governor = Governor(
            store_path=f"/tmp/farming_{int(time.time()*1000)}",
            use_semantic=False
        )

        governor.boundaries.create_boundary("target", "metric", 100)

        attacker = AttackGovernorFarming()
        tighten_count, final_limit = attacker.attack(governor, "target")

        # RESULT: Did the governor tighten multiple times?
        assert tighten_count > 0, "At least one tightening occurred"
        assert final_limit < 100, "Boundary is tighter than initial"

        # The farming attack exploits:
        # - Pattern detector's blindness (can't distinguish attack from load)
        # - Metrics' Goodhart vulnerability (violation_rate down ≠ system better)
        # - Rollback's threshold (small degradation doesn't trigger)


class TestConstraintMigration:
    """
    Can adversary move attack across boundaries?
    """

    def test_migration_progressive_system_degradation(self):
        """
        Adversary attacks boundary 0 → tightened
        Adversary attacks boundary 1 → tightened
        Adversary attacks boundary 2 → tightened

        System is monotonic (✓) but weaponized.
        """
        governor = Governor(
            store_path=f"/tmp/migration_{int(time.time()*1000)}",
            use_semantic=False
        )

        # Create multiple boundaries
        boundaries = ["cpu", "memory", "io"]
        for bid in boundaries:
            governor.boundaries.create_boundary(bid, "metric", 100)

        attacker = AttackConstraintMigration()
        results = attacker.attack(governor, boundaries)

        # RESULT: All boundaries tighter (monotonic)
        for bid in boundaries:
            boundary = governor.boundaries.get_boundary(bid)
            assert boundary.version >= 1, "Boundary evolved"
            # Version numbers only increase (✓)

        # But system as a whole is progressively constrained


class TestGoodhart:
    """
    Can adversary exploit Goodhart's Law?
    """

    def test_goodhart_attack_system_death_looks_like_improvement(self):
        """
        Extreme tightening makes system unable to execute.
        Violations disappear (because nothing runs).
        Effectiveness oracle says "improved" while system is dead.
        """
        governor = Governor(
            store_path=f"/tmp/goodhart_{int(time.time()*1000)}",
            use_semantic=False
        )

        governor.boundaries.create_boundary("target", "metric", 100)

        attacker = AttackGoodhart()
        outcome, confidence, pre, post = attacker.attack(governor, "target")

        # CRITICAL QUESTION:
        # Does the system report "improved" while throughput collapsed?

        # If outcome == IMPROVED: Goodhart attack succeeded
        # The governor is optimizing for the wrong metric

        # Pre: violation_rate = 0.20, throughput = 1000
        # Post: violation_rate = 0.02, throughput = 10
        #
        # Metrics oracle sees: violation_rate improved (good!)
        # But ignores: throughput collapsed (bad!)

        assert outcome == EffectivenessOutcome.DEGRADED, \
            "Oracle correctly detects Goodhart pattern"

        # With the fix: oracle recognizes violation_rate improvement
        # is offset by throughput collapse, and flags as DEGRADED
        assert confidence > 0.9, "High confidence in Goodhart detection"


class TestPrimitiveVulnerability:
    """
    Document: fundamental vulnerabilities in the architecture.
    """

    def test_document_fundamental_constraints(self):
        """
        If the deception attacks succeed, what does that tell us
        about the proposed "governance primitive"?

        Outcomes:

        1. If semantic poisoning works:
           - Semantic layer alone cannot distinguish
             "legitimate high-pressure" from "attack"
           - Need additional mechanism (e.g., historical baseline,
             rate-of-change detection, external validation)

        2. If governor farming works:
           - Pattern detection + metrics can be weaponized
           - Blind tightening (even with metrics) is exploitable
           - Need stricter constraints on tightening frequency

        3. If constraint migration works:
           - Monotonicity within boundaries doesn't prevent
             system-wide degradation
           - Need cross-boundary reasoning

        4. If Goodhart works:
           - Effectiveness metrics are incomplete
           - Optimizing for violation_rate misses throughput collapse
           - Need richer measurement (efficiency = throughput/violations)
        """
        vulnerabilities = {
            "semantic_poisoning": "Context can be spoofed",
            "governor_farming": "Pattern detection can be weaponized",
            "constraint_migration": "Monotonicity per-boundary allows system-wide attack",
            "goodhart": "Metrics can optimize system to death",
        }

        # These aren't failures—they're design insights.
        # Each vulnerability points to a required mechanism:

        design_responses = {
            "semantic_poisoning": "Need rate-of-change detection, baseline comparison",
            "governor_farming": "Need tightening frequency limits, latency measurement",
            "constraint_migration": "Need cross-boundary analysis, resource pool reasoning",
            "goodhart": "Need throughput-aware metrics, efficiency measurement",
        }

        assert len(vulnerabilities) == len(design_responses)
