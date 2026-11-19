# Shadow test: setup guide (v1)

For William N. King. About 10 minutes to set up, then about 10 minutes a week.

## What this does, in plain words
While you use Claude Code as normal, a small recorder notes when each tool call starts and ends, and a second small program checks once a second whether the call's processes are still doing anything (using the processor, or reading and writing). Nothing is stopped or changed. Once a week you look at a short list of long calls and mark each one "stuck" or "working". The scorer then tells us whether "looks idle" is a trustworthy sign of "stuck". That is the one thing that could beat the 8 to 11 points of saving per point of honest cost that duration alone gives.

## What it stores and what it never stores
Stored: the tool name, a scrambled session code, start and end times, and processor and read/write counts for the call. Never stored: commands, file names, file contents, outputs or prompts. Everything stays in one folder on your Chromebook: `~/.stack_shadow/`. Nothing is sent anywhere.

## Setup (one time)
1. **Get the tools.** In your Linux terminal, make sure the `experimental` repo is on your Chromebook and up to date (`git pull` on `main`). The tools are in `tools/shadow/`.
2. **Turn on the recorder.** Open (or create) the file `~/.claude/settings.json` and add a "hooks" section that runs `tools/shadow/shadow_hook.py` before and after every tool call (the "PreToolUse" and "PostToolUse" events, for all tools). If the file already has a "hooks" section, add to it, do not replace it. The recorder never blocks a call and never prints anything, so if it breaks, Claude Code carries on as usual.
3. **Start the watcher.** In a separate terminal tab run `python3 tools/shadow/shadow_watcher.py` and leave it open while you work. If you close it, nothing breaks; the calls from that time just have no activity readings.
4. **Check it works (the pilot).** Use Claude Code for a normal day. Then look for the file `~/.stack_shadow/calls.jsonl`: it should have lines in it. If it does not, tell me and I will fix the recorder.

## Each week (10 minutes)
1. Run `python3 tools/shadow/shadow_report.py review --deadline 60`. It writes `review.csv`: every call longer than 15 seconds plus a small random sample of shorter ones.
2. Open `review.csv` in any spreadsheet and fill the `label` column with `stuck` (it was hung, looping, or you cancelled it) or `working` (it was doing real work, even if slow). If you are not sure, leave it blank.
3. Run `python3 tools/shadow/shadow_report.py score --labels review.csv`. It prints two numbers and a verdict:
   - **r**: of the calls you called stuck, how many looked idle (we want this high).
   - **q**: of the calls you called working, how many wrongly looked idle (we want this low).
4. Send me the printed result (the `review.csv` has no commands or file names in it, so it is safe to share).

## What counts as a pass
Decided before any data: the idea is supported only if r is clearly at least 60% and q is clearly at most 10%. If r is clearly below 60% or q clearly above 10%, it is not supported. Anything in between means "keep collecting".

## Honest limits
- Calls that wait on the network look idle too, so q may come out high. That would be a real result, not a failure of the test.
- Calls that run in parallel are skipped, because activity cannot be assigned to one call.
- You may not label many real stuck calls in normal work. If r has too few examples, I can add a deliberately looping test tool to measure hangs only; that would not tell us about real runaways.
- It assumes Claude Code's own process starts the recorder. If the first day shows no activity readings, that assumption is wrong on your setup and I will adjust.

## If you want to stop
Delete the "hooks" section you added to `~/.claude/settings.json`, close the watcher tab, and delete the `~/.stack_shadow/` folder.
