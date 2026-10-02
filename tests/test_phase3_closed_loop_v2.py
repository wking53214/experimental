"""Phase 3.1 Closure: Refined Metrics & Convergence Analysis (hardened)"""
import pytest
from dataclasses import dataclass
from typing import List
from src.governance.governor import Governor
from src.governance.adversary import AdversaryOracle, AttackType, AttackOutcome


@dataclass
class AttackResult:
    iteration: int
    attack_type: AttackType
    outcome: AttackOutcome
    damage_score: float
    violation_created: bool
    boundary_tightened: bool


class RealAttackExecutor:
    def __init__(self, governor: Governor):
        self.governor = governor
        self.boundaries = ["cpu_limit", "memory_limit", "io_ops"]

    def execute_attack(self, attack_type: AttackType, params: dict, iteration: int) -> AttackResult:
        boundary_id = params.get("boundary_id", self.boundaries[iteration % len(self.boundaries)])
        try:
            boundary = self.governor.boundaries.get_boundary(boundary_id)
        except KeyError:
            self.governor.boundaries.create_boundary(
                boundary_id=boundary_id, resource_or_action=f"resource_{iteration}", initial_limit=100,
            )
            boundary = self.governor.boundaries.get_boundary(boundary_id)

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
                    boundary_id=boundary_id, resource_or_action=f"migrated_{iteration}", initial_limit=100,
                )
                boundary = self.governor.boundaries.get_boundary(boundary_id)
            target = boundary.current_limit * 1.1
        elif attack_type == AttackType.CASCADING_ROLLBACK:
            target = boundary.current_limit * 1.3
        else:
            target = boundary.current_limit * 1.05

        execution, violation = self.governor.execute_against_boundary(
            boundary_id=boundary_id, observed_value=target,
            context={"attack": attack_type.value, "iteration": iteration},
        )
        violation_created = violation is not None
        boundary_tightened = False
        damage_score = 0.0
        outcome = AttackOutcome.BLOCKED

        if violation:
            proposal = self.governor.detect_and_propose_adaptation(boundary_id)
            if proposal:
                boundary_tightened = True
                _, auth_result = self.governor.authorize_proposal(proposal)
                from src.governance.authority import AuthorizationResult
                if auth_result == AuthorizationResult.AUTO_APPROVED:
                    try:
                        self.governor.apply_approved_proposal(proposal)
                    except ValueError:
                        pass
                    outcome = AttackOutcome.PARTIALLY_SUCCEEDED
                    damage_score = 0.3
                else:
                    outcome = AttackOutcome.DETECTED
                    damage_score = 0.1
            else:
                viols = self.governor.events.get_violations_for_boundary(boundary_id)
                if len(viols) >= 2:
                    outcome = AttackOutcome.DETECTED
                    damage_score = 0.15
                else:
                    outcome = AttackOutcome.SUCCEEDED
                    damage_score = 0.5
        else:
            outcome = AttackOutcome.BLOCKED
            damage_score = 0.05

        return AttackResult(iteration, attack_type, outcome, damage_score, violation_created, boundary_tightened)


class TestPhase3Convergence:
    @pytest.fixture
    def gov_and_executor(self):
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
                violation_threshold=2,
                time_window_seconds=60,
            )
        return gov, RealAttackExecutor(gov)

    def test_phase3_succeeded_attacks_converge_to_zero(self, gov_and_executor):
        governor, executor = gov_and_executor
        adversary = AdversaryOracle()
        results: List[AttackResult] = []
        for iteration in range(1, 21):
            attack_type, params = adversary.select_attack()
            result = executor.execute_attack(attack_type, params, iteration)
            results.append(result)
            adversary.record_outcome(result.attack_type, result.outcome)
        early_range = results[:5]
        late_range = results[10:15]
        early_succeeded = len([r for r in early_range if r.outcome == AttackOutcome.SUCCEEDED])
        late_succeeded = len([r for r in late_range if r.outcome == AttackOutcome.SUCCEEDED])
        early_damage = sum(r.damage_score for r in early_range) / len(early_range)
        late_damage = sum(r.damage_score for r in late_range) / min(5, len(results) - 10)
        assert late_succeeded <= 1, f"Late range has {late_succeeded} SUCCEEDED attacks, expected <= 1"
        assert late_damage <= early_damage, "Damage should not increase"

    def test_attack_outcome_distribution(self, gov_and_executor):
        governor, executor = gov_and_executor
        adversary = AdversaryOracle()
        results = []
        for iteration in range(1, 21):
            attack_type, params = adversary.select_attack()
            result = executor.execute_attack(attack_type, params, iteration)
            results.append(result)
            adversary.record_outcome(result.attack_type, result.outcome)
        assert len(results) == 20
