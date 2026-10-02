"""
Phase 9C: Evolutionary Adversary - Genetic Algorithm Attack Generation

Tests whether the generative detection system can detect novel attacks
produced by evolutionary search over attack parameters.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional
import numpy as np


@dataclass
class AttackChromosome:
    """Attack strategy genome."""
    metric_targets: List[str]
    magnitudes: List[float]
    durations: List[float]
    delays: List[float]
    fitness: float = 0.0


class EvolutionaryAdversary:
    """Genetic algorithm for generating novel multi-metric attacks."""

    def __init__(self, population_size: int = 20, generations: int = 10, mutation_rate: float = 0.2):
        self.population_size = population_size
        self.generations = generations
        self.mutation_rate = mutation_rate
        self.population: List[AttackChromosome] = []
        self.history: List[Dict] = []

    def generate_initial_population(self, metrics: List[str]) -> List[AttackChromosome]:
        population = []
        for _ in range(self.population_size):
            n = max(1, np.random.randint(1, min(4, len(metrics) + 1)))
            targets = list(np.random.choice(metrics, size=n, replace=False))
            chromosome = AttackChromosome(
                metric_targets=targets,
                magnitudes=[float(np.random.uniform(1.5, 5.0)) for _ in targets],
                durations=[float(np.random.uniform(1.0, 20.0)) for _ in targets],
                delays=[float(np.random.uniform(0.0, 10.0)) for _ in targets],
            )
            population.append(chromosome)
        self.population = population
        return population

    def mutate(self, chromosome: AttackChromosome, metrics: List[str]) -> AttackChromosome:
        mags = list(chromosome.magnitudes)
        durs = list(chromosome.durations)
        dels = list(chromosome.delays)
        targets = list(chromosome.metric_targets)

        if np.random.random() < self.mutation_rate and mags:
            i = np.random.randint(0, len(mags))
            mags[i] = float(np.clip(mags[i] * np.random.uniform(0.5, 1.5), 0.5, 10.0))
        if np.random.random() < self.mutation_rate and durs:
            i = np.random.randint(0, len(durs))
            durs[i] = float(np.clip(durs[i] * np.random.uniform(0.5, 1.5), 0.5, 30.0))
        if np.random.random() < self.mutation_rate and dels:
            i = np.random.randint(0, len(dels))
            dels[i] = float(np.clip(dels[i] + np.random.uniform(-2, 2), 0.0, 20.0))

        return AttackChromosome(
            metric_targets=targets,
            magnitudes=mags,
            durations=durs,
            delays=dels,
            fitness=chromosome.fitness,
        )

    def crossover(self, parent1: AttackChromosome, parent2: AttackChromosome, *_args) -> AttackChromosome:
        """Cross over two chromosomes. Extra args ignored for API compatibility."""
        if len(parent1.metric_targets) < 2 or len(parent2.metric_targets) < 2:
            return parent1

        point = int(np.random.randint(1, len(parent1.metric_targets)))

        child = AttackChromosome(
            metric_targets=parent1.metric_targets[:point] + parent2.metric_targets[point:],
            magnitudes=parent1.magnitudes[:point] + parent2.magnitudes[point:],
            durations=parent1.durations[:point] + parent2.durations[point:],
            delays=parent1.delays[:point] + parent2.delays[point:],
        )
        return child

    def evaluate_fitness(self, chromosome: AttackChromosome, damage: float, detected: bool) -> float:
        fitness = damage
        if detected:
            fitness *= 0.3
        chromosome.fitness = fitness
        return fitness

    def select_parents(self) -> tuple:
        ranked = sorted(self.population, key=lambda c: c.fitness, reverse=True)
        if len(ranked) < 2:
            return ranked[0], ranked[0]
        return ranked[0], ranked[1]

    def evolve_generation(self, metrics: List[str]) -> List[AttackChromosome]:
        new_pop = []
        ranked = sorted(self.population, key=lambda c: c.fitness, reverse=True)
        elite_n = max(1, self.population_size // 5)
        new_pop.extend(ranked[:elite_n])

        while len(new_pop) < self.population_size:
            p1, p2 = self.select_parents()
            child = self.crossover(p1, p2)
            child = self.mutate(child, metrics)
            new_pop.append(child)

        self.population = new_pop[: self.population_size]
        return self.population

    def run_evolution(self, metrics: List[str], evaluate_fn) -> AttackChromosome:
        self.generate_initial_population(metrics)
        best = self.population[0]
        for gen in range(self.generations):
            for chrom in self.population:
                damage, detected = evaluate_fn(chrom)
                self.evaluate_fitness(chrom, damage, detected)
            ranked = sorted(self.population, key=lambda c: c.fitness, reverse=True)
            if ranked[0].fitness > best.fitness:
                best = ranked[0]
            self.history.append({"generation": gen, "best_fitness": ranked[0].fitness})
            self.evolve_generation(metrics)
        return best
