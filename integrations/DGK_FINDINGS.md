# Defects found in DGK while integrating (not fixed here)

Found by running DGK's real code (commit `da256f314d0b80cb79a3b4652a89dea1cf60ca3d`, Python 3.11) with hostile telemetry. DGK is a separate repository and was not changed. `integrations/dgk_governed.py` works around all of these; stock DGK still has them.

## Results (one fresh kernel per cell, an authorized caller, otherwise calm telemetry)

| Value in one field | latency / abort_rate / reentry_rate | load_depth / determinism_index |
|---|---|---|
| `NaN` | **COMMITTED** (limit bypassed) | **COMMITTED** |
| `+inf`, `-inf` | **unhandled `ValueError`** | **unhandled `ValueError`** |
| `-10000`, `-1e9` (latency) | **unhandled `ValueError`** | **unhandled `ValueError`** |
| small negatives (`-0.001` to `-601`) | **COMMITTED** (a latency of -600 passes `> 500`) | COMMITTED |
| `1e308` | REJECTED (correct) | COMMITTED |

## The three defects

1. **NaN bypasses the health limits.** `HealthLimitCheck.verify_bounds` uses `value > limit`, which is `False` for NaN, so a NaN reading in any field is committed to the ledger and the audit trail.
2. **One NaN permanently corrupts the classifier.** After a single NaN transaction `stability_profile["lyapunov_energy"]` is NaN for the life of the process, and the regime sequence diverges from a clean kernel's (in the reproduction it never reaches `ANOMALOUS_DRIFT`). The poisoned statistics are in `RunningStats` and are not reset.
3. **Infinities and large negatives raise out of `process_transaction`.** `calculate_entropy` (`dgk/stability.py`) takes `math.log2` of a normalized probability that can be outside (0, 1], raising `ValueError`. The exception escapes after authentication and before any refusal is recorded, so the failed call leaves no trace in the audit trail. A negative latency from clock skew is enough in practice for the small-negative case to commit nonsense, and a large one to crash.

All three need an authorized caller, so this is robustness and integrity, not an unauthenticated attack.

## Reproduce

```python
import math, os, secrets, tempfile
from dgk import Kernel

def fresh():
    k = Kernel(log_path=os.path.join(tempfile.mkdtemp(), "a.log"))
    tok = secrets.token_urlsafe(32); k.callers.register("ops", tok, partitions=["p1"]); return k, tok
CALM = {"latency": 100.0, "abort_rate": 0.01, "reentry_rate": 0.1, "load_depth": 100.0, "determinism_index": 0.99}

k, tok = fresh()
print(k.process_transaction("p1", {**CALM, "latency": math.nan}, "hi", "ops", tok)["transaction_status"])   # COMMITTED
print(k.process_transaction("p1", CALM, "hi", "ops", tok)["stability_profile"]["lyapunov_energy"])           # nan
k, tok = fresh()
k.process_transaction("p1", {**CALM, "latency": math.inf}, "hi", "ops", tok)                                # ValueError
```

## Suggested fix (for the DGK repository)

Validate once, at the door, and refuse with a recorded cause instead of raising. In `Kernel._run_transaction`, after the identity check and before `_read_telemetry` is used:

```python
for name in ("latency", "abort_rate", "reentry_rate", "load_depth", "determinism_index"):
    v = float(telemetry_map.get(name, 0.0))
    if not math.isfinite(v) or v < 0:
        return self._refuse(partition_id, caller_id, f"telemetry {name} is not a finite non-negative number",
                            "TELEMETRY_INVALID")
```

and make `HealthLimitCheck.verify_bounds` fail closed on non-finite values (`not math.isfinite(v) or v > limit`). Add a regression test per cell of the table above. `tests/test_integration_dgk.py` in this repository contains equivalents that can be copied.
