#!/usr/bin/env bash
# Mendicant Bias — Context Lifecycle Monitor
# Runs as a Claude Code Stop hook after every assistant response.
# Tracks message count and injects warnings into model context when approaching limits.

COUNTER_FILE="$HOME/.mendicant_msg_count"
CHECKPOINT_DIR="$HOME/Desktop/Project_Hail_Mary/mendicant_bias/.mendicant"

WARNING_THRESHOLD=80
CRITICAL_THRESHOLD=120
CHECKPOINT_INTERVAL=30

# Initialize counter if missing
if [ ! -f "$COUNTER_FILE" ]; then
    echo "0" > "$COUNTER_FILE"
fi

# Increment
COUNT=$(cat "$COUNTER_FILE" 2>/dev/null || echo "0")
COUNT=$((COUNT + 1))
echo "$COUNT" > "$COUNTER_FILE"

# Periodic checkpoint reminder
if [ $((COUNT % CHECKPOINT_INTERVAL)) -eq 0 ] && [ "$COUNT" -gt 0 ]; then
    cat <<EOJSON
{
  "hookSpecificOutput": {
    "hookEventName": "Stop",
    "additionalContext": "[Mendicant Context Monitor] Message $COUNT of ~$CRITICAL_THRESHOLD. Consider writing a checkpoint to $CHECKPOINT_DIR/checkpoint.json with: current task, completed steps, pending steps, key decisions, active files."
  }
}
EOJSON
    exit 0
fi

# Warning threshold
if [ "$COUNT" -eq "$WARNING_THRESHOLD" ]; then
    cat <<EOJSON
{
  "hookSpecificOutput": {
    "hookEventName": "Stop",
    "additionalContext": "[Mendicant Context Monitor] WARNING: $COUNT messages. Context quality may degrade. Write a structured checkpoint NOW to $CHECKPOINT_DIR/checkpoint.json. Include: current task, completed steps, pending steps, key decisions, active files."
  }
}
EOJSON
    exit 0
fi

# Critical threshold
if [ "$COUNT" -ge "$CRITICAL_THRESHOLD" ]; then
    cat <<EOJSON
{
  "hookSpecificOutput": {
    "hookEventName": "Stop",
    "additionalContext": "[Mendicant Context Monitor] CRITICAL: $COUNT messages. Context is degraded. Write final checkpoint to $CHECKPOINT_DIR/checkpoint.json, then tell the user to start a fresh session. The next session reads the checkpoint to resume."
  }
}
EOJSON
    exit 0
fi
