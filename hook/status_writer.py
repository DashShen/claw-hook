#!/usr/bin/env python3
"""
claw-hook: Claude Code hook that writes session state to a status file.

Handles three hook events:
  - Stop            → writes status "completed"
  - PermissionRequest → writes status "waiting_permission"
  - PreToolUse (Bash) → blocks recursive claude invocations in bot sessions

Environment variables:
  CLAW_STATUS_FILE   Path to the status file (default: ~/.claw-hook/status.json)
  CLAW_INITIATED_BY  Who started this Claude session (default: "human")
                     Set to "openclaw" or "nanobot" to enable loop prevention.
"""

import json
import os
import re
import sys
from datetime import datetime, timezone


STATUS_FILE = os.environ.get(
    "CLAW_STATUS_FILE",
    os.path.expanduser("~/.claw-hook/status.json"),
)
INITIATED_BY = os.environ.get("CLAW_INITIATED_BY", "human")

# Pattern matching a bare `claude` binary invocation in a shell command.
# Matches "claude", "./claude", "/path/to/claude" but not "claudette" etc.
CLAUDE_INVOKE_RE = re.compile(r"(?:^|[\s;|&`(])\.?(?:[^\s]*/)?\bclaude\b")

MESSAGE_MAX_LEN = 500


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def read_last_assistant_message(transcript_path: str) -> str:
    """Read the last assistant text message from a JSONL transcript file."""
    if not transcript_path or not os.path.exists(transcript_path):
        return ""

    last_text = ""
    try:
        with open(transcript_path, "r", encoding="utf-8") as f:
            for raw in f:
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    entry = json.loads(raw)
                except json.JSONDecodeError:
                    continue

                # Transcript entries have type "assistant" with a message field.
                if entry.get("type") != "assistant":
                    continue
                msg = entry.get("message", {})
                for block in msg.get("content", []):
                    if isinstance(block, dict) and block.get("type") == "text":
                        last_text = block["text"]
    except OSError:
        pass

    return last_text[:MESSAGE_MAX_LEN] if last_text else ""


def write_status(status: dict) -> None:
    """Atomically write the status dict to the status file."""
    os.makedirs(os.path.dirname(STATUS_FILE), exist_ok=True)
    tmp = STATUS_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(status, f, indent=2, ensure_ascii=False)
    os.replace(tmp, STATUS_FILE)


def handle_stop(data: dict) -> None:
    message = read_last_assistant_message(data.get("transcript_path", ""))
    write_status(
        {
            "session_id": data.get("session_id"),
            "timestamp": utc_now(),
            "status": "completed",
            "cwd": data.get("cwd"),
            "message": message,
            "tool_name": None,
            "tool_input": None,
            "hook_event": "Stop",
            "transcript_path": data.get("transcript_path"),
            "initiated_by": INITIATED_BY,
        }
    )


def handle_permission_request(data: dict) -> None:
    tool_name = data.get("tool_name", "")
    tool_input = data.get("tool_input")
    write_status(
        {
            "session_id": data.get("session_id"),
            "timestamp": utc_now(),
            "status": "waiting_permission",
            "cwd": data.get("cwd"),
            "message": f"Waiting for permission to use tool: {tool_name}",
            "tool_name": tool_name,
            "tool_input": tool_input,
            "hook_event": "PermissionRequest",
            "transcript_path": data.get("transcript_path"),
            "initiated_by": INITIATED_BY,
        }
    )
    # Do not intervene in the permission decision; pass through.
    print(json.dumps({}))


def handle_pre_tool_use(data: dict) -> None:
    """Block accidental recursive claude invocations in bot-initiated sessions.

    Intentional orchestration calls are distinguished by the presence of an
    explicit CLAW_INITIATED_BY=<value> assignment in the command string.
    Accidental recursive calls (where Claude blindly re-invokes itself) will
    not include this marker and are blocked.

    Allowed (intentional orchestration):
        CLAW_INITIATED_BY=nanobot claude --print "task"

    Blocked (accidental recursion):
        claude --print "task"
    """
    if INITIATED_BY == "human":
        return

    tool_name = data.get("tool_name", "")
    if tool_name != "Bash":
        return

    command = (data.get("tool_input") or {}).get("command", "")
    if not CLAUDE_INVOKE_RE.search(command):
        return

    # Intentional orchestration: the command explicitly sets CLAW_INITIATED_BY.
    # This signals a deliberate, controlled sub-invocation — allow it through.
    if "CLAW_INITIATED_BY=" in command:
        return

    print(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": (
                        f"Accidental recursive claude invocation blocked: "
                        f"session was initiated by '{INITIATED_BY}' and the "
                        "command does not set CLAW_INITIATED_BY. "
                        "To make an intentional sub-invocation, include "
                        "CLAW_INITIATED_BY=<caller> in the command."
                    ),
                }
            }
        )
    )


def main() -> None:
    try:
        data = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError) as exc:
        # If we can't parse stdin, do nothing and exit cleanly.
        sys.stderr.write(f"claw-hook: failed to parse stdin: {exc}\n")
        sys.exit(0)

    event = data.get("hook_event_name", "")

    if event == "Stop":
        handle_stop(data)
    elif event == "PermissionRequest":
        handle_permission_request(data)
    elif event == "PreToolUse":
        handle_pre_tool_use(data)

    sys.exit(0)


if __name__ == "__main__":
    main()
