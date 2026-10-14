# Reproducibility

## Environment

| Item | Value |
|------|--------|
| Python | ≥ 3.10 (developed on 3.12) |
| OS | Linux (any recent distro) |
| Core deps | `pytest`, `numpy` (see `requirements.txt`) |

## One-command test

```bash
pip install -r requirements.txt
./scripts/run_all_tests.sh
# or: python -m pytest tests/ -q
```

Expected: **460+ tests passed** in roughly 20–40 seconds on a modern laptop CPU.

## Multi-seed protocol (Phase 10A)

```bash
python scripts/run_multiseed.py --seeds 200 --out results/multiseed_summary.json   # reference results (about 30 s)
python scripts/run_multiseed.py --seeds 10 --strict    # exit 1 unless every 95% interval supports its target
```

## STACK trace replays

```bash
python experiments/stack_trace_sim.py --csv testdata/traces/stack_trace_v1.csv        # Python model of the kernel, ~7 s
python experiments/stack_trace_multiseed.py --seeds 20 --sweep-seeds 10              # 20 fresh seeds x 3 rosters + sweep, ~4 min on 4 cores
python -m pytest tests/test_trace_sim.py -q                                            # model vs the kernel's committed results, ~16 s
python experiments/stack_trace_multiseed.py --seeds 20 --seed-base 4000 --policies recommended,validated --no-sweep --tag _validated_bounded
PYTHONPATH=. python -m experiments.scorecard                                          # criterion 10 reads the file above
# reference (needs the Rust kernel example built; see experiments/stack_trace_replay.py):
GOVERNED_WORKLOAD_BIN=... python experiments/stack_trace_replay.py --csv testdata/traces/stack_trace_v1.csv
```

## Notes

- Timings are approximate and hardware-dependent.
- Some Phase 9 helpers emit benign NumPy empty-slice warnings under scale tests.
- Authority invariant (no auto-LOOSEN / auto-DISABLE) is checked inside the suite and multi-seed runner.
