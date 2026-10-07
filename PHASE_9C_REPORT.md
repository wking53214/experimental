# Phase 9C: Evolutionary Adversary

**Status:** Implemented and tested | **Tests:** 5 in `tests/test_phase9b_e_integration.py` plus 12 in `tests/test_phase9c_attack_kinds.py`, all passing | **Source:** `src/governance/phase9c_evolutionary.py` (153 lines)

## What it is
A genetic algorithm that evolves attack strategies, to test how robust a detector is against attacks nobody wrote by hand. The caller supplies the fitness: a function from an attack to `(damage, detected)`.

## How it works
- **Genome (`AttackChromosome`)**: target metrics, a magnitude, duration and delay for each, and a kind for each (`kinds`).
- **Population**: 20 attacks per generation, 10 generations, by default. Initial magnitudes 1.5 to 5.0, durations 1 to 20, delays 0 to 10.
- **Fitness**: damage, multiplied by 0.3 if the attack was detected.
- **Next generation**: the top 20% are kept unchanged; the rest are made by single-point crossover of the two fittest parents, then mutated (probability 0.2 per attribute type). `run_evolution` returns the best attack found.
- **Attack kinds** (`attack_kinds`, default shift only, which behaves exactly as before): `shift` moves the mean; `variance` scales the spread around the mean; `oscillate` alternates the sign of a shift each step; `decorrelate` blends in independent noise so the metric keeps its mean and spread but loses its link to the others. The effects are applied by `apply_attack` in `experiments/phase9_end_to_end.py`, not in `src`.

## Measured
200 attacks (20 x 10) evolve in about 3 ms with a trivial fitness function; the cost in practice is the fitness evaluations.

## What the experiments show
See `PHASE9_EXPERIMENT_RESULTS.md`. Against every detector with an acceptable false-positive rate, evolution found attacks that evade it, typically small shifts sustained over many steps. Detection of the default generative detector fell from 84% to 48% over 10 generations. Attacks evolved against the hardened detector sit near the chance floor for weak variants.

## Integration
Used only by the experiments in `experiments/`. It is not wired into the governor or the detector pipeline.

## Limitations
- Parent selection always uses the two fittest attacks, so diversity is low.
- "Damage" is defined by the caller; the experiments use magnitude times duration, which is not comparable across attack kinds.
- Detection counts any alarm during the attack window, so false alarms inflate detection rates (a chance floor of about 35-40% for a 20-step window).
- Attack effects are synthetic and applied to synthetic Gaussian data.
