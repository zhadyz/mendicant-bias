#!/usr/bin/env bash
# Mendicant Bias status line for Claude Code
# Shows: [MENDICANT] middleware status + memory count + pattern count

# Check if gateway is running
HEALTH=$(curl -s --max-time 1 http://localhost:8001/health 2>/dev/null || echo "")
if echo "$HEALTH" | grep -q "mendicant-bias"; then
    # Gateway is live — get real stats
    STATUS=$(curl -s --max-time 1 http://localhost:8001/hooks/status 2>/dev/null || echo "{}")
    SESSIONS=$(echo "$STATUS" | python3 -c "import sys,json; print(json.load(sys.stdin).get('sessions',{}).get('active_sessions',0))" 2>/dev/null || echo "0")
    VERIFICATIONS=$(echo "$STATUS" | python3 -c "import sys,json; print(json.load(sys.stdin).get('sessions',{}).get('total_verifications',0))" 2>/dev/null || echo "0")
    echo "⬡ MENDICANT BIAS │ ONLINE │ sessions:${SESSIONS} verified:${VERIFICATIONS}"
else
    # Gateway offline — check if MCP is configured
    if command -v mendicant &>/dev/null; then
        echo "⬡ MENDICANT BIAS │ MCP READY"
    else
        echo "⬡ MENDICANT BIAS │ OFFLINE"
    fi
fi
