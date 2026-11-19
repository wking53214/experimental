#!/usr/bin/env python3
"""Claude Code PreToolUse/PostToolUse hook: record call metadata only. Never blocks, never prints, never raises.

Stores: event type, tool_use_id, session id (hashed), tool name, timestamp, and the parent pid (the Claude process, for
the watcher). It does NOT store commands, file paths, arguments or outputs."""
import hashlib, json, os, sys, time
LOG = os.environ.get("STACK_SHADOW_LOG", os.path.expanduser("~/.stack_shadow/calls.jsonl"))

def main():
    try:
        ev = json.load(sys.stdin)
        name = ev.get("hook_event_name", "")
        kind = {"PreToolUse": "start", "PostToolUse": "end"}.get(name)
        if kind is None:
            return
        rec = {"t": kind, "id": str(ev.get("tool_use_id", ""))[:80], "ts": time.time(), "tool": str(ev.get("tool_name", ""))[:60],
               "sess": hashlib.sha256(str(ev.get("session_id", "")).encode()).hexdigest()[:10], "ppid": os.getppid()}
        os.makedirs(os.path.dirname(LOG), exist_ok=True)
        with open(LOG, "a") as f:
            f.write(json.dumps(rec) + "\n")
    except Exception:
        pass

if __name__ == "__main__":
    main()
