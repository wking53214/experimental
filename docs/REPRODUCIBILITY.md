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

## Notes

- Timings are approximate and hardware-dependent.
- Some Phase 9 helpers emit benign NumPy empty-slice warnings under scale tests.
- Authority invariant (no auto-LOOSEN / auto-DISABLE) is checked inside the suite and multi-seed runner.
