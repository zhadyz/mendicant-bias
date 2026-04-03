#!/usr/bin/env python3
"""
Mendicant Bias — Intelligent status line for Claude Code.

ONLY shows when Mendicant is actively involved in processing.
If Claude Code is working on its own without Mendicant, the
status line is empty — clean terminal, no clutter.

Visible when:
  - A Mendicant hook is actively firing (classifying, verifying)
  - A Mendicant MCP tool was called in the current turn

Invisible when:
  - Normal Claude Code operation (no Mendicant involvement)
  - Gateway is offline
  - No hooks have fired recently
"""

import json
import os
import sys
import time
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

ICON = "\u2b21"  # ⬡
NAME = "MENDICANT"
SEP = "\u2500\u2500"  # ──

SIGNAL_FILE = "/tmp/mendicant_hook_active"
GATEWAY = "http://localhost:8001"


def _render(parts: list[tuple[str, str]]) -> str:
    return "".join(f"{color}{text}{RESET}" for color, text in parts)


def main():
    # Check if Mendicant is ACTIVELY processing right now
    # Signal file written by gateway hooks, cleared when done
    hook_msg = None
    try:
        with open(SIGNAL_FILE, "r") as f:
            stat = os.fstat(f.fileno())
            age = time.time() - stat.st_mtime
            if age < 10:  # Signal expires after 10 seconds
                hook_msg = f.read().strip()
    except (OSError, IOError):
        pass

    if hook_msg:
        # Mendicant is actively thinking — show bright blue with activity
        print(_render([
            (BLUE + BOLD, f"{ICON} {NAME}"),
            (DIMMER, f" {SEP} "),
            (BLUE, hook_msg),
        ]))
        return

    # No active hook — output nothing. Clean terminal.
    # Mendicant is invisible when it's not involved.


if __name__ == "__main__":
    main()
