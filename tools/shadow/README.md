# Shadow test tooling (metadata only, never blocks)

1. Add `shadow_hook.py` as a Claude Code hook for PreToolUse and PostToolUse (all tools). It writes to `~/.stack_shadow/calls.jsonl`.
2. Run `python3 shadow_watcher.py` in the background. It samples CPU use of the running call's processes each second.
3. Weekly: `python3 shadow_report.py review --deadline 60`, fill the `label` column with `stuck` or `working`, then `python3 shadow_report.py score --labels review.csv`.

The report scores two definitions of no progress: CPU idle, and no bytes read or written. Limits (see docs/SHADOW_TEST_PLAN.md): CPU idleness detects hangs and waits, the I/O check adds silent loops (and may flag honest silent computation); concurrent tool calls are excluded; nothing is stored about commands, paths or outputs.
