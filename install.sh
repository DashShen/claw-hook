#!/usr/bin/env bash
# install.sh — Install claw-hooks into the current user's Claude Code setup.
#
# What this script does:
#   1. Copies hooks/status_writer.py to ~/.claw-hooks/hooks/
#   2. Merges hook configuration into ~/.claude/settings.json
#
# Usage:
#   bash install.sh
#   CLAW_STATUS_FILE=/custom/path/status.json bash install.sh

set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALL_DIR="${HOME}/.claw-hooks"
HOOK_SCRIPT="${INSTALL_DIR}/hooks/status_writer.py"

# ── 1. Install hook script ────────────────────────────────────────────────────

mkdir -p "${INSTALL_DIR}/hooks"
cp "${REPO_DIR}/hooks/status_writer.py" "${HOOK_SCRIPT}"
chmod +x "${HOOK_SCRIPT}"
echo "✓ Installed hook script → ${HOOK_SCRIPT}"

# ── 2. Merge hook configuration into ~/.claude/settings.json ─────────────────

if ! command -v python3 &>/dev/null; then
  echo "ERROR: python3 is required. Please install Python 3." >&2
  exit 1
fi

mkdir -p "${HOME}/.claude"

_CLAW_REPO_SETTINGS="${REPO_DIR}/settings.json" python3 <<'PYEOF'
import json, os, sys

settings_path = os.path.expanduser("~/.claude/settings.json")
repo_settings = os.environ.get("_CLAW_REPO_SETTINGS", "")

with open(repo_settings, "r") as f:
    new_config = json.load(f)

if os.path.exists(settings_path):
    with open(settings_path, "r") as f:
        try:
            existing = json.load(f)
        except json.JSONDecodeError:
            existing = {}
else:
    existing = {}

existing_hooks = existing.setdefault("hooks", {})
new_hooks = new_config.get("hooks", {})
changed = False

for event, entries in new_hooks.items():
    if event not in existing_hooks:
        existing_hooks[event] = entries
        changed = True
        print(f"  + Added hook event: {event}")
    else:
        our_cmd = entries[0]["hooks"][0]["command"]
        already = any(
            h.get("command") == our_cmd
            for entry in existing_hooks[event]
            for h in entry.get("hooks", [])
        )
        if not already:
            existing_hooks[event].extend(entries)
            changed = True
            print(f"  + Merged hook into existing event: {event}")
        else:
            print(f"  ~ Already registered: {event} (skipped)")

if changed:
    with open(settings_path, "w") as f:
        json.dump(existing, f, indent=2)
    print(f"✓ Updated {settings_path}")
else:
    print("✓ No changes needed — hooks already registered")
PYEOF

# ── 3. Done ───────────────────────────────────────────────────────────────────

echo ""
echo "Installation complete."
echo ""
echo "Status file will be written to: ${CLAW_STATUS_FILE:-${HOME}/.claw-hooks/status.json}"
echo ""
echo "To tell the hook that a session was started by OpenClaw, set:"
echo "  CLAW_INITIATED_BY=openclaw claude --print 'your task'"
echo ""
echo "Optional overrides via environment variables:"
echo "  CLAW_STATUS_FILE   — custom path for the status JSON file"
echo "  CLAW_INITIATED_BY  — who started the session (human / openclaw / nanobot)"
