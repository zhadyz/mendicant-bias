#!/usr/bin/env python3
"""
Mendicant Bias — Intelligent status line for Claude Code.

CC pipes the full session state as JSON to stdin on every refresh.
We parse it, query the Mendicant gateway for live session state,
and render a contextual status that reflects real-time activity.

Output uses ANSI 24-bit color (RGB).

Behavior:
  Processing:  ⬡ MENDICANT ── verifying Write...        (bright blue)
  Aware:       ⬡ MENDICANT ── RESEARCH ctx:42% $0.0312  (blue + context)
  Standby:     ⬡ MENDICANT                              (dim blue)
  Offline:     (nothing)
"""

import json
import os
import sys
import urllib.request

# Force UTF-8 output on Windows
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# Colors (24-bit ANSI)
BLUE = "\033[38;2;0;170;255m"
DIM = "\033[38;2;60;80;120m"
DIMMER = "\033[38;2;45;50;65m"
RESET = "\033[0m"
BOLD = "\033[1m"

ICON = "⬡"
NAME = "MENDICANT"
SEP = "──"

GATEWAY = "http://localhost:8001"


def _fetch_json(url: str, timeout: float = 0.3) -> dict | None:
    try:
        resp = urllib.request.urlopen(url, timeout=timeout)
        return json.loads(resp.read())
    except Exception:
        return None


def _parse_cc_state(raw: str) -> dict:
    """Parse CC's session state JSON from stdin."""
    try:
        return json.loads(raw)
    except Exception:
        return {}


def _render(parts: list[tuple[str, str]]) -> str:
    """Render colored segments. Each part is (color, text)."""
    return "".join(f"{color}{text}{RESET}" for color, text in parts)


def main():
    # Read CC's session state from stdin
    cc_state = {}
    try:
        raw = sys.stdin.read()
        if raw.strip():
            cc_state = _parse_cc_state(raw)
    except Exception:
        pass

    # Query Mendicant gateway for live hook status
    hook_status = _fetch_json(f"{GATEWAY}/hooks/status")
    gateway_alive = hook_status is not None

    if not gateway_alive:
        # Check if mendicant CLI exists at all
        import shutil
        if shutil.which("mendicant"):
            # Installed but gateway not running — dim presence
            print(_render([(DIM, f"{ICON} {NAME}")]))
        # else: nothing — completely clean terminal
        return

    sessions = hook_status.get("sessions", {})
    active = sessions.get("active_sessions", 0)
    verifications = sessions.get("total_verifications", 0)
    tool_calls = sessions.get("total_tool_calls", 0)

    # Check if a hook is currently processing (signal file)
    hook_msg = None
    try:
        with open("/tmp/mendicant_hook_active", "r") as f:
            import os, time
            stat = os.fstat(f.fileno())
            age = time.time() - stat.st_mtime
            if age < 15:  # Fresh signal
                hook_msg = f.read().strip()
    except (OSError, IOError):
        pass

    # Extract CC context info
    ctx = cc_state.get("context_window", {})
    used_pct = ctx.get("used_percentage", 0) if isinstance(ctx, dict) else 0
    cost_data = cc_state.get("cost", {})
    total_cost = cost_data.get("total_cost_usd", 0) if isinstance(cost_data, dict) else 0

    # Build the status line
    segments: list[tuple[str, str]] = []

    if hook_msg:
        # ACTIVE: Mendicant is processing right now
        segments.append((BLUE + BOLD, f"{ICON} {NAME}"))
        segments.append((DIMMER, f" {SEP} "))
        segments.append((BLUE, hook_msg))
    else:
        # AWARE: Mendicant is online, show contextual info
        segments.append((BLUE, f"{ICON} {NAME}"))

        # Build info parts
        info = []

        # Show context usage if significant
        if used_pct > 10:
            info.append(f"ctx:{used_pct:.0f}%")

        # Show verification count if any
        if verifications > 0:
            info.append(f"✓{verifications}")

        if info:
            segments.append((DIMMER, f" {SEP} "))
            segments.append((DIM, " ".join(info)))

    print(_render(segments))


if __name__ == "__main__":
    main()
