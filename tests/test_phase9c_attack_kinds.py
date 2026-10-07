"""Tests for Phase 9C attack families."""
import numpy as np
import pytest

from experiments.phase9_end_to_end import CORR, METRICS, MEANS, SD, apply_attack, sample
from src.governance.phase9c_evolutionary import (
    ATTACK_KINDS, AttackChromosome, EvolutionaryAdversary,
)

M = ["a", "b", "c", "d"]


def test_default_is_shift_only():
    adv = EvolutionaryAdversary(population_size=10)
    pop = adv.generate_initial_population(M)
    assert all(set(c.kinds) == {"shift"} for c in pop)


def test_single_kind_consumes_no_extra_randomness():
    np.random.seed(5)
    a = EvolutionaryAdversary(population_size=6).generate_initial_population(M)
    np.random.seed(5)
    b = EvolutionaryAdversary(population_size=6, attack_kinds=("shift",)).generate_initial_population(M)
    assert [c.magnitudes for c in a] == [c.magnitudes for c in b]


def test_invalid_kind_rejected():
    with pytest.raises(ValueError):
        EvolutionaryAdversary(attack_kinds=("shift", "bogus"))
    with pytest.raises(ValueError):
        EvolutionaryAdversary(attack_kinds=())


def test_multi_kind_population_mixes_kinds_and_stays_aligned():
    np.random.seed(0)
    adv = EvolutionaryAdversary(population_size=40, attack_kinds=ATTACK_KINDS)
    pop = adv.generate_initial_population(M)
    assert len({k for c in pop for k in c.kinds}) > 1
    for c in pop:
        assert len(c.kinds) == len(c.metric_targets) == len(c.magnitudes)
        assert set(c.kinds) <= set(ATTACK_KINDS)


def test_mutation_and_crossover_keep_kinds_aligned():
    np.random.seed(1)
    adv = EvolutionaryAdversary(population_size=20, mutation_rate=1.0, attack_kinds=ATTACK_KINDS)
    pop = adv.generate_initial_population(M)
    for p1, p2 in zip(pop, pop[1:]):
        child = adv.mutate(adv.crossover(p1, p2), M)
        assert len(child.kinds) == len(child.metric_targets) == len(child.magnitudes)
        assert set(child.kinds) <= set(ATTACK_KINDS)


def test_chromosome_without_kinds_still_crosses_over():
    adv = EvolutionaryAdversary()
    a = AttackChromosome(["x", "y"], [1.5, 2.0], [3, 4], [0, 1])
    b = AttackChromosome(["y", "z"], [2.5, 1.2], [5, 6], [1, 2])
    child = adv.crossover(a, b)
    assert len(child.kinds) == len(child.metric_targets)


def test_full_evolution_runs_with_all_kinds():
    adv = EvolutionaryAdversary(population_size=8, generations=3, attack_kinds=ATTACK_KINDS)
    best = adv.run_evolution(M, lambda c: (0.5, False))
    assert best.kinds is not None


class TestEffects:
    N = 4000

    def _stream(self, kind, mag, metric="latency"):
        rng = np.random.default_rng(0)
        out = []
        for i in range(self.N):
            o = sample(rng)
            apply_attack(o, metric, kind, mag, i, rng)
            out.append([o[m] for m in METRICS])
        return np.array(out)

    def test_variance_attack_preserves_mean_and_scales_spread(self):
        x = self._stream("variance", 3.0)
        j = METRICS.index("latency")
        assert x[:, j].mean() == pytest.approx(MEANS[j], abs=0.1 * SD[j])
        assert x[:, j].std() == pytest.approx(3.0 * SD[j], rel=0.1)

    def test_oscillation_averages_to_zero_but_swings(self):
        x = self._stream("oscillate", 4.0)
        j = METRICS.index("latency")
        assert x[:, j].mean() == pytest.approx(MEANS[j], abs=0.1 * SD[j])
        assert x[:, j].std() > 2.0 * SD[j]

    def test_decorrelate_keeps_marginals_but_breaks_correlation(self):
        x = self._stream("decorrelate", 5.0)  # full replacement with independent noise
        j, q = METRICS.index("latency"), METRICS.index("queue_depth")
        assert x[:, j].mean() == pytest.approx(MEANS[j], abs=0.1 * SD[j])
        assert x[:, j].std() == pytest.approx(SD[j], rel=0.1)
        assert CORR[j, q] > 0.7
        assert abs(np.corrcoef(x[:, j], x[:, q])[0, 1]) < 0.15

    def test_shift_moves_the_mean(self):
        x = self._stream("shift", 3.0)
        j = METRICS.index("latency")
        assert x[:, j].mean() == pytest.approx(MEANS[j] + 2.0 * SD[j], abs=0.1 * SD[j])

    def test_unknown_kind_raises(self):
        with pytest.raises(ValueError):
            apply_attack(sample(np.random.default_rng(0)), "latency", "bogus", 2.0, 0, np.random.default_rng(0))
