"""
Phase 7A: Generative Adversary Testing

Replace fixed attack catalog with open-ended exploration:
- Evolutionary search over attack parameters
- Composition operators (combine, sequence, interleave attacks)
- Novel parameterizations beyond the fixed 6 types
- Multi-step attack plans based on observed system state

Hypothesis: Layered defense is robust to novel attacks, not just tuned to
the known catalog. Defense should contain generative adversary or clearly
characterize failure modes.
"""

import pytest
import random
from dataclasses import dataclass
from collections import defaultdict
from typing import List, Tuple
from src.governance.governor import Governor
from src.governance.adversary import AttackType, AttackOutcome


@dataclass
class AttackIndividual:
    """Represents one candidate attack in evolutionary search."""
    boundary_id: str
    attack_type: AttackType
    magnitude: float
    duration: int  # iterations to sustain attack
    delay: int    # iterations to wait before attack
    fitness: float = 0.0
    success_count: int = 0


class EvolutionaryAdversary:
    """
    Open-ended adversary using evolutionary search.

    Explores attack parameter space through:
    - Random mutation of magnitude, duration, delay
    - Crossover (combine successful attacks)
    - Selection (keep high-fitness individuals)
    """

    def __init__(self, boundaries: List[str], population_size: int = 20):
        self.boundaries = boundaries
        self.population_size = population_size
        self.population = self._init_population()
        self.generation = 0
        self.best_fitness = 0.0
        self.fitness_history = []

    def _init_population(self) -> List[AttackIndividual]:
        """Create initial random population."""
        pop = []
        for _ in range(self.population_size):
            individual = AttackIndividual(
                boundary_id=random.choice(self.boundaries),
                attack_type=random.choice(list(AttackType)),
                magnitude=random.uniform(1.1, 1.5),
                duration=random.randint(1, 10),
                delay=random.randint(0, 5),
            )
            pop.append(individual)
        return pop

    def select_best_attack(self) -> AttackIndividual:
        """Select the highest-fitness individual for execution."""
        return max(self.population, key=lambda x: x.fitness)

    def record_outcome(self, individual: AttackIndividual, outcome: AttackOutcome):
        """Update fitness based on outcome."""
        if outcome == AttackOutcome.SUCCEEDED:
            individual.fitness += 1.0
            individual.success_count += 1
        elif outcome == AttackOutcome.PARTIALLY_SUCCEEDED:
            individual.fitness += 0.3
        else:
            individual.fitness -= 0.1

        individual.fitness = max(0, individual.fitness)  # Prevent negative fitness

    def evolve(self):
        """Apply evolutionary operators: mutation, crossover, selection."""
        self.generation += 1

        # Sort by fitness
        self.population.sort(key=lambda x: x.fitness, reverse=True)

        # Keep top 50%, generate offspring through mutation/crossover
        elite = self.population[:self.population_size // 2]

        offspring = []
        for _ in range(self.population_size // 2):
            if random.random() < 0.7 and len(elite) > 1:
                # Crossover: combine two elite individuals
                parent1, parent2 = random.sample(elite, 2)
                child = AttackIndividual(
                    boundary_id=random.choice([parent1.boundary_id, parent2.boundary_id]),
                    attack_type=random.choice([parent1.attack_type, parent2.attack_type]),
                    magnitude=(parent1.magnitude + parent2.magnitude) / 2,
                    duration=(parent1.duration + parent2.duration) // 2,
                    delay=(parent1.delay + parent2.delay) // 2,
                )
            else:
                # Mutation: random mutation of an elite individual
                parent = random.choice(elite)
                child = AttackIndividual(
                    boundary_id=parent.boundary_id if random.random() < 0.8 else random.choice(self.boundaries),
                    attack_type=parent.attack_type if random.random() < 0.8 else random.choice(list(AttackType)),
                    magnitude=parent.magnitude * random.uniform(0.9, 1.1),
                    duration=max(1, parent.duration + random.randint(-2, 2)),
                    delay=max(0, parent.delay + random.randint(-1, 1)),
                )

            offspring.append(child)

        self.population = elite + offspring

        # Track best fitness
        best = max(self.population, key=lambda x: x.fitness)
        self.best_fitness = best.fitness
        self.fitness_history.append(best.fitness)


class GenerativeAdversaryExecutor:
    """Execute attacks from evolved population."""

    def __init__(self, governor: Governor, boundaries: List[str]):
        self.governor = governor
        self.boundaries = boundaries
        self.attack_queue = []
        self.current_attack = None
        self.current_attack_step = 0

    def execute_queued_attack(self, iteration: int) -> Tuple[bool, AttackOutcome]:
        """Execute the next step of queued attacks."""
        if not self.current_attack:
            return False, None

        # Check if attack duration is exhausted
        if self.current_attack_step >= self.current_attack.duration:
            self.current_attack = None
            self.current_attack_step = 0
            return False, None

        # Execute one step of the attack
        boundary = self.governor.boundaries.get_boundary(self.current_attack.boundary_id)
        target_value = boundary.current_limit * self.current_attack.magnitude

        execution, violation = self.governor.execute_against_boundary(
            boundary_id=self.current_attack.boundary_id,
            observed_value=target_value,
            context={
                "attack_type": self.current_attack.attack_type.value,
                "generation": "generative",
                "iteration": iteration,
            },
        )

        outcome = AttackOutcome.SUCCEEDED
        if violation:
            proposal = self.governor.detect_and_propose_adaptation(self.current_attack.boundary_id)
            if proposal:
                self.governor.authorize_proposal(proposal)
                outcome = AttackOutcome.DETECTED

        self.current_attack_step += 1
        return True, outcome

    def queue_attack(self, individual: AttackIndividual):
        """Queue an evolved attack for execution."""
        self.current_attack = individual
        self.current_attack_step = 0


class TestPhase7aGenerativeAdversary:
    """Test defense against open-ended evolved attacks."""

    @pytest.fixture
    def generative_setup(self):
        """Setup for generative adversary testing."""
        gov = Governor(store_path="/tmp/test_phase7a_generative", use_semantic=True)

        boundaries = [f"evolve_{i:02d}" for i in range(10)]
        for boundary_id in boundaries:
            gov.boundaries.create_boundary(
                boundary_id=boundary_id,
                resource_or_action=f"resource_{boundary_id}",
                initial_limit=1000,
            )
            gov.patterns.create_pattern(
                pattern_id=f"{boundary_id}_pattern",
                boundary_id=boundary_id,
                violation_threshold=2,
                time_window_seconds=30,
            )

        adversary = EvolutionaryAdversary(boundaries, population_size=20)
        executor = GenerativeAdversaryExecutor(gov, boundaries)

        return gov, adversary, executor, boundaries

    def test_evolutionary_search_contained(self, generative_setup):
        """Run evolutionary search over 10 generations, 500 total attacks."""
        gov, adversary, executor, boundaries = generative_setup

        print("\n=== Phase 7A: Evolutionary Adversary Search ===\n")

        outcomes_by_gen = defaultdict(lambda: {"succeeded": 0, "detected": 0})

        # 10 generations, ~50 attacks per generation
        for generation in range(10):
            print(f"Generation {generation}: ", end="")
            gen_outcomes = {"succeeded": 0, "detected": 0}

            for attack_iter in range(50):
                # Select attack from current population
                individual = adversary.select_best_attack()

                # Queue and execute
                executor.queue_attack(individual)
                attacked, outcome = executor.execute_queued_attack(generation * 50 + attack_iter)

                if attacked:
                    adversary.record_outcome(individual, outcome)
                    if outcome == AttackOutcome.SUCCEEDED:
                        gen_outcomes["succeeded"] += 1
                    else:
                        gen_outcomes["detected"] += 1

            # Evolve population for next generation
            adversary.evolve()

            success_rate = gen_outcomes["succeeded"] / (gen_outcomes["succeeded"] + gen_outcomes["detected"]) \
                if (gen_outcomes["succeeded"] + gen_outcomes["detected"]) > 0 else 0

            print(f"Success={success_rate:.0%}, Best fitness={adversary.best_fitness:.1f}")
            outcomes_by_gen[generation] = gen_outcomes

        # Analysis
        print(f"\n=== Evolutionary Search Results ===")
        early_gen_success = sum(
            outcomes_by_gen[i]["succeeded"]
            for i in range(3)
        ) / max(1, sum(
            outcomes_by_gen[i]["succeeded"] + outcomes_by_gen[i]["detected"]
            for i in range(3)
        ))

        late_gen_success = sum(
            outcomes_by_gen[i]["succeeded"]
            for i in range(7, 10)
        ) / max(1, sum(
            outcomes_by_gen[i]["succeeded"] + outcomes_by_gen[i]["detected"]
            for i in range(7, 10)
        ))

        print(f"Early generations (0-2) success: {early_gen_success:.0%}")
        print(f"Late generations (7-9) success: {late_gen_success:.0%}")
        print(f"Fitness trajectory: {[f'{f:.1f}' for f in adversary.fitness_history]}")

        # Defense should hold: late success lower than early
        assert late_gen_success < early_gen_success, "Evolutionary search not contained"
        assert late_gen_success < 0.3, "Late generation success too high"

        print(f"\n✓ Evolutionary adversary contained despite novel parameterizations")

    def test_attack_composition_multipart(self, generative_setup):
        """Test multi-step attack sequences (attack composition)."""
        gov, adversary, executor, boundaries = generative_setup

        print("\n=== Phase 7A: Multi-Step Attack Composition ===\n")

        # Simulate a multi-step attack: CONSTRAINT_MIGRATION → VIOLATION_INJECTION → CASCADING_ROLLBACK
        attacks = [
            AttackIndividual(
                boundary_id=boundaries[0],
                attack_type=AttackType.CONSTRAINT_MIGRATION,
                magnitude=1.15,
                duration=3,
                delay=0,
            ),
            AttackIndividual(
                boundary_id=boundaries[1],
                attack_type=AttackType.VIOLATION_INJECTION,
                magnitude=1.30,
                duration=5,
                delay=3,
            ),
            AttackIndividual(
                boundary_id=boundaries[2],
                attack_type=AttackType.CASCADING_ROLLBACK,
                magnitude=1.25,
                duration=4,
                delay=6,
            ),
        ]

        iteration = 0
        succeeded_count = 0
        detected_count = 0

        # Execute sequence
        for step in range(20):
            for i, attack in enumerate(attacks):
                if step >= attack.delay and step < attack.delay + attack.duration:
                    boundary = gov.boundaries.get_boundary(attack.boundary_id)
                    target = boundary.current_limit * attack.magnitude

                    execution, violation = gov.execute_against_boundary(
                        boundary_id=attack.boundary_id,
                        observed_value=target,
                        context={"attack": attack.attack_type.value, "step": step},
                    )

                    if violation:
                        proposal = gov.detect_and_propose_adaptation(attack.boundary_id)
                        if proposal:
                            detected_count += 1
                            gov.authorize_proposal(proposal)
                        else:
                            succeeded_count += 1
                    else:
                        succeeded_count += 1

        print(f"Multi-step attack results:")
        print(f"  Succeeded: {succeeded_count}")
        print(f"  Detected: {detected_count}")

        # Defense should detect most of the composed attack
        total = succeeded_count + detected_count
        detection_rate = detected_count / total if total > 0 else 0

        assert detection_rate > 0.5, "Composed attacks too effective"
        print(f"\n✓ Multi-step attack composition partially detected ({detection_rate:.0%})")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
