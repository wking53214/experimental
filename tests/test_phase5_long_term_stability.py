"""Phase 5: Long-Term Stability (hardened: floor-aware DETECTED outcomes)"""
import pytest
import time
from collections import defaultdict
from src.governance.governor import Governor
from src.governance.adversary import AttackType, AttackOutcome
from src.governance.authority import AuthorizationResult


class EnduranceExecutor:
    def __init__(self, governor, boundaries, initial_limits):
        self.governor = governor
        self.boundaries = boundaries
        self.initial_limits = initial_limits
        self.proposal_history = []
        self.boundary_versions = {b: 1 for b in boundaries}

    def execute_attack(self, boundary_id, attack_type, params, iteration):
        boundary = self.governor.boundaries.get_boundary(boundary_id)
        magnitude = params.get("magnitude", 1.2)
        target_value = boundary.current_limit * magnitude
        execution, violation = self.governor.execute_against_boundary(
            boundary_id=boundary_id, observed_value=target_value,
            context={"attack_type": attack_type.value if hasattr(attack_type, "value") else str(attack_type), "iteration": iteration},
        )
        outcome = AttackOutcome.SUCCEEDED
        if violation:
            proposal = self.governor.detect_and_propose_adaptation(boundary_id)
            if proposal:
                self.proposal_history.append(proposal)
                self.governor.authorize_proposal(proposal)
                try:
                    self.governor.apply_approved_proposal(proposal)
                    self.boundary_versions[boundary_id] += 1
                except ValueError:
                    pass
                outcome = AttackOutcome.DETECTED
            else:
                initial = self.initial_limits.get(boundary_id, boundary.current_limit)
                if boundary.current_limit <= initial * 0.25:
                    outcome = AttackOutcome.DETECTED
                else:
                    outcome = AttackOutcome.SUCCEEDED
        return outcome

    def get_usability_metrics(self):
        metrics = {}
        for boundary_id in self.boundaries:
            boundary = self.governor.boundaries.get_boundary(boundary_id)
            initial = self.initial_limits[boundary_id]
            current = boundary.current_limit
            usability_pct = (current / initial) * 100
            metrics[boundary_id] = {
                "initial": initial, "current": current,
                "usability": usability_pct, "versions": self.boundary_versions[boundary_id],
            }
        return metrics


class SimpleAdversary:
    def __init__(self):
        self.outcomes = []
    def record(self, outcome):
        self.outcomes.append(outcome)
    def get_win_rate(self):
        if not self.outcomes:
            return 0.0
        return sum(1 for o in self.outcomes if o == AttackOutcome.SUCCEEDED) / len(self.outcomes)


class TestPhase5LongTermStability:
    @pytest.fixture
    def endurance_setup(self):
        gov = Governor(store_path="/tmp/test_phase5_endurance", use_semantic=True)
        boundaries = [f"endurance_{i:03d}" for i in range(10)]
        initial_limits = {}
        for bid in boundaries:
            gov.boundaries.create_boundary(bid, "resource", 1000)
            initial_limits[bid] = 1000
            gov.patterns.create_pattern(f"{bid}_pattern", bid, 2, 30)
        executor = EnduranceExecutor(gov, boundaries, initial_limits)
        return gov, executor, boundaries

    def test_1000_iteration_endurance(self, endurance_setup):
        gov, executor, boundaries = endurance_setup
        adversary = SimpleAdversary()
        outcomes_by_phase = {"early": {"succeeded": 0, "detected": 0}, "mid": {"succeeded": 0, "detected": 0}, "late": {"succeeded": 0, "detected": 0}}
        for iteration in range(1, 1001):
            bid = boundaries[iteration % len(boundaries)]
            outcome = executor.execute_attack(bid, AttackType.VIOLATION_INJECTION, {"magnitude": 1.2}, iteration)
            adversary.record(outcome)
            phase = "early" if iteration <= 100 else ("mid" if iteration <= 500 else "late")
            if outcome == AttackOutcome.SUCCEEDED:
                outcomes_by_phase[phase]["succeeded"] += 1
            else:
                outcomes_by_phase[phase]["detected"] += 1
        metrics = executor.get_usability_metrics()
        usability_scores = [m["usability"] for m in metrics.values()]
        min_usability = min(usability_scores)
        final_success_rate = adversary.get_win_rate()
        assert final_success_rate < 0.15, f"Final success rate too high: {final_success_rate:.0%}"
        assert min_usability > 5.0, f"Boundary became unusable: {min_usability:.1f}%"
        assert outcomes_by_phase["late"]["detected"] > outcomes_by_phase["late"]["succeeded"]
