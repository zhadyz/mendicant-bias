#!/usr/bin/env bash
# Mendicant Bias — intelligent status line for Claude Code
#
# CC pipes session state as JSON to stdin. We parse it to detect
# whether Mendicant is actively processing (hooks firing, MCP tools
# being called) and show dynamic status accordingly.
#
# When idle:    nothing (clean terminal)
# When active:  ⬡ MENDICANT BIAS ── thinking...

# Read CC's session state from stdin (non-blocking)
INPUT=""
if read -t 0.1 -r INPUT 2>/dev/null; then
    # Parse with Python for reliability
    STATUS=$(echo "$INPUT" | python3 -c "
import sys, json

try:
    data = json.load(sys.stdin)
except:
    print('idle')
    sys.exit(0)

# Check if Mendicant MCP tools are in the active tool list
# CC includes tool info in the session state
model_info = data.get('model', {})
model_name = model_info.get('display_name', '') if isinstance(model_info, dict) else ''

# Check context usage to show smart info
ctx = data.get('context_window', {})
used_pct = ctx.get('used_percentage', 0) if isinstance(ctx, dict) else 0
cost = data.get('cost', {})
total_cost = cost.get('total_cost_usd', 0) if isinstance(cost, dict) else 0

# Format cost
if total_cost > 0:
    cost_str = f'\${total_cost:.4f}'
else:
    cost_str = ''

# Format context
if used_pct > 0:
    ctx_str = f'ctx:{used_pct:.0f}%'
else:
    ctx_str = ''

# Build the segments
parts = []
if ctx_str:
    parts.append(ctx_str)
if cost_str:
    parts.append(cost_str)

if parts:
    print('active|' + ' '.join(parts))
else:
    print('idle')
" 2>/dev/null || echo "idle")
else
    STATUS="idle"
fi

# Now check if Mendicant gateway is actually processing
# Use a lockfile-based approach: hooks write a temp file when active
HOOK_ACTIVE_FILE="/tmp/mendicant_hook_active"
HOOK_TS=0
if [ -f "$HOOK_ACTIVE_FILE" ]; then
    HOOK_TS=$(stat -c %Y "$HOOK_ACTIVE_FILE" 2>/dev/null || stat -f %m "$HOOK_ACTIVE_FILE" 2>/dev/null || echo 0)
    NOW=$(date +%s)
    AGE=$(( NOW - HOOK_TS ))
    if [ "$AGE" -lt 30 ]; then
        HOOK_MSG=$(cat "$HOOK_ACTIVE_FILE" 2>/dev/null || echo "processing")
        # Blue ANSI: \033[34m ... \033[0m
        echo -e "\033[38;2;0;170;255m⬡ MENDICANT\033[0m \033[38;2;60;60;80m──\033[0m \033[38;2;0;170;255m${HOOK_MSG}\033[0m"
        exit 0
    fi
fi

# Check gateway status (fast, 200ms timeout)
HEALTH=$(curl -s --max-time 0.2 http://localhost:8001/health 2>/dev/null || echo "")

if echo "$HEALTH" | grep -q "mendicant-bias"; then
    # Gateway online
    case "$STATUS" in
        active*)
            DETAIL="${STATUS#active|}"
            echo -e "\033[38;2;0;170;255m⬡ MENDICANT\033[0m \033[38;2;60;60;80m──\033[0m \033[38;2;100;100;120m${DETAIL}\033[0m"
            ;;
        *)
            # Online but idle — show minimal presence
            echo -e "\033[38;2;0;170;255m⬡ MENDICANT\033[0m"
            ;;
    esac
elif command -v mendicant &>/dev/null; then
    # MCP available but gateway not running — minimal
    echo -e "\033[38;2;60;80;120m⬡ MENDICANT\033[0m"
fi
# If nothing is available, output nothing — clean terminal
