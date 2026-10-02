"""
Phase 3.1: Adaptive Adversary Oracle

Test whether an intelligent adversary that learns from each response
can escape the defense layers.

Adversary knows:
- Governor's rules and thresholds
- Which attacks succeeded/failed
- Can observe: was attack blocked? Detected? Partially successful?

Adversary adapts:
- If violation injection failed → try metrics manipulation
- If metrics manipulation detected → try constraint migration
- If one boundary tightened → attack adjacent boundary
- Learn which combinations work

Goal: Degrade system without violating monotonicity rule.
"""
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, List
import time
import random


class AttackType(Enum):
    """Attack vectors the adversary can deploy."""
    VIOLATION_INJECTION = "violation_injection"
    METRICS_LYING = "metrics_lying"
    CONSTRAINT_MIGRATION = "constraint_migration"
    SEMANTIC_POISONING = "semantic_poisoning"
    CASCADING_ROLLBACK = "cascading_rollback"
    GOVERNOR_FARMING = "governor_farming"


class AttackOutcome(Enum):
    """How the attack was handled by governor."""
    SUCCEEDED = "succeeded"  # Attack achieved goal
    PARTIALLY_SUCCEEDED = "partially_succeeded"  # Some damage
    DETECTED = "detected"  # Caught but not fully blocked
    BLOCKED = "blocked"  # Completely stopped


@dataclass
class AttackAttempt:
    """Record of one attack attempt."""
    iteration: int
    attack_type: AttackType
    parameters: dict = field(default_factory=dict)
    outcome: Optional[AttackOutcome] = None
    timestamp: float = field(default_factory=time.time)
    damage_score: float = 0.0  # How much system degraded (0-1)
    confidence: float = 0.0    # How confident in prediction


class AdversaryOracle:
    """
    Intelligent adversary that learns from governor responses.

    Strategy:
    1. Try attack
    2. Observe outcome
    3. Learn: did this work? Can we improve?
    4. Adapt strategy for next iteration
    5. Measure: iterations until governor contains all attacks
    """

    def __init__(self):
        self.iterations = 0
        self.attack_history: List[AttackAttempt] = []
        self.learning_state = {
            "successful_attacks": [],
            "blocked_attacks": [],
            "detected_attacks": [],
            "partially_successful": [],
        }
        self.strategy_effectiveness = {}

    def select_attack(self) -> tuple[AttackType, dict]:
        """
        Choose next attack based on learning.

        Early iterations: explore all attack types
        Later iterations: exploit what worked, adapt blocked attacks
        """
        self.iterations += 1

        # Exploration phase (first 5 iterations)
        if self.iterations <= 5:
            return self._explore_attack()

        # Exploitation phase (iterations 6+)
        return self._exploit_successful_attacks()

    def _explore_attack(self) -> tuple[AttackType, dict]:
        """Try each attack type systematically."""
        attack_types = [
            AttackType.VIOLATION_INJECTION,
            AttackType.METRICS_LYING,
            AttackType.SEMANTIC_POISONING,
            AttackType.CASCADING_ROLLBACK,
            AttackType.CONSTRAINT_MIGRATION,
            AttackType.GOVERNOR_FARMING,
        ]

        idx = min(self.iterations - 1, len(attack_types) - 1)
        attack_type = attack_types[idx]

        params = self._generate_parameters(attack_type)
        return attack_type, params

    def _exploit_successful_attacks(self) -> tuple[AttackType, dict]:
        """
        Focus on attacks that worked.

        Learning strategy:
        - If violation injection partially worked: increase magnitude
        - If metrics lying detected: add more concurrent metrics
        - If constraint migration worked: migrate to different boundaries
        """
        if self.learning_state["successful_attacks"]:
            # Attack that fully succeeded: repeat with variations
            base_attack = random.choice(self.learning_state["successful_attacks"])
            params = self._amplify_attack(base_attack)
            return base_attack, params

        if self.learning_state["partially_successful"]:
            # Attack that partially worked: improve it
            base_attack = random.choice(self.learning_state["partially_successful"])
            params = self._improve_attack(base_attack)
            return base_attack, params

        if self.learning_state["detected_attacks"]:
            # Attack that was detected: try to disguise it
            base_attack = random.choice(self.learning_state["detected_attacks"])
            params = self._disguise_attack(base_attack)
            return base_attack, params

        # Fallback: try something random
        return random.choice(list(AttackType)), {}

    def record_outcome(
        self,
        attack_type: AttackType,
        outcome: AttackOutcome,
        damage_score: float = 0.0,
    ) -> None:
        """
        Record how the attack was handled.
        Update learning state for next iteration.
        """
        attempt = AttackAttempt(
            iteration=self.iterations,
            attack_type=attack_type,
            outcome=outcome,
            damage_score=damage_score,
        )
        self.attack_history.append(attempt)

        # Update learning state
        if outcome == AttackOutcome.SUCCEEDED:
            self.learning_state["successful_attacks"].append(attack_type)
        elif outcome == AttackOutcome.PARTIALLY_SUCCEEDED:
            self.learning_state["partially_successful"].append(attack_type)
        elif outcome == AttackOutcome.DETECTED:
            self.learning_state["detected_attacks"].append(attack_type)
        elif outcome == AttackOutcome.BLOCKED:
            self.learning_state["blocked_attacks"].append(attack_type)

    def _generate_parameters(self, attack_type: AttackType) -> dict:
        """Generate initial attack parameters."""
        if attack_type == AttackType.VIOLATION_INJECTION:
            return {"magnitude": 150, "count": 3}
        elif attack_type == AttackType.METRICS_LYING:
            return {"fake_degradation": 0.5}
        elif attack_type == AttackType.SEMANTIC_POISONING:
            return {"claimed_context": "database_migration"}
        elif attack_type == AttackType.CASCADING_ROLLBACK:
            return {"rollback_count": 5}
        elif attack_type == AttackType.CONSTRAINT_MIGRATION:
            return {"boundary_targets": ["cpu", "memory", "io"]}
        elif attack_type == AttackType.GOVERNOR_FARMING:
            return {"farming_cycles": 3}
        return {}

    def _amplify_attack(self, attack_type: AttackType) -> dict:
        """Increase magnitude of successful attack."""
        base_params = self._generate_parameters(attack_type)

        if "magnitude" in base_params:
            base_params["magnitude"] = int(base_params["magnitude"] * 1.2)
        if "count" in base_params:
            base_params["count"] += 1
        if "fake_degradation" in base_params:
            base_params["fake_degradation"] = min(1.0, base_params["fake_degradation"] + 0.1)

        return base_params

    def _improve_attack(self, attack_type: AttackType) -> dict:
        """Refine partially successful attack."""
        return self._amplify_attack(attack_type)

    def _disguise_attack(self, attack_type: AttackType) -> dict:
        """Try to evade detection of known attack."""
        params = self._generate_parameters(attack_type)

        # Hide the attack signature
        if attack_type == AttackType.VIOLATION_INJECTION:
            params["spread_over_time"] = True
            params["magnitude"] = 140  # Just under typical threshold
        elif attack_type == AttackType.METRICS_LYING:
            params["gradual_degradation"] = True

        return params

    def get_success_rate(self) -> float:
        """What percentage of attacks succeeded or partially succeeded?"""
        if not self.attack_history:
            return 0.0

        successful = sum(
            1 for a in self.attack_history
            if a.outcome in [AttackOutcome.SUCCEEDED, AttackOutcome.PARTIALLY_SUCCEEDED]
        )
        return successful / len(self.attack_history)

    def get_containment_iteration(self) -> Optional[int]:
        """
        At which iteration was the adversary fully contained?
        (No successful attacks for N consecutive iterations)
        """
        containment_threshold = 3  # N iterations with no success = contained
        consecutive_blocked = 0

        for attempt in reversed(self.attack_history):
            if attempt.outcome == AttackOutcome.BLOCKED:
                consecutive_blocked += 1
                if consecutive_blocked >= containment_threshold:
                    return attempt.iteration
            else:
                consecutive_blocked = 0

        return None

    def get_attack_summary(self) -> dict:
        """Summary of attack effectiveness."""
        return {
            "total_iterations": self.iterations,
            "total_attempts": len(self.attack_history),
            "success_rate": self.success_rate(),
            "learning_state": self.learning_state,
            "containment_iteration": self.get_containment_iteration(),
        }

    def success_rate(self) -> float:
        """Alias for get_success_rate."""
        return self.get_success_rate()
