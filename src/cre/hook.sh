#!/bin/bash
# Claude Rule Enforcer — Legacy shell hook wrapper
# Prefer using `cre gate` directly (installed via pip install claude-rule-enforcer)
# This script is kept for backward compatibility only.

# Check toggle file — zero overhead when disabled
if [ ! -f "$HOME/.claude/cre_enabled" ]; then
  echo '{}'
  exit 0
fi

INPUT=$(cat)
TOOL=$(echo "$INPUT" | jq -r '.tool_name // empty')

# Gate Bash, Write, and Edit tools
if [ "$TOOL" != "Bash" ] && [ "$TOOL" != "Write" ] && [ "$TOOL" != "Edit" ]; then
  echo '{}'
  exit 0
fi

# Try cre CLI first (if installed), fall back to direct Python
if command -v cre &>/dev/null; then
  RESULT=$(echo "$INPUT" | timeout 12 cre gate 2>/dev/null)
else
  CRE_GATE_DIR="${CRE_GATE_DIR:-$(cd "$(dirname "$0")" && pwd)}"
  RESULT=$(echo "$INPUT" | timeout 12 python3 -m cre.gate 2>/dev/null)
fi

PY_EXIT=$?

if [ $PY_EXIT -ne 0 ] && [ $PY_EXIT -ne 2 ]; then
  echo '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"deny","permissionDecisionReason":"Policy gate error (exit '$PY_EXIT') — retry or check logs at /tmp/cre.log"}}'
  exit 2
fi

if [ -z "$RESULT" ]; then
  echo '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"deny","permissionDecisionReason":"Policy gate returned empty response — retry or check logs"}}'
  exit 2
fi

echo "$RESULT"
exit $PY_EXIT
