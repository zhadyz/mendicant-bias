#!/usr/bin/env bash
# Reset the message counter and activate Mendicant status badge
echo "0" > "$HOME/.mendicant_msg_count"

# Signal the statusline that a Mendicant session is active
echo "active" > "${TMPDIR:-/tmp}/mendicant_session"

echo "[Mendicant] Counter reset. Fresh context."
