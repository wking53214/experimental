# Shadow test plan (live, observe-only)

**Purpose.** Round 26 simulated a progress heartbeat and found its value rests on two assumed numbers. This test measures them on real work.
- r: of long calls a person labels "stuck", the share that show no progress.
- q: of long calls labeled "working", the share that falsely show no progress.

**Mode.** Shadow: nothing is killed or changed. Tooling is in `tools/shadow/` (hook, watcher, report). Metadata only: tool name, hashed session, timing, CPU use of the call's processes. No commands, paths or outputs are stored.

**Where.** William's own Claude Code sessions via tool-call hooks (decided), after a one-week pilot on one machine to check the signals are usable.

**Labels.** Weekly review list: every long call (over 0.25 of the deadline, default 60 s) plus a 3% random sample of others. Each is labeled stuck or working by a person. Outcome clues (cancelled, immediately retried) may be used to help.

**Pre-registered pass rule (before any real data).** Heartbeat supported only if the 95% Wilson interval for r lies entirely at or above 0.6 and the interval for q lies entirely at or below 0.1. NOT SUPPORTED if r's interval lies below 0.6 or q's lies above 0.1. Otherwise UNDECIDED and collection continues. The scorer (`shadow_report.py score`) prints this verdict.

**Known limits, stated now.**
1. CPU idleness detects hangs and waits. A busy loop burns CPU and looks like progress, so this signal cannot see loops. Output growth would be needed and is not collected in v1. The pilot should tell us how much that matters.
2. A waiting-on-network honest call is also CPU idle, so q may be high for exactly the calls that matter. That would be a result, not a failure of the test.
3. Parallel tool calls are excluded because CPU cannot be attributed to one call.
4. The hook's parent process is assumed to be the Claude process. If that is wrong on the Chromebook setup, the pilot will show zero samples.
5. About 100 labeled long honest calls are needed to pin q to roughly 5 points; real runaways may be too rare to measure r from normal work alone. A deliberately looping test tool can measure detection of a hang (r for hangs only) and is not evidence about real runaways.

**Pilot success criteria.** At least 90% of long calls have 5 or more CPU samples; no hook failure blocks a tool; the weekly review takes under 10 minutes.
