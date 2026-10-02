"""
Phase 9C: Evolutionary Adversary - Genetic Algorithm Attack Generation

Tests whether the generative detector can catch novel attacks evolved via
genetic search. Population of attack strategies mutates and crosses over,
trying to maximize damage while evading detection.
"""

import numpy as np
from typing import Dict, List, Tuple
from dataclasses import dataclass


@dataclass
class AttackChromosome:
    """Genetic representation of an attack strategy."""
    metric_targets: List[str]  # Which metrics to attack
    magnitudes: List[float]  # How much to deviate each (1.0 = baseline)
    durations: List[int]  # How many iterations each metric attacked
    delays: List[int]  # When to start attacking (iteration)
    fitness: float = 0.0
    detection_count: int = 0  # Times detected by any detector


class EvolutionaryAdversary:
    """
    Genetic algorithm for evolving novel attack strategies.

    Population of attacks evolves to maximize damage while minimizing detection.
    Tests whether detector can catch evolved (never-before-coded) attacks.
    """

    def __init__(self,
                 population_size: int = 20,
                 generations: int = 10,
                 mutation_rate: float = 0.3,
                 crossover_rate: float = 0.7):
        """
        Initialize evolutionary adversary.

        Args:
            population_size: Number of attack strategies per generation
            generations: Number of evolution cycles
            mutation_rate: Probability of mutation per gene
            crossover_rate: Probability of crossing over
        """
        self.population_size = population_size
        self.generations = generations
        self.mutation_rate = mutation_rate
        self.crossover_rate = crossover_rate

        self.population: List[AttackChromosome] = []
        self.best_fitness_history: List[float] = []
        self.detection_history: List[Tuple[int, int]] = []  # (generation, detections)

    def generate_initial_population(self, metric_names: List[str]) -> List[AttackChromosome]:
        """Generate random initial population."""
        population = []
        for _ in range(self.population_size):
            # Randomly select 1-4 metrics to attack
            n_targets = np.random.randint(1, min(5, len(metric_names) + 1))
            targets = list(np.random.choice(metric_names, size=n_targets, replace=False))

            chromosome = AttackChromosome(
                metric_targets=targets,
                magnitudes=[np.random.uniform(0.5, 2.0) for _ in targets],
                durations=[np.random.randint(1, 11) for _ in targets],
                delays=[np.random.randint(0, 6) for _ in targets],
            )
            population.append(chromosome)

        return population

    def mutate(self, chromosome: AttackChromosome, metric_names: List[str]) -> AttackChromosome:
        """Apply mutation to attack chromosome."""
        mutant = AttackChromosome(
            metric_targets=chromosome.metric_targets.copy(),
            magnitudes=chromosome.magnitudes.copy(),
            durations=chromosome.durations.copy(),
            delays=chromosome.delays.copy(),
        )

        for i in range(len(mutant.magnitudes)):
            if np.random.random() < self.mutation_rate:
                # Mutate magnitude
                mutant.magnitudes[i] = np.clip(
                    mutant.magnitudes[i] * np.random.uniform(0.8, 1.2),
                    0.5, 2.5
                )

            if np.random.random() < self.mutation_rate:
                # Mutate duration
                mutant.durations[i] = max(1, int(mutant.durations[i] + np.random.randint(-2, 3)))

            if np.random.random() < self.mutation_rate:
                # Mutate delay
                mutant.delays[i] = max(0, int(mutant.delays[i] + np.random.randint(-2, 3)))

        return mutant

    def crossover(self, parent1: AttackChromosome, parent2: AttackChromosome) -> AttackChromosome:
        """Cross over two chromosomes."""
        if len(parent1.metric_targets) < 2 or len(parent2.metric_targets) < 2:
            return parent1

        # Single-point crossover
        point = np.random.randint(1, len(parent1.metric_targets))

        child = AttackChromosome(
            metric_targets=parent1.metric_targets[:point] + parent2.metric_targets[point:],
            magnitudes=parent1.magnitudes[:point] + parent2.magnitudes[point:],
            durations=parent1.durations[:point] + parent2.durations[point:],
            delays=parent1.delays[:point] + parent2.delays[point:],
        )

        return child

    def evaluate_fitness(self, chromosome: AttackChromosome, damage: float, detected: bool) -> float:
        """
        Compute fitness: maximize damage, minimize detection.

        Args:
            chromosome: Attack strategy
            damage: How much damage this attack inflicted (0-1)
            detected: Whether it was detected

        Returns:
            Fitness score (0-1, higher is better for attacker)
        """
        # Base fitness from damage
        fitness = damage

        # Penalty if detected
        if detected:
            chromosome.detection_count += 1
            fitness *= 0.5  # Cut fitness in half if detected

        return fitness

    def evolve_population(self, fitness_scores: List[float]) -> List[AttackChromosome]:
        """
        Selection + mutation + crossover to evolve next generation.

        Args:
            fitness_scores: Fitness value for each chromosome

        Returns:
            New population
        """
        # Normalize fitness
        if max(fitness_scores) > 0:
            normalized = np.array(fitness_scores) / max(fitness_scores)
        else:
            normalized = np.ones(len(fitness_scores)) / len(fitness_scores)

        # Selection (roulette wheel)
        indices = np.random.choice(
            len(self.population),
            size=self.population_size,
            p=normalized / normalized.sum(),
            replace=True
        )

        new_population = []
        for i in range(self.population_size):
            if np.random.random() < self.crossover_rate and i < self.population_size - 1:
                # Crossover
                parent1 = self.population[indices[i]]
                parent2 = self.population[indices[i + 1]]
                child = self.crossover(parent1, parent2)
            else:
                # Copy parent
                child = AttackChromosome(
                    metric_targets=self.population[indices[i]].metric_targets.copy(),
                    magnitudes=self.population[indices[i]].magnitudes.copy(),
                    durations=self.population[indices[i]].durations.copy(),
                    delays=self.population[indices[i]].delays.copy(),
                )

            # Mutation
            child = self.mutate(child, [])
            new_population.append(child)

        return new_population

    def run_evolution(self,
                     metric_names: List[str],
                     fitness_callback) -> Dict:
        """
        Run full evolutionary optimization.

        Args:
            metric_names: Available metrics to attack
            fitness_callback: Function(attack_chromosome) -> (damage, detected)

        Returns:
            Dict with evolution results
        """
        self.population = self.generate_initial_population(metric_names)

        for gen in range(self.generations):
            # Evaluate fitness
            fitness_scores = []
            detections_this_gen = 0

            for chromosome in self.population:
                damage, detected = fitness_callback(chromosome)
                fitness = self.evaluate_fitness(chromosome, damage, detected)
                fitness_scores.append(fitness)

                if detected:
                    detections_this_gen += 1

            # Track
            best_fitness = max(fitness_scores) if fitness_scores else 0.0
            self.best_fitness_history.append(best_fitness)
            self.detection_history.append((gen, detections_this_gen))

            # Evolve
            self.population = self.evolve_population(fitness_scores)

        return {
            "total_generations": self.generations,
            "best_fitness": max(self.best_fitness_history),
            "average_fitness": np.mean(self.best_fitness_history),
            "fitness_history": self.best_fitness_history,
            "detection_history": self.detection_history,
            "total_evolved_attacks": len(self.population),
            "attack_detection_rate": sum(1 for _, detections in self.detection_history for _ in range(detections)) / (self.generations * self.population_size) if self.generations > 0 else 0.0,
        }
