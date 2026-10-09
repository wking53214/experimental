# Trace test data (`stack_trace_v1`)

Synthetic task traces for the STACK governance loop, built to outlast the current experiments: many agents, many behaviours, three resources, 32 time slices. Generated deterministically by `experiments/gen_trace.py` (seed 20261009); `MANIFEST.json` holds row counts and SHA-256 so drift is detectable. **Synthetic**: durations, tokens and memory are drawn from distributions chosen by the generator. Nothing here is production data. A real trace with the same columns can replace `stack_trace_v1.csv` and be replayed unchanged.

## Files

| File | Rows | Purpose |
|---|---|---|
| `stack_trace_v1.csv` | 200,000 | One row per task. 25 agents x 8,000 tasks, about 30 days each |
| `stack_agents_v1.csv` | 25 | Per-agent ground truth: behaviour profile and whether tightening is the right response |
| `MANIFEST.json` | | Seed, counts, hashes, defaults |

## `stack_trace_v1.csv` columns

| Column | Meaning |
|---|---|
| `task_id` | Unique id (`<agent>-<seq>`) |
| `agent_id` | Agent (matches `stack_agents_v1.csv`) |
| `seq` | 0-based task index within the agent; time slice = `seq // 250` |
| `ts_s` | Start time, seconds since 2026-01-01T00:00:00Z |
| `day_index`, `hour_of_day` | Derived from `ts_s`; duration has a daily cycle |
| `team`, `host_id`, `kernel_version` | Context a real trace would carry; not used by the loop yet |
| `priority` | 0 low, 1 normal, 2 high (not used by the loop yet) |
| `capability` | Capability the task used (`read`, `write`, `net`, `exec`); for the future capability_denied limit |
| `input_size_bytes` | Task input size; mildly correlated with duration |
| `duration_ns` | How long the task would run if not preempted |
| `tokens_requested` | Tokens the task would use |
| `memory_requested_bytes` | Peak memory the task would use |
| `retry_count` | Retries so far (0 mostly) |
| `expected_load` | 1 if the scheduler declared this a heavy job in advance (the bridge's "expected load" hook) |
| `regime` | Generator's label for what this task is: `normal`, `runaway`, `regressed`, `drifting`, `leak`, `token_burst`, `batch`, `inflated`, `lowslow` |
| `is_bad` | 1 if the task is a genuine defect that a limit should cut (runaway, leaked memory over capacity, token burst, attacker task) |

Defaults used by the replay (also in `MANIFEST.json`): deadline budget 100 ms, tokens capacity 4,000, memory capacity 512 MiB.

## `stack_agents_v1.csv` columns

`agent_id, profile, should_tighten, team, description`. `should_tighten` is 1 when the right behaviour is for the limit to come down on its own, 0 when it is not (healthy, legitimately slow, scheduled batch, regressed). It is the label the replay scores tightening decisions against.
